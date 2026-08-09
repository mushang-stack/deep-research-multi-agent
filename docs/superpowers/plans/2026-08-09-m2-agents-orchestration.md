# M2 四 Agent 编排层(Agents + 子 Agent 派发 + main.py)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 M1 的 `AgentLoop` 原语组合成四个有分工的 agent,实现 Orchestrator 动态决策驱动的并行子 agent 编排,并接通 `main.py` 真实链路 + 闭环 M1 的 config-wiring 待办。

**Architecture:** 三个子 agent(Researcher/Verifier/Writer)各自是一个 `AgentLoop`,区别只是 system prompt + 工具集;Orchestrator 也是 `AgentLoop`,其 `ToolRegistry` 注册三个**内部工具**(`dispatch_research`/`verify_findings`/`write_report`),fn 不直接干活而是派发子 agent。`dispatch_research` 用 `ThreadPoolExecutor` 并发跑 N 个 Researcher;子 agent 输出 JSON → 派发层 parse → 精简字符串回填(上下文隔离);`write_report` 把 Pydantic `Report` 存入闭包 holder(机制级确定性提取,不依赖 Orchestrator 转述)。`agents/system.py` 的 `build_system` 工厂做 config wiring(零侵入:生产走 config,client 默认值留 fallback)。本里程碑测试全 mock,**不烧 API**;真实链路靠手动 `python main.py` 冒烟一次。

**Tech Stack:** Python 3.11+、pydantic v2、concurrent.futures(ThreadPoolExecutor)、pytest + pytest-mock。复用 M1 的 `core/`(agent_loop/tool_registry/robust/schemas/config)、`llm/`(DeepSeek/GLM)、`tools/`(博查/trafilatura)。

**对应 spec:** [docs/superpowers/specs/2026-08-09-m2-agents-orchestration-design.md](../specs/2026-08-09-m2-agents-orchestration-design.md)

---

## 里程碑边界(本 plan 范围)

**做:** `agents/`(prompts + researcher/verifier/writer/orchestrator + system 工厂)、`main.py` CLI、子 agent 并行派发 + 容错、上下文隔离、`research_max_rounds` 闭包护栏、Report holder 硬提取、config-wiring 闭环、全 mock 测试 + 手动冒烟。

**不做(留给后续里程碑):** 9 维评估指标 + 基准集(M3)、Streamlit UI(M4)、真实基准回归测试(M3)。

**验收:** `pytest -q` 全绿(全 mock,M1 的 55 个保持绿 + M2 新增);`agents/test_system.py` 断言 config 值注入 client;手动 `python main.py "问题"` 真跑出一份带引用报告。

---

## File Structure

仓库根 = 项目根。新增/改动文件如下:

| 文件 | 职责 | 本里程碑创建 |
|---|---|---|
| `agents/prompts.py` | 四个 agent 的 system prompt(集中管理) | Task 1 |
| `agents/researcher.py` | Researcher:构造 AgentLoop + web 工具 registry | Task 2 |
| `agents/verifier.py` | Verifier:构造 AgentLoop + web 工具 registry | Task 3 |
| `agents/writer.py` | Writer:构造 AgentLoop + 无工具 | Task 4 |
| `agents/orchestrator.py` | Orchestrator 构造 + 三个内部派发工具 + round 闭包 + Report holder | Task 5/6/7/8 |
| `agents/dispatch.py` | 派发原语:并发跑子 agent、合并、容错、JSON 清洗解析 | Task 6 |
| `agents/system.py` | `build_system()` 工厂:组装 + config wiring | Task 9 |
| `main.py` | 极薄 CLI 入口 | Task 10 |
| `tests/test_agents_*.py` | 各 agent + 派发 + 工厂的 mock 测试 | 各 Task |

> **设计说明(对 spec 的落地细化):** spec 的 `orchestrator.py` 承载 Orchestrator 构造 + 三个派发工具;派发的并发/合并/容错逻辑抽到独立的 `agents/dispatch.py`(单一职责,且 dispatch 的测试可独立于 Orchestrator)。`build_system` 工厂放 `agents/system.py` 而非 `main.py`(让测试能 import 组装逻辑,main.py 保持极薄)。

---

## 约定

- **TDD:** 每个 Task 先写失败测试 → 跑红 → 写最小实现 → 跑绿 → 提交。
- **commit 粒度:** 每个 Task 一次 commit,conventional commits(`feat:`/`test:`)。
- **不烧 API:** 所有测试用 FakeClient/ScriptedClient(M1 已建立的模式),绝不真实调 DeepSeek/GLM/博查。
- **Python 3.11+:** 用 `str | None` 等新语法。
- **复用 M1 原语:** 不改 `core/`、`llm/`、`tools/`(config-wiring 是注入层面,零侵入)。

---

### Task 1: `agents/prompts.py` — 四个 agent 的 system prompt

集中管理四份 prompt。每份明确:角色、可用工具、**严格 JSON 输出契约**(派发层靠 json.loads 解析,见 spec §2.4)。Orchestrator 的 prompt 还包含"调 write_report 后返回收尾语"与"轮次护栏"的引导。

**Files:**
- Create: `agents/prompts.py`
- Test: `tests/test_prompts.py`

- [ ] **Step 1: 写失败测试 `tests/test_prompts.py`**

```python
from agents.prompts import (
    ORCHESTRATOR_PROMPT, RESEARCHER_PROMPT, VERIFIER_PROMPT, WRITER_PROMPT,
)


def test_all_prompts_nonempty():
    for p in (ORCHESTRATOR_PROMPT, RESEARCHER_PROMPT, VERIFIER_PROMPT, WRITER_PROMPT):
        assert isinstance(p, str) and len(p) > 50


def test_researcher_demands_json_and_real_sources():
    assert "findings" in RESEARCHER_PROMPT
    assert "web_search" in RESEARCHER_PROMPT and "web_read" in RESEARCHER_PROMPT


def test_verifier_outputs_results_verdicts():
    assert "results" in VERIFIER_PROMPT
    for v in ("supported", "unsupported", "weak"):
        assert v in VERIFIER_PROMPT


def test_writer_has_no_tools_and_cites():
    assert "finding_id" in WRITER_PROMPT
    # Writer 无工具:prompt 里不应承诺任何工具
    assert "web_search" not in WRITER_PROMPT and "web_read" not in WRITER_PROMPT


def test_orchestrator_knows_dispatch_tools_and_wrapup():
    for t in ("dispatch_research", "verify_findings", "write_report"):
        assert t in ORCHESTRATOR_PROMPT
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/test_prompts.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'agents.prompts'`

- [ ] **Step 3: 写最小实现 `agents/prompts.py`**

```python
"""四个 agent 的 system prompt(集中管理,spec §3 / §4)。
关键:子 agent 最终必须输出严格 JSON(派发层 json.loads 解析,做上下文隔离)。"""


ORCHESTRATOR_PROMPT = """你是深度研究系统的规划中枢(Orchestrator)。用户给你一个研究问题,你要动态决定如何推进。

你只有三个内部工具,分别派发子 agent 去做具体工作:
- dispatch_research(sub_questions): 对一组子问题并发检索,返回结构化 Findings 列表(JSON)。
- verify_findings(findings): 复核这些 Findings 是否有来源支撑,返回 VerificationResult 列表(JSON)。
- write_report(outline, verified_findings): 把已验证 Findings 综合成带引用报告。

工作方式(由你即兴决定,不固定流程):
1. 先在思考中把问题拆成若干子问题,然后调 dispatch_research 检索。
2. 拿到 Findings 后调 verify_findings 复核。
3. 看 VerificationResult:若大量 unsupported/weak 说明有缺口,可再调 dispatch_research 补检(注意:检索轮次有上限,达到上限时工具会提示你,届时必须停止补检)。
4. 覆盖足够后,调 write_report 让撰写者产出报告。

重要:调完 write_report 并收到报告结果后,直接用一句话收尾(如"报告已生成"),不要再调用任何工具。真正的报告由系统在后台提取,你无需复述报告内容。"""


RESEARCHER_PROMPT = """你是检索者(Researcher),针对单个研究子问题找资料。

可用工具:
- web_search(query): 网页搜索,返回搜索结果列表(标题+URL+摘要)。
- web_read(url): 提取指定 URL 的正文。

工作方式:
1. 用 web_search 搜该子问题。
2. 对最相关的几个结果用 web_read 读正文。
3. 基于读到的真实内容提炼 Findings。

铁律:每条 Finding 的 claim 必须来自你 web_read 实际读到的内容,source_url 必须是真实访问过的 URL。绝不允许编造 claim 或来源。

完成后,只输出如下严格 JSON(不要 markdown 代码块、不要任何额外文字):
{"findings": [{"id": "f1", "claim": "结论陈述", "source_url": "https://...", "source_title": "来源标题", "excerpt": "支撑原文摘录", "confidence": 0.0到1.0}]}
id 用 f1、f2... 递增。confidence 是你对这条 claim 被来源支撑程度的自评。"""


VERIFIER_PROMPT = """你是验证者(Verifier),复核一批 Findings 是否有来源支撑。

可用工具:
- web_read(url): 重新打开来源 URL 核对内容。
- web_search(query): 交叉印证。

工作方式:
1. 对每条 Finding,用 web_read 打开它的 source_url,核对 claim 是否真的被该来源支撑。
2. 必要时 web_search 交叉印证。
3. 对每条给出 verdict: supported(明确支撑) / unsupported(来源不支撑或来源失效) / weak(部分支撑/相关性弱)。

对 unsupported 或 weak 的,在 suggested_query 给一个更准确的检索词,供规划者补检。

完成后,只输出如下严格 JSON(不要 markdown 代码块、不要任何额外文字):
{"results": [{"finding_id": "f1", "verdict": "supported", "reason": "依据", "suggested_query": null}]}
每条 Finding 都要有对应的一条 result(finding_id 对应)。"""


WRITER_PROMPT = """你是撰写者(Writer),把已验证的 Findings 综合成结构化、带引用的研究报告。

你没有工具,只能使用用户消息里传入的 verified_findings。绝不能编造任何来源或事实——所有论断必须基于传入的 Findings。

工作方式:
1. 按 outline 组织章节。
2. 每个论断在 citations 里标注它依赖的 finding_id(可多个)。
3. sources 汇总所有被引用的 source_url。

完成后,只输出如下严格 JSON(不要 markdown 代码块、不要任何额外文字):
{"sections": [{"heading": "章节标题", "content": "正文", "citations": ["f1"]}], "sources": ["https://..."]}"""
```

- [ ] **Step 4: 跑测试验证通过**

Run: `pytest tests/test_prompts.py -v`
Expected: PASS(5 passed)

- [ ] **Step 5: Commit**

```bash
git add agents/prompts.py tests/test_prompts.py
git commit -m "feat(agents): add four agent system prompts"
```

---

### Task 2: `agents/_webtools.py` — web 工具绑定共享 registry

Researcher 与 Verifier 都用 `web_search` + `web_read` 两组工具,绑定逻辑一致,抽出共享构造(单一职责,DRY)。`web_search` fn 调 `search_client.search` 并把 `SearchResult`(Pydantic)`model_dump` 成可序列化 dict;`web_read` fn 调 `fetch_text`。

**Files:**
- Create: `agents/_webtools.py`
- Test: `tests/test_webtools.py`

- [ ] **Step 1: 写失败测试 `tests/test_webtools.py`**

```python
from agents._webtools import build_web_registry
from core.schemas import SearchResult


class _FakeSearch:
    def __init__(self, results):
        self._r = results
        self.queries = []

    def search(self, query):
        self.queries.append(query)
        return self._r


def test_registry_has_both_tools():
    reg = build_web_registry(_FakeSearch([]))
    assert set(reg.names()) == {"web_search", "web_read"}


def test_web_search_calls_client_and_dumps(monkeypatch):
    fake = _FakeSearch([SearchResult(title="T", url="https://x", snippet="s")])
    reg = build_web_registry(fake)
    out = reg.execute("web_search", {"query": "量子计算"})
    assert fake.queries == ["量子计算"]
    assert out == [{"title": "T", "url": "https://x", "snippet": "s"}]


def test_web_read_calls_fetch_text(monkeypatch):
    from agents import _webtools
    monkeypatch.setattr(_webtools, "fetch_text", lambda url, max_chars=8000: "正文")
    reg = build_web_registry(_FakeSearch([]), max_chars=20)
    out = reg.execute("web_read", {"url": "https://x"})
    assert out == "正文"


def test_web_search_schema_is_function():
    reg = build_web_registry(_FakeSearch([]))
    s = reg.schemas()
    names = {x["function"]["name"] for x in s}
    assert names == {"web_search", "web_read"}
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/test_webtools.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'agents._webtools'`

- [ ] **Step 3: 写最小实现 `agents/_webtools.py`**

```python
"""web 工具(web_search + web_read)绑定的共享 registry 构造。Researcher/Verifier 复用。"""
from core.tool_registry import ToolRegistry
from tools.web_read import fetch_text


def build_web_registry(search_client, *, max_chars: int = 8000) -> ToolRegistry:
    """绑定 web_search(→ search_client.search,model_dump 成 dict)与 web_read(→ fetch_text)。"""
    reg = ToolRegistry()
    reg.register(
        "web_search",
        lambda query: [r.model_dump() for r in search_client.search(query)],
        description="网页搜索:给定 query 返回相关网页列表(标题/URL/摘要)。",
        parameters={"type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"]},
    )
    reg.register(
        "web_read",
        lambda url: fetch_text(url, max_chars=max_chars),
        description="提取指定 URL 的网页正文。",
        parameters={"type": "object",
                    "properties": {"url": {"type": "string"}},
                    "required": ["url"]},
    )
    return reg
```

- [ ] **Step 4: 跑测试验证通过**

Run: `pytest tests/test_webtools.py -v`
Expected: PASS(4 passed)

- [ ] **Step 5: Commit**

```bash
git add agents/_webtools.py tests/test_webtools.py
git commit -m "feat(agents): add shared web-tool registry builder"
```

---

### Task 3: `agents/researcher.py` — Researcher agent

薄工厂:用 `build_web_registry` + `RESEARCHER_PROMPT` 构造一个 AgentLoop。

**Files:**
- Create: `agents/researcher.py`
- Test: `tests/test_researcher.py`

- [ ] **Step 1: 写失败测试 `tests/test_researcher.py`**

```python
from agents.researcher import make_researcher
from agents.prompts import RESEARCHER_PROMPT
from llm.base import LLMClient, LLMResponse, ToolCall
from core.schemas import SearchResult


class FakeClient(LLMClient):
    def __init__(self, responses):
        self._r = list(responses)

    def chat(self, **kw):
        return self._r.pop(0)


class _FakeSearch:
    def search(self, query):
        return [SearchResult(title="T", url="https://x", snippet="s")]


def test_researcher_wiring():
    agent = make_researcher(client=FakeClient([]), search_client=_FakeSearch())
    assert agent.system_prompt == RESEARCHER_PROMPT
    assert agent.name == "researcher"
    assert set(agent.registry.names()) == {"web_search", "web_read"}


def test_researcher_runs_search_then_json():
    client = FakeClient([
        LLMResponse(content="", tool_calls=[
            ToolCall(id="1", name="web_search", arguments='{"query":"x"}')]),
        LLMResponse(content='{"findings": [{"id":"f1","claim":"c","source_url":"https://x"}]}'),
    ])
    agent = make_researcher(client=client, search_client=_FakeSearch())
    out = agent.run("研究 x")
    assert "findings" in out.content
    assert len(out.history) >= 4  # system + user + assistant(tool) + tool + assistant
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/test_researcher.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'agents.researcher'`

- [ ] **Step 3: 写最小实现 `agents/researcher.py`**

```python
"""Researcher:单子问题检索 agent(AgentLoop + web 工具)。"""
from core.agent_loop import AgentLoop
from ._webtools import build_web_registry
from .prompts import RESEARCHER_PROMPT


def make_researcher(*, client, search_client, max_chars: int = 8000,
                    max_steps: int = 12) -> AgentLoop:
    return AgentLoop(
        client=client, system_prompt=RESEARCHER_PROMPT,
        registry=build_web_registry(search_client, max_chars=max_chars),
        max_steps=max_steps, name="researcher",
    )
```

- [ ] **Step 4: 跑测试验证通过**

Run: `pytest tests/test_researcher.py -v`
Expected: PASS(2 passed)

- [ ] **Step 5: Commit**

```bash
git add agents/researcher.py tests/test_researcher.py
git commit -m "feat(agents): add Researcher agent"
```

---

### Task 4: `agents/verifier.py` — Verifier agent

薄工厂:同样用 `build_web_registry`(交叉印证需 web_read + web_search)+ `VERIFIER_PROMPT`。

**Files:**
- Create: `agents/verifier.py`
- Test: `tests/test_verifier.py`

- [ ] **Step 1: 写失败测试 `tests/test_verifier.py`**

```python
from agents.verifier import make_verifier
from agents.prompts import VERIFIER_PROMPT
from llm.base import LLMClient, LLMResponse


class FakeClient(LLMClient):
    def __init__(self, responses):
        self._r = list(responses)

    def chat(self, **kw):
        return self._r.pop(0)


class _FakeSearch:
    def search(self, query):
        return []


def test_verifier_wiring():
    agent = make_verifier(client=FakeClient([]), search_client=_FakeSearch())
    assert agent.system_prompt == VERIFIER_PROMPT
    assert agent.name == "verifier"
    assert set(agent.registry.names()) == {"web_search", "web_read"}


def test_verifier_outputs_results_json():
    client = FakeClient([
        LLMResponse(content='{"results": [{"finding_id":"f1","verdict":"supported"}]}'),
    ])
    agent = make_verifier(client=client, search_client=_FakeSearch())
    out = agent.run('{"findings": [{"id":"f1"}]}')
    assert "results" in out.content and "supported" in out.content
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/test_verifier.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'agents.verifier'`

- [ ] **Step 3: 写最小实现 `agents/verifier.py`**

```python
"""Verifier:复核 Findings 来源支撑的 agent(AgentLoop + web 工具,交叉印证)。"""
from core.agent_loop import AgentLoop
from ._webtools import build_web_registry
from .prompts import VERIFIER_PROMPT


def make_verifier(*, client, search_client, max_chars: int = 8000,
                  max_steps: int = 12) -> AgentLoop:
    return AgentLoop(
        client=client, system_prompt=VERIFIER_PROMPT,
        registry=build_web_registry(search_client, max_chars=max_chars),
        max_steps=max_steps, name="verifier",
    )
```

- [ ] **Step 4: 跑测试验证通过**

Run: `pytest tests/test_verifier.py -v`
Expected: PASS(2 passed)

- [ ] **Step 5: Commit**

```bash
git add agents/verifier.py tests/test_verifier.py
git commit -m "feat(agents): add Verifier agent"
```

---

### Task 5: `agents/writer.py` — Writer agent(无工具,防幻觉硬保证)

Writer 显式不传 registry(`AgentLoop` 的 `registry=None` → `tools=None`),从结构上杜绝调外部工具/编造来源。

**Files:**
- Create: `agents/writer.py`
- Test: `tests/test_writer.py`

- [ ] **Step 1: 写失败测试 `tests/test_writer.py`**

```python
from agents.writer import make_writer
from agents.prompts import WRITER_PROMPT
from llm.base import LLMClient, LLMResponse


class FakeClient(LLMClient):
    def __init__(self, responses):
        self._r = list(responses)
        self.last_tools = None

    def chat(self, *, messages, tools=None, **kw):
        self.last_tools = tools
        return self._r.pop(0)


def test_writer_has_no_tools():
    agent = make_writer(client=FakeClient([]))
    assert agent.system_prompt == WRITER_PROMPT
    assert agent.name == "writer"
    assert agent.registry is None  # 无工具:防幻觉硬保证


def test_writer_passes_no_tools_to_client():
    client = FakeClient([
        LLMResponse(content='{"sections": [], "sources": []}'),
    ])
    agent = make_writer(client=client)
    agent.run('{"findings": []}')
    assert client.last_tools is None  # 确认 client 收到 tools=None


def test_writer_outputs_report_json():
    client = FakeClient([
        LLMResponse(content='{"sections":[{"heading":"H","content":"C","citations":["f1"]}],"sources":["https://x"]}'),
    ])
    agent = make_writer(client=client)
    out = agent.run('{"findings": [{"id":"f1"}]}')
    assert "sections" in out.content and "sources" in out.content
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/test_writer.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'agents.writer'`

- [ ] **Step 3: 写最小实现 `agents/writer.py`**

```python
"""Writer:综合带引用报告的 agent。无工具(registry=None),只能用传入数据——防幻觉硬保证。"""
from core.agent_loop import AgentLoop
from .prompts import WRITER_PROMPT


def make_writer(*, client, max_steps: int = 12) -> AgentLoop:
    return AgentLoop(
        client=client, system_prompt=WRITER_PROMPT,
        registry=None,  # 无工具
        max_steps=max_steps, name="writer",
    )
```

- [ ] **Step 4: 跑测试验证通过**

Run: `pytest tests/test_writer.py -v`
Expected: PASS(3 passed)

- [ ] **Step 5: Commit**

```bash
git add agents/writer.py tests/test_writer.py
git commit -m "feat(agents): add Writer agent (no tools, anti-hallucination)"
```

---

### Task 6: `agents/dispatch.py` — JSON 清洗/解析 + 并行派发原语

派发层的两个职责集中在此(单一职责,便于独立测试):
1. **JSON 清洗/解析**:子 agent 被要求输出严格 JSON,但模型常套 markdown 代码块或带杂字。`clean_json` 去包裹;`parse_*` 用 M1 的 `safe_parse_arguments` 兜底,坏 JSON/坏项静默跳过(容错,对应 spec §2.4 / §9 的 ~60% 成功率边角)。
2. **`dispatch_research`**:`ThreadPoolExecutor` 并发跑 N 个 researcher,合并 findings,**单点失败容错**(记入 failures 不阻塞),id 去重重编号(多 researcher 可能都用 "f1")。

**Files:**
- Create: `agents/dispatch.py`
- Test: `tests/test_dispatch.py`

- [ ] **Step 1: 写失败测试 `tests/test_dispatch.py`**

```python
import threading
import time

from agents.dispatch import (
    clean_json, parse_findings, parse_results, parse_report, dispatch_research,
)


def test_clean_json_strips_markdown_fence():
    assert clean_json('```json\n{"a":1}\n```') == '{"a":1}'
    assert clean_json('{"a":1}') == '{"a":1}'
    assert clean_json("") == ""
    assert clean_json("  ```\n{}\n```  ") == "{}"


def test_parse_findings_valid():
    text = '{"findings": [{"id":"f1","claim":"c","source_url":"https://x"}]}'
    fs = parse_findings(text)
    assert len(fs) == 1 and fs[0].claim == "c"


def test_parse_findings_tolerates_bad_json():
    assert parse_findings("not json at all") == []
    assert parse_findings('{"findings": [{"id":"x"}]}') == []  # 缺 source_url → 跳过坏项


def test_parse_results_and_report():
    rs = parse_results('{"results":[{"finding_id":"f1","verdict":"supported"}]}')
    assert rs[0].verdict == "supported"
    rep = parse_report('{"sections":[{"heading":"H","content":"C"}],"sources":["https://x"]}')
    assert rep is not None and rep.sections[0].heading == "H"
    assert parse_report("garbage") is None


def _result(content):
    return type("R", (), {"content": content})()


def test_dispatch_merges_findings_and_renumbers():
    def run_one(sq):
        return _result('{"findings": [{"id":"f1","claim":sq,"source_url":"https://x"}]}')
    out = dispatch_research(["q1", "q2"], run_one)
    assert [f["id"] for f in out["findings"]] == ["f1", "f2"]  # 去重重编号
    assert {f["claim"] for f in out["findings"]} == {"q1", "q2"}
    assert out["failures"] == []


def test_dispatch_tolerates_single_failure():
    def run_one(sq):
        if sq == "bad":
            raise RuntimeError("boom")
        return _result('{"findings": [{"id":"f1","claim":sq,"source_url":"https://x"}]}')
    out = dispatch_research(["good", "bad"], run_one)
    assert len(out["findings"]) == 1
    assert len(out["failures"]) == 1 and out["failures"][0]["sub_question"] == "bad"


def test_dispatch_runs_concurrently():
    lock = threading.Lock()
    state = {"active": 0, "max": 0}

    def run_one(sq):
        with lock:
            state["active"] += 1
            state["max"] = max(state["max"], state["active"])
        time.sleep(0.05)
        with lock:
            state["active"] -= 1
        return _result('{"findings":[]}')

    dispatch_research(["q1", "q2", "q3"], run_one)
    assert state["max"] >= 2  # 确实并发(非串行)
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/test_dispatch.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'agents.dispatch'`

- [ ] **Step 3: 写最小实现 `agents/dispatch.py`**

```python
"""子 agent 派发原语(集中管理,单一职责):
- JSON 清洗/解析:子 agent 输出严格 JSON,但模型常套 markdown 代码块/带杂字,这里清洗+兜底。
- 并行派发 dispatch_research:ThreadPoolExecutor 并发跑 N 个 researcher,合并 findings,单点容错。"""
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from core.robust import safe_parse_arguments
from core.schemas import Finding, VerificationResult, Report


def clean_json(text: str) -> str:
    """去除 markdown 代码块包裹(```json ... ```)与首尾空白。"""
    if not text:
        return ""
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*\n?", "", s)
        s = re.sub(r"\n?```\s*$", "", s)
    return s.strip()


def _items(text: str, key: str) -> list[dict]:
    """从 {"<key>":[...]} 提取列表。坏 JSON → []。"""
    data = safe_parse_arguments(clean_json(text))
    if not isinstance(data, dict):
        return []
    return data.get(key, []) or []


def parse_findings(text: str) -> list[Finding]:
    out = []
    for it in _items(text, "findings"):
        try:
            out.append(Finding(**it))
        except Exception:
            continue  # 坏项跳过(容错)
    return out


def parse_results(text: str) -> list[VerificationResult]:
    out = []
    for it in _items(text, "results"):
        try:
            out.append(VerificationResult(**it))
        except Exception:
            continue
    return out


def parse_report(text: str) -> Optional[Report]:
    data = safe_parse_arguments(clean_json(text))
    if not isinstance(data, dict):
        return None
    try:
        return Report(**data)
    except Exception:
        return None


def dispatch_research(sub_questions: list[str], run_one, *, max_workers: int | None = None) -> dict:
    """并发跑 N 个 researcher(每个 sub_question 一个),合并 findings,单点容错。

    run_one(sub_question) -> 对象(需有 .content 属性,JSON 字符串,如 AgentResult)。
    单个 run_one 抛异常(Escalation 等)不阻塞整体,记入 failures。
    返回 {"findings":[...], "failures":[...]}(回填给 Orchestrator 的精简结构)。"""
    findings: list[Finding] = []
    failures: list[dict] = []
    workers = max_workers or max(1, len(sub_questions))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        future_map = {ex.submit(run_one, sq): sq for sq in sub_questions}
        for fut in as_completed(future_map):
            sq = future_map[fut]
            try:
                result = fut.result()
                findings.extend(parse_findings(result.content))
            except Exception as e:  # 单点失败:容错,不阻塞整体
                failures.append({"sub_question": sq, "error": repr(e)})
    # 多 researcher 可能都用 "f1" → 去重重编号,避免 id 冲突
    for i, f in enumerate(findings, 1):
        f.id = f"f{i}"
    return {"findings": [f.model_dump() for f in findings], "failures": failures}
```

- [ ] **Step 4: 跑测试验证通过**

Run: `pytest tests/test_dispatch.py -v`
Expected: PASS(7 passed)

- [ ] **Step 5: Commit**

```bash
git add agents/dispatch.py tests/test_dispatch.py
git commit -m "feat(agents): add JSON parsing + parallel dispatch primitive"
```

---

### Task 7: `agents/orchestrator.py` — Orchestrator + 三个内部派发工具 + holder + 轮次护栏

Orchestrator 是一个 `AgentLoop`,registry 注册三个内部工具。**关键设计:** 三个工具调用的子 agent runner 通过参数注入(`run_researcher`/`run_verifier`/`run_writer`),system.py 在生产时用共享 client 绑定真实 runner,测试时注入 fake runner——这样 Orchestrator 的循环用 FakeClient 驱动、子 agent 用 fake,互不干扰。

状态用闭包:
- `holder`:`write_report` 把 Pydantic `Report` 存入,`get_report()` 取出(机制级确定性提取,spec §2.6)。
- `round_counter`:`dispatch_research` 每调用 +1,超 `research_max_rounds` 时返回引导提示(spec §2.5,不改 AgentLoop)。

**Files:**
- Create: `agents/orchestrator.py`
- Test: `tests/test_orchestrator_tools.py`

- [ ] **Step 1: 写失败测试 `tests/test_orchestrator_tools.py`**

```python
import json

from agents.orchestrator import make_orchestrator
from agents.prompts import ORCHESTRATOR_PROMPT
from llm.base import LLMClient, LLMResponse


class FakeClient(LLMClient):
    def __init__(self, responses):
        self._r = list(responses)

    def chat(self, **kw):
        return self._r.pop(0)


def _result(content):
    return type("R", (), {"content": content})()


def _make(run_researcher, run_verifier, run_writer, *, research_max_rounds=3):
    return make_orchestrator(
        client=FakeClient([]),
        run_researcher=run_researcher, run_verifier=run_verifier, run_writer=run_writer,
        research_max_rounds=research_max_rounds,
    )


def test_orchestrator_wiring():
    loop, get_report = _make(lambda sq: _result('{"findings":[]}'),
                              lambda f: _result('{"results":[]}'),
                              lambda m: _result('{"sections":[],"sources":[]}'))
    assert loop.system_prompt == ORCHESTRATOR_PROMPT
    assert loop.name == "orchestrator"
    assert set(loop.registry.names()) == {"dispatch_research", "verify_findings", "write_report"}
    assert get_report() is None  # 初始无报告


def test_dispatch_research_calls_runner_and_merges():
    seen = []

    def run_researcher(sq):
        seen.append(sq)
        return _result('{"findings": [{"id":"f1","claim":sq,"source_url":"https://x"}]}')

    loop, _ = _make(run_researcher, lambda f: _result('{}'), lambda m: _result('{}'))
    out = loop.registry.execute("dispatch_research", {"sub_questions": ["q1", "q2"]})
    assert set(seen) == {"q1", "q2"}
    assert len(out["findings"]) == 2
    assert out["failures"] == []


def test_dispatch_research_round_guard():
    loop, _ = _make(lambda sq: _result('{"findings":[]}'),
                    lambda f: _result('{}'), lambda m: _result('{}'),
                    research_max_rounds=2)
    # 前两轮正常派发
    assert "findings" in loop.registry.execute("dispatch_research", {"sub_questions": ["q1"]})
    assert "findings" in loop.registry.execute("dispatch_research", {"sub_questions": ["q2"]})
    # 第三轮超限 → 引导收敛(不再派发)
    out = loop.registry.execute("dispatch_research", {"sub_questions": ["q3"]})
    assert out["status"] == "max_rounds_reached"


def test_verify_findings_parses_results():
    def run_verifier(findings_json):
        data = json.loads(findings_json)
        assert data["findings"] == [{"id": "f1"}]
        return _result('{"results":[{"finding_id":"f1","verdict":"supported"}]}')

    loop, _ = _make(lambda sq: _result('{}'), run_verifier, lambda m: _result('{}'))
    out = loop.registry.execute("verify_findings", {"findings": [{"id": "f1"}]})
    assert out["results"][0]["verdict"] == "supported"


def test_write_report_stores_in_holder():
    def run_writer(msg):
        return _result('{"sections":[{"heading":"H","content":"C","citations":["f1"]}],"sources":["https://x"]}')

    loop, get_report = _make(lambda sq: _result('{}'), lambda f: _result('{}'), run_writer)
    out = loop.registry.execute("write_report",
                                {"outline": "大纲", "verified_findings": [{"id": "f1"}]})
    assert out["sections"][0]["heading"] == "H"
    report = get_report()
    assert report is not None  # holder 硬提取
    assert report.sections[0].citations == ["f1"]


def test_write_report_unparseable_not_stored():
    loop, get_report = _make(lambda sq: _result('{}'), lambda f: _result('{}'),
                             lambda m: _result("not json"))
    out = loop.registry.execute("write_report", {"outline": "x", "verified_findings": []})
    assert "error" in out
    assert get_report() is None
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/test_orchestrator_tools.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'agents.orchestrator'`

- [ ] **Step 3: 写最小实现 `agents/orchestrator.py`**

```python
"""Orchestrator:规划中枢。AgentLoop + 三个内部派发工具。
子 agent runner 注入(生产由 system.py 绑定共享 client,测试注入 fake)。
状态用闭包:report_holder(机制级确定性提取)、round_counter(轮次护栏)。"""
import json

from core.agent_loop import AgentLoop
from core.tool_registry import ToolRegistry
from .dispatch import dispatch_research, parse_results, parse_report
from .prompts import ORCHESTRATOR_PROMPT


def make_orchestrator(*, client, run_researcher, run_verifier, run_writer,
                      max_steps: int = 12, research_max_rounds: int = 3):
    """返回 (orchestrator_loop, get_report)。

    run_researcher(sub_question) -> AgentResult   (content = Findings JSON)
    run_verifier(findings_json)  -> AgentResult   (content = Results JSON)
    run_writer(user_message)     -> AgentResult   (content = Report JSON)
    get_report() -> Report | None   (从 holder 取;None 表示从未成功 write_report)
    """
    holder: dict = {}
    round_counter: dict = {"n": 0}

    def _dispatch_research(sub_questions: list[str]) -> dict:
        round_counter["n"] += 1
        if round_counter["n"] > research_max_rounds:
            return {"status": "max_rounds_reached",
                    "message": "已达最大研究轮次,请直接 write_report,不要再检索。"}
        return dispatch_research(sub_questions, run_researcher)

    def _verify_findings(findings: list[dict]) -> dict:
        msg = json.dumps({"findings": findings}, ensure_ascii=False)
        result = run_verifier(msg)
        results = parse_results(result.content)
        return {"results": [r.model_dump() for r in results]}

    def _write_report(outline: str, verified_findings: list[dict]) -> dict:
        msg = json.dumps({"outline": outline, "verified_findings": verified_findings},
                         ensure_ascii=False)
        result = run_writer(msg)
        report = parse_report(result.content)
        if report is None:
            return {"error": "writer 产出不可解析,请重试或基于现有 findings 重写"}
        holder["report"] = report  # 机制级确定性提取
        return report.model_dump()

    reg = ToolRegistry()
    reg.register(
        "dispatch_research", _dispatch_research,
        description="对一组子问题并发检索,返回 {findings:[...], failures:[...]}。",
        parameters={"type": "object",
                    "properties": {"sub_questions": {"type": "array", "items": {"type": "string"}}},
                    "required": ["sub_questions"]},
    )
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

    loop = AgentLoop(client=client, system_prompt=ORCHESTRATOR_PROMPT,
                     registry=reg, max_steps=max_steps, name="orchestrator")

    def get_report():
        return holder.get("report")

    return loop, get_report
```

- [ ] **Step 4: 跑测试验证通过**

Run: `pytest tests/test_orchestrator_tools.py -v`
Expected: PASS(6 passed)

- [ ] **Step 5: Commit**

```bash
git add agents/orchestrator.py tests/test_orchestrator_tools.py
git commit -m "feat(agents): add Orchestrator with dispatch/verify/write tools + holder"
```

---

### Task 8: Orchestrator 端到端集成(FakeClient 驱动整条链路)

用 FakeClient 驱动 Orchestrator 走完 `dispatch_research → verify_findings → write_report → 收尾`,子 agent 用 fake runner。验证:① 整条链路成立;② Report 从 holder 取到;③ 上下文隔离(回填给 Orchestrator 的 tool 消息是结构化 JSON,非原始网页正文);④ Orchestrator 从未调 write_report 时 holder 为 None(干净失败信号)。

**Files:**
- Create: `tests/test_orchestrator_e2e.py`

- [ ] **Step 1: 写集成测试 `tests/test_orchestrator_e2e.py`**

```python
from agents.orchestrator import make_orchestrator
from llm.base import LLMClient, LLMResponse, ToolCall


class FakeClient(LLMClient):
    def __init__(self, responses):
        self._r = list(responses)

    def chat(self, **kw):
        return self._r.pop(0)


def _result(content):
    return type("R", (), {"content": content})()


def test_end_to_end_dispatch_verify_write():
    client = FakeClient([
        LLMResponse(content="", tool_calls=[ToolCall(
            id="1", name="dispatch_research",
            arguments='{"sub_questions":["q1","q2"]}')]),
        LLMResponse(content="", tool_calls=[ToolCall(
            id="2", name="verify_findings",
            arguments='{"findings":[{"id":"f1"}]}')]),
        LLMResponse(content="", tool_calls=[ToolCall(
            id="3", name="write_report",
            arguments='{"outline":"O","verified_findings":[{"id":"f1"}]}')]),
        LLMResponse(content="报告已生成"),
    ])
    loop, get_report = make_orchestrator(
        client=client,
        run_researcher=lambda sq: _result('{"findings":[{"id":"f1","claim":sq,"source_url":"https://x"}]}'),
        run_verifier=lambda msg: _result('{"results":[{"finding_id":"f1","verdict":"supported"}]}'),
        run_writer=lambda msg: _result('{"sections":[{"heading":"H","content":"C","citations":["f1"]}],"sources":["https://x"]}'),
    )
    out = loop.run("研究问题")

    assert out.content == "报告已生成"
    report = get_report()
    assert report is not None
    assert report.sections[0].citations == ["f1"]
    # 上下文隔离:回填的 tool 消息是结构化 JSON,不是原始网页正文
    tool_msgs = [m for m in out.history if m.get("role") == "tool"]
    assert any("findings" in m["content"] for m in tool_msgs)


def test_finishes_without_write_report_yields_none():
    client = FakeClient([LLMResponse(content="我无法完成")])  # 直接收尾,未调 write_report
    loop, get_report = make_orchestrator(
        client=client,
        run_researcher=lambda sq: _result("{}"),
        run_verifier=lambda msg: _result("{}"),
        run_writer=lambda msg: _result("{}"),
    )
    loop.run("问题")
    assert get_report() is None  # 从未产出报告 → 调用方据此判定失败
```

- [ ] **Step 2: 跑测试验证通过(实现已在 Task 7 就位)**

Run: `pytest tests/test_orchestrator_e2e.py -v`
Expected: PASS(2 passed)

- [ ] **Step 3: Commit**

```bash
git add tests/test_orchestrator_e2e.py
git commit -m "test(agents): end-to-end Orchestrator dispatch→verify→write chain"
```

---

### Task 9: `agents/system.py` — build_system 工厂 + config wiring(闭环 M1 待办)

`build_system(config, *, client=None, search_client=None)` 是整个系统的组装点。从 `config.yaml` 读模型/工具/guard 参数**显式注入**各构造函数(生产链路 config 是唯一真相源,client 硬编码默认仅作 fallback → 零侵入,闭环 M1 待办)。子 agent runner 在此绑定共享 client + search_client。

**Files:**
- Create: `agents/system.py`
- Test: `tests/test_system.py`

- [ ] **Step 1: 写失败测试 `tests/test_system.py`**

```python
from agents.system import build_system
from core.config import load_config
from llm.base import LLMClient, LLMResponse


def _write_cfg(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(
        "models:\n"
        "  generator: {name: deepseek-chat, base_url: 'https://api.deepseek.com', temperature: 0.3, max_tokens: 4096}\n"
        "tools:\n"
        "  web_search: {endpoint: 'https://api.bochaai.com/v1/web-search', count: 8}\n"
        "  web_read: {max_chars: 8000}\n"
        "guards: {agent_max_steps: 12, research_max_rounds: 3, request_max_retries: 3, request_backoff_base: 1.5}\n",
        encoding="utf-8",
    )
    return load_config(p)


class _StubClient:
    def chat(self, **kw):
        raise AssertionError("build_system 不应调用 client.chat")


def test_build_system_wires_config_to_clients(monkeypatch, tmp_path):
    cfg = _write_cfg(tmp_path)
    captured = {}
    monkeypatch.setattr("agents.system.DeepSeekClient",
                        lambda **kw: captured.setdefault("ds", kw) or _StubClient())
    monkeypatch.setattr("agents.system.BochaSearchClient",
                        lambda **kw: captured.setdefault("bocha", kw))
    monkeypatch.setenv("BOCHA_API_KEY", "sk-b")

    loop, get_report = build_system(cfg)
    # M1 待办验收:config 值确实注入了 client 构造
    assert captured["ds"]["model"] == "deepseek-chat"
    assert captured["ds"]["temperature"] == 0.3
    assert captured["ds"]["max_tokens"] == 4096
    assert captured["bocha"]["endpoint"] == "https://api.bochaai.com/v1/web-search"
    assert captured["bocha"]["count"] == 8
    # guard 注入
    assert loop.max_steps == 12
    assert loop.name == "orchestrator"
    assert get_report() is None


def test_build_system_uses_injected_clients(tmp_path):
    cfg = _write_cfg(tmp_path)

    class FakeClient(LLMClient):
        def chat(self, **kw):
            return LLMResponse(content="{}")

    loop, _ = build_system(cfg, client=FakeClient(), search_client=object())
    assert isinstance(loop.client, FakeClient)  # 注入的 client 被直接采用
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/test_system.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'agents.system'`

- [ ] **Step 3: 写最小实现 `agents/system.py`**

```python
"""系统组装工厂:从 config 构造四个 agent 并接好 Orchestrator 的派发工具。
config wiring:生产链路一律从 config 注入(client 硬编码默认仅作 fallback)→ 零侵入,闭环 M1 待办。"""
from llm.base import LLMClient
from llm.deepseek_client import DeepSeekClient
from tools.web_search import BochaSearchClient
from core.config import Config, env

from .researcher import make_researcher
from .verifier import make_verifier
from .writer import make_writer
from .orchestrator import make_orchestrator


def build_system(config: Config, *, client: LLMClient | None = None,
                 search_client=None):
    """组装整个系统,返回 (orchestrator_loop, get_report)。

    client / search_client 可注入(测试用 fake);为 None 时按 config + env 构造真实 client。
    """
    gen = config["models"]["generator"]
    client = client or DeepSeekClient(
        base_url=gen["base_url"], model=gen["name"],
        temperature=gen["temperature"], max_tokens=gen["max_tokens"],
    )

    ws = config["tools"]["web_search"]
    search_client = search_client or BochaSearchClient(
        api_key=env("BOCHA_API_KEY"), endpoint=ws["endpoint"], count=ws["count"],
    )

    max_chars = config["tools"]["web_read"]["max_chars"]
    max_steps = config["guards"]["agent_max_steps"]
    research_max_rounds = config["guards"]["research_max_rounds"]

    def _run_researcher(sub_question):
        return make_researcher(client=client, search_client=search_client,
                               max_chars=max_chars).run(sub_question)

    def _run_verifier(findings_json):
        return make_verifier(client=client, search_client=search_client,
                             max_chars=max_chars).run(findings_json)

    def _run_writer(user_message):
        return make_writer(client=client).run(user_message)

    return make_orchestrator(
        client=client,
        run_researcher=_run_researcher, run_verifier=_run_verifier, run_writer=_run_writer,
        max_steps=max_steps, research_max_rounds=research_max_rounds,
    )
```

- [ ] **Step 4: 跑测试验证通过**

Run: `pytest tests/test_system.py -v`
Expected: PASS(2 passed)

- [ ] **Step 5: Commit**

```bash
git add agents/system.py tests/test_system.py
git commit -m "feat(agents): add build_system factory with config wiring"
```

---

### Task 10: `main.py` CLI + README 冒烟说明 + 全量验证(M2 收尾)

`main.py` 极薄:解析 argv → `build_system` → `orchestrator.run` → 从 holder 取 Report → 渲染 markdown 打印。组装逻辑全在 `system.py`,故 main.py 仅 `render_markdown` 与 argv 处理需测。

**Files:**
- Create: `main.py`
- Create: `tests/test_main.py`
- Modify: `README.md`

- [ ] **Step 1: 写失败测试 `tests/test_main.py`**

```python
from main import render_markdown, main
from core.schemas import Report, ReportSection


def test_render_markdown_sections_and_sources():
    rep = Report(
        sections=[ReportSection(heading="结论", content="某结论。", citations=["f1", "f2"])],
        sources=["https://a", "https://b"],
    )
    md = render_markdown(rep)
    assert "## 结论" in md
    assert "某结论。" in md
    assert "f1" in md and "f2" in md
    assert "## 来源" in md
    assert "https://a" in md and "https://b" in md


def test_main_no_args_prints_usage(capsys):
    assert main([]) == 1
    assert "用法" in capsys.readouterr().out
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/test_main.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'main'`

- [ ] **Step 3: 写最小实现 `main.py`**

```python
"""CLI 入口:python main.py "研究问题"。
组装系统 → 跑 Orchestrator → 从 holder 取 Report(机制级确定性)→ 渲染 markdown 打印。"""
import sys

from core.config import load_config
from agents.system import build_system


def render_markdown(report) -> str:
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
            lines.append(f"{i}. {s}")
    return "\n".join(lines)


def main(argv=None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if not argv:
        print('用法: python main.py "研究问题"')
        return 1
    question = " ".join(argv)
    cfg = load_config()
    loop, get_report = build_system(cfg)
    loop.run(question)
    report = get_report()
    if report is None:
        print("未能生成报告(Orchestrator 未产出 write_report)。", file=sys.stderr)
        return 2
    print(render_markdown(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 跑测试验证通过**

Run: `pytest tests/test_main.py -v`
Expected: PASS(2 passed)

- [ ] **Step 5: 更新 `README.md`(里程碑状态 + 手动冒烟说明)**

把 README 的"当前里程碑"段(第 7–9 行):

```
## 当前里程碑:M1 地基 ✅

已完成:harness 原语(AgentLoop / ToolRegistry / robustness)、LLM 客户端(DeepSeek 生成 + GLM-5.2 裁判)、博查搜索、trafilatura 提取、Pydantic 数据结构。全部单测覆盖,不烧 API。
```

替换为:

```
## 当前里程碑:M2 四 Agent 编排层 ✅

- **M1 地基:** harness 原语(AgentLoop / ToolRegistry / robustness)、LLM 客户端(DeepSeek 生成 + GLM-5.2 裁判)、博查搜索、trafilatura 提取、Pydantic 数据结构。
- **M2 编排:** 四个 agent(orchestrator / researcher / verifier / writer)+ 子 agent 并行派发(ThreadPoolExecutor)+ 上下文隔离(子 agent 输出 JSON → 派发层 parse → 精简字符串回填)+ Report holder 硬提取(最终报告不经过 Orchestrator 转述)+ `main.py` 真实链路。

全部单测覆盖、不烧 API(全 mock);真实链路靠手动冒烟(见下)。
```

再把"后续里程碑"段(第 20–24 行):

```
## 后续里程碑

- **M2** 四个 agent(orchestrator / researcher / verifier / writer)+ 子 agent 派发 + `main.py` 真实链路
- **M3** 评估体系(9 维指标 + 基准集 + 基线对比 + 上线门槛)
- **M4** Streamlit 实时编排可视化
```

替换为:

```
## 手动真实冒烟(M2 收尾,会产生少量 API 费用)

填好 `.env` 的 `DEEPSEEK_API_KEY` / `BOCHA_API_KEY` 后,真跑一次验证真实链路:

```
python main.py "2026 年大模型推理优化的主流技术路线有哪些?"
```

## 后续里程碑

- **M3** 评估体系(9 维指标 + 基准集 + 基线对比 + 上线门槛)
- **M4** Streamlit 实时编排可视化
```

- [ ] **Step 6: 跑全量测试(里程碑验收)**

Run: `pytest -q`
Expected: 全绿,约 90 passed(M1 的 55 + M2 新增 ~35),**无网络调用**(全 mock):
- test_prompts:5 · test_webtools:4 · test_researcher:2 · test_verifier:2 · test_writer:3
- test_dispatch:7 · test_orchestrator_tools:6 · test_orchestrator_e2e:2
- test_system:2 · test_main:2

- [ ] **Step 7: Commit**

```bash
git add main.py tests/test_main.py README.md
git commit -m "feat: add main.py CLI + README smoke (M2 complete)"
```

---

## M2 完成定义(Definition of Done)

- [ ] 上述 10 个 Task 全部完成,每个都有 commit。
- [ ] `pytest -q` 全绿(约 90 passed),**无网络调用**(全 mock)。
- [ ] `test_system.py` 证明 M1 config-wiring 待办闭环(config 值注入 client 构造)。
- [ ] `test_orchestrator_e2e.py` 证明 dispatch→verify→write 整条链路 + Report 从 holder 取 + 上下文隔离(回填结构化 JSON)。
- [ ] 手动 `python main.py "问题"` 真跑出一份带引用报告(收尾时执行一次)。
- [ ] 可进入 M3:评估体系。

---

## Self-Review(plan 作者自检记录)

**1. Spec 覆盖(本里程碑范围内的 spec 章节):**
- spec §2.1 模块结构 → Task 1(prompts)/2(_webtools)/3(researcher)/4(verifier)/5(writer)/7(orchestrator)/9(system)。✅
- spec §2.3 派发机制(批量+并发+容错)→ Task 6 `dispatch_research`(并发、合并、单点容错,均有测试)。✅
- spec §2.4 上下文隔离(JSON 清洗/parse/精简回填)→ Task 6 `clean_json`/`parse_*`(派发层 parse,回填 `model_dump` 精简字符串)+ Task 8 断言 tool 消息含结构化 JSON。✅
- spec §2.5 `research_max_rounds` 闭包护栏 → Task 7 `_dispatch_research` 闭包计数 + `test_dispatch_research_round_guard`。✅
- spec §2.6 Report holder 硬提取 → Task 7 `holder` + `get_report` + `test_write_report_stores_in_holder` / Task 8 `test_finishes_without_write_report_yields_none`。✅
- spec §3 职责契约(工具集/输出 JSON)→ Task 1 prompts(契约)+ Task 3/4/5 工具绑定 + Writer 无工具断言。✅
- spec §5 config wiring(零侵入)→ Task 9 `build_system`(生产注入 config,client 默认留 fallback,monkeypatch 测试验收)。✅
- spec §6 测试矩阵 → 各 test 文件逐一对应(注:spec 表里的 `test_orchestrator_dispatch.py` 拆成了 `test_orchestrator_tools.py`+`test_orchestrator_e2e.py`,职责更清晰,覆盖不缩水)。✅
- spec §8 DoD → 上方 Definition of Done。✅

**2. Placeholder 扫描:** 无 TBD/TODO/"参照 Task N";每个代码步骤含完整代码;main.py / system.py / 各 agent 均给全实现;README 改动给出精确 old→new 块。

**3. 类型/命名一致性核对:**
- `make_researcher`/`make_verifier`/`make_writer`/`make_orchestrator` 签名跨 Task 一致(均 `client=` keyword;researcher/verifier 另需 `search_client=`,writer 不需要)。
- `dispatch.dispatch_research(sub_questions, run_one, *, max_workers=None)` 在 Task 6 定义、Task 7 `_dispatch_research` 调用,参数名一致。
- `parse_findings`/`parse_results`/`parse_report`/`clean_json` 定义(Task 6)与调用(Task 7)一致。
- `make_orchestrator` 返回 `(loop, get_report)` 在 Task 7 定义、Task 9 `build_system` 透传、Task 10 `main` 消费,一致。
- runner 契约 `run_researcher(sq)->AgentResult`、`run_verifier(findings_json)->AgentResult`、`run_writer(user_message)->AgentResult`:Task 7 docstring、Task 9 绑定、Task 8 fake,均以 `.content` 取 JSON,一致。
- `AgentResult.content`(M1 已定义)是各 runner 返回对象被读取的字段,与 M1 原语一致。

**4. 一致性补充:** `build_system` 透传 `make_orchestrator` 的返回值(不拆包),故 `main.py` 用 `loop, get_report = build_system(cfg)` —— Task 9 / Task 10 一致。
