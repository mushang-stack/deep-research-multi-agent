# 单 Agent 基线对比 + Verifier 消融 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 eval 管线上加 `--system {multi,no_verify,baseline}` 三档 + 对比工具,产出"多 agent+verifier 超越单 agent 基线"的实证数字,零改 judge/metrics/trace。

**Architecture:** B 消融 = `build_system(verify=False)`(orchestrator 不注册 verify_findings);C 基线 = 新 `build_baseline_system`(单决策 agent + web 工具 + `write_report`,后者内嵌复用 Writer)。三档 findings 都经 `role=="tool"` 消息产出 → `trace.extract_findings` 通用。`eval/compare.py` 加载多份 scorecard 算 Δ。

**Tech Stack:** Python 3 / pytest(TDD,全 mock)/ Pydantic / 现有自研 harness(`AgentLoop` + `ToolRegistry`)。

**对应 spec:** [2026-08-12-m3-baseline-comparison-design.md](../specs/2026-08-12-m3-baseline-comparison-design.md)

---

## 文件结构

| 文件 | 动作 | 职责 |
|---|---|---|
| `agents/prompts.py` | 改 | 加 `NO_VERIFY_ORCHESTRATOR_PROMPT`、`BASELINE_PROMPT` |
| `agents/orchestrator.py` | 改 | `make_orchestrator` 加 `verify` 参数 |
| `agents/system.py` | 改 | `build_system` 加 `verify` 参数并透传 |
| `agents/baseline.py` | 新 | `make_baseline` + `build_baseline_system`(模式 C) |
| `eval/run_eval.py` | 改 | `--system` flag + `_build_fn_for` + 分层 results 目录 |
| `eval/compare.py` | 新 | `compare`/`render_table` + CLI |
| `config.yaml` | 改 | `guards.baseline_max_steps` |
| `README.md` | 改 | 加"基线对比与消融"节(真跑后填数) |
| `tests/test_orchestrator_tools.py` | 改 | verify=False 用例 |
| `tests/test_system.py` | 新 | `build_system(verify=)` 透传 |
| `tests/test_prompts.py` | 改 | 新 prompt 非空 |
| `tests/test_baseline.py` | 新 | C 模式接线 + write_report + trace 通用 |
| `tests/test_run_eval.py` | 改 | `_build_fn_for` + `--system` 分层目录 |
| `tests/test_compare.py` | 新 | Δ 数学 + 诚实报数 |

---

## Task 1:模式 B 消融 —— `NO_VERIFY_ORCHESTRATOR_PROMPT` + `make_orchestrator(verify=)`

**Files:**
- Modify: `agents/prompts.py`(末尾追加常量)
- Modify: `agents/orchestrator.py`(`make_orchestrator` 签名 + 注册逻辑)
- Test: `tests/test_orchestrator_tools.py`(追加 2 用例)

- [ ] **Step 1: 写失败测试**

在 `tests/test_orchestrator_tools.py` 顶部 import 行追加 `NO_VERIFY_ORCHESTRATOR_PROMPT`,并在文件末尾追加:

```python
from agents.prompts import NO_VERIFY_ORCHESTRATOR_PROMPT


def test_orchestrator_verify_false_strips_verify_tool():
    loop, get_report = make_orchestrator(
        client=FakeClient([]),
        run_researcher=lambda sq: _result('{"findings":[]}'),
        run_verifier=lambda f: _result('{}'),
        run_writer=lambda m: _result('{}'),
        verify=False,
    )
    assert loop.system_prompt == NO_VERIFY_ORCHESTRATOR_PROMPT
    assert "verify_findings" not in set(loop.registry.names())
    assert set(loop.registry.names()) == {"dispatch_research", "write_report"}
    assert get_report() is None


def test_orchestrator_verify_true_default_keeps_verify_tool():
    # 默认 verify=True:行为与现有完全一致(回归保护)
    loop, _ = make_orchestrator(
        client=FakeClient([]),
        run_researcher=lambda sq: _result('{}'),
        run_verifier=lambda f: _result('{}'),
        run_writer=lambda m: _result('{}'),
    )
    assert loop.system_prompt == ORCHESTRATOR_PROMPT
    assert "verify_findings" in set(loop.registry.names())
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_orchestrator_tools.py::test_orchestrator_verify_false_strips_verify_tool -v`
Expected: FAIL(`NO_VERIFY_ORCHESTRATOR_PROMPT` 未定义 / `verify` 参数不存在)

- [ ] **Step 3: 在 `agents/prompts.py` 末尾追加 prompt**

```python
NO_VERIFY_ORCHESTRATOR_PROMPT = """你是深度研究系统的规划中枢(Orchestrator,无验证消融模式)。用户给你一个研究问题,你要动态决定如何推进。

你只有两个内部工具:
- dispatch_research(sub_questions): 对一组子问题并发检索,返回结构化 Findings 列表(JSON)。
- write_report(outline, verified_findings): 把 Findings 综合成带引用报告。

工作方式(由你即兴决定,不固定流程):
1. 先在思考中把问题拆成若干子问题,然后调 dispatch_research 检索。
2. 拿到 Findings 后,【不经复核】直接调 write_report 综合报告(本模式无验证步骤,findings 直通撰写者)。
3. 若 dispatch 返回 0 findings,可再调一次补检(检索轮次有上限,达到上限时工具会提示你,届时必须停止)。

重要:调完 write_report 并收到报告结果后,直接用一句话收尾(如"报告已生成"),不要再调用任何工具。真正的报告由系统在后台提取,你无需复述报告内容。"""
```

- [ ] **Step 4: 改 `agents/orchestrator.py` —— `make_orchestrator` 加 `verify`**

把签名改为(加 `verify: bool = True`):

```python
def make_orchestrator(*, client, run_researcher, run_verifier, run_writer,
                      max_steps: int = 12, research_max_rounds: int = 3,
                      verify: bool = True):
```

在 `reg = ToolRegistry()` 之前选定 prompt,并把 `verify_findings` 的注册包进 `if verify:`。最终 `ToolRegistry` 段落改为:

```python
    prompt = ORCHESTRATOR_PROMPT if verify else NO_VERIFY_ORCHESTRATOR_PROMPT

    reg = ToolRegistry()
    reg.register(
        "dispatch_research", _dispatch_research,
        description="对一组子问题并发检索,返回 {findings:[...], failures:[...]}。",
        parameters={"type": "object",
                    "properties": {"sub_questions": {"type": "array", "items": {"type": "string"}}},
                    "required": ["sub_questions"]},
    )
    if verify:
        reg.register(
            "verify_findings", _verify_findings,
            description="复核一批 findings 是否有来源支撑,返回 {results:[{finding_id,verdict,reason,...}]}。",
            parameters={"type": "object",
                        "properties": {"findings": {"type": "array", "items": {"type": "object"}}},
                        "required": ["findings"]},
        )
    reg.register(
        "write_report", _write_report,
        description="基于已验证 findings 综合带引用报告。调用后用一句话收尾,不再调任何工具。",
        parameters={"type": "object",
                    "properties": {"outline": {"type": "string"},
                                   "verified_findings": {"type": "array", "items": {"type": "object"}}},
                    "required": ["outline", "verified_findings"]},
    )

    loop = AgentLoop(client=client, system_prompt=prompt,
                     registry=reg, max_steps=max_steps, name="orchestrator")
```

并在文件顶部 import 行追加:`from .prompts import ORCHESTRATOR_PROMPT, NO_VERIFY_ORCHESTRATOR_PROMPT`

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/test_orchestrator_tools.py -v`
Expected: PASS(含两个新用例 + 全部既有用例回归绿)

- [ ] **Step 6: 提交**

```bash
git add agents/prompts.py agents/orchestrator.py tests/test_orchestrator_tools.py
git commit -m "feat(orchestrator): make_orchestrator 加 verify 参数 + NO_VERIFY 消融 prompt"
```

---

## Task 2:`build_system(verify=)` 透传

**Files:**
- Modify: `agents/system.py`(`build_system` 签名 + 透传到 `make_orchestrator`)
- Test: `tests/test_system.py`(新建)

- [ ] **Step 1: 写失败测试**

新建 `tests/test_system.py`:

```python
from core.config import Config
from llm.base import LLMClient
from agents.system import build_system


class _FakeClient(LLMClient):
    def chat(self, **kw):
        raise AssertionError("build 阶段不应调 chat")


class _FakeSearch:
    def search(self, query):
        return []


def _cfg():
    return Config({"models": {"generator": {}},
                   "tools": {"web_search": {}, "web_read": {"max_chars": 8000}},
                   "guards": {"agent_max_steps": 12, "research_max_rounds": 3}})


def test_build_system_verify_false_strips_verify_tool():
    loop, _ = build_system(_cfg(), client=_FakeClient(), search_client=_FakeSearch(), verify=False)
    assert "verify_findings" not in set(loop.registry.names())


def test_build_system_verify_true_default_keeps_verify_tool():
    loop, _ = build_system(_cfg(), client=_FakeClient(), search_client=_FakeSearch())
    assert "verify_findings" in set(loop.registry.names())
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_system.py -v`
Expected: FAIL(`build_system() got an unexpected keyword argument 'verify'`)

- [ ] **Step 3: 改 `agents/system.py`**

`build_system` 签名加 `verify: bool = True`,并在 `return make_orchestrator(...)` 透传:

```python
def build_system(config: Config, *, client: LLMClient | None = None,
                 search_client=None, verify: bool = True):
```

return 段改为:

```python
    return make_orchestrator(
        client=client,
        run_researcher=_run_researcher, run_verifier=_run_verifier, run_writer=_run_writer,
        max_steps=max_steps, research_max_rounds=research_max_rounds,
        verify=verify,
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_system.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add agents/system.py tests/test_system.py
git commit -m "feat(system): build_system 透传 verify 参数"
```

---

## Task 3:`BASELINE_PROMPT`

**Files:**
- Modify: `agents/prompts.py`(末尾追加)
- Test: `tests/test_prompts.py`(追加用例)

- [ ] **Step 1: 写失败测试**

在 `tests/test_prompts.py` 末尾追加(若该文件已有别的 prompt 非空检查,沿用其风格;否则新增):

```python
from agents.prompts import BASELINE_PROMPT


def test_baseline_prompt_nonempty_and_mentions_tools():
    assert isinstance(BASELINE_PROMPT, str) and len(BASELINE_PROMPT) > 100
    assert "web_search" in BASELINE_PROMPT
    assert "web_read" in BASELINE_PROMPT
    assert "write_report" in BASELINE_PROMPT
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_prompts.py::test_baseline_prompt_nonempty_and_mentions_tools -v`
Expected: FAIL(`cannot import name 'BASELINE_PROMPT'`)

- [ ] **Step 3: 在 `agents/prompts.py` 末尾追加**

```python
BASELINE_PROMPT = """你是单人研究助理(Baseline),独立完成"检索 + 撰写"全过程,没有验证者帮你复核来源。

可用工具:
- web_search(query): 网页搜索,返回搜索结果列表(标题+URL+摘要)。
- web_read(url): 提取指定 URL 的正文。
- write_report(outline, findings): 把你编译的 findings 综合成带引用报告(系统后台用撰写者成文)。调用后用一句话收尾,不再调任何工具。

工作方式:
1. 用 web_search 搜研究问题;从结果里挑最相关的 URL 用 web_read 读正文。可多次搜索与读取,直到你认为资料足够。
2. 基于读到的正文,提炼结构化 findings。
3. 调 write_report(outline, findings) 提交,outline 是报告大纲,findings 是你编译的列表。findings 元素结构:{"id":"f1","claim":"结论陈述","source_url":"https://...","source_title":"来源标题","excerpt":"支撑原文原句","confidence":0.0到1.0},id 用 f1、f2... 递增。

铁律(claim 与 excerpt 必须严格对应,这是评估质量的关键):
- excerpt 必须是支撑该 claim 的原文原句——从你 web_read 读到的正文里直接复制,不要改写、不要翻译润色。
- claim 必须是这段 excerpt 的直接改写:只陈述 excerpt 里明确写到的事实,不添加 excerpt 外的内容。若想陈述 excerpt 外的事实,必须另读一个能支撑它的页面,用那段原文作 excerpt。
- source_url 必须是你真实访问过且能打开该原文的 URL,绝不允许编造 claim、excerpt 或来源。"""
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_prompts.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add agents/prompts.py tests/test_prompts.py
git commit -m "feat(prompts): 加 BASELINE_PROMPT(单 agent 基线)"
```

---

## Task 4:模式 C —— `make_baseline` + `build_baseline_system`

**Files:**
- Create: `agents/baseline.py`
- Test: `tests/test_baseline.py`(新建)

- [ ] **Step 1: 写失败测试**

新建 `tests/test_baseline.py`:

```python
import json

from llm.base import LLMClient, LLMResponse, ToolCall
from core.schemas import SearchResult
from agents.baseline import make_baseline
from agents.prompts import BASELINE_PROMPT
from eval.trace import extract_findings


class FakeClient(LLMClient):
    def __init__(self, responses):
        self._r = list(responses)

    def chat(self, **kw):
        return self._r.pop(0)


class _FakeSearch:
    def search(self, query):
        return [SearchResult(title="T", url="https://x", snippet="s")]


_REPORT_JSON = '{"sections":[{"heading":"H","content":"C","citations":["f1"]}],"sources":["https://x"]}'
_FINDINGS = [{"id": "f1", "claim": "投机解码加速推理", "source_url": "https://x",
              "source_title": "T", "excerpt": "摘录原文", "confidence": 0.9}]


def test_baseline_wiring_no_verifier():
    loop, get_report = make_baseline(client=FakeClient([]), search_client=_FakeSearch())
    assert loop.name == "baseline"
    assert loop.system_prompt == BASELINE_PROMPT
    assert set(loop.registry.names()) == {"web_search", "web_read", "write_report"}
    assert "verify_findings" not in loop.registry.names()  # 基线无 verifier
    assert get_report() is None


def test_write_report_stores_report_and_returns_findings():
    client = FakeClient([LLMResponse(content=_REPORT_JSON)])  # writer 的唯一一次响应
    loop, get_report = make_baseline(client=client, search_client=_FakeSearch())
    out = loop.registry.execute("write_report", {"outline": "大纲", "findings": _FINDINGS})
    assert "findings" in out
    assert out["findings"][0]["claim"] == "投机解码加速推理"
    report = get_report()
    assert report is not None
    assert report.sections[0].heading == "H"


def test_write_report_empty_findings_refused():
    client = FakeClient([])  # writer 不应被调用
    loop, get_report = make_baseline(client=client, search_client=_FakeSearch())
    out = loop.registry.execute("write_report", {"outline": "x", "findings": []})
    assert "error" in out
    assert get_report() is None


def test_write_report_unparseable_writer_not_stored():
    client = FakeClient([LLMResponse(content="not json")])
    loop, get_report = make_baseline(client=client, search_client=_FakeSearch())
    out = loop.registry.execute("write_report", {"outline": "x", "findings": _FINDINGS})
    assert "error" in out
    assert get_report() is None


def test_findings_extractable_via_same_trace():
    # write_report 返回 {"findings":[...]} → 成为 history 里 role==tool 消息 → 同一 trace 抽得到
    client = FakeClient([LLMResponse(content=_REPORT_JSON)])
    loop, _ = make_baseline(client=client, search_client=_FakeSearch())
    out = loop.registry.execute("write_report", {"outline": "大纲", "findings": _FINDINGS})
    simulated_history = [{"role": "tool", "content": json.dumps(out, ensure_ascii=False)}]
    findings = extract_findings(simulated_history)
    assert len(findings) == 1
    assert findings[0]["claim"] == "投机解码加速推理"


def test_baseline_loop_runs_and_produces_report_with_traceable_findings():
    # 完整 loop:baseline 直接调 write_report(跳过搜索)→ 内部 writer 成文 → 收尾
    client = FakeClient([
        LLMResponse(content="", tool_calls=[ToolCall(
            id="1", name="write_report",
            arguments=json.dumps({"outline": "大纲", "findings": _FINDINGS}))]),
        LLMResponse(content=_REPORT_JSON),   # writer 的响应
        LLMResponse(content="报告已生成"),    # baseline 收尾
    ])
    loop, get_report = make_baseline(client=client, search_client=_FakeSearch(), max_steps=4)
    result = loop.run("研究 X")
    assert get_report() is not None
    findings = extract_findings(result.history)
    assert len(findings) == 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_baseline.py -v`
Expected: FAIL(`No module named 'agents.baseline'`)

- [ ] **Step 3: 新建 `agents/baseline.py`**

```python
"""单 agent 基线(M3 后续 · 模式 C):一个决策 agent + 复用 Writer 子例程。
无 verifier、无 dispatch fan-out、无 orchestrator 拆解。接口与 build_system 一致:(loop, get_report)。
write_report 内部调 make_writer(复用、无工具)把 agent 编译的 findings 综合成 Report,存 holder,
并返回 {"findings":[...]} 进 history 供 eval/trace 抽取 → 三档 trace 通用。"""
import json

from core.agent_loop import AgentLoop
from .dispatch import parse_findings, parse_report
from .observe import progress
from .prompts import BASELINE_PROMPT
from .writer import make_writer
from ._webtools import build_web_registry


def make_baseline(*, client, search_client, max_chars: int = 8000,
                  writer_max_steps: int = 12, max_steps: int = 16):
    """返回 (baseline_loop, get_report)。

    单决策 agent 拥有 web_search / web_read / write_report。write_report(outline, findings):
    校验 findings → 调 make_writer 成文 → Report 入 holder → 返回 {"findings":[...], "n_sections":N}。
    """
    holder: dict = {}
    reg = build_web_registry(search_client, max_chars=max_chars)

    def _write_report(outline: str, findings: list) -> dict:
        if not findings:
            progress("[baseline] ✗ write_report 被拒绝:无 findings")
            return {"error": "无 findings,无法生成报告。不要用空 findings 调用 write_report。"}
        parsed = parse_findings(json.dumps({"findings": findings}, ensure_ascii=False))
        if not parsed:
            progress("[baseline] ✗ write_report:findings 全部不合法")
            return {"error": "findings 全部不合法,请重新编译后重试。"}
        for i, f in enumerate(parsed, 1):
            f.id = f"f{i}"
        msg = json.dumps({"outline": outline,
                          "verified_findings": [f.model_dump() for f in parsed]},
                         ensure_ascii=False)
        result = make_writer(client=client, max_steps=writer_max_steps).run(msg)
        report = parse_report(result.content)
        if report is None:
            snippet = (result.content or "")[:200]
            progress(f"[baseline] ✗ Writer 输出无法解析为 Report。前 200 字:{snippet!r}")
            return {"error": "writer 产出不可解析,请重试或基于现有 findings 重写"}
        progress(f"[baseline] ✓ 报告已生成({len(report.sections)} 节)")
        holder["report"] = report
        return {"findings": [f.model_dump() for f in parsed],
                "n_sections": len(report.sections)}

    reg.register(
        "write_report", _write_report,
        description="基于你编译的 findings 综合带引用报告(系统后台撰写者成文)。调用后用一句话收尾,不再调任何工具。",
        parameters={"type": "object",
                    "properties": {"outline": {"type": "string"},
                                   "findings": {"type": "array", "items": {"type": "object"}}},
                    "required": ["outline", "findings"]},
    )

    loop = AgentLoop(client=client, system_prompt=BASELINE_PROMPT,
                     registry=reg, max_steps=max_steps, name="baseline")

    def get_report():
        return holder.get("report")

    return loop, get_report


def build_baseline_system(config, *, client=None, search_client=None):
    """从 config 组装单 agent 基线,返回 (loop, get_report)。签名与 build_system 对齐。"""
    from llm.deepseek_client import DeepSeekClient
    from tools.web_search import BochaSearchClient
    from core.config import env

    gen = config["models"]["generator"]
    client = client or DeepSeekClient(
        base_url=gen["base_url"], model=gen["name"],
        temperature=gen["temperature"], max_tokens=gen["max_tokens"])
    ws = config["tools"]["web_search"]
    search_client = search_client or BochaSearchClient(
        api_key=env("BOCHA_API_KEY"), endpoint=ws["endpoint"], count=ws["count"])
    max_chars = config["tools"]["web_read"]["max_chars"]
    agent_max = config["guards"]["agent_max_steps"]
    baseline_max = config["guards"].get("baseline_max_steps", agent_max)
    return make_baseline(client=client, search_client=search_client,
                         max_chars=max_chars, writer_max_steps=agent_max,
                         max_steps=baseline_max)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_baseline.py -v`
Expected: PASS(全部 6 用例)

- [ ] **Step 5: 提交**

```bash
git add agents/baseline.py tests/test_baseline.py
git commit -m "feat(baseline): 单 agent 基线 build_baseline_system(模式 C)"
```

---

## Task 5:eval `--system` flag + `_build_fn_for` + 分层 results

**Files:**
- Modify: `eval/run_eval.py`
- Test: `tests/test_run_eval.py`(追加)

- [ ] **Step 1: 写失败测试**

在 `tests/test_run_eval.py` 顶部 import 段调整:把 `from llm.base import LLMResponse` 改为同时引入 `LLMClient`,并在 `eval.run_eval` 的 import 末尾加 `_build_fn_for`:

```python
from llm.base import LLMClient, LLMResponse
from eval.run_eval import evaluate_question, load_benchmark, main, report_to_text, _build_fn_for
```

在文件末尾追加(其中 `_FakeGenClient`/`_NoopSearch` 为 build 阶段不调 chat 的假对象):

```python
class _FakeGenClient(LLMClient):
    def chat(self, **kw):
        raise AssertionError("build 阶段不应调 chat")


class _NoopSearch:
    def search(self, query):
        return []


def _cfg_for_build():
    return Config({"models": {"generator": {}},
                   "tools": {"web_search": {}, "web_read": {"max_chars": 8000}},
                   "guards": {"agent_max_steps": 12, "research_max_rounds": 3}})


def test_build_fn_for_multi_and_baseline_identity():
    from agents.system import build_system
    from agents.baseline import build_baseline_system
    assert _build_fn_for("multi") is build_system
    assert _build_fn_for("baseline") is build_baseline_system


def test_build_fn_for_no_verify_strips_verify_tool():
    cfg = _cfg_for_build()
    loop_nv, _ = _build_fn_for("no_verify")(cfg, client=_FakeGenClient(), search_client=_NoopSearch())
    assert "verify_findings" not in set(loop_nv.registry.names())
    loop_m, _ = _build_fn_for("multi")(cfg, client=_FakeGenClient(), search_client=_NoopSearch())
    assert "verify_findings" in set(loop_m.registry.names())


def test_build_fn_for_unknown_raises():
    import pytest
    with pytest.raises(ValueError):
        _build_fn_for("bogus")


def test_main_system_baseline_writes_to_subdir(tmp_path):
    bench = _bench_with(tmp_path, ["q1"])
    out = tmp_path / "out"
    judge = FakeGLMClient([
        LLMResponse(content='{"support":"supported","source_real":true,"reason":""}'),
        LLMResponse(content='{"covered":true,"reason":""}'),
    ])
    cfg = Config({"thresholds": {"grounding_min": 0.85}})
    rc = main(["--benchmark", str(bench), "--system", "baseline"],
              results_dir=out, run_one_fn=_fake_run_one, judge_client=judge, cfg=cfg)
    assert rc == 0
    assert (out / "baseline" / "q1.json").exists()
    assert (out / "baseline" / "scorecard.json").exists()


def test_main_system_multi_writes_to_root_no_subdir(tmp_path):
    # multi(默认)写到 results 根,不加 /multi 子目录(向后兼容已提交基线)
    bench = _bench_with(tmp_path, ["q1"])
    out = tmp_path / "out"
    judge = FakeGLMClient([
        LLMResponse(content='{"support":"supported","source_real":true,"reason":""}'),
        LLMResponse(content='{"covered":true,"reason":""}'),
    ])
    cfg = Config({"thresholds": {"grounding_min": 0.85}})
    rc = main(["--benchmark", str(bench), "--system", "multi"],
              results_dir=out, run_one_fn=_fake_run_one, judge_client=judge, cfg=cfg)
    assert rc == 0
    assert (out / "q1.json").exists()
    assert not (out / "multi").exists()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_run_eval.py -k "build_fn_for or system_baseline or system_multi" -v`
Expected: FAIL(`cannot import name '_build_fn_for'`)

- [ ] **Step 3: 改 `eval/run_eval.py`**

3a. 顶部 import 段,在 `from agents.system import build_system` 下一行加:

```python
from agents.baseline import build_baseline_system
```

3b. 在 `run_one` 之前加 `_build_fn_for`,并把 `run_one` 改为按 system 选 build fn:

```python
def _build_fn_for(system: str):
    """system 名 → build 函数。multi/no_verify/baseline。供 run_one 与测试复用。"""
    if system == "multi":
        return build_system
    if system == "no_verify":
        return lambda cfg, **kw: build_system(cfg, verify=False, **kw)
    if system == "baseline":
        return build_baseline_system
    raise ValueError(f"unknown system: {system!r}")


def run_one(question: str, cfg, *, client=None, search_client=None, system: str = "multi"):
    """跑一次系统,返回 (report, history)。report 可能为 None。"""
    build_fn = _build_fn_for(system)
    loop, get_report = build_fn(cfg, client=client, search_client=search_client)
    result = loop.run(question)
    return get_report(), result.history
```

3c. `evaluate_question` 签名加 `system: str = "multi"`,默认 runner 透传:

```python
def evaluate_question(item: dict, cfg, *, judge_client,
                      client=None, search_client=None, run_one_fn=None,
                      judge_concurrency: int = 10, system: str = "multi"):
    """单题评估 → scorecard。"""
    question = item["question"]
    runner = run_one_fn or (lambda q: run_one(q, cfg, client=client,
                                              search_client=search_client, system=system))
```

3d. `main` 加 `--system` 参数、选 system、分层 results 目录、`_eval_one` 透传 system。相关改动:

argparse 段加(`--limit` 之后):

```python
    parser.add_argument("--system", choices=["multi", "no_verify", "baseline"], default="multi",
                        help="评估的系统配置:multi=完整 / no_verify=消融(去 verifier) / baseline=单 agent")
```

`main` body 里 `judge_client = judge_client or GLMClient()` 之前加:

```python
    system = args.system
```

results 目录段改为(替换原 `res_dir = Path(...)` 一行 + `res_dir.mkdir(...)`):

```python
    res_dir = Path(args.results or results_dir or (_EVAL_DIR / "results"))
    if system != "multi":
        res_dir = res_dir / system
    res_dir.mkdir(parents=True, exist_ok=True)
```

`_eval_one` 里 `evaluate_question(...)` 调用加 `system=system`:

```python
        try:
            sc = evaluate_question(it, cfg, judge_client=judge_client,
                                  run_one_fn=run_one_fn, judge_concurrency=judge_concurrency,
                                  system=system)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_run_eval.py -v`
Expected: PASS(全部既有 + 5 个新用例)

- [ ] **Step 5: 提交**

```bash
git add eval/run_eval.py tests/test_run_eval.py
git commit -m "feat(eval): --system {multi,no_verify,baseline} + 分层 results 目录"
```

---

## Task 6:`eval/compare.py` 三方对比

**Files:**
- Create: `eval/compare.py`
- Test: `tests/test_compare.py`(新建)

- [ ] **Step 1: 写失败测试**

新建 `tests/test_compare.py`:

```python
import json

from eval.compare import compare, render_table


def _sc(grounding, halluc=0.0, citation=1.0, coverage=1.0, success=1.0):
    return {"mean_grounding": grounding, "mean_hallucination": halluc,
            "mean_citation": citation, "mean_coverage": coverage, "success_rate": success}


def test_compare_two_systems_architecture_delta():
    cmp = compare({"multi": _sc(0.932, 0.046), "baseline": _sc(0.760, 0.190)})
    assert "A-C (architecture)" in cmp["deltas"]
    assert "A-B (verifier)" not in cmp["deltas"]
    d = cmp["deltas"]["A-C (architecture)"]
    assert d["Grounding"] == round(0.932 - 0.760, 3)
    assert d["幻觉率"] == round(0.046 - 0.190, 3)


def test_compare_three_systems_both_deltas():
    cmp = compare({"multi": _sc(0.90, 0.05), "no_verify": _sc(0.80, 0.10), "baseline": _sc(0.70, 0.20)})
    assert "A-B (verifier)" in cmp["deltas"]
    assert "A-C (architecture)" in cmp["deltas"]
    assert cmp["deltas"]["A-B (verifier)"]["Grounding"] == round(0.90 - 0.80, 3)


def test_compare_honest_when_baseline_beats_multi():
    # 基线 grounding 反超 → Δ 为负,照实报(不取绝对值、不隐藏)
    cmp = compare({"multi": _sc(0.70), "baseline": _sc(0.90)})
    d = cmp["deltas"]["A-C (architecture)"]
    assert d["Grounding"] == round(0.70 - 0.90, 3)
    assert d["Grounding"] < 0


def test_compare_rows_carry_all_system_values():
    cmp = compare({"multi": _sc(0.9), "baseline": _sc(0.7)})
    g = next(r for r in cmp["rows"] if r["key"] == "mean_grounding")
    assert g["multi"] == 0.9 and g["baseline"] == 0.7


def test_compare_missing_value_yields_none_delta():
    # 某指标为 None(如 N=0 导致 grounding 不可用)→ Δ 记 None,不抛
    cmp = compare({"multi": {"mean_grounding": None}, "baseline": _sc(0.7)})
    assert cmp["deltas"]["A-C (architecture)"]["Grounding"] is None


def test_render_table_contains_labels_and_delta_name():
    cmp = compare({"multi": _sc(0.9, 0.05), "baseline": _sc(0.7, 0.15)})
    txt = render_table(cmp)
    assert "Grounding" in txt and "幻觉率" in txt
    assert "A-C (architecture)" in txt


def test_compare_cli_writes_comparison_json(tmp_path, monkeypatch):
    # 造两份 scorecard,跑 CLI → 产 comparison.json
    (tmp_path / "scorecard.json").write_text(json.dumps(_sc(0.9, 0.05)), encoding="utf-8")
    bdir = tmp_path / "baseline"
    bdir.mkdir()
    (bdir / "scorecard.json").write_text(json.dumps(_sc(0.7, 0.15)), encoding="utf-8")
    from eval.compare import main as cmp_main
    rc = cmp_main(["--systems", "multi,baseline", "--results-dir", str(tmp_path)])
    assert rc == 0
    out = json.loads((tmp_path / "comparison.json").read_text(encoding="utf-8"))
    assert "A-C (architecture)" in out["deltas"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_compare.py -v`
Expected: FAIL(`No module named 'eval.compare'`)

- [ ] **Step 3: 新建 `eval/compare.py`**

```python
"""加载多份 scorecard → 算两两 Δ → 对比表 + comparison.json。纯函数 + CLI。
报告两组 Δ(若对应 system 在场):A−B = verifier 价值,A−C = 整套架构价值。"""
import argparse
import json
import sys
from pathlib import Path

_METRICS = [
    ("mean_grounding", "Grounding"),
    ("mean_hallucination", "幻觉率"),
    ("mean_citation", "引用准确率"),
    ("mean_coverage", "覆盖率"),
    ("success_rate", "成功率"),
]


def _delta(a: dict, b: dict) -> dict:
    out = {}
    for key, label in _METRICS:
        va, vb = a.get(key), b.get(key)
        out[label] = round(va - vb, 3) if isinstance(va, (int, float)) and isinstance(vb, (int, float)) else None
    return out


def compare(scorecards: dict) -> dict:
    """scorecards = {system名: scorecard}。返回 {systems, rows, deltas}。
    rows 每 metric 一行含各 system 值;deltas 含 A-B / A-C(缺系统则跳过)。诚实规则:反超 Δ 为负照实报。"""
    systems = list(scorecards)
    rows = []
    for key, label in _METRICS:
        row = {"key": key, "metric": label}
        for s in systems:
            row[s] = scorecards[s].get(key)
        rows.append(row)
    deltas = {}
    if "multi" in scorecards:
        if "no_verify" in scorecards:
            deltas["A-B (verifier)"] = _delta(scorecards["multi"], scorecards["no_verify"])
        if "baseline" in scorecards:
            deltas["A-C (architecture)"] = _delta(scorecards["multi"], scorecards["baseline"])
    return {"systems": systems, "rows": rows, "deltas": deltas}


def render_table(cmp: dict) -> str:
    systems = cmp["systems"]
    lines = ["=== 基线对比 ==="]
    lines.append("指标".ljust(12) + "".join(s.ljust(14) for s in systems))
    for row in cmp["rows"]:
        cells = []
        for s in systems:
            v = row.get(s)
            cells.append(f"{v:.3f}".ljust(14) if isinstance(v, (int, float)) else "N/A".ljust(14))
        lines.append(row["metric"].ljust(12) + "".join(cells))
    for name, d in cmp["deltas"].items():
        parts = []
        for k, v in d.items():
            if v is None:
                continue
            sign = "+" if v >= 0 else ""
            parts.append(f"{k}: {sign}{v}")
        lines.append(f"{name} → " + ", ".join(parts))
    return "\n".join(lines)


def main(argv=None, *, results_dir=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.compare",
                                     description="对比多 system 的 scorecard(Δ + 表)")
    parser.add_argument("--systems", default="multi,no_verify,baseline",
                        help="逗号分隔的 system 名(默认全三档;缺失自动跳过)")
    parser.add_argument("--results-dir", default=None, help="scorecard 根目录(默认 eval/results)")
    args = parser.parse_args(argv)
    base = Path(args.results_dir or results_dir or Path(__file__).resolve().parent / "results")

    scorecards = {}
    for s in [x.strip() for x in args.systems.split(",") if x.strip()]:
        p = base / "scorecard.json" if s == "multi" else base / s / "scorecard.json"
        if not p.exists():
            print(f"[compare] 跳过缺失的 {s}:{p}", file=sys.stderr)
            continue
        scorecards[s] = json.loads(p.read_text(encoding="utf-8"))
    if not scorecards:
        print("无可用 scorecard。先跑 python -m eval.run_eval --system <multi|no_verify|baseline>。",
              file=sys.stderr)
        return 1

    cmp = compare(scorecards)
    print(render_table(cmp))
    (base / "comparison.json").write_text(
        json.dumps(cmp, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_compare.py -v`
Expected: PASS(全部 7 用例)

- [ ] **Step 5: 提交**

```bash
git add eval/compare.py tests/test_compare.py
git commit -m "feat(eval): compare 三方对比(Δ + 表 + comparison.json)"
```

---

## Task 7:`config.yaml` baseline_max_steps + 全量测试绿

**Files:**
- Modify: `config.yaml`(guards 段加一行)

- [ ] **Step 1: 改 config**

在 `config.yaml` 的 `guards:` 段(`researcher_max_steps` 与 `research_max_rounds` 附近)加:

```yaml
  baseline_max_steps: 16          # 单 agent 基线步数(M3 后续 C:≥ agent_max_steps,故意宽裕抗质疑)
```

- [ ] **Step 2: 跑全量单测**

Run: `pytest -q`
Expected: PASS(既有 153 + 本计划新增,全 mock 不烧 API)。记录实际总数用于 README/DoD。

- [ ] **Step 3: 提交**

```bash
git add config.yaml
git commit -m "chore(config): guards.baseline_max_steps=16"
```

---

## Task 8:手动真跑 3 档 + README + 收尾提交

> ⚠️ 本任务烧真实 API(DeepSeek + 博查 + GLM 裁判),需 `.env` 配齐 key。3 档 × 5 题。若想先验证管线,可对各档加 `--limit 2` 跑趋势。

**Files:**
- Run: `python -m eval.run_eval`(三档)
- Run: `python -m eval.compare`
- Modify: `README.md`(填真实数字)

- [ ] **Step 1: 真跑 A multi(复跑,确认可复现)**

Run: `python -m eval.run_eval --system multi`
Expected: `eval/results/scorecard.json` 更新;终端打印质量 scorecard,`passed=true`。

- [ ] **Step 2: 真跑 B no_verify(消融)**

Run: `python -m eval.run_eval --system no_verify`
Expected: `eval/results/no_verify/scorecard.json` 产出。

- [ ] **Step 3: 真跑 C baseline(单 agent)**

Run: `python -m eval.run_eval --system baseline`
Expected: `eval/results/baseline/scorecard.json` 产出。

- [ ] **Step 4: 出三方对比**

Run: `python -m eval.compare`
Expected: 终端打印对比表(指标 / multi / no_verify / baseline + A−B、A−C Δ),写 `eval/results/comparison.json`。**抄录这 3 列数字与两组 Δ**,下一步填 README。

- [ ] **Step 5: 填 README**

在 `README.md` 的"三阶段质量修复"节之后、"提速"节之前(或"后续里程碑"之前),插入一节。**用 Step 4 抄录的真实数字替换下方 `<...>` 占位**:

```markdown
### 基线对比与消融(M3 后续)

在同一套 eval 管线(同 DeepSeek 生成 / 同博查工具 / 同 GLM-5.2 裁判 / 同 5 题基准)上跑三档:

| 配置 | 编排 | Verifier | Grounding | 幻觉率 | 引用准确率 | 覆盖率 | 成功率 |
|---|:---:|:---:|---|---|---|---|---|
| **A multi**(完整系统) | ✅ | ✅ | <A_g> | <A_h> | <A_cite> | <A_cov> | <A_suc> |
| **B no_verify**(消融) | ✅ | ❌ | <B_g> | <B_h> | <B_cite> | <B_cov> | <B_suc> |
| **C baseline**(单 agent) | ❌ | ❌ | <C_g> | <C_h> | <C_cite> | <C_cov> | <C_suc> |

- **Verifier 价值(A−B)**:Grounding +<dAB_g>、幻觉率 −<dAB_h>。
- **整套架构价值(A−C)**:Grounding +<dAC_g>、幻觉率 −<dAC_h>。

结论:引入 Verifier 与多 agent 编排使事实准确率提升、幻觉率下降,全面超越单 agent 基线。(B 与 A 严格等预算;C 给更宽裕步数以抗"削弱基线"质疑。)
```

> 若某指标基线/消融反超,照实保留负 Δ 并在结论里诚实说明,不挑数。

- [ ] **Step 6: 提交结果与 README**

```bash
git add README.md eval/results/no_verify eval/results/baseline eval/results/comparison.json eval/results/scorecard.json
git commit -m "chore(eval): 三档基线对比真实结果 + README 基线对比节"
```

（`eval/results/` 各 `*.json` 是否纳入 git 跟随仓库现有约定;若 `.gitignore` 已忽略 results,则只提交 README,并 `git add -f` 关键 scorecard。)

---

## Definition of Done(对应 spec §7)

- [ ] `build_baseline_system` + `verify=False` 消融 + `eval/compare.py` 就位,**零改 judge/metrics/trace**
- [ ] `pytest -q` 全绿(既有 153 + 新增,全 mock)
- [ ] `test_baseline` 证明 findings 经同一 `trace` 可抽、verifier 缺席、report=None 分支
- [ ] `test_orchestrator`/`test_system` 证明 `verify=False` 只动 verifier 一个变量
- [ ] `test_compare` 证明 Δ 数学正确 + 诚实报数(反超照实)
- [ ] 手动真跑 3 档 × 5 题 → `comparison.json` + 三方对比表,数字合理
- [ ] README 加"基线对比与消融"节(verifier 价值 + 架构价值的头号结论)
- [ ] 可进入 M3 后续 A(运维遥测)
