# 运维遥测(延迟/成本/利用率)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给多 agent 系统加运维遥测——每次 eval run 产出延迟/成本/利用率三柱报告,作品集定位、最轻量。

**Architecture:** 一个 `TelemetrySink`(线程安全)注入 `AgentLoop`(唯一执行原语,自带 agent 名),单点覆盖生成链路的 token 聚合(顺修多步漏算 bug)+ 步数 + 墙钟;裁判链路用 `CountingClient` 包装 judge_client、零改契约计数。每题结果 JSON 加 `telemetry` 字段,scorecard 聚合,控制台打表。

**Tech Stack:** Python 3,pytest + pytest-mock,PyYAML,OpenAI SDK(已有),`threading.Lock`,`time.perf_counter`。零新依赖。

**对应 spec:** [docs/superpowers/specs/2026-08-13-m3-ops-telemetry-design.md](../specs/2026-08-13-m3-ops-telemetry-design.md)

**范围:** 核心遥测(sink + 两埋点 + pricing + eval 产物 + 测试)+ multi 5 题真跑填 README 样例(中等档)。compare 遥测 Δ 不做。

---

## 文件结构

| 文件 | 责任 | 动作 |
|---|---|---|
| `core/telemetry.py` | `AgentRecord`/`TelemetrySink`/`CountingClient`/`compute_cost`/`build_telemetry`/`aggregate_telemetry` | 新建 |
| `core/agent_loop.py` | `AgentLoop` 加 `recorder` 注入;`run()` 累加 token+计步+计墙钟+上报;`AgentResult.usage` 改聚合 | 改 |
| `agents/{researcher,verifier,writer,orchestrator}.py` | 各 `make_*` 加 `recorder=None` 透传给 `AgentLoop` | 改 |
| `agents/system.py` | `build_system` 加 `recorder=None`,透传 `make_orchestrator` + 三个 `_run_*` 闭包 | 改 |
| `agents/baseline.py` | `make_baseline`/`build_baseline_system` 加 `recorder=None`,透传 baseline loop + 内部 `make_writer` | 改 |
| `eval/run_eval.py` | `run_one` 建 sink 注入;`evaluate_question` 包 `CountingClient`+组装 telemetry;scorecard 聚合+控制台表 | 改 |
| `config.yaml` | `pricing` 段 | 改 |
| `tests/test_telemetry.py` | sink/compute_cost/CountingClient/build_telemetry/aggregate_telemetry/make_* wiring | 新建 |
| `tests/test_agent_loop.py` | 多步 token 聚合 + recorder 上报(收敛/触顶)+ 向后兼容 | 扩 |
| `tests/test_run_eval.py` | evaluate_question telemetry 字段 + judge 计数 + scorecard 聚合 | 扩 |
| `README.md` | "运维遥测"节 | 改 |

**测试约定:** 全 Fake/Mock 不烧 API(沿用本仓 FakeClient 模式);每个 Task 先写失败测试 → 跑红 → 实现 → 跑绿 → 提交。`pytest -q` 全程保持绿(改动均向后兼容:`recorder=None` 默认、telemetry 字段纯新增)。

---

## Task 1: `core/telemetry.py` — AgentRecord + TelemetrySink + compute_cost

**Files:**
- Create: `core/telemetry.py`
- Test: `tests/test_telemetry.py`

- [ ] **Step 1: 写失败测试**(新建 `tests/test_telemetry.py`)

```python
import threading

from core.telemetry import AgentRecord, TelemetrySink, compute_cost


def test_sink_records_and_snapshots():
    sink = TelemetrySink()
    sink.record(AgentRecord(name="researcher", steps=4, max_steps=6,
                            prompt_tokens=100, completion_tokens=20,
                            wall_s=1.5, hit_max=False))
    snap = sink.snapshot()
    assert len(snap) == 1
    assert snap[0]["name"] == "researcher"
    assert snap[0]["steps"] == 4


def test_sink_aggregate_by_name_rolls_up_multi_instances():
    sink = TelemetrySink()
    for s in (4, 5, 6):
        sink.record(AgentRecord(name="researcher", steps=s, max_steps=6,
                                prompt_tokens=10, completion_tokens=2,
                                wall_s=1.0, hit_max=False))
    sink.record(AgentRecord(name="writer", steps=1, max_steps=12,
                            prompt_tokens=5, completion_tokens=5,
                            wall_s=0.5, hit_max=False))
    roll = sink.aggregate_by_name()
    assert roll["researcher"]["n"] == 3
    assert roll["researcher"]["total_steps"] == 15
    assert roll["researcher"]["mean_steps"] == 5.0
    assert roll["researcher"]["prompt_tokens"] == 30
    assert roll["researcher"]["hit_max"] == 0
    assert roll["writer"]["n"] == 1


def test_sink_concurrent_record_is_thread_safe():
    sink = TelemetrySink()

    def worker(i):
        sink.record(AgentRecord(name="researcher", steps=4, max_steps=6,
                                prompt_tokens=1, completion_tokens=1,
                                wall_s=0.1, hit_max=False))
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    roll = sink.aggregate_by_name()
    assert roll["researcher"]["n"] == 50
    assert roll["researcher"]["prompt_tokens"] == 50


def test_compute_cost_basic():
    pricing = {"input_per_1m": 0.14, "output_per_1m": 0.28}
    assert compute_cost(1_000_000, 1_000_000, pricing) == 0.42


def test_compute_cost_none_when_pricing_missing():
    assert compute_cost(100, 50, None) is None
    assert compute_cost(100, 50, {}) is None
```

- [ ] **Step 2: 跑红**

Run: `pytest tests/test_telemetry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.telemetry'`

- [ ] **Step 3: 实现**(新建 `core/telemetry.py`)

```python
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
```

- [ ] **Step 4: 跑绿**

Run: `pytest tests/test_telemetry.py -v`
Expected: 5 passed

- [ ] **Step 5: 提交**

```bash
git add core/telemetry.py tests/test_telemetry.py
git commit -m "feat(telemetry): AgentRecord + TelemetrySink + compute_cost"
```

## Task 2: `core/telemetry.py` — CountingClient(裁判计数包装)

**Files:**
- Modify: `core/telemetry.py`(文件顶部 `import` 区追加 `from llm.base import LLMClient, LLMResponse`)
- Test: `tests/test_telemetry.py`(追加)

- [ ] **Step 1: 写失败测试**(追加到 `tests/test_telemetry.py`)

```python
from llm.base import LLMClient, LLMResponse
from core.telemetry import CountingClient


class _ScriptedJudge(LLMClient):
    """按顺序返回带 usage 的响应。"""
    def __init__(self, responses):
        self._r = list(responses)

    def chat(self, *, messages, tools=None, model=None, temperature=None, max_tokens=None):
        return self._r.pop(0)


def test_counting_client_delegates_and_accumulates():
    inner = _ScriptedJudge([
        LLMResponse(content="a", usage={"prompt_tokens": 10, "completion_tokens": 5}),
        LLMResponse(content="b", usage={"prompt_tokens": 20, "completion_tokens": 8}),
    ])
    jc = CountingClient(inner)
    assert jc.chat(messages=[]).content == "a"
    assert jc.chat(messages=[]).content == "b"
    snap = jc.snapshot()
    assert snap["calls"] == 2
    assert snap["prompt_tokens"] == 30
    assert snap["completion_tokens"] == 13


def test_counting_client_handles_missing_usage():
    inner = _ScriptedJudge([LLMResponse(content="x")])
    jc = CountingClient(inner)
    jc.chat(messages=[])
    assert jc.snapshot()["calls"] == 1
    assert jc.snapshot()["prompt_tokens"] == 0


def test_counting_client_concurrent_safe():
    inner = _ScriptedJudge([
        LLMResponse(content="x", usage={"prompt_tokens": 1, "completion_tokens": 1})
    ] * 100)
    jc = CountingClient(inner)

    def worker():
        jc.chat(messages=[])
    threads = [threading.Thread(target=worker) for _ in range(100)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    snap = jc.snapshot()
    assert snap["calls"] == 100
    assert snap["prompt_tokens"] == 100
```

- [ ] **Step 2: 跑红**

Run: `pytest tests/test_telemetry.py::test_counting_client_delegates_and_accumulates -v`
Expected: FAIL — `ImportError: cannot import name 'CountingClient'`

- [ ] **Step 3: 实现**(在 `core/telemetry.py` 顶部 import 区加 `from llm.base import LLMClient, LLMResponse`,文件末尾追加)

```python
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
```

- [ ] **Step 4: 跑绿**

Run: `pytest tests/test_telemetry.py -v`
Expected: 8 passed(5 + 3)

- [ ] **Step 5: 提交**

```bash
git add core/telemetry.py tests/test_telemetry.py
git commit -m "feat(telemetry): CountingClient 裁判链路计数包装"
```

## Task 3: `core/telemetry.py` — build_telemetry + aggregate_telemetry(纯函数)

**Files:**
- Modify: `core/telemetry.py`(追加)
- Test: `tests/test_telemetry.py`(追加)

- [ ] **Step 1: 写失败测试**(追加)

```python
from core.telemetry import build_telemetry, aggregate_telemetry


def _rollup():
    return {
        "researcher": {"name": "researcher", "n": 3, "total_steps": 12, "max_steps": 6,
                       "prompt_tokens": 8000, "completion_tokens": 1500, "wall_s": 18.0,
                       "hit_max": 0, "mean_steps": 4.0},
        "writer": {"name": "writer", "n": 1, "total_steps": 1, "max_steps": 12,
                   "prompt_tokens": 1500, "completion_tokens": 1200, "wall_s": 2.0,
                   "hit_max": 0, "mean_steps": 1.0},
    }


def test_build_telemetry_structure_and_cost():
    pricing = {"deepseek": {"input_per_1m": 0.14, "output_per_1m": 0.28},
               "glm": {"input_per_1m": 0.28, "output_per_1m": 1.12}}
    judge = {"calls": 27, "prompt_tokens": 9000, "completion_tokens": 800}
    t = build_telemetry(_rollup(), judge, judge_wall_s=6.0,
                        question_wall_s=42.0, pricing=pricing)
    assert t["wall_s"] == 42.0
    assert t["generator"]["totals"]["prompt_tokens"] == 9500     # 8000+1500
    assert t["generator"]["totals"]["completion_tokens"] == 2700
    assert t["generator"]["totals"]["cost_usd"] == 9500 / 1e6 * 0.14 + 2700 / 1e6 * 0.28
    assert t["judge"]["cost_usd"] == 9000 / 1e6 * 0.28 + 800 / 1e6 * 1.12
    assert t["judge"]["calls"] == 27
    assert t["cost_usd"]["total"] == t["generator"]["totals"]["cost_usd"] + t["judge"]["cost_usd"]
    assert t["utilization"]["researcher"]["budget_used"] == 4.0 / 6
    assert t["utilization"]["researcher"]["hit_max"] == 0


def test_build_telemetry_pricing_none_costs_null():
    t = build_telemetry(_rollup(), {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0},
                        judge_wall_s=0.0, question_wall_s=1.0, pricing=None)
    assert t["cost_usd"]["generator"] is None
    assert t["cost_usd"]["judge"] is None
    assert t["cost_usd"]["total"] is None


def test_aggregate_telemetry_two_questions():
    pricing = {"deepseek": {"input_per_1m": 0.14, "output_per_1m": 0.28},
               "glm": {"input_per_1m": 0.28, "output_per_1m": 1.12}}
    t1 = build_telemetry(_rollup(), {"calls": 10, "prompt_tokens": 1000, "completion_tokens": 100},
                         3.0, 40.0, pricing)
    t2 = build_telemetry(_rollup(), {"calls": 20, "prompt_tokens": 2000, "completion_tokens": 200},
                         6.0, 44.0, pricing)
    agg = aggregate_telemetry([t1, t2])
    assert agg["n_questions"] == 2
    assert agg["mean_wall_s"] == 42.0
    assert agg["total_cost_usd"]["total"] == t1["cost_usd"]["total"] + t2["cost_usd"]["total"]
    assert agg["agents"]["researcher"]["mean_steps"] == 4.0
```

- [ ] **Step 2: 跑红**

Run: `pytest tests/test_telemetry.py::test_build_telemetry_structure_and_cost -v`
Expected: FAIL — `ImportError: cannot import name 'build_telemetry'`

- [ ] **Step 3: 实现**(追加到 `core/telemetry.py` 末尾)

```python
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
                             "hit_max": d.get("hit_max", 0)}

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

    steps, budget = {}, {}
    for t in valid:
        for name, u in t.get("utilization", {}).items():
            steps.setdefault(name, []).append(u.get("mean_steps", 0.0))
            budget.setdefault(name, []).append(u.get("budget_used", 0.0))
    agents = {name: {"mean_steps": sum(v) / len(v),
                     "mean_budget_used": sum(budget[name]) / len(budget[name])}
              for name, v in steps.items()}

    return {"n_questions": n, "mean_wall_s": mean_wall, "total_cost_usd": total_cost,
            "eval_wall_s": None, "agents": agents}
```

- [ ] **Step 4: 跑绿**

Run: `pytest tests/test_telemetry.py -v`
Expected: 11 passed(8 + 3)

- [ ] **Step 5: 提交**

```bash
git add core/telemetry.py tests/test_telemetry.py
git commit -m "feat(telemetry): build_telemetry + aggregate_telemetry 纯函数"
```

## Task 4: `core/agent_loop.py` — recorder 注入 + token 聚合(修多步漏算 bug)

**Files:**
- Modify: `core/agent_loop.py`
- Test: `tests/test_agent_loop.py`(追加;注意现有测试不碰 `AgentResult.usage`,改聚合零破)

- [ ] **Step 1: 写失败测试**(追加到 `tests/test_agent_loop.py`,顶部 import 区补 `from core.telemetry import TelemetrySink`)

```python
def test_usage_aggregates_across_steps():
    # 3 步:前两步 tool_call,第三步收敛 → usage 求和(原 bug 只留末步)
    c = FakeClient([
        LLMResponse(content="", usage={"prompt_tokens": 100, "completion_tokens": 10},
                    tool_calls=[ToolCall(id="c1", name="echo", arguments='{"text":"a"}')]),
        LLMResponse(content="", usage={"prompt_tokens": 200, "completion_tokens": 20},
                    tool_calls=[ToolCall(id="c2", name="echo", arguments='{"text":"b"}')]),
        LLMResponse(content="done", usage={"prompt_tokens": 300, "completion_tokens": 30}),
    ])
    loop = AgentLoop(client=c, system_prompt="sys", registry=_registry_with_echo())
    out = loop.run("x")
    assert out.usage == {"prompt_tokens": 600, "completion_tokens": 60}


def test_recorder_records_on_converge():
    c = FakeClient([
        LLMResponse(content="", usage={"prompt_tokens": 100, "completion_tokens": 10},
                    tool_calls=[ToolCall(id="c1", name="echo", arguments='{"text":"a"}')]),
        LLMResponse(content="done", usage={"prompt_tokens": 50, "completion_tokens": 5}),
    ])
    sink = TelemetrySink()
    loop = AgentLoop(client=c, system_prompt="sys", registry=_registry_with_echo(),
                     max_steps=12, name="researcher", recorder=sink)
    loop.run("x")
    snap = sink.snapshot()
    assert len(snap) == 1
    assert snap[0]["name"] == "researcher"
    assert snap[0]["steps"] == 2
    assert snap[0]["max_steps"] == 12
    assert snap[0]["prompt_tokens"] == 150
    assert snap[0]["hit_max"] is False


def test_recorder_records_hit_max_before_escalation():
    looping = LLMResponse(content="", usage={"prompt_tokens": 10, "completion_tokens": 1},
                          tool_calls=[ToolCall(id="c1", name="echo", arguments='{"text":"x"}')])
    c = FakeClient([looping] * 100)
    reg = ToolRegistry()
    reg.register("echo", lambda text: "ok", description="d", parameters={"type": "object"})
    sink = TelemetrySink()
    loop = AgentLoop(client=c, system_prompt="sys", registry=reg, max_steps=3,
                     name="researcher", recorder=sink)
    with pytest.raises(Escalation):
        loop.run("loop")
    snap = sink.snapshot()
    assert len(snap) == 1
    assert snap[0]["hit_max"] is True
    assert snap[0]["steps"] == 3


def test_recorder_none_backward_compat():
    c = FakeClient([LLMResponse(content="ok")])
    loop = AgentLoop(client=c, system_prompt="sys")       # 不传 recorder
    assert loop.run("hi").content == "ok"
```

- [ ] **Step 2: 跑红**

Run: `pytest tests/test_agent_loop.py::test_usage_aggregates_across_steps -v`
Expected: FAIL — `out.usage` 只有末步 `{prompt_tokens:300, completion_tokens:30}`(或 `__init__() got unexpected kwarg 'recorder'`)

- [ ] **Step 3: 实现**(`core/agent_loop.py`)

顶部 import 改为:
```python
import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from llm.base import LLMClient
from .robust import safe_parse_arguments, Escalation
from .telemetry import AgentRecord
```

`AgentLoop.__init__` 加 `recorder=None` 参数与赋值:
```python
    def __init__(self, *, client: LLMClient, system_prompt: str,
                 registry: Optional[Any] = None, model: Optional[str] = None,
                 temperature: Optional[float] = None,
                 max_tokens: Optional[int] = None,
                 max_steps: int = 12, name: str = "agent",
                 recorder=None):
        self.client = client
        self.system_prompt = system_prompt
        self.registry = registry
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_steps = max_steps
        self.name = name
        self.recorder = recorder
```

`run()` 整体替换为(累加 token + perf_counter + 收敛/触顶上报):
```python
    def run(self, user_message: str, context_messages: Optional[list] = None) -> AgentResult:
        messages: list[dict] = [{"role": "system", "content": self.system_prompt}]
        if context_messages:
            messages += context_messages
        messages.append({"role": "user", "content": user_message})

        tools = self.registry.schemas() if self.registry else None
        start = time.perf_counter()
        total_prompt = 0
        total_completion = 0
        steps = 0

        for _ in range(self.max_steps):
            steps += 1
            resp = self.client.chat(
                messages=messages, tools=tools,
                model=self.model, temperature=self.temperature, max_tokens=self.max_tokens,
            )
            u = resp.usage or {}
            total_prompt += u.get("prompt_tokens", 0) or 0
            total_completion += u.get("completion_tokens", 0) or 0

            assistant_msg: dict = {"role": "assistant", "content": resp.content}
            if resp.tool_calls:
                assistant_msg["tool_calls"] = [
                    {"id": t.id, "type": "function",
                     "function": {"name": t.name, "arguments": t.arguments}}
                    for t in resp.tool_calls
                ]
            messages.append(assistant_msg)

            # 无 tool_call → 收敛
            if not resp.tool_calls:
                wall = time.perf_counter() - start
                self._record(steps, total_prompt, total_completion, wall, hit_max=False)
                return AgentResult(
                    content=resp.content, history=messages,
                    usage={"prompt_tokens": total_prompt,
                           "completion_tokens": total_completion})

            # 有 tool_call → 执行并回填
            for tc in resp.tool_calls:
                args = safe_parse_arguments(tc.arguments)
                try:
                    result = self.registry.execute(tc.name, args)
                except Exception as e:
                    result = f"ERROR executing {tc.name}: {e!r}"
                messages.append({"role": "tool", "tool_call_id": tc.id,
                                 "content": _to_text(result)})

        # 用尽 max_steps → 上报后抛 Escalation(sink 先记,不丢)
        wall = time.perf_counter() - start
        self._record(steps, total_prompt, total_completion, wall, hit_max=True)
        raise Escalation(
            f"{self.name} hit max_steps={self.max_steps}",
            context={"name": self.name, "steps": self.max_steps},
        )

    def _record(self, steps, prompt, completion, wall_s, *, hit_max):
        if self.recorder is not None:
            self.recorder.record(AgentRecord(
                name=self.name, steps=steps, max_steps=self.max_steps,
                prompt_tokens=prompt, completion_tokens=completion,
                wall_s=wall_s, hit_max=hit_max))
```

- [ ] **Step 4: 跑绿**

Run: `pytest tests/test_agent_loop.py tests/test_telemetry.py -v`
Expected: agent_loop 原有 9 + 新 4 = 13 passed;telemetry 仍 11 passed

- [ ] **Step 5: 提交**

```bash
git add core/agent_loop.py tests/test_agent_loop.py
git commit -m "feat(agent-loop): recorder 注入 + 多步 token 聚合(修末步漏算)"
```

## Task 5: 透传 recorder 到所有 agent 构造点(6 文件,向后兼容)

**Files:**
- Modify: `agents/researcher.py`, `agents/verifier.py`, `agents/writer.py`, `agents/orchestrator.py`, `agents/system.py`, `agents/baseline.py`
- Test: `tests/test_telemetry.py`(追加 wiring 段)

- [ ] **Step 1: 写失败测试**(追加到 `tests/test_telemetry.py`)

```python
from agents.researcher import make_researcher
from agents.verifier import make_verifier
from agents.writer import make_writer
from agents.orchestrator import make_orchestrator
from agents.system import build_system
from agents.baseline import build_baseline_system
from core.config import Config


class _DummySearch:
    def search(self, query):
        return []


class _FakeGen(LLMClient):
    def chat(self, **kw):
        raise AssertionError("构造阶段不应调 chat")


def _cfg_for_build():
    return Config({"models": {"generator": {}},
                   "tools": {"web_search": {}, "web_read": {"max_chars": 8000}},
                   "guards": {"agent_max_steps": 12, "research_max_rounds": 3}})


def test_make_researcher_threads_recorder():
    sink = TelemetrySink()
    loop = make_researcher(client=_FakeGen(), search_client=_DummySearch(), recorder=sink)
    assert loop.recorder is sink


def test_make_verifier_threads_recorder():
    sink = TelemetrySink()
    loop = make_verifier(client=_FakeGen(), search_client=_DummySearch(), recorder=sink)
    assert loop.recorder is sink


def test_make_writer_threads_recorder():
    sink = TelemetrySink()
    loop = make_writer(client=_FakeGen(), recorder=sink)
    assert loop.recorder is sink


def test_make_orchestrator_threads_recorder():
    sink = TelemetrySink()
    loop, _ = make_orchestrator(client=_FakeGen(), run_researcher=lambda q: None,
                                run_verifier=lambda f: None, run_writer=lambda m: None,
                                recorder=sink)
    assert loop.recorder is sink


def test_build_system_threads_recorder_to_orchestrator():
    sink = TelemetrySink()
    loop, _ = build_system(_cfg_for_build(), client=_FakeGen(),
                           search_client=_DummySearch(), recorder=sink)
    assert loop.recorder is sink


def test_build_baseline_system_threads_recorder():
    sink = TelemetrySink()
    loop, _ = build_baseline_system(_cfg_for_build(), client=_FakeGen(),
                                    search_client=_DummySearch(), recorder=sink)
    assert loop.recorder is sink
```

> 说明:三个 `_run_*` 闭包内的 researcher/verifier/writer 是**按调用现建**的,无法在不跑流程的前提下静态窥探;它们的 recorder 透传由 `make_researcher/make_verifier/make_writer` 各自的单元测试覆盖(上面已测),`build_system` 只做 `recorder=recorder` 转发。端到端由 Task 10 真跑验证。

- [ ] **Step 2: 跑红**

Run: `pytest tests/test_telemetry.py::test_make_researcher_threads_recorder -v`
Expected: FAIL — `TypeError: make_researcher() got an unexpected keyword argument 'recorder'`

- [ ] **Step 3: 实现**

**`agents/researcher.py`**(`make_researcher` 加参数 + 透传):
```python
def make_researcher(*, client, search_client, max_chars: int = 8000,
                    max_steps: int = 12, recorder=None) -> AgentLoop:
    return AgentLoop(
        client=client, system_prompt=RESEARCHER_PROMPT,
        registry=build_web_registry(search_client, max_chars=max_chars),
        max_steps=max_steps, name="researcher", recorder=recorder,
    )
```

**`agents/verifier.py`**(同型):
```python
def make_verifier(*, client, search_client, max_chars: int = 8000,
                  max_steps: int = 12, recorder=None) -> AgentLoop:
    return AgentLoop(
        client=client, system_prompt=VERIFIER_PROMPT,
        registry=build_web_registry(search_client, max_chars=max_chars),
        max_steps=max_steps, name="verifier", recorder=recorder,
    )
```

**`agents/writer.py`**(同型):
```python
def make_writer(*, client, max_steps: int = 12, recorder=None) -> AgentLoop:
    return AgentLoop(
        client=client, system_prompt=WRITER_PROMPT,
        registry=None,
        max_steps=max_steps, name="writer", recorder=recorder,
    )
```

**`agents/orchestrator.py`**(`make_orchestrator` 签名加 `recorder=None`,构造 loop 处透传):
```python
def make_orchestrator(*, client, run_researcher, run_verifier, run_writer,
                      max_steps: int = 12, research_max_rounds: int = 3,
                      verify: bool = True, recorder=None):
```
文件末尾构造处:
```python
    loop = AgentLoop(client=client, system_prompt=prompt,
                     registry=reg, max_steps=max_steps, name="orchestrator",
                     recorder=recorder)
```

**`agents/system.py`**(`build_system` 签名加 `recorder=None`;三个闭包 + `make_orchestrator` 透传):
```python
def build_system(config: Config, *, client: LLMClient | None = None,
                 search_client=None, verify: bool = True, recorder=None):
```
闭包内:
```python
    def _run_researcher(sub_question):
        progress(f"  [researcher] 检索子问题:{sub_question}")
        result = make_researcher(client=client, search_client=search_client,
                                 max_chars=max_chars, max_steps=researcher_max_steps,
                                 recorder=recorder).run(sub_question)
        content = result.content or ""
        progress(f"  [researcher] 完成 → content {len(content)} 字,前 120 字:{content[:120]!r}")
        return result

    def _run_verifier(findings_json):
        progress("  [verifier] 复核 findings 来源支撑")
        return make_verifier(client=client, search_client=search_client,
                             max_chars=max_chars, max_steps=max_steps,
                             recorder=recorder).run(findings_json)

    def _run_writer(user_message):
        progress("  [writer] 综合带引用报告(无工具,仅用传入 findings)")
        return make_writer(client=client, max_steps=max_steps,
                           recorder=recorder).run(user_message)

    return make_orchestrator(
        client=client,
        run_researcher=_run_researcher, run_verifier=_run_verifier, run_writer=_run_writer,
        max_steps=max_steps, research_max_rounds=research_max_rounds, verify=verify,
        recorder=recorder,
    )
```

**`agents/baseline.py`**(`make_baseline` 签名加 `recorder=None`;baseline loop + 内部 `make_writer` 透传;`build_baseline_system` 签名 + `make_baseline` 调用处透传):
```python
def make_baseline(*, client, search_client, max_chars: int = 8000,
                  writer_max_steps: int = 12, max_steps: int = 16, recorder=None):
```
`_write_report` 内 `make_writer` 调用:
```python
        result = make_writer(client=client, max_steps=writer_max_steps,
                             recorder=recorder).run(msg)
```
baseline loop 构造:
```python
    loop = AgentLoop(client=client, system_prompt=BASELINE_PROMPT,
                     registry=reg, max_steps=max_steps, name="baseline",
                     recorder=recorder)
```
```python
def build_baseline_system(config: Config, *, client: LLMClient | None = None,
                          search_client=None, recorder=None):
    ...
    return make_baseline(client=client, search_client=search_client,
                         max_chars=max_chars, writer_max_steps=agent_max,
                         max_steps=baseline_max, recorder=recorder)
```

- [ ] **Step 4: 跑绿**

Run: `pytest tests/test_telemetry.py tests/test_agent_loop.py tests/test_baseline.py tests/test_orchestrator_tools.py -q`
Expected: 全绿(wiring 新增 6;现有 baseline/orchestrator 测试因 `recorder=None` 默认不破)

- [ ] **Step 5: 提交**

```bash
git add agents/ tests/test_telemetry.py
git commit -m "feat(agents): make_*/build_system/build_baseline 透传 recorder"
```

## Task 6: `config.yaml` — pricing 段

**Files:**
- Modify: `config.yaml`(文件末尾追加)
- Test: `tests/test_telemetry.py`(追加)

- [ ] **Step 1: 写失败测试**(追加到 `tests/test_telemetry.py`)

```python
def test_config_has_pricing_section():
    from core.config import load_config
    pricing = load_config()["pricing"]
    assert "deepseek" in pricing and "glm" in pricing
    for m in ("deepseek", "glm"):
        assert pricing[m]["input_per_1m"] > 0
        assert pricing[m]["output_per_1m"] > 0
```

- [ ] **Step 2: 跑红**

Run: `pytest tests/test_telemetry.py::test_config_has_pricing_section -v`
Expected: FAIL — `KeyError: 'pricing'`(或 `None` 解包)

- [ ] **Step 3: 实现**(在 `config.yaml` 末尾,`eval:` 段之后追加)

```yaml

pricing:                       # 成本估算单价(USD/1M tokens);公开标价·截至 2026-08·近似·可调
  deepseek:                    # deepseek-chat=V4-Flash;取 cache-miss(保守,不假设命中)
    input_per_1m: 0.14         #   api-docs.deepseek.com/quick_start/pricing
    output_per_1m: 0.28
  glm:                         # GLM-5.2 实标未公开,暂按同族 GLM-4.5(¥2/¥8 per 1M)@7.2 换算
    input_per_1m: 0.28         #   bigmodel.cn/pricing;实标公布后更新
    output_per_1m: 1.12
```

- [ ] **Step 4: 跑绿**

Run: `pytest tests/test_telemetry.py::test_config_has_pricing_section -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add config.yaml tests/test_telemetry.py
git commit -m "feat(config): pricing 段(deepseek/glm USD per 1M tokens)"
```

## Task 7: `eval/run_eval.py` — run_one 注入 sink + evaluate_question 组装 telemetry

**Files:**
- Modify: `eval/run_eval.py`
- Test: `tests/test_run_eval.py`(追加)

- [ ] **Step 1: 写失败测试**(追加到 `tests/test_run_eval.py`)

```python
def test_evaluate_question_emits_telemetry_with_judge_counts():
    judge = FakeGLMClient([
        LLMResponse(content='{"support":"supported","source_real":true,"reason":""}',
                    usage={"prompt_tokens": 100, "completion_tokens": 10}),
        LLMResponse(content='{"covered":true,"reason":""}',
                    usage={"prompt_tokens": 80, "completion_tokens": 8}),
    ])
    item = {"id": "qt", "question": "Q", "key_facts": ["投机解码加速推理"]}
    cfg = Config({"thresholds": {"grounding_min": 0.85},
                  "pricing": {"deepseek": {"input_per_1m": 0.14, "output_per_1m": 0.28},
                              "glm": {"input_per_1m": 0.28, "output_per_1m": 1.12}}})
    sc = evaluate_question(item, cfg=cfg, judge_client=judge,
                           run_one_fn=lambda q: (_canned_report(), _canned_history_with_findings()))
    tel = sc["telemetry"]
    assert tel["judge"]["calls"] == 2                    # 1 finding + 1 key_fact
    assert tel["judge"]["prompt_tokens"] == 180          # 100+80
    assert tel["judge"]["cost_usd"] == 180 / 1e6 * 0.28 + 18 / 1e6 * 1.12
    assert tel["wall_s"] >= 0.0
    assert "generator" in tel and "utilization" in tel


def test_evaluate_question_report_none_still_emits_telemetry():
    judge = FakeGLMClient([])
    item = {"id": "qn", "question": "Q", "key_facts": ["x"]}
    sc = evaluate_question(item, cfg=None, judge_client=judge, run_one_fn=lambda q: (None, []))
    assert sc["success"] is False
    assert sc["telemetry"]["judge"]["calls"] == 0
    assert sc["telemetry"]["cost_usd"]["total"] is None   # cfg 无 pricing
```

- [ ] **Step 2: 跑红**

Run: `pytest tests/test_run_eval.py::test_evaluate_question_emits_telemetry_with_judge_counts -v`
Expected: FAIL — `KeyError: 'telemetry'`(返回的 sc 无 telemetry 字段)

- [ ] **Step 3: 实现**(`eval/run_eval.py`)

顶部 import 区补:
```python
import time
...
from core.telemetry import TelemetrySink, CountingClient, build_telemetry
```

`run_one` 加 `recorder=None` 并透传给 `build_fn`:
```python
def run_one(question: str, cfg, *, client=None, search_client=None,
            system: str = "multi", recorder=None):
    """跑一次系统,返回 (report, history)。report 可能为 None。"""
    build_fn = _build_fn_for(system)
    loop, get_report = build_fn(cfg, client=client, search_client=search_client,
                                recorder=recorder)
    result = loop.run(question)
    return get_report(), result.history
```

`evaluate_question` 整体替换:
```python
def evaluate_question(item: dict, cfg, *, judge_client,
                      client=None, search_client=None, run_one_fn=None,
                      judge_concurrency: int = 10, system: str = "multi"):
    """单题评估 → scorecard(含 telemetry)。"""
    question = item["question"]
    gen_sink = TelemetrySink()
    runner = run_one_fn or (lambda q: run_one(q, cfg, client=client,
                                              search_client=search_client, system=system,
                                              recorder=gen_sink))
    q_start = time.perf_counter()
    report, history = runner(question)
    question_wall_s = time.perf_counter() - q_start
    gen_rollup = gen_sink.aggregate_by_name()

    pricing = cfg.get("pricing") if cfg is not None else None

    if report is None:
        telemetry = build_telemetry(
            gen_rollup, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0},
            judge_wall_s=0.0, question_wall_s=question_wall_s, pricing=pricing)
        return {"id": item["id"], "question": question,
                **compute_question_metrics([], [], report_produced=False),
                "telemetry": telemetry}

    findings = extract_findings(history)
    text = report_to_text(report)
    jc = CountingClient(judge_client)
    j_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=judge_concurrency) as pool:
        finding_verdicts = list(pool.map(
            lambda f: judge_finding(claim=f.get("claim", ""),
                                    excerpt=f.get("excerpt", ""),
                                    source_url=f.get("source_url", ""),
                                    client=jc),
            findings))
        key_fact_verdicts = list(pool.map(
            lambda kf: judge_key_fact(key_fact=kf, report_text=text, client=jc),
            item.get("key_facts", [])))
    judge_wall_s = time.perf_counter() - j_start

    telemetry = build_telemetry(gen_rollup, jc.snapshot(), judge_wall_s,
                                question_wall_s, pricing)
    return {"id": item["id"], "question": question,
            **compute_question_metrics(finding_verdicts, key_fact_verdicts, report_produced=True),
            "telemetry": telemetry}
```

> 注:`_eval_one` 的异常兜底分支(main 内)会覆盖 sc,**不强制加 telemetry**(失败题 telemetry 缺失由 aggregate_telemetry 的 `if t` 过滤处理)。现有 `test_main_question_error_does_not_abort_run` 不查 telemetry,不破。

- [ ] **Step 4: 跑绿**

Run: `pytest tests/test_run_eval.py -q`
Expected: 全绿(原有 + 新 2)

- [ ] **Step 5: 提交**

```bash
git add eval/run_eval.py tests/test_run_eval.py
git commit -m "feat(eval): run_one 注入 sink + evaluate_question 组装 telemetry(judge计数+成本)"
```

## Task 8: `eval/run_eval.py` — scorecard 聚合 + eval_wall_s + 控制台遥测表

**Files:**
- Modify: `eval/run_eval.py`(`main` 末段 + 新增 `_print_telemetry`)
- Test: `tests/test_run_eval.py`(追加)

- [ ] **Step 1: 写失败测试**(追加到 `tests/test_run_eval.py`)

```python
def test_main_aggregates_telemetry_into_scorecard(tmp_path):
    bench = _bench_with(tmp_path, ["q1", "q2"])
    out = tmp_path / "out"
    judge = FakeGLMClient([
        LLMResponse(content='{"support":"supported","source_real":true,"reason":""}',
                    usage={"prompt_tokens": 100, "completion_tokens": 10}),
        LLMResponse(content='{"covered":true,"reason":""}',
                    usage={"prompt_tokens": 80, "completion_tokens": 8}),
    ] * 2)   # 2 题 × (1 finding + 1 keyfact) = 4
    cfg = Config({"thresholds": {"grounding_min": 0.85},
                  "pricing": {"deepseek": {"input_per_1m": 0.14, "output_per_1m": 0.28},
                              "glm": {"input_per_1m": 0.28, "output_per_1m": 1.12}}})
    rc = main(["--benchmark", str(bench), "--results", str(out)],
              run_one_fn=_fake_run_one, judge_client=judge, cfg=cfg)
    assert rc == 0
    summary = json.loads((out / "scorecard.json").read_text(encoding="utf-8"))
    tel = summary["telemetry"]
    assert tel["n_questions"] == 2
    assert tel["eval_wall_s"] is not None and tel["eval_wall_s"] >= 0.0
    assert tel["total_cost_usd"]["total"] is not None


def test_print_telemetry_runs(capsys):
    from eval.run_eval import _print_telemetry
    _print_telemetry({"telemetry": {
        "eval_wall_s": 12.3, "mean_wall_s": 6.0,
        "total_cost_usd": {"generator": 0.01, "judge": 0.005, "total": 0.015},
        "agents": {"researcher": {"mean_steps": 4.0, "mean_budget_used": 0.67}}}})
    out = capsys.readouterr().out
    assert "运维遥测" in out
    assert "researcher" in out
```

- [ ] **Step 2: 跑红**

Run: `pytest tests/test_run_eval.py::test_main_aggregates_telemetry_into_scorecard -v`
Expected: FAIL — `KeyError: 'telemetry'`(scorecard 无 telemetry 聚合)

- [ ] **Step 3: 实现**

import 行(把 Task 7 的 telemetry import 补上 `aggregate_telemetry`):
```python
from core.telemetry import (TelemetrySink, CountingClient,
                            build_telemetry, aggregate_telemetry)
```

新增 `_print_telemetry`(放在 `_print_summary` 之后):
```python
def _print_telemetry(summary: dict) -> None:
    t = summary.get("telemetry") or {}
    print("\n=== 运维遥测 telemetry ===")
    ew, mw = t.get("eval_wall_s"), t.get("mean_wall_s")
    print(f"整批墙钟 {ew:.1f}s" if ew is not None else "整批墙钟 N/A", end="  ")
    print(f"单题均值 {mw:.1f}s" if mw is not None else "单题均值 N/A")
    tc = t.get("total_cost_usd") or {}

    def _c(k):
        v = tc.get(k) if isinstance(tc, dict) else None
        return f"${v:.4f}" if v is not None else "N/A"

    print(f"总成本 产品(DeepSeek){_c('generator')}  "
          f"评估(GLM){_c('judge')}  合计{_c('total')}")
    for name, a in (t.get("agents") or {}).items():
        ms = a.get("mean_steps", 0.0)
        bu = a.get("mean_budget_used", 0.0)
        print(f"  [{name}] 均值 {ms:.1f} 步  预算占用 {bu:.0%}")
```

`main()` 末段(替换 `with ThreadPoolExecutor...` 到 `return 0`):
```python
    # 题间并发(question_concurrency=1 等价串行,向后兼容);pool.map 保序
    eval_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=question_concurrency) as pool:
        scorecards = list(pool.map(_eval_one, items))
    eval_wall_s = time.perf_counter() - eval_start

    summary = aggregate(scorecards, cfg["thresholds"]["grounding_min"])
    summary["telemetry"] = aggregate_telemetry(
        [sc.get("telemetry") for sc in scorecards], cfg.get("pricing"))
    summary["telemetry"]["eval_wall_s"] = eval_wall_s
    (res_dir / "scorecard.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _print_summary(summary)
    _print_telemetry(summary)
    return 0
```

- [ ] **Step 4: 跑绿**

Run: `pytest tests/test_run_eval.py -q`
Expected: 全绿(含新 2)

- [ ] **Step 5: 全量回归 + 提交**

Run: `pytest -q`
Expected: 全绿(现有 + 全部新增)

```bash
git add eval/run_eval.py tests/test_run_eval.py
git commit -m "feat(eval): scorecard telemetry 聚合 + eval_wall_s + 控制台遥测表"
```

## Task 9: README — 「运维遥测」节(方法论)

**Files:**
- Modify: `README.md`(在「基线对比与消融」节之后追加)

- [ ] **Step 1: 写入方法论节**(纯文档,真实数字样例由 Task 10 填入对应代码块)

在 README 的基线对比节之后追加:

```markdown
## 运维遥测(延迟 / 成本 / 利用率)

系统在 eval 管线上内置运维可观测,每次 `python -m eval.run_eval` 除质量 scorecard 外,
额外产出**延迟/成本/利用率三柱**报告(逐题 `telemetry` 字段 + scorecard 聚合 + 控制台表)。

**埋点设计(单点覆盖,最小侵入):**
- **生成链路**:`TelemetrySink` 注入 `AgentLoop`(全仓唯一执行原语,自带 agent 名
  `orchestrator/researcher/verifier/writer`)。`run()` 内累加各步 token、计步、
  `perf_counter` 计墙钟,收敛/触顶都上报。**一处覆盖四柱 agent**。
- **裁判链路**:`CountingClient` 包装 GLM judge client,Lock 下累加 usage 与调用次数,
  **零改 judge 契约**。
- 顺修一个 bug:`AgentResult.usage` 原只留最后一步,多步 researcher 漏算约 4/5 token,
  现改为跨步聚合。

**三柱口径:**

| 柱 | 指标 | 口径 |
|---|---|---|
| 延迟 | `wall_s` | 整批 / 单题 / 单 agent 角色(researcher 按单个计) |
| 成本 | `cost_usd` | token/1M × 单价;**产品链路(DeepSeek)与评估链路(GLM)分列** |
| 利用率 | `budget_used` | `steps / max_steps`(researcher 理想 4-5 / 上限 6);附触顶次数 |

**成本估算说明:** 单价在 `config.yaml` 的 `pricing` 段,取公开标价(截至 2026-08,近似,
可在 config 调整):DeepSeek-chat(V4-Flash)取 cache-miss 保守价 input \$0.14 / output
\$0.28 per 1M;GLM-5.2 实标未公开,暂按同族 GLM-4.5 换算 input \$0.28 / output \$1.12。
**这是量级估算而非计费依据。**

真实样例(multi 5 题):
<!-- TELEMETRY_SAMPLE_INSERT -->
```

- [ ] **Step 2: 验证**

Run: `grep -c "运维遥测" README.md`
Expected: ≥ 2(标题 + 表内)

- [ ] **Step 3: 提交**

```bash
git add README.md
git commit -m "docs(readme): 运维遥测节(三柱口径+埋点设计+成本估算说明)"
```

---

## Task 10: 真跑 multi 5 题 → 填 README 真实样例(中等档,烧 API)

> **前置:** `.env` 含 `DEEPSEEK_API_KEY` / `GLM_API_KEY` / `BOCHA_API_KEY`。本步花真实 API
> 预算(DeepSeek + 博查 + GLM,5 题全集)。先 `--limit 3` 验证量级再全跑。

**Files:**
- Run: `python -m eval.run_eval`(默认 multi)
- Modify: `README.md`(把样例填入 Task 9 留的 `TELEMETRY_SAMPLE_INSERT` 处)

- [ ] **Step 1: smoke 3 题**

Run: `python -m eval.run_eval --limit 3`
Expected: 控制台同时打印「质量 scorecard」与「运维遥测 telemetry」两段表;`eval/results/q001.json`
含 `telemetry` 字段;`eval/results/scorecard.json` 含 `telemetry` 聚合。**确认成本/延迟量级合理
(单题产品成本应在 \$0.0x 量级)再继续。**

- [ ] **Step 2: 全量 5 题**

Run: `python -m eval.run_eval`
Expected: 5 题完成,scorecard 质量仍过线(`mean_grounding ≥ 0.85`),telemetry 聚合就位。

- [ ] **Step 3: 填 README 样例**

把 `eval/results/scorecard.json` 的 `telemetry` 节(整批墙钟 / 单题均值 / 总成本产品|评估|合计 /
各 agent 均值步数·预算占用)摘成简洁表格,替换 README 中 `<!-- TELEMETRY_SAMPLE_INSERT -->`。
数字务必取自真实 scorecard,注明题量与日期。

- [ ] **Step 4: 提交结果 + README**

```bash
git add eval/results/ README.md
git commit -m "chore(eval): multi 5 题真实 telemetry 样例 + README 填数"
```

- [ ] **Step 5: 最终全量回归**

Run: `pytest -q`
Expected: 全绿

---

## Self-Review(计划作者自检)

**1. Spec 覆盖核对:**
- §2 三柱(latency/cost/utilization)→ Task 1-3(sink/cost/build)+ Task 4(步数+墙钟+token)+ Task 7-8(产物)。✅
- §3 AgentLoop 单点埋点 → Task 4。✅ CountingClient 裁判埋点 → Task 2+7。✅
- §4.4 单题 telemetry schema → Task 3 `build_telemetry`(字段一一对应:wall_s/generator/by_agent/totals/judge/cost_usd/utilization)。✅
- 修 usage bug → Task 4(`test_usage_aggregates_across_steps`)。✅
- pricing config → Task 6。✅ scorecard 聚合 + 控制台 → Task 8。✅ README → Task 9。✅ 真跑样例 → Task 10。✅
- recorder=None 向后兼容 → Task 4 `test_recorder_none_backward_compat` + Task 5 默认参数。✅

**2. Placeholder 扫描:** 无 TBD/TODO;每个 Task 有完整测试代码 + 实现代码 + 命令 + 期望。Task 9 的
`TELEMETRY_SAMPLE_INSERT` 是文档占位,显式由 Task 10 用真实数字替换(非代码空缺)。Task 10 是手动真跑,
步骤为命令 + 期望而非代码,符合预期。

**3. 类型/签名一致性:**
- `AgentRecord` 字段(name/steps/max_steps/prompt_tokens/completion_tokens/wall_s/hit_max)在 Task 1 定义,
  Task 4 `_record` 构造时字段一致。✅
- `TelemetrySink.record/aggregate_by_name` 在 Task 1,被 Task 4(recorder.record)、Task 7(gen_sink.aggregate_by_name)调用,签名一致。✅
- `CountingClient.chat(**kw)/snapshot()` 在 Task 2,Task 7 `jc.snapshot()` 返回 `{calls,prompt_tokens,completion_tokens}`
  与 `build_telemetry` 的 `judge_stat` 取键一致。✅
- `build_telemetry(gen_rollup, judge_stat, judge_wall_s, question_wall_s, pricing)` Task 3 定义,Task 7 调用参数顺序一致。✅
- `aggregate_telemetry(per_question_telemetries, pricing)` Task 3 定义,Task 8 调用一致。✅
- `make_*/build_system/build_baseline_system` 的 `recorder=None` 在 Task 5 全链路一致,Task 7 `run_one` 透传 `recorder=recorder` 给 `build_fn`。✅

**4. 向后兼容/不破现有测试:**
- `AgentResult.usage` 改聚合:现有 `test_agent_loop.py` 不查 usage → 不破(Task 4 已列)。
- `make_*` 加默认参数 → 现有构造调用不破。
- `evaluate_question` 加 telemetry 字段:现有测试只查质量字段 → 不破;`cfg=None` 经 `if cfg is not None` 保护 → 不崩。
- `run_one_fn` 注入路径 generator 段为空(注入函数不经 sink),telemetry 仍产出(judge 段 + 空 generator)→ 不破现有 2 元组注入测试。

**5. Scope:** 10 个 Task,逐个可独立提交、保持绿;核心(Task 1-9)全 mock 零 API,真跑(Task 10)单独末尾。单个施工图覆盖。









