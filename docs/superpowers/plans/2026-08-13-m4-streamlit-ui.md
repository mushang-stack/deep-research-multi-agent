# M4 Streamlit 实时编排可视化 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付一个双模式(真跑 + 回放)的 Streamlit 单页应用,把多 agent 协作过程实时可视化,复用已有 agent 系统(零改决策逻辑)与遥测层。

**Architecture:** 类型化事件总线(`core/events.py` + `agents/observe.py:emit`)在叙事边界发结构化事件,取代现有 `progress()` 调用点(保留 stderr 行为);UI 后台线程跑编排、前端轮询 `EventSink.snapshot()` + `TelemetrySink` 重画;真跑自动录 trace,回放喂同一渲染路径。逻辑全沉纯函数,`ui/app.py` 薄壳不单测。

**Tech Stack:** Python、Streamlit、pytest(全 mock)、Pydantic、threading。

**Spec:** `docs/superpowers/specs/2026-08-13-m4-streamlit-ui-design.md`

---

## 文件结构

| 文件 | 动作 | 职责 |
|---|---|---|
| `core/events.py` | 新建 | `Event` + `EventSink` + `events_to_json`/`events_from_json` |
| `agents/observe.py` | 修改 | 新增 `emit`/`set_sink`/`clear_sink`/`get_sink` |
| `agents/orchestrator.py` | 修改 | 10 处 `progress`→`emit`(叙事边界 + verify verdict 统计) |
| `agents/system.py` | 修改 | `_run_researcher` 2 处 `progress`→`emit`(research_start/done) |
| `ui/view.py` | 新建 | 纯视图构建器(timeline/telemetry/report markdown) |
| `main.py` | 修改 | `render_markdown` 改 import 自 `ui.view` |
| `ui/controller.py` | 新建 | `run_research` + trace 录制/回放 |
| `ui/app.py` | 新建 | Streamlit 薄壳 |
| `ui/record_trace.py` | 新建 | 无头录 trace CLI |
| `ui/traces/.gitkeep` | 新建 | 占位(DoD 真跑后落真实 trace) |
| `tests/test_events.py` | 新建 | 事件模型测试 |
| `tests/test_observe.py` | 修改 | 追加 emit 测试 |
| `tests/test_orchestrator_events.py` | 新建 | orchestrator emit 接线测试 |
| `tests/test_system_events.py` | 新建 | system.py research_start/done 测试 |
| `tests/test_view.py` | 新建 | view 纯函数测试 |
| `tests/test_controller.py` | 新建 | controller 测试 |
| `README.md` | 修改 | 追加 M4 段 |

**约定提醒:** `tests/conftest.py` 的 autouse fixture 已设 `RESEARCH_QUIET=1`,故 `progress`/`emit` 在测试里默认静默(不污染 stdout/stderr)。`emit` 无 sink 时退化为 `progress`,eval/CLI 链路行为不变。

---

## Task 1: `core/events.py` — 事件模型 + 线程安全收集器

**Files:**
- Create: `core/events.py`
- Test: `tests/test_events.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_events.py
import threading
import time

import pytest

from core.events import Event, EventSink, events_to_json, events_from_json


def test_event_defaults():
    e = Event(kind="x", role=None, text="t", payload={"a": 1})
    assert e.kind == "x"
    assert e.payload == {"a": 1}
    assert e.ts == 0.0


def test_sink_records_and_snapshots():
    sink = EventSink()
    sink.record(Event(kind="run_start", role=None, text="hi", payload={}))
    snap = sink.snapshot()
    assert len(snap) == 1
    assert snap[0]["kind"] == "run_start"
    assert snap[0]["ts"] >= 0.0


def test_sink_ts_monotonic_increasing():
    sink = EventSink()
    sink.record(Event(kind="a", role=None, text="", payload={}))
    time.sleep(0.01)
    sink.record(Event(kind="b", role=None, text="", payload={}))
    s = sink.snapshot()
    assert s[1]["ts"] > s[0]["ts"]


def test_sink_concurrent_record_thread_safe():
    sink = EventSink()

    def worker():
        sink.record(Event(kind="research_start", role="researcher", text="x", payload={}))

    threads = [threading.Thread(target=worker) for _ in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(sink.snapshot()) == 50


def test_snapshot_is_isolated_copy():
    sink = EventSink()
    sink.record(Event(kind="a", role=None, text="", payload={}))
    snap = sink.snapshot()
    snap.clear()
    assert len(sink.snapshot()) == 1  # 外部改动不影响内部


def test_events_json_roundtrip():
    events = [{"kind": "x", "role": "researcher", "text": "t", "payload": {"n": 3}, "ts": 1.5}]
    s = events_to_json(events)
    back = events_from_json(s)
    assert back == events
    assert '"kind": "x"' in s  # ensure_ascii=False 仍合法
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_events.py -v`
Expected: FAIL with `ImportError` (module `core.events` 不存在)。

- [ ] **Step 3: Write minimal implementation**

```python
# core/events.py
"""类型化事件总线(M4 UI 实时编排可视化用)。
- Event:单条事件(kind/role/text/payload/ts)。
- EventSink:线程安全收集器(同 TelemetrySink 风格);record 时打 ts(monotonic 相对 t0)。
- events_to_json/from_json:序列化往返(trace 录制/回放)。

无 sink 注入时,agents 链路行为完全不变(emit 退化为 observe.progress)。"""
import json
import threading
import time
from dataclasses import dataclass, asdict


@dataclass
class Event:
    kind: str
    role: str | None
    text: str
    payload: dict
    ts: float = 0.0


class EventSink:
    def __init__(self):
        self._lock = threading.Lock()
        self._records: list[Event] = []
        self._t0 = time.monotonic()

    def record(self, event: Event) -> None:
        with self._lock:
            event.ts = time.monotonic() - self._t0
            self._records.append(event)

    def snapshot(self) -> list[dict]:
        with self._lock:
            return [asdict(e) for e in self._records]


def events_to_json(events: list[dict]) -> str:
    return json.dumps(events, ensure_ascii=False)


def events_from_json(s: str) -> list[dict]:
    return json.loads(s)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_events.py -v`
Expected: PASS(6 tests)。

- [ ] **Step 5: Commit**

```bash
git add core/events.py tests/test_events.py
git commit -m "feat(events): Event + EventSink + 序列化(M4 事件总线地基)"
```

---

## Task 2: `agents/observe.py` — `emit` + sink 注册

**Files:**
- Modify: `agents/observe.py`
- Test: `tests/test_observe.py`(追加)

- [ ] **Step 1: Write the failing test**(追加到 `tests/test_observe.py` 末尾)

```python
# 追加到 tests/test_observe.py
from core.events import EventSink
from agents.observe import emit, set_sink, clear_sink, get_sink


def test_emit_without_sink_is_silent_when_quiet(capsys, monkeypatch):
    monkeypatch.setenv("RESEARCH_QUIET", "1")
    emit("run_start", None, "[start] hi", question="q")
    cap = capsys.readouterr()
    assert cap.err == "" and cap.out == ""


def test_emit_without_sink_writes_stderr_when_not_quiet(capsys, monkeypatch):
    monkeypatch.delenv("RESEARCH_QUIET", raising=False)
    emit("run_start", None, "[start] hi", question="q")
    cap = capsys.readouterr()
    assert "[start] hi" in cap.err


def test_emit_with_sink_records_event():
    sink = EventSink()
    set_sink(sink)
    try:
        emit("research_start", "researcher", "检索子问题:q1", sub_question="q1")
    finally:
        clear_sink()
    snap = sink.snapshot()
    assert len(snap) == 1
    assert snap[0]["kind"] == "research_start"
    assert snap[0]["role"] == "researcher"
    assert snap[0]["payload"] == {"sub_question": "q1"}


def test_set_clear_get_sink():
    sink = EventSink()
    assert get_sink() is None
    set_sink(sink)
    assert get_sink() is sink
    clear_sink()
    assert get_sink() is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_observe.py -v`
Expected: FAIL with `ImportError: cannot import name 'emit'`。

- [ ] **Step 3: Write minimal implementation**(在 `agents/observe.py` 末尾追加)

```python
# 追加到 agents/observe.py
from core.events import Event

_active_sink = None


def set_sink(sink):
    global _active_sink
    _active_sink = sink


def clear_sink():
    global _active_sink
    _active_sink = None


def get_sink():
    return _active_sink


def emit(kind, role, text, **payload):
    """类型化事件:① progress(text) 打 stderr(RESEARCH_QUIET 静默);
    ② 若设了 active sink,构造 Event 推入。无 sink 时退化为纯日志(不影响 CLI/eval 链路)。"""
    progress(text)
    sink = get_sink()
    if sink is not None:
        sink.record(Event(kind=kind, role=role, text=text, payload=payload))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_observe.py -v`
Expected: PASS(6 tests:原 2 + 新 4)。

- [ ] **Step 5: Commit**

```bash
git add agents/observe.py tests/test_observe.py
git commit -m "feat(observe): emit + sink 注册(M4 事件 taps)"
```

---

## Task 3: `agents/orchestrator.py` — 叙事边界 emit 接线 + verify verdict 统计

**Files:**
- Modify: `agents/orchestrator.py`(import + 3 个闭包 `_dispatch_research`/`_verify_findings`/`_write_report`)
- Test: `tests/test_orchestrator_events.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_orchestrator_events.py
import json
import pytest

from agents.orchestrator import make_orchestrator
from agents.observe import set_sink, clear_sink
from core.events import EventSink
from llm.base import LLMClient


class _NoChat(LLMClient):
    def chat(self, **kw):
        raise AssertionError("工具执行不应调生成模型")


def _result(content):
    return type("R", (), {"content": content})()


@pytest.fixture
def sink():
    s = EventSink()
    set_sink(s)
    yield s
    clear_sink()


def _kinds(sink):
    return [e["kind"] for e in sink.snapshot()]


def test_research_round_events(sink):
    def run_researcher(sq):
        return _result(json.dumps({"findings": [{"id": "f1", "claim": sq, "source_url": "https://x"}]}))
    loop, _ = make_orchestrator(client=_NoChat(), run_researcher=run_researcher,
                                run_verifier=lambda f: _result("{}"), run_writer=lambda m: _result("{}"))
    loop.registry.execute("dispatch_research", {"sub_questions": ["q1", "q2"]})
    kinds = _kinds(sink)
    assert "research_round" in kinds and "research_round_done" in kinds
    rr = next(e for e in sink.snapshot() if e["kind"] == "research_round")
    assert rr["payload"]["round"] == 1
    assert rr["payload"]["n_subquestions"] == 2
    assert rr["payload"]["is_gap"] is False
    rrd = next(e for e in sink.snapshot() if e["kind"] == "research_round_done")
    assert rrd["payload"]["findings"] == 2
    assert rrd["payload"]["failures"] == 0


def test_research_round_gap_flag(sink):
    loop, _ = make_orchestrator(
        client=_NoChat(),
        run_researcher=lambda sq: _result('{"findings":[{"id":"f1","claim":"c","source_url":"https://x"}]}'),
        run_verifier=lambda f: _result("{}"), run_writer=lambda m: _result("{}"),
        research_max_rounds=3)
    loop.registry.execute("dispatch_research", {"sub_questions": ["q1"]})
    loop.registry.execute("dispatch_research", {"sub_questions": ["q2"]})
    rounds = [e for e in sink.snapshot() if e["kind"] == "research_round"]
    assert rounds[0]["payload"]["is_gap"] is False
    assert rounds[1]["payload"]["is_gap"] is True


def test_verify_done_tally(sink):
    def run_verifier(findings_json):
        return _result(json.dumps({"results": [
            {"finding_id": "f1", "verdict": "supported"},
            {"finding_id": "f2", "verdict": "supported"},
            {"finding_id": "f3", "verdict": "unsupported"},
            {"finding_id": "f4", "verdict": "weak"},
        ]}))
    loop, _ = make_orchestrator(client=_NoChat(), run_researcher=lambda sq: _result("{}"),
                                run_verifier=run_verifier, run_writer=lambda m: _result("{}"))
    loop.registry.execute("verify_findings",
                          {"findings": [{"id": "f1"}, {"id": "f2"}, {"id": "f3"}, {"id": "f4"}]})
    kinds = _kinds(sink)
    assert "verify_start" in kinds and "verify_done" in kinds
    vd = next(e for e in sink.snapshot() if e["kind"] == "verify_done")
    assert vd["payload"]["supported"] == 2
    assert vd["payload"]["unsupported"] == 1
    assert vd["payload"]["weak"] == 1
    assert vd["role"] == "verifier"


def test_write_and_report_events(sink):
    def run_writer(msg):
        return _result('{"sections":[{"heading":"H","content":"C","citations":["f1"]}],"sources":["https://x"]}')
    loop, _ = make_orchestrator(client=_NoChat(), run_researcher=lambda sq: _result("{}"),
                                run_verifier=lambda f: _result("{}"), run_writer=run_writer)
    loop.registry.execute("write_report", {"outline": "大纲", "verified_findings": [{"id": "f1"}]})
    kinds = _kinds(sink)
    assert "write_start" in kinds and "report_ready" in kinds
    rr = next(e for e in sink.snapshot() if e["kind"] == "report_ready")
    assert rr["payload"]["sections"] == 1


def test_warn_on_empty_findings_refused(sink):
    loop, _ = make_orchestrator(client=_NoChat(), run_researcher=lambda sq: _result("{}"),
                                run_verifier=lambda f: _result("{}"),
                                run_writer=lambda m: _result("{}"))
    loop.registry.execute("write_report", {"outline": "x", "verified_findings": []})
    warns = [e for e in sink.snapshot() if e["kind"] == "warn"]
    assert len(warns) == 1
    assert warns[0]["payload"]["reason"] == "write_refused_no_findings"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_orchestrator_events.py -v`
Expected: FAIL(sink 里没有 research_round 等事件 —— 当前仍是 `progress`,不发事件)。

- [ ] **Step 3: Modify `agents/orchestrator.py`**

**3a. 改 import**(把 `from .observe import progress` 改成):

```python
from .observe import emit
```

**3b. 替换 `_dispatch_research` 闭包**(整段替换):

```python
    def _dispatch_research(sub_questions: list[str]) -> dict:
        round_counter["n"] += 1
        if round_counter["n"] > research_max_rounds:
            if findings_total["n"] == 0:
                emit("warn", None,
                     f"[orchestrator] ⚠ 已达最大研究轮次({research_max_rounds})且未获得任何 findings",
                     reason="max_rounds_no_findings")
                return {"status": "no_findings",
                        "message": "已达最大研究轮次且未获得任何 findings。不要调用 write_report,直接用一句话结束(说明未能获取到资料)。"}
            emit("warn", None,
                 f"[orchestrator] ⚠ 已达最大研究轮次({research_max_rounds}),请直接 write_report 收尾",
                 reason="max_rounds_reached")
            return {"status": "max_rounds_reached",
                    "message": "已达最大研究轮次,请直接 write_report,不要再检索。"}
        emit("research_round", None,
             f"[orchestrator] dispatch_research 第 {round_counter['n']}/{research_max_rounds} 轮,派发 {len(sub_questions)} 个子问题",
             round=round_counter["n"], max_rounds=research_max_rounds,
             n_subquestions=len(sub_questions), is_gap=round_counter["n"] > 1)
        out = dispatch_research(sub_questions, run_researcher)
        findings_total["n"] += len(out["findings"])
        emit("research_round_done", None,
             f"[orchestrator] dispatch_research 完成 → {len(out['findings'])} 条 findings,{len(out['failures'])} 个失败子问题",
             round=round_counter["n"], findings=len(out["findings"]), failures=len(out["failures"]))
        return out
```

**3c. 替换 `_verify_findings` 闭包**(整段替换,新增 verdict 统计):

```python
    def _verify_findings(findings: list[dict]) -> dict:
        emit("verify_start", "verifier",
             f"[orchestrator] verify_findings → 复核 {len(findings)} 条 findings",
             findings=len(findings))
        msg = json.dumps({"findings": findings}, ensure_ascii=False)
        result = run_verifier(msg)
        results = parse_results(result.content)
        supported = sum(1 for r in results if r.verdict == "supported")
        weak = sum(1 for r in results if r.verdict == "weak")
        unsupported = sum(1 for r in results if r.verdict == "unsupported")
        emit("verify_done", "verifier",
             f"[orchestrator] verify_findings 完成 → {len(results)} 条结果(supported {supported}/weak {weak}/unsupported {unsupported})",
             results=len(results), supported=supported, weak=weak, unsupported=unsupported)
        return {"results": [r.model_dump() for r in results]}
```

**3d. 替换 `_write_report` 闭包**(整段替换):

```python
    def _write_report(outline: str, verified_findings: list[dict]) -> dict:
        if not verified_findings:
            emit("warn", None,
                 "[orchestrator] ✗ write_report 被拒绝:无 verified findings(避免空报告)",
                 reason="write_refused_no_findings")
            return {"error": "无 verified findings,无法生成报告。不要用空 findings 调用 write_report。"}
        emit("write_start", "writer",
             f"[orchestrator] write_report → 让 Writer 综合报告(outline {len(outline)} 字,{len(verified_findings)} 条 verified findings)",
             outline_len=len(outline), verified_findings=len(verified_findings))
        msg = json.dumps({"outline": outline, "verified_findings": verified_findings},
                         ensure_ascii=False)
        result = run_writer(msg)
        report = parse_report(result.content)
        if report is None:
            snippet = (result.content or "")[:200]
            emit("warn", None,
                 f"[orchestrator] ✗ Writer 输出无法解析为 Report。原始 content 前 200 字:{snippet!r}",
                 reason="writer_unparseable")
            return {"error": "writer 产出不可解析,请重试或基于现有 findings 重写"}
        emit("report_ready", None,
             f"[orchestrator] ✓ 报告已生成({len(report.sections)} 节),存入 holder",
             sections=len(report.sections))
        holder["report"] = report  # 机制级确定性提取
        return report.model_dump()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_orchestrator_events.py tests/test_orchestrator_tools.py tests/test_orchestrator_e2e.py -v`
Expected: PASS(新 5 + 既有 orchestrator 测试不破 —— emit 在 RESEARCH_QUIET 下等价 progress)。

- [ ] **Step 5: Commit**

```bash
git add agents/orchestrator.py tests/test_orchestrator_events.py
git commit -m "feat(orchestrator): 叙事边界 emit 接线 + verify verdict 统计(M4)"
```

---

## Task 4: `agents/system.py` — researcher 事件(research_start/done)

**Files:**
- Modify: `agents/system.py`(`_run_researcher` 闭包 + import)
- Test: `tests/test_system_events.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_system_events.py
import pytest

from agents.system import build_system
from agents.observe import set_sink, clear_sink
from core.events import EventSink
from core.config import Config
from llm.base import LLMClient, LLMResponse


class _DummySearch:
    def search(self, query):
        return []


class _FinalClient(LLMClient):
    """立即返回无 tool_call 的最终内容,让 researcher loop 1 步收敛(不烧真实 API)。"""
    def chat(self, **kw):
        return LLMResponse(content='{"findings":[]}', usage={})


@pytest.fixture
def sink():
    s = EventSink()
    set_sink(s)
    yield s
    clear_sink()


def _cfg():
    return Config({"models": {"generator": {}},
                   "tools": {"web_search": {}, "web_read": {"max_chars": 8000}},
                   "guards": {"agent_max_steps": 12, "research_max_rounds": 3}})


def test_researcher_emits_start_done(sink):
    loop, _ = build_system(_cfg(), client=_FinalClient(), search_client=_DummySearch())
    loop.registry.execute("dispatch_research", {"sub_questions": ["q1"]})
    kinds = [e["kind"] for e in sink.snapshot()]
    assert "research_start" in kinds
    assert "research_done" in kinds
    starts = [e for e in sink.snapshot() if e["kind"] == "research_start"]
    assert starts[0]["role"] == "researcher"
    assert starts[0]["payload"]["sub_question"] == "q1"


def test_researcher_done_has_content_len(sink):
    loop, _ = build_system(_cfg(), client=_FinalClient(), search_client=_DummySearch())
    loop.registry.execute("dispatch_research", {"sub_questions": ["q1"]})
    done = [e for e in sink.snapshot() if e["kind"] == "research_done"][0]
    assert "content_len" in done["payload"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_system_events.py -v`
Expected: FAIL(sink 无 research_start/done)。

- [ ] **Step 3: Modify `agents/system.py`**

**3a. 改 import**:`from .observe import progress` → `from .observe import progress, emit`(`_run_verifier`/`_run_writer` 仍用 progress)。

**3b. 替换 `_run_researcher` 闭包**(整段替换):

```python
    def _run_researcher(sub_question):
        emit("research_start", "researcher",
             f"  [researcher] 检索子问题:{sub_question}",
             sub_question=sub_question)
        result = make_researcher(client=client, search_client=search_client,
                                 max_chars=max_chars, max_steps=researcher_max_steps,
                                 recorder=recorder).run(sub_question)
        content = result.content or ""
        emit("research_done", "researcher",
             f"  [researcher] 完成 → content {len(content)} 字",
             sub_question=sub_question, content_len=len(content))
        return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_system_events.py tests/test_system.py -v`
Expected: PASS(新 2 + 既有 system 测试不破)。

- [ ] **Step 5: Commit**

```bash
git add agents/system.py tests/test_system_events.py
git commit -m "feat(system): researcher research_start/done 事件(M4)"
```

---

## Task 5: `ui/view.py` — 纯视图构建器 + `render_report_markdown` 从 main.py 抽出

**Files:**
- Create: `ui/view.py`
- Modify: `main.py`(import `render_report_markdown` 自 `ui.view`,删除本地 `render_markdown`)
- Modify: `tests/test_main.py`(import 改自 `ui.view`)
- Test: `tests/test_view.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_view.py
from ui.view import (render_report_markdown, timeline_rows, render_timeline_md,
                     telemetry_tiles, render_telemetry_md, _bar)
from core.schemas import Report, ReportSection


def test_render_report_markdown_sections_and_sources_links():
    rep = Report(
        sections=[ReportSection(heading="结论", content="某结论。", citations=["f1", "f2"])],
        sources=["https://a", "https://b"],
    )
    md = render_report_markdown(rep)
    assert "## 结论" in md
    assert "某结论。" in md
    assert "## 来源" in md
    assert "[https://a](https://a)" in md  # 可点击链接


def test_timeline_rows_basic_and_icons():
    events = [
        {"kind": "run_start", "role": None, "text": "[start] hi", "payload": {}, "ts": 0.0},
        {"kind": "report_ready", "role": None, "text": "报告就绪", "payload": {"sections": 3}, "ts": 1.0},
    ]
    rows = timeline_rows(events)
    assert rows[0]["icon"] == "▶"
    assert rows[1]["icon"] == "📄"
    assert rows[1]["payload"]["sections"] == 3


def test_timeline_parallel_inference():
    # 3 个 research_start 在 0.5s 内 → 首个标 parallel=3
    events = [
        {"kind": "research_start", "role": "researcher", "text": "[researcher] q1", "payload": {}, "ts": 0.1},
        {"kind": "research_start", "role": "researcher", "text": "[researcher] q2", "payload": {}, "ts": 0.2},
        {"kind": "research_start", "role": "researcher", "text": "[researcher] q3", "payload": {}, "ts": 0.3},
        {"kind": "research_done", "role": "researcher", "text": "[researcher] done", "payload": {}, "ts": 2.0},
    ]
    rows = timeline_rows(events)
    assert rows[0]["parallel"] == 3
    assert rows[1]["parallel"] is None
    assert rows[3]["parallel"] is None


def test_render_timeline_md_strips_tag_and_renders():
    events = [{"kind": "research_start", "role": "researcher",
               "text": "  [researcher] 检索子问题:q1", "payload": {}, "ts": 0.1}]
    md = render_timeline_md(events)
    assert "🔍" in md
    assert "检索子问题:q1" in md
    assert "[researcher]" not in md  # 前导 tag 已剥


def test_bar_half():
    assert _bar(0.5) == "█████░░░░░"
    assert _bar(0.0) == "░░░░░░░░░░"
    assert _bar(1.0) == "██████████"


def test_telemetry_tiles_budget_and_cost():
    rollup = {"researcher": {"name": "researcher", "n": 2, "total_steps": 10, "max_steps": 6,
                             "prompt_tokens": 8000, "completion_tokens": 1500,
                             "wall_s": 12.0, "hit_max": 0, "mean_steps": 5.0}}
    pricing = {"deepseek": {"input_per_1m": 0.14, "output_per_1m": 0.28}}
    tiles = telemetry_tiles(rollup, pricing)
    t = tiles["researcher"]
    assert t["mean_steps"] == 5.0
    assert t["max_steps"] == 6
    assert abs(t["budget_used"] - 5.0 / 6) < 1e-9
    assert t["cost_usd"] is not None and t["cost_usd"] > 0


def test_render_telemetry_md_contains_bar_and_pct():
    rollup = {"writer": {"name": "writer", "n": 1, "total_steps": 1, "max_steps": 12,
                         "prompt_tokens": 100, "completion_tokens": 50,
                         "wall_s": 1.0, "hit_max": 0, "mean_steps": 1.0}}
    md = render_telemetry_md(rollup, {"deepseek": {"input_per_1m": 0.14, "output_per_1m": 0.28}})
    assert "█" in md
    assert "writer" in md
    assert "%" in md
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_view.py -v`
Expected: FAIL(`ImportError: No module named 'ui.view'`)。

- [ ] **Step 3: Write minimal implementation**

```python
# ui/view.py
"""纯视图构建器(M4 UI)。全函数无副作用,单测覆盖;app.py 只做 st.* 渲染。"""
import re

from core.telemetry import compute_cost

_PARALLEL_THRESHOLD = 0.5  # 秒;连续 research_start ts 跨度小于此 → 视为并行

_ICONS = {
    "run_start": "▶", "research_round": "🧩", "research_start": "🔍",
    "research_done": "·", "research_round_done": "✓", "verify_start": "🔎",
    "verify_done": "✓", "write_start": "✍", "report_ready": "📄",
    "warn": "⚠", "run_done": "✓", "error": "✗",
}


def render_report_markdown(report) -> str:
    """渲染 Report → markdown(从 main.py 抽出复用)。来源为可点击 markdown 链接。"""
    lines = []
    for sec in report.sections:
        lines.append(f"## {sec.heading}\n")
        lines.append(sec.content)
        if sec.citations:
            lines.append("\n\n*引用: " + ", ".join(sec.citations) + "*")
        lines.append("\n")
    if report.sources:
        lines.append("## 来源\n")
        for i, s in enumerate(report.sources, 1):
            lines.append(f"{i}. [{s}]({s})")
    return "\n".join(lines)


def _clean_text(text: str) -> str:
    text = text.strip()
    return re.sub(r"^\[[^\]]*\]\s*", "", text)  # 去前导 [tag]


def _parallel_badges(events):
    """{event_index: 并行数} 给每批连续 research_start(ts 跨度 < 阈值)的首个。"""
    badges = {}
    i = 0
    while i < len(events):
        if events[i]["kind"] != "research_start":
            i += 1
            continue
        j = i
        while (j < len(events) and events[j]["kind"] == "research_start"
               and events[j]["ts"] - events[i]["ts"] < _PARALLEL_THRESHOLD):
            j += 1
        n = j - i
        if n > 1:
            badges[i] = n
        i = j
    return badges


def timeline_rows(events):
    """events: list[dict](EventSink.snapshot / trace)。返回展示行列表。"""
    badges = _parallel_badges(events)
    rows = []
    for idx, e in enumerate(events):
        rows.append({
            "icon": _ICONS.get(e["kind"], "·"),
            "role": e.get("role"),
            "text": e.get("text", ""),
            "kind": e["kind"],
            "payload": e.get("payload", {}),
            "parallel": badges.get(idx),
        })
    return rows


def render_timeline_md(events) -> str:
    rows = timeline_rows(events)
    lines = ["**实时编排**", ""]
    for r in rows:
        role_tag = f"**{r['role']}** " if r["role"] else ""
        extra = ""
        p = r["payload"]
        if r["kind"] == "research_round" and p.get("is_gap"):
            extra = "  ⟲ 缺口→再检索"
        elif r["kind"] == "research_round_done":
            extra = f"  → {p.get('findings', 0)} 条 findings"
        elif r["kind"] == "verify_done" and p.get("unsupported", 0):
            extra = f"  ⚠ {p['unsupported']} 条不支撑"
        elif r["kind"] == "report_ready":
            extra = f"  → {p.get('sections', 0)} 节"
        par = f"  ｜并行 ×{r['parallel']}" if r["parallel"] else ""
        lines.append(f"{r['icon']} {role_tag}{_clean_text(r['text'])}{extra}{par}")
    return "\n".join(lines)


def _bar(frac) -> str:
    frac = max(0.0, min(1.0, frac))
    filled = int(round(frac * 10))
    return "█" * filled + "░" * (10 - filled)


def telemetry_tiles(rollup, pricing):
    """rollup: TelemetrySink.aggregate_by_name()。pricing: cfg['pricing']。
    返回 {name: {mean_steps, max_steps, budget_used, cost_usd}}。"""
    deepseek_p = (pricing or {}).get("deepseek")
    out = {}
    for name, d in rollup.items():
        ms = d.get("mean_steps", 0.0)
        mx = d.get("max_steps", 0) or 0
        cost = compute_cost(d.get("prompt_tokens", 0), d.get("completion_tokens", 0), deepseek_p)
        out[name] = {"mean_steps": ms, "max_steps": mx,
                     "budget_used": (ms / mx) if mx else 0.0,
                     "cost_usd": cost}
    return out


def render_telemetry_md(rollup, pricing) -> str:
    tiles = telemetry_tiles(rollup, pricing)
    if not tiles:
        return ""
    lines = ["**Agent 利用率(实时)**", ""]
    for name, t in tiles.items():
        pct = int(round(t["budget_used"] * 100))
        cost = f"  💰 ${t['cost_usd']:.4f}" if t["cost_usd"] is not None else ""
        lines.append(f"`{name}`  {_bar(t['budget_used'])}  "
                     f"{t['mean_steps']:.1f}/{t['max_steps']} 步 ({pct}%){cost}")
    return "\n".join(lines)
```

- [ ] **Step 4: Refactor `main.py` to reuse `render_report_markdown`**

把 `main.py` 里 `render_markdown` 函数**整段删除**,在文件顶部 import 区加:

```python
from ui.view import render_report_markdown
```

把 `main()` 函数体里的 `print(render_markdown(report))` 改成 `print(render_report_markdown(report))`。

- [ ] **Step 5: Update `tests/test_main.py`**

把第 1 行 `from main import render_markdown, main` 改成:

```python
from main import main
from ui.view import render_report_markdown
```

把 `test_render_markdown_sections_and_sources` 里 `md = render_markdown(rep)` 改成 `md = render_report_markdown(rep)`,并追加断言 `assert "[https://a](https://a)" in md`。

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_view.py tests/test_main.py -v`
Expected: PASS。

- [ ] **Step 7: Commit**

```bash
git add ui/view.py main.py tests/test_view.py tests/test_main.py
git commit -m "feat(view): 纯视图构建器 + render_report_markdown 抽出复用(M4)"
```

---

## Task 6: `ui/controller.py` — `run_research` + trace 录制/回放

**Files:**
- Create: `ui/controller.py`
- Test: `tests/test_controller.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_controller.py
import json

from core.config import Config
from core.events import EventSink
from core.telemetry import TelemetrySink
from core.schemas import Report, ReportSection
from ui.controller import (run_research, save_trace, load_trace, slugify,
                           trace_path, list_traces)


class _FakeLoop:
    def __init__(self, exc=None):
        self.question = None
        self._exc = exc

    def run(self, question):
        self.question = question
        if self._exc:
            raise self._exc


def _fake_run_fn(report=None, exc=None):
    def fn(cfg):
        loop = _FakeLoop(exc=exc)

        def get_report():
            return report

        return loop, get_report
    return fn


def _cfg():
    return Config({})


def test_run_research_success_events_and_report():
    sink = EventSink(); tele = TelemetrySink()
    rep = Report(sections=[ReportSection(heading="H", content="C", citations=[])], sources=[])
    out = run_research("问题", _cfg(), sink, tele, run_fn=_fake_run_fn(report=rep))
    assert out is rep
    kinds = [e["kind"] for e in sink.snapshot()]
    assert kinds[0] == "run_start"
    assert "run_done" in kinds
    done = next(e for e in sink.snapshot() if e["kind"] == "run_done")
    assert done["payload"]["success"] is True


def test_run_research_exception_emits_error_and_returns_none():
    sink = EventSink(); tele = TelemetrySink()
    out = run_research("问题", _cfg(), sink, tele,
                       run_fn=_fake_run_fn(exc=RuntimeError("boom")))
    assert out is None
    kinds = [e["kind"] for e in sink.snapshot()]
    assert "error" in kinds
    done = next(e for e in sink.snapshot() if e["kind"] == "run_done")
    assert done["payload"]["success"] is False


def test_save_load_trace_roundtrip(tmp_path):
    rep = Report(sections=[ReportSection(heading="H", content="C", citations=["f1"])],
                 sources=["https://x"])
    events = [{"kind": "run_start", "role": None, "text": "hi", "payload": {}, "ts": 0.0}]
    path = tmp_path / "t.json"
    save_trace(str(path), "问题", events, rep, {"researcher": {"mean_steps": 5.0}}, "success")
    data = load_trace(str(path))
    assert data["question"] == "问题"
    assert data["events"] == events
    assert data["status"] == "success"
    assert data["report"]["sections"][0]["heading"] == "H"
    # 反序列化报告可重建 Report
    rebuilt = Report(**data["report"])
    assert rebuilt.sections[0].citations == ["f1"]


def test_save_trace_null_report(tmp_path):
    path = tmp_path / "t.json"
    save_trace(str(path), "q", [], None, {}, "failed")
    assert load_trace(str(path))["report"] is None


def test_slugify_sanitizes():
    assert slugify("对比 RAG 与 微调?") == "对比-RAG-与-微调"
    assert slugify("a/b\\c:d") != ""


def test_trace_path_unique(tmp_path):
    p1 = trace_path("问题", traces_dir=str(tmp_path))
    save_trace(p1, "问题", [], None, {}, "failed")
    p2 = trace_path("问题", traces_dir=str(tmp_path))
    assert p1 != p2


def test_list_traces(tmp_path):
    save_trace(str(tmp_path / "a.json"), "q1", [], None, {}, "failed")
    save_trace(str(tmp_path / "b.json"), "q2", [], None, {}, "failed")
    assert len(list_traces(traces_dir=str(tmp_path))) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_controller.py -v`
Expected: FAIL(`ImportError: No module named 'ui.controller'`)。

- [ ] **Step 3: Write minimal implementation**

```python
# ui/controller.py
"""UI 控制器:后台线程目标 + trace 录制/回放。纯逻辑可单测,不依赖 Streamlit。"""
import glob
import json
import os
import re

from agents.observe import emit, set_sink, clear_sink
from agents.system import build_system

_TRACE_DIR = os.path.join(os.path.dirname(__file__), "traces")


def run_research(question, cfg, event_sink, telemetry_sink, *, run_fn=None):
    """后台线程目标。返回 Report | None。
    run_fn=None → 生产 build_system(cfg, recorder=telemetry_sink)。
    run_fn(cfg) -> (loop, get_report) → 测试用 fake,不烧 API。"""
    set_sink(event_sink)
    try:
        emit("run_start", None, f"[start] 研究问题:{question}", question=question)
        if run_fn is None:
            loop, get_report = build_system(cfg, recorder=telemetry_sink)
        else:
            loop, get_report = run_fn(cfg)
        loop.run(question)
        report = get_report()
        emit("run_done", None,
             f"[done] {'✓ 报告生成' if report else '✗ 报告未生成'}",
             success=bool(report))
        return report
    except Exception as e:
        emit("error", None, f"[error] 运行失败:{e!r}", reason=repr(e))
        emit("run_done", None, "[done] ✗ 失败", success=False)
        return None
    finally:
        clear_sink()


def slugify(text, maxlen=30):
    s = re.sub(r"[^\w一-鿿]+", "-", text).strip("-")
    return s[:maxlen] or "trace"


def trace_path(question, traces_dir=None):
    d = traces_dir or _TRACE_DIR
    base = slugify(question)
    path = os.path.join(d, f"{base}.json")
    i = 1
    while os.path.exists(path):
        path = os.path.join(d, f"{base}-{i}.json")
        i += 1
    return path


def save_trace(path, question, events, report, telemetry, status):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    data = {
        "question": question,
        "events": events,
        "report": report.model_dump() if report is not None else None,
        "telemetry": telemetry,
        "status": status,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_trace(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def list_traces(traces_dir=None):
    d = traces_dir or _TRACE_DIR
    return sorted(glob.glob(os.path.join(d, "*.json")))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_controller.py -v`
Expected: PASS(7 tests)。

- [ ] **Step 5: Commit**

```bash
git add ui/controller.py tests/test_controller.py
git commit -m "feat(controller): run_research 后台目标 + trace 录制/回放(M4)"
```

---

## Task 7: `ui/app.py` + `ui/record_trace.py` + README + 冒烟测试

**Files:**
- Create: `ui/app.py`
- Create: `ui/record_trace.py`
- Create: `ui/traces/.gitkeep`
- Modify: `README.md`(追加 M4 段)
- Test: `tests/test_ui_smoke.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ui_smoke.py
import importlib
import os
import py_compile


_ROOT = os.path.join(os.path.dirname(__file__), "..")


def test_app_py_compiles():
    py_compile.compile(os.path.join(_ROOT, "ui", "app.py"), doraise=True)


def test_record_trace_compiles_and_imports():
    py_compile.compile(os.path.join(_ROOT, "ui", "record_trace.py"), doraise=True)
    importlib.import_module("ui.record_trace")  # top-level 无副作用,可安全 import
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ui_smoke.py -v`
Expected: FAIL(`ui/app.py` 不存在)。

- [ ] **Step 3: Create `ui/app.py`**

```python
"""Streamlit 单页:深度研究多 agent 实时编排可视化。
运行:streamlit run ui/app.py
双模式:真实运行(后台线程 + 事件轮询)/ 回放(从 trace JSON 回放)。"""
import threading
import time

import streamlit as st
from dotenv import load_dotenv

from agents.observe import set_sink, clear_sink  # noqa: F401(set_sink/clear_sink 由 controller 间接用)
from core.config import load_config
from core.events import EventSink
from core.schemas import Report
from core.telemetry import TelemetrySink
from ui import view
from ui.controller import run_research, save_trace, load_trace, list_traces, trace_path

load_dotenv()
_TIMEOUT_S = 480  # 8 分钟硬上限,demo 安全网

st.set_page_config(page_title="AI-PM-Agent · 深度研究", layout="wide")
cfg = load_config()
PRICING = cfg.get("pricing") or {}

st.title("AI-PM-Agent · 深度研究多 Agent 编排")
mode = st.radio("模式", ["真实运行", "回放"], horizontal=True)


def _render_realtime(sink, tele, elapsed_ph, tl_ph, tm_ph):
    ev = sink.snapshot()
    tl_ph.markdown(view.render_timeline_md(ev))
    tm_ph.markdown(view.render_telemetry_md(tele.aggregate_by_name(), PRICING))
    elapsed_ph.caption(f"⏱ {ev[-1]['ts']:.0f}s  ·  事件 {len(ev)}" if ev else "启动中…")


if mode == "真实运行":
    question = st.text_input("研究问题", value="对比 RAG 与微调的适用场景与工程权衡")
    if st.button("▶ 开始") and question and not st.session_state.get("running"):
        st.session_state["running"] = True
        sink = EventSink()
        tele = TelemetrySink()
        holder = {}

        def target():
            try:
                holder["report"] = run_research(question, cfg, sink, tele)
            except Exception as e:  # controller 已兜底,双保险
                holder["error"] = repr(e)
            finally:
                holder["done"] = True

        threading.Thread(target=target, daemon=True).start()
        elapsed_ph = st.empty(); tl_ph = st.empty(); tm_ph = st.empty()
        deadline = time.monotonic() + _TIMEOUT_S
        while not holder.get("done"):
            if time.monotonic() > deadline:
                holder["error"] = "运行超时(>8min)"
                break
            _render_realtime(sink, tele, elapsed_ph, tl_ph, tm_ph)
            time.sleep(0.3)
        _render_realtime(sink, tele, elapsed_ph, tl_ph, tm_ph)
        st.session_state["running"] = False

        report = holder.get("report")
        if holder.get("error"):
            st.error(f"运行失败:{holder['error']}")
        if report is not None:
            st.markdown("---")
            st.markdown(view.render_report_markdown(report))
            try:
                p = trace_path(question)
                save_trace(p, question, sink.snapshot(), report,
                           tele.aggregate_by_name(), "success")
                st.success(f"已录制 trace → {p}(可在回放模式重放)")
            except Exception as e:
                st.warning(f"trace 录制失败:{e!r}")
        elif not holder.get("error"):
            st.warning("Orchestrator 未产出报告(holder 为空)。")
else:  # 回放
    traces = list_traces()
    if not traces:
        st.info("暂无 trace。先在「真实运行」跑一次(或等 DoD 录制的开箱 trace)。")
    else:
        choice = st.selectbox("选择 trace", traces)
        speed = st.select_slider("回放速度", options=["立即", "快", "慢"], value="快")
        if st.button("▶ 回放") and choice:
            data = load_trace(choice)
            delay = {"立即": 0.0, "快": 0.05, "慢": 0.3}[speed]
            tl_ph = st.empty(); tm_ph = st.empty()
            shown = []
            for ev in data["events"]:
                shown.append(ev)
                tl_ph.markdown(view.render_timeline_md(shown))
                if data.get("telemetry"):
                    tm_ph.markdown(view.render_telemetry_md(data["telemetry"], PRICING))
                time.sleep(delay)
            st.markdown("---")
            if data.get("report"):
                st.markdown(view.render_report_markdown(Report(**data["report"])))
            st.caption(f"问题:{data.get('question')}  ·  status: {data.get('status')}")
```

- [ ] **Step 4: Create `ui/record_trace.py`**

```python
"""无头录制 trace:python -m ui.record_trace "研究问题"
跑真实 build_system,把事件+报告+遥测存成 trace JSON 供 UI 回放。DoD / 批量录素材用。"""
import sys

from dotenv import load_dotenv

from core.config import load_config
from core.events import EventSink
from core.telemetry import TelemetrySink
from ui.controller import run_research, save_trace, trace_path


def main(argv=None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if not argv:
        print('用法: python -m ui.record_trace "研究问题"', file=sys.stderr)
        return 1
    load_dotenv()
    question = " ".join(argv)
    cfg = load_config()
    sink = EventSink()
    tele = TelemetrySink()
    report = run_research(question, cfg, sink, tele)
    path = trace_path(question)
    save_trace(path, question, sink.snapshot(), report,
               tele.aggregate_by_name(), "success" if report else "failed")
    print(f"[record_trace] trace → {path}  status={'success' if report else 'failed'}",
          file=sys.stderr)
    return 0 if report else 2


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Create `ui/traces/.gitkeep`**(空文件,占位)

- [ ] **Step 6: Append M4 section to `README.md`**

在 README 合适位置(评估体系之后)追加:

```markdown
## M4 · Streamlit 实时编排可视化

实时把多 agent 协作过程(拆解 → 并行检索 → 验证 → 缺口再检索 → 撰写)可视化,支持真实运行与回放双模式。

**运行:**

```bash
streamlit run ui/app.py
```

- **真实运行**:输入研究问题 → 后台线程跑编排,前端轮询事件实时滚动时间线 + Agent 利用率/成本条 → 完成渲染带引用报告并自动录制 trace(`ui/traces/*.json`)。
- **回放**:选择已录制 trace,按节奏重放同一渲染路径(零失败风险,面试 live demo 兜底)。

**无头录 trace(批量素材 / DoD):**

```bash
python -m ui.record_trace "对比 RAG 与微调的适用场景"
```

**实时信号源**:`agents/observe.py:emit` 类型化事件总线(取代 `progress` 调用点,零改 agent 决策逻辑)+ `core/telemetry.py` 运维遥测。单题真跑 ~5min / ~$0.05(DeepSeek,cache-miss 口径)。
```

- [ ] **Step 7: Run smoke test + full suite to verify**

Run: `python -m pytest tests/test_ui_smoke.py -v` → PASS
Run: `python -m pytest -q` → 全绿(既有 + 新增;`app.py` 不参与 import,不影响)。

- [ ] **Step 8: Commit**

```bash
git add ui/app.py ui/record_trace.py ui/traces/.gitkeep tests/test_ui_smoke.py README.md
git commit -m "feat(ui): Streamlit 双模式 app + 无头录 trace + README(M4)"
```

---

## Task 8: DoD — 真跑录制开箱 trace + 回放验证 + 全量验收

**Files:** `ui/traces/<question>.json`(新增,真实产物)

> 本任务烧真实 API(DeepSeek + 博查;~5min / ~$0.05)。GLM 裁判**不**参与(UI 只走生成链路)。

- [ ] **Step 1: 真跑录制 trace**

Run:
```bash
python -m ui.record_trace "对比 RAG 与微调的适用场景与工程权衡"
```
Expected: stderr 打出实时编排过程(researcher/verifier/writer),末尾 `trace → ui/traces/对比-RAG-与微调...json  status=success`,exit 0。

- [ ] **Step 2: 校验 trace 内容**

Run(一次性 python 检查):
```bash
python -c "import json,glob; d=json.load(open(sorted(glob.glob('ui/traces/*.json'))[-1],encoding='utf-8')); print('events',len(d['events']),'report',bool(d['report']),'status',d['status']); print('kinds',sorted({e['kind'] for e in d['events']}))"
```
Expected: events 数 > 0,report 为真,status=success;kinds 含 `run_start`/`research_round`/`research_start`/`verify_start`/`write_start`/`report_ready`/`run_done`。

- [ ] **Step 3: 回放渲染冒烟**(不启浏览器,纯函数验证回放路径)

```bash
python -c "
import json, glob
from core.config import load_config
from ui import view
p = sorted(glob.glob('ui/traces/*.json'))[-1]
d = json.load(open(p, encoding='utf-8'))
pricing = load_config().get('pricing') or {}
print(view.render_timeline_md(d['events'])[:300])
print('---')
print(view.render_telemetry_md(d['telemetry'], pricing)[:200])
"
```
Expected: 打印出时间线(含 🔍/✓/📄 等)+ 遥测条(含成本),无异常。

- [ ] **Step 4: 提交开箱 trace**

```bash
git add ui/traces/*.json
git commit -m "chore(ui): DoD 真跑 trace(开箱回放素材,M4)"
```
(仅提交本次 DoD 的 1 个 trace;如 `ui/traces/` 下有调试残留,先清掉再 add。)

- [ ] **Step 5: 全量测试 + 收尾**

Run: `python -m pytest -q` → 全绿(目标 ~225:206 基线 + 新增 test_events 6 + test_observe 4 + orchestrator_events 5 + system_events 2 + view 7 + controller 7 + ui_smoke 2 ≈ 239)。

最终整体 review(参考 superpowers:requesting-code-review):spec 各节均有 Task 覆盖;跨模块契约(emit 词表 / Event 字段 / trace schema)一致;无 Critical/Important。更新 memory(项目文件)记录 M4 完成。

---

## Self-Review(plan 写完自检)

**1. Spec 覆盖:**
- §3 分层 → Task 1(core/events)/2(observe)/5(view)/6(controller)/7(app) ✓
- §4 事件模型 → Task 1 ✓
- §5 observe.emit → Task 2 ✓
- §6 事件词表 → Task 3(orchestrator 8 种)+ Task 4(research_start/done)+ Task 6(run_start/run_done/error)✓
- §7 controller + trace → Task 6 ✓
- §8 Streamlit 双模式 + 线程 + 失败兜底 → Task 7 ✓
- §9 测试策略(纯函数全测、app 不测)→ 各 Task 测试 + Task 7 冒烟 ✓
- §10 DoD → Task 8 ✓

**2. 占位符扫描:** 无 TBD/TODO;所有 step 含完整代码。`ui/traces/.gitkeep` 为有意占位(DoD 落真实 trace)。

**3. 类型/命名一致:** `Event(kind,role,text,payload,ts)` 全程一致;`emit(kind,role,text,**payload)` 一致;trace schema `{question,events,report,telemetry,status}` 在 Task 6 save/load 与 Task 7 app 回放一致;`render_*_md` / `telemetry_tiles` 命名跨 Task 一致。

