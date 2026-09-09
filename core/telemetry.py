"""运维遥测:延迟/成本/利用率埋点与聚合。

- AgentRecord:单次 agent run 的记录(步数/token/墙钟/触顶)。
- TelemetrySink:线程安全收集器,注入 AgentLoop;按 name 卷起多实例(researcher 并发)。
- CountingClient:包装裁判 client,Lock 下累加 usage 与调用次数,零改 judge 契约。
- compute_cost / build_telemetry / aggregate_telemetry:纯函数。
"""
import threading
from dataclasses import dataclass

from llm.base import LLMClient, LLMResponse


@dataclass
class AgentRecord:
    name: str
    steps: int
    max_steps: int
    prompt_tokens: int
    completion_tokens: int
    wall_s: float
    hit_max: bool
    degraded: bool = False      # P1:partial 降级产出
    stop_reason: str = ""       # "" 正常 | "max_steps" | "token_budget"
    compactions: int = 0        # P2:压缩触发次数


class TelemetrySink:
    def __init__(self):
        self._lock = threading.Lock()
        self._records: list[AgentRecord] = []

    def record(self, rec: AgentRecord) -> None:
        with self._lock:
            self._records.append(rec)

    def snapshot(self) -> list[dict]:
        with self._lock:
            return [r.__dict__ for r in self._records]

    def aggregate_by_name(self) -> dict:
        with self._lock:
            recs = list(self._records)
        out: dict = {}
        for r in recs:
            d = out.setdefault(r.name, {"name": r.name, "n": 0, "total_steps": 0,
                                        "max_steps": r.max_steps,
                                        "prompt_tokens": 0, "completion_tokens": 0,
                                        "wall_s": 0.0, "hit_max": 0,
                                        "degraded": 0, "compactions": 0})
            d["n"] += 1
            d["total_steps"] += r.steps
            d["max_steps"] = max(d["max_steps"], r.max_steps)
            d["prompt_tokens"] += r.prompt_tokens
            d["completion_tokens"] += r.completion_tokens
            d["wall_s"] += r.wall_s
            d["hit_max"] += int(r.hit_max)
            d["degraded"] += int(r.degraded)
            d["compactions"] += r.compactions
        for d in out.values():
            d["mean_steps"] = d["total_steps"] / d["n"] if d["n"] else 0.0
            d["wall_s"] = d["wall_s"] / d["n"] if d["n"] else 0.0
        return out


def compute_cost(prompt_tokens, completion_tokens, pricing) -> float | None:
    """USD 成本 = pt/1e6*in + ct/1e6*out。pricing 缺失/缺键 → None。"""
    if not pricing:
        return None
    try:
        return (prompt_tokens / 1_000_000) * pricing["input_per_1m"] \
             + (completion_tokens / 1_000_000) * pricing["output_per_1m"]
    except (KeyError, TypeError):
        return None


class CountingClient(LLMClient):
    """包装裁判 client:委托 chat、Lock 下累加 usage 与调用次数。零改 judge 契约。"""

    def __init__(self, inner: LLMClient):
        self._inner = inner
        self._lock = threading.Lock()
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0

    def chat(self, *, messages, tools=None, model=None, temperature=None, max_tokens=None):
        resp = self._inner.chat(messages=messages, tools=tools, model=model,
                                temperature=temperature, max_tokens=max_tokens)
        u = resp.usage if isinstance(resp, LLMResponse) else {}
        u = u or {}
        with self._lock:
            self.calls += 1
            self.prompt_tokens += u.get("prompt_tokens", 0) or 0
            self.completion_tokens += u.get("completion_tokens", 0) or 0
        return resp

    def snapshot(self) -> dict:
        with self._lock:
            return {"calls": self.calls,
                    "prompt_tokens": self.prompt_tokens,
                    "completion_tokens": self.completion_tokens}


def build_telemetry(gen_rollup, judge_stat, judge_wall_s, question_wall_s, pricing) -> dict:
    """组装单题 telemetry(gen_rollup = sink.aggregate_by_name() 输出)。"""
    deepseek_p = (pricing or {}).get("deepseek")
    glm_p = (pricing or {}).get("glm")

    gen_pt = sum(d["prompt_tokens"] for d in gen_rollup.values())
    gen_ct = sum(d["completion_tokens"] for d in gen_rollup.values())
    gen_cost = compute_cost(gen_pt, gen_ct, deepseek_p)
    judge_cost = compute_cost(judge_stat.get("prompt_tokens", 0),
                              judge_stat.get("completion_tokens", 0), glm_p)
    total_cost = None if (gen_cost is None and judge_cost is None) \
                      else (gen_cost or 0) + (judge_cost or 0)

    utilization = {}
    for name, d in gen_rollup.items():
        ms = d.get("mean_steps", 0.0)
        mx = d.get("max_steps", 0) or 0
        utilization[name] = {"mean_steps": ms, "max_steps": mx,
                             "budget_used": (ms / mx) if mx else 0.0,
                             "hit_max": d.get("hit_max", 0),
                             "degraded": d.get("degraded", 0),
                             "compactions": d.get("compactions", 0)}

    return {
        "wall_s": question_wall_s,
        "generator": {"by_agent": list(gen_rollup.values()),
                      "totals": {"prompt_tokens": gen_pt, "completion_tokens": gen_ct,
                                 "cost_usd": gen_cost}},
        "judge": {"calls": judge_stat.get("calls", 0),
                  "prompt_tokens": judge_stat.get("prompt_tokens", 0),
                  "completion_tokens": judge_stat.get("completion_tokens", 0),
                  "wall_s": judge_wall_s, "cost_usd": judge_cost},
        "cost_usd": {"generator": gen_cost, "judge": judge_cost, "total": total_cost},
        "utilization": utilization,
    }


def aggregate_telemetry(per_question_telemetries, pricing=None) -> dict:
    """跨题聚合进 scorecard。per_question_telemetries: telemetry dict 列表(可含 None)。"""
    valid = [t for t in per_question_telemetries if t]
    n = len(valid)
    if n == 0:
        return {"n_questions": 0, "mean_wall_s": None, "total_cost_usd": None,
                "eval_wall_s": None, "agents": {}}
    mean_wall = sum(t.get("wall_s", 0.0) for t in valid) / n

    def _sum_cost(key):
        vals = [t["cost_usd"][key] for t in valid
                if t.get("cost_usd") and t["cost_usd"][key] is not None]
        return sum(vals) if vals else None

    g, j, tot = _sum_cost("generator"), _sum_cost("judge"), _sum_cost("total")
    total_cost = (None if (g is None and j is None and tot is None)
                  else {"generator": g, "judge": j, "total": tot})

    steps, budget, degraded, compactions = {}, {}, {}, {}
    for t in valid:
        for name, u in t.get("utilization", {}).items():
            steps.setdefault(name, []).append(u.get("mean_steps", 0.0))
            budget.setdefault(name, []).append(u.get("budget_used", 0.0))
            degraded.setdefault(name, []).append(u.get("degraded", 0))
            compactions.setdefault(name, []).append(u.get("compactions", 0))
    agents = {name: {"mean_steps": sum(v) / len(v),
                     "mean_budget_used": sum(budget[name]) / len(budget[name]),
                     "degraded_total": int(sum(degraded[name])),
                     "mean_compactions": sum(compactions[name]) / len(compactions[name])}
              for name, v in steps.items()}

    return {"n_questions": n, "mean_wall_s": mean_wall, "total_cost_usd": total_cost,
            "eval_wall_s": None, "agents": agents}
