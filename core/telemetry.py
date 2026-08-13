"""运维遥测:延迟/成本/利用率埋点与聚合。

- AgentRecord:单次 agent run 的记录(步数/token/墙钟/触顶)。
- TelemetrySink:线程安全收集器,注入 AgentLoop;按 name 卷起多实例(researcher 并发)。
- CountingClient:包装裁判 client,Lock 下累加 usage 与调用次数,零改 judge 契约。
- compute_cost / build_telemetry / aggregate_telemetry:纯函数。
"""
import threading
from dataclasses import dataclass


@dataclass
class AgentRecord:
    name: str
    steps: int
    max_steps: int
    prompt_tokens: int
    completion_tokens: int
    wall_s: float
    hit_max: bool


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
                                        "wall_s": 0.0, "hit_max": 0})
            d["n"] += 1
            d["total_steps"] += r.steps
            d["max_steps"] = max(d["max_steps"], r.max_steps)
            d["prompt_tokens"] += r.prompt_tokens
            d["completion_tokens"] += r.completion_tokens
            d["wall_s"] += r.wall_s
            d["hit_max"] += int(r.hit_max)
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
