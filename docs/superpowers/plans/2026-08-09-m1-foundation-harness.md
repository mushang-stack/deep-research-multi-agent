# M1 地基层(harness + LLM client + 工具)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 从零搭好项目地基 —— 一个可用 mock 客户端跑通、完全单测覆盖的 agent harness(AgentLoop + ToolRegistry + 重试/兜底/护栏)以及 DeepSeek/GLM 生成客户端、博查搜索、trafilatura 网页提取工具,为 M2(四个 agent)提供可调用的原语。

**Architecture:** 自研 harness,核心原语是 `AgentLoop`(调模型→有 tool_call 则经 `ToolRegistry` 执行并回填、无则返回)。LLM 客户端走 OpenAI 兼容协议(DeepSeek 生成 + GLM 裁判共享一套 `OpenAICompatClient`)。子 agent 产出以 Pydantic 结构化对象回传(上下文隔离)。`core/robust.py` 提供重试/坏 tool-call 兜底解析/escalation。本里程碑全部测试用 mock,**不烧 API**;真实链路集成留到 M2。

**Tech Stack:** Python 3.11+、openai SDK(OpenAI 兼容)、httpx、pydantic v2、trafilatura、pyyaml、pytest + pytest-mock。

**对应 spec:** [docs/superpowers/specs/2026-08-08-deep-research-multi-agent-design.md](../../specs/2026-08-08-deep-research-multi-agent-design.md) §3.2 数据结构、§3.4 外部服务、§4 Harness、§7 技术栈、§8 项目结构、§9 错误处理与测试。

---

## 里程碑边界(本 plan 范围)

**做:** 项目脚手架、`core/`(schemas/config/robust/tool_registry/agent_loop)、`llm/`(base/openai_compat/deepseek/glm)、`tools/`(web_search/web_read)、pytest 全绿 + 一个用 FakeClient 跑通的端到端 smoke 测试。

**不做(留给后续里程碑):** 四个 agent 的 system prompt 与工具绑定(M2)、子 agent 派发(M2)、`main.py` 真实链路(M2)、评估体系(M3)、Streamlit UI(M4)。

**验收:** `pytest -q` 全绿;`tests/test_smoke_agent_loop.py` 用 FakeClient + echo 工具证明「模型发 tool_call → registry 执行 → 结果回填 → 循环 → 最终返回」整条链路成立。

---

## File Structure

仓库根 = 项目根(spec §8 里的 `deepseek-research-agent/` 即本仓库 `e:\record\Agent`)。新增/改动文件如下:

| 文件 | 职责 | 本里程碑创建 |
|---|---|---|
| `pyproject.toml` | pytest 配置(`pythonpath=.` 让 `core/` 等顶层包可导入)+ 工具配置 | Task 1 |
| `requirements.txt` | 依赖锁定 | Task 1 |
| `config.yaml` | 模型名/阈值/护栏参数(单一配置源) | Task 1 |
| `.env.example` | API key 模板(真实 `.env` 已被 .gitignore 忽略) | Task 1 |
| `README.md` | 项目概览 + setup + 跑测试 | Task 12 |
| `core/__init__.py` 等 | 各包初始化(空文件) | Task 1 |
| `core/config.py` | 读 `config.yaml` + 取环境变量 | Task 2 |
| `core/schemas.py` | Pydantic 数据结构(SubQuestion/ResearchPlan/Finding/VerificationResult/Report*/SearchResult) | Task 3 |
| `core/robust.py` | 重试 / 坏 tool-call 兜底解析 / TransientError / Escalation | Task 5 |
| `core/tool_registry.py` | 工具注册 + 分发 | Task 10 |
| `core/agent_loop.py` | 核心原语 AgentLoop | Task 11 |
| `llm/base.py` | LLMClient 抽象 + ToolCall/LLMResponse 数据类 | Task 4 |
| `llm/openai_compat.py` | OpenAI 兼容客户端(翻译异常+重试+映射),DeepSeek/GLM 共用 | Task 6 |
| `llm/deepseek_client.py` | DeepSeek 生成客户端(薄子类) | Task 7 |
| `llm/glm_client.py` | GLM 裁判客户端(薄子类,默认 GLM-5.2) | Task 7 |
| `tools/web_search.py` | 博查 Bocha 搜索 → List[SearchResult] | Task 8 |
| `tools/web_read.py` | trafilatura 提取网页正文 | Task 9 |
| `tests/test_*.py` | 单测 | 各 Task |

> **对 spec §8 的偏离(已记录):** 新增 `core/config.py`(读 config.yaml,否则每个 client 各读一遍,不 DRY)与 `llm/openai_compat.py`(DeepSeek/GLM 共享 OpenAI 兼容逻辑,避免两份重复)。spec 的文件清单是起点,这两处是合理的 DRY 抽取。

---

## 约定

- **TDD:** 每个 Task 先写失败测试 → 跑红 → 写最小实现 → 跑绿 → 提交。
- **commit 粒度:** 每个 Task 一次 commit,信息用 conventional commits(`feat:`/`chore:`/`test:`)。
- **不烧 API:** 本里程碑所有测试用 mock/fake,绝不真实调用 DeepSeek/GLM/博查。
- **Python 版本:** 3.11+(用 `str | None` 等新语法)。创建虚拟环境:`python -m venv .venv && .venv/Scripts/activate`(Windows Git Bash)。

---

### Task 1: 项目脚手架

**Files:**
- Create: `pyproject.toml`
- Create: `requirements.txt`
- Create: `config.yaml`
- Create: `.env.example`
- Create: `core/__init__.py`, `llm/__init__.py`, `tools/__init__.py`, `agents/__init__.py`, `eval/__init__.py`, `ui/__init__.py`, `tests/__init__.py`(均空文件)
- Create: `tests/conftest.py`

`.gitignore` 已包含 Python/secrets/eval 产物,**无需改动**。

- [ ] **Step 1: 创建 `requirements.txt`**

```
openai>=1.30
httpx>=0.27
pydantic>=2.6
trafilatura>=1.12
pyyaml>=6.0
streamlit>=1.30
python-dotenv>=1.0
pytest>=8.0
pytest-mock>=3.12
```

- [ ] **Step 2: 创建 `pyproject.toml`**

```toml
[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
addopts = "-q"
```

> `pythonpath=["."]` 让仓库根进入 `sys.path`,从而 `from core.config import ...` 等顶层包导入成立(无 src layout)。

- [ ] **Step 3: 创建 `config.yaml`**

```yaml
# 单一配置源:模型名、工具参数、护栏阈值。运行时由 core/config.py 读取。
models:
  generator:                       # 生成模型
    name: deepseek-chat            # DeepSeek 函数调用模型(spec 记 V4;运行时用其最新 function-calling 模型,可按需替换)
    base_url: https://api.deepseek.com
    temperature: 0.3
    max_tokens: 4096
  judge:                           # 裁判模型(仅评估环节用,规避自评偏差)
    name: glm-5.2                  # 智谱最新 GLM-5.2
    base_url: https://open.bigmodel.cn/api/paas/v4   # 智谱 OpenAI 兼容端点(API 版本路径,与模型版本无关)
    temperature: 0.0
    max_tokens: 2048

tools:
  web_search:                      # 博查 Bocha
    endpoint: https://api.bochaai.com/v1/web-search
    count: 8
  web_read:                        # trafilatura 本地提取
    max_chars: 8000                # 正文截断,防爆上下文

guards:
  agent_max_steps: 12              # 单 agent 最大循环步数(防无限循环/成本失控)
  research_max_rounds: 3           # Orchestrator 最大研究轮次(M2 用)
  request_max_retries: 3           # 瞬时错误重试次数
  request_backoff_base: 1.5        # 指数退避基数(秒)

thresholds:
  grounding_min: 0.85              # 上线门槛:Grounding ≥ 85%(M3 用)
```

- [ ] **Step 4: 创建 `.env.example`**

```
# 复制为 .env 并填入真实 key(.env 已被 .gitignore 忽略,绝不提交)
DEEPSEEK_API_KEY=
GLM_API_KEY=
BOCHA_API_KEY=
```

- [ ] **Step 5: 创建包 `__init__.py`(空文件)**

对以下每个路径建空文件:`core/__init__.py`、`llm/__init__.py`、`tools/__init__.py`、`agents/__init__.py`、`eval/__init__.py`、`ui/__init__.py`、`tests/__init__.py`。

- [ ] **Step 6: 创建 `tests/conftest.py`(占位,后续 Task 加 fixture)**

```python
"""共享 pytest fixtures。各 Task 按需在此追加。"""
```

- [ ] **Step 7: 装依赖并验证 pytest 可运行**

Run:
```bash
python -m venv .venv && source .venv/Scripts/activate
pip install -r requirements.txt
pytest
```
Expected: `no tests ran` 退出码 5(pytest 未发现用例,但能正常启动即说明脚手架 OK)。

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml requirements.txt config.yaml .env.example core/__init__.py llm/__init__.py tools/__init__.py agents/__init__.py eval/__init__.py ui/__init__.py tests/__init__.py tests/conftest.py
git commit -m "chore: project scaffolding (config, deps, package layout)"
```

---

### Task 2: `core/config.py` — 配置与环境变量

**Files:**
- Create: `core/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: 写失败测试 `tests/test_config.py`**

```python
import pytest
from core.config import Config, load_config, env


def test_load_config_reads_yaml(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "models:\n  generator:\n    name: deepseek-chat\n    temperature: 0.3\n",
        encoding="utf-8",
    )
    cfg = load_config(cfg_file)
    assert cfg["models"]["generator"]["name"] == "deepseek-chat"
    assert cfg["models"]["generator"]["temperature"] == 0.3


def test_config_get_returns_default():
    cfg = Config({"a": 1})
    assert cfg.get("a") == 1
    assert cfg.get("missing", "fallback") == "fallback"


def test_env_raises_when_missing(monkeypatch):
    monkeypatch.delenv("FAKE_KEY", raising=False)
    with pytest.raises(RuntimeError, match="FAKE_KEY"):
        env("FAKE_KEY")


def test_env_returns_value(monkeypatch):
    monkeypatch.setenv("FAKE_KEY", "sk-abc")
    assert env("FAKE_KEY") == "sk-abc"
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/test_config.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'core.config'`

- [ ] **Step 3: 写最小实现 `core/config.py`**

```python
"""配置加载:读 config.yaml + 取环境变量。单一配置入口。"""
import os
from pathlib import Path

import yaml


class Config:
    """对 config.yaml dict 的薄封装,支持 [] 与 get。"""

    def __init__(self, data: dict):
        self._d = data

    def __getitem__(self, key):
        return self._d[key]

    def get(self, key, default=None):
        return self._d.get(key, default)


def load_config(path=None) -> Config:
    """加载 config.yaml。不传 path 时默认读仓库根的 config.yaml。"""
    if path is None:
        path = Path(__file__).resolve().parents[1] / "config.yaml"
    with open(path, "r", encoding="utf-8") as f:
        return Config(yaml.safe_load(f))


def env(name: str) -> str:
    """取必填环境变量;缺失则显式报错(见 .env.example)。"""
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"缺少环境变量 {name}(见 .env.example)")
    return value
```

- [ ] **Step 4: 跑测试验证通过**

Run: `pytest tests/test_config.py -v`
Expected: PASS(4 passed)

- [ ] **Step 5: Commit**

```bash
git add core/config.py tests/test_config.py
git commit -m "feat(core): add config loader (config.yaml + env)"
```

---

### Task 3: `core/schemas.py` — Pydantic 数据结构

对应 spec §3.2。这些结构贯穿全系统(M2/M3 都用),是上下文隔离的载体,本 Task 一次定稿。

**Files:**
- Create: `core/schemas.py`
- Test: `tests/test_schemas.py`

- [ ] **Step 1: 写失败测试 `tests/test_schemas.py`**

```python
import pytest
from pydantic import ValidationError

from core.schemas import (
    SubQuestion, ResearchPlan, Finding, VerificationResult,
    ReportSection, Report, SearchResult,
)


def test_subquestion_defaults():
    sq = SubQuestion(id="q1", text="什么是 X?")
    assert sq.angle == ""


def test_research_plan():
    plan = ResearchPlan(question="深度研究 A", sub_questions=[
        SubQuestion(id="q1", text="A 的定义", angle="概念"),
    ])
    assert plan.sub_questions[0].angle == "概念"
    assert len(plan.sub_questions) == 1


def test_finding_required_fields():
    f = Finding(id="f1", claim="某结论", source_url="https://example.com/a")
    assert f.source_title == "" and f.excerpt == "" and f.confidence == 0.0


def test_finding_missing_required_raises():
    with pytest.raises(ValidationError):
        Finding(id="f1", claim="x")  # 缺 source_url


def test_verification_result_verdict():
    v = VerificationResult(finding_id="f1", verdict="supported", reason="来源匹配")
    assert v.suggested_query is None


def test_report_with_citations():
    sec = ReportSection(heading="结论", content="...", citations=["f1", "f2"])
    rep = Report(sections=[sec], sources=["https://example.com/a"])
    assert rep.sections[0].citations == ["f1", "f2"]


def test_search_result():
    r = SearchResult(title="t", url="u")
    assert r.snippet == ""
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/test_schemas.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'core.schemas'`

- [ ] **Step 3: 写最小实现 `core/schemas.py`**

```python
"""Pydantic 数据结构(spec §3.2)。子 agent 以这些结构化对象回传,做上下文隔离。"""
from typing import Optional

from pydantic import BaseModel, Field


class SubQuestion(BaseModel):
    id: str
    text: str
    angle: str = ""


class ResearchPlan(BaseModel):
    question: str
    sub_questions: list[SubQuestion]


class Finding(BaseModel):
    id: str
    claim: str
    source_url: str
    source_title: str = ""
    excerpt: str = ""
    confidence: float = 0.0


class VerificationResult(BaseModel):
    finding_id: str
    verdict: str  # supported / unsupported / weak
    reason: str = ""
    suggested_query: Optional[str] = None


class ReportSection(BaseModel):
    heading: str
    content: str
    citations: list[str] = Field(default_factory=list)  # finding_id 列表


class Report(BaseModel):
    sections: list[ReportSection]
    sources: list[str] = Field(default_factory=list)


class SearchResult(BaseModel):
    """web_search 的单条返回。非 Finding —— 是否采信由 Researcher 决定(M2)。"""
    title: str
    url: str
    snippet: str = ""
```

- [ ] **Step 4: 跑测试验证通过**

Run: `pytest tests/test_schemas.py -v`
Expected: PASS(7 passed)

- [ ] **Step 5: Commit**

```bash
git add core/schemas.py tests/test_schemas.py
git commit -m "feat(core): add Pydantic data structures (spec §3.2)"
```

---

### Task 4: `llm/base.py` — LLM 客户端抽象接口

定义 DeepSeek/GLM 共同遵守的接口与返回数据类。AgentLoop 只依赖此抽象,故可用 FakeClient 单测。

**Files:**
- Create: `llm/base.py`
- Test: `tests/test_llm_base.py`

- [ ] **Step 1: 写失败测试 `tests/test_llm_base.py`**

```python
from llm.base import LLMClient, LLMResponse, ToolCall


def test_toolcall_and_response_construction():
    tc = ToolCall(id="call_1", name="web_search", arguments='{"query":"x"}')
    assert tc.name == "web_search"

    resp = LLMResponse(content="hello")
    assert resp.content == "hello"
    assert resp.tool_calls == []
    assert resp.usage == {}


def test_response_with_tool_calls():
    resp = LLMResponse(content="", tool_calls=[
        ToolCall(id="c1", name="web_search", arguments='{"query":"a"}'),
    ])
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].arguments == '{"query":"a"}'


def test_llmclient_is_abstract():
    import pytest
    # ABC 有未实现的 abstractmethod → 实例化即抛 TypeError(在 ABCMeta.__call__ 阶段)
    with pytest.raises(TypeError):
        LLMClient()
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/test_llm_base.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'llm.base'`

- [ ] **Step 3: 写最小实现 `llm/base.py`**

```python
"""LLM 客户端统一接口。AgentLoop 仅依赖此抽象,故可用 FakeClient 单测。"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # 模型返回的原始 JSON 字符串(可能非法,由 robust.safe_parse_arguments 兜底)


@dataclass
class LLMResponse:
    content: str  # 助手文本(有 tool_calls 时可能为空)
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict = field(default_factory=dict)  # {prompt_tokens, completion_tokens}
    raw: Any = None  # 保留原始返回,便于调试


class LLMClient(ABC):
    """生成模型客户端抽象。DeepSeek/GLM 各实现一份 chat。"""

    @abstractmethod
    def chat(
        self,
        *,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        ...
```

- [ ] **Step 4: 跑测试验证通过**

Run: `pytest tests/test_llm_base.py -v`
Expected: PASS(3 passed)
> 说明:`test_llmclient_is_abstract` 验证未实现 `chat` 的 ABC 无法正常使用 —— ABC 直接实例化在调用抽象方法时抛 TypeError,测试用 `pytest.raises(Exception)` 覆盖。

- [ ] **Step 5: Commit**

```bash
git add llm/base.py tests/test_llm_base.py
git commit -m "feat(llm): add LLMClient interface + ToolCall/LLMResponse"
```

---

### Task 5: `core/robust.py` — 重试 / 兜底解析 / Escalation

对应 spec §4 健壮性层与 §9 护栏。本 Task 实现:瞬时错误重试(指数退避)、坏 tool-call 参数兜底解析、`TransientError`/`Escalation` 异常、瞬时状态码分类。`Escalation` 用于「自主失败显式打 flag,而非静默崩」。

**Files:**
- Create: `core/robust.py`
- Test: `tests/test_robust.py`

- [ ] **Step 1: 写失败测试 `tests/test_robust.py`**

```python
import json
import pytest

from core.robust import (
    TransientError, Escalation, retry_with_backoff, safe_parse_arguments,
)


def test_retry_succeeds_first_try():
    calls = []
    def fn():
        calls.append(1)
        return "ok"
    assert retry_with_backoff(fn, retries=3, base=1.0, sleep=lambda s: None) == "ok"
    assert len(calls) == 1


def test_retry_recovers_after_transient():
    calls = []
    def fn():
        calls.append(1)
        if len(calls) < 3:
            raise TransientError("503")
        return "ok"
    sleeps = []
    out = retry_with_backoff(fn, retries=5, base=2.0, sleep=sleeps.append)
    assert out == "ok"
    assert len(calls) == 3
    # 指数退避:base^0=1, base^1=2
    assert sleeps == [1.0, 2.0]


def test_retry_exhausts_then_reraises():
    def fn():
        raise TransientError("always")
    with pytest.raises(TransientError):
        retry_with_backoff(fn, retries=3, base=1.0, sleep=lambda s: None)


def test_retry_does_not_swallow_non_transient():
    def fn():
        raise ValueError("boom")
    with pytest.raises(ValueError):
        retry_with_backoff(fn, retries=3, base=1.0, sleep=lambda s: None,
                           exceptions=(TransientError,))


def test_safe_parse_valid_json():
    assert safe_parse_arguments('{"query": "x"}') == {"query": "x"}


def test_safe_parse_malformed_returns_empty():
    # V4 复杂场景结构化输出偶发非法 JSON;兜底为空 dict 而非崩溃
    assert safe_parse_arguments("{not json") == {}


def test_safe_parse_none_returns_empty():
    assert safe_parse_arguments(None) == {}


def test_escalation_carries_context():
    e = Escalation("hit max_steps", context={"agent": "researcher"})
    assert e.reason == "hit max_steps"
    assert e.context["agent"] == "researcher"
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/test_robust.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'core.robust'`

- [ ] **Step 3: 写最小实现 `core/robust.py`**

```python
"""健壮性层(spec §4 / §9):重试、坏 tool-call 兜底解析、Escalation。
注:本模块不 import openai,保持与 SDK 解耦 —— 瞬时错误由各 client 翻译成 TransientError。"""
import json
import time
from typing import Callable


class TransientError(Exception):
    """瞬时错误(503/超时/连接),可重试。"""


class Escalation(Exception):
    """自主失败:显式上抛而非静默崩。携带 reason + context。"""

    def __init__(self, reason: str, context: dict | None = None):
        super().__init__(reason)
        self.reason = reason
        self.context = context or {}


def retry_with_backoff(
    fn: Callable,
    *,
    retries: int = 3,
    base: float = 1.5,
    sleep: Callable[[float], None] = time.sleep,
    exceptions: tuple = (TransientError,),
):
    """对瞬时错误做指数退避重试;非指定异常直接上抛。"""
    last_exc = None
    for attempt in range(retries):
        try:
            return fn()
        except exceptions as e:
            last_exc = e
            if attempt == retries - 1:
                raise
            sleep(base ** attempt)
    raise last_exc  # 逻辑上不可达


def safe_parse_arguments(raw) -> dict:
    """兜底解析模型返回的 tool-call 参数。
    DeepSeek V4 复杂场景结构化输出成功率约 60%(spec §10),坏参数兜底为空 dict。"""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}
```

- [ ] **Step 4: 跑测试验证通过**

Run: `pytest tests/test_robust.py -v`
Expected: PASS(8 passed)

- [ ] **Step 5: Commit**

```bash
git add core/robust.py tests/test_robust.py
git commit -m "feat(core): add robustness layer (retry, fallback parse, escalation)"
```

---

### Task 6: `llm/openai_compat.py` — OpenAI 兼容客户端(DeepSeek/GLM 共用)

封装「调 OpenAI 兼容 `chat.completions.create` → 把 SDK 异常翻译成 `TransientError` → 重试 → 映射成 `LLMResponse`」。DeepSeek 与 GLM(智谱)都是 OpenAI 兼容协议,共用此基类,各自只设默认值(Task 7)。

**Files:**
- Create: `llm/openai_compat.py`
- Test: `tests/test_openai_compat.py`

- [ ] **Step 1: 写失败测试 `tests/test_openai_compat.py`**

```python
import httpx
import openai
import pytest

from llm.openai_compat import OpenAICompatClient, _TRANSIENT_STATUS
from core.robust import TransientError


# ---- fake openai 对象(模拟 SDK 返回结构,不触网)----
class _FakeFunction:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, id, name, arguments):
        self.id = id
        self.function = _FakeFunction(name, arguments)


class _FakeMessage:
    def __init__(self, content, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _FakeUsage:
    def __init__(self, p, c):
        self.prompt_tokens = p
        self.completion_tokens = c


class _FakeResponse:
    def __init__(self, message, usage=None):
        self.choices = [type("C", (), {"message": message})()]
        self.usage = usage


class _FakeCompletions:
    def __init__(self, side_effects):
        self._side_effects = list(side_effects)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        eff = self._side_effects.pop(0)
        if isinstance(eff, Exception):
            raise eff
        return eff


def _client_with(side_effects):
    completions = _FakeCompletions(side_effects)
    fake = type("O", (), {"chat": type("CH", (), {"completions": completions})()})()
    return OpenAICompatClient(api_key="k", base_url="https://x", model="m",
                              retries=3, backoff=1.5,
                              sleep=lambda s: None, client=fake), completions


def test_transient_status_set():
    assert 503 in _TRANSIENT_STATUS and 429 in _TRANSIENT_STATUS
    assert 400 not in _TRANSIENT_STATUS and 401 not in _TRANSIENT_STATUS


def test_chat_maps_text_response():
    resp = _FakeResponse(_FakeMessage(content="hello"))
    c, completions = _client_with([resp])
    out = c.chat(messages=[{"role": "user", "content": "hi"}])
    assert out.content == "hello"
    assert out.tool_calls == []
    assert out.usage == {}  # usage=None → 空 dict
    assert completions.calls[0]["model"] == "m"


def test_chat_maps_tool_calls_and_usage():
    msg = _FakeMessage(content="", tool_calls=[
        _FakeToolCall(id="c1", name="web_search", arguments='{"query":"x"}'),
    ])
    resp = _FakeResponse(msg, usage=_FakeUsage(10, 5))
    c, _ = _client_with([resp])
    out = c.chat(messages=[{"role": "user", "content": "q"}], tools=[{"type": "function"}])
    assert len(out.tool_calls) == 1
    assert out.tool_calls[0].name == "web_search"
    assert out.tool_calls[0].arguments == '{"query":"x"}'
    assert out.usage == {"prompt_tokens": 10, "completion_tokens": 5}


def test_chat_retries_on_timeout_then_succeeds():
    timeout = openai.APITimeoutError(request=httpx.Request("POST", "https://x"))
    ok = _FakeResponse(_FakeMessage(content="recovered"))
    c, completions = _client_with([timeout, ok])
    out = c.chat(messages=[{"role": "user", "content": "q"}])
    assert out.content == "recovered"
    assert len(completions.calls) == 2  # 第一次超时→重试→第二次成功


def test_chat_retries_exhaust_then_raises_transient():
    timeout = openai.APITimeoutError(request=httpx.Request("POST", "https://x"))
    c, _ = _client_with([timeout, timeout, timeout])
    with pytest.raises(TransientError):
        c.chat(messages=[{"role": "user", "content": "q"}])


def test_chat_does_not_retry_on_non_transient():
    req = httpx.Request("POST", "https://x")
    bad = openai.BadRequestError(
        "bad", response=httpx.Response(400, request=req), body=None)
    c, completions = _client_with([bad])
    with pytest.raises(openai.BadRequestError):
        c.chat(messages=[{"role": "user", "content": "q"}])
    assert len(completions.calls) == 1  # 非瞬时错误不重试
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/test_openai_compat.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'llm.openai_compat'`

- [ ] **Step 3: 写最小实现 `llm/openai_compat.py`**

```python
"""OpenAI 兼容客户端:DeepSeek/GLM 共用。负责 SDK 异常翻译→TransientError、重试、响应映射。"""
import time

import openai
from openai import OpenAI

from .base import LLMClient, LLMResponse, ToolCall
from core.robust import retry_with_backoff, TransientError

# 瞬时状态码:重试;其余(400/401/403/404…)直接上抛
_TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})


class OpenAICompatClient(LLMClient):
    def __init__(self, *, api_key, base_url, model, temperature=0.3, max_tokens=4096,
                 retries=3, backoff=1.5, sleep=time.sleep, client=None):
        # client 可注入:测试传 fake,生产时由各子类(DeepSeek/GLM)用真实 OpenAI
        self._client = client or OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._retries = retries
        self._backoff = backoff
        self._sleep = sleep

    def chat(self, *, messages, tools=None, model=None, temperature=None, max_tokens=None):
        kwargs = {
            "model": model or self.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.temperature,
            "max_tokens": max_tokens or self.max_tokens,
        }
        if tools:
            kwargs["tools"] = tools

        def _call():
            try:
                return self._client.chat.completions.create(**kwargs)
            except openai.APITimeoutError as e:
                raise TransientError("timeout") from e
            except openai.APIConnectionError as e:
                raise TransientError("connection") from e
            except openai.APIStatusError as e:
                if e.status_code in _TRANSIENT_STATUS:
                    raise TransientError(f"status {e.status_code}") from e
                raise  # 非瞬时(400/401/403/404…)直接上抛,不重试

        resp = retry_with_backoff(_call, retries=self._retries,
                                  base=self._backoff, sleep=self._sleep)
        msg = resp.choices[0].message
        tool_calls = [
            ToolCall(id=tc.id, name=tc.function.name, arguments=tc.function.arguments or "{}")
            for tc in (msg.tool_calls or [])
        ]
        usage = {}
        if getattr(resp, "usage", None):
            usage = {
                "prompt_tokens": resp.usage.prompt_tokens,
                "completion_tokens": resp.usage.completion_tokens,
            }
        return LLMResponse(content=msg.content or "", tool_calls=tool_calls, usage=usage, raw=resp)
```

- [ ] **Step 4: 跑测试验证通过**

Run: `pytest tests/test_openai_compat.py -v`
Expected: PASS(6 passed)

- [ ] **Step 5: Commit**

```bash
git add llm/openai_compat.py tests/test_openai_compat.py
git commit -m "feat(llm): add OpenAI-compatible client (translation, retry, mapping)"
```

---

### Task 7: `llm/deepseek_client.py` + `llm/glm_client.py` — 薄子类

两个生成/裁判客户端,各自只设默认值(base_url/model/temperature/max_tokens),其余继承 `OpenAICompatClient`。**DeepSeek 生成、GLM-5.2 裁判**(spec §3.4)。

**Files:**
- Create: `llm/deepseek_client.py`
- Create: `llm/glm_client.py`
- Test: `tests/test_clients_defaults.py`

- [ ] **Step 1: 写失败测试 `tests/test_clients_defaults.py`**

```python
import pytest

from llm.deepseek_client import DeepSeekClient
from llm.glm_client import GLMClient


def test_deepseek_defaults(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds")
    c = DeepSeekClient()
    assert c.model == "deepseek-chat"
    assert c.temperature == 0.3
    assert c.max_tokens == 4096


def test_deepseek_requires_env(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        DeepSeekClient()


def test_glm_defaults(monkeypatch):
    monkeypatch.setenv("GLM_API_KEY", "sk-glm")
    c = GLMClient()
    assert c.model == "glm-5.2"          # 智谱最新 GLM-5.2,仅评估用
    assert c.temperature == 0.0
    assert c.max_tokens == 2048


def test_glm_requires_env(monkeypatch):
    monkeypatch.delenv("GLM_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GLM_API_KEY"):
        GLMClient()


def test_glm_model_override(monkeypatch):
    monkeypatch.setenv("GLM_API_KEY", "sk-glm")
    c = GLMClient(model="glm-4")          # 可按需覆盖
    assert c.model == "glm-4"
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/test_clients_defaults.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'llm.deepseek_client'`

- [ ] **Step 3: 写最小实现 `llm/deepseek_client.py`**

```python
"""DeepSeek 生成客户端(OpenAI 兼容)。薄子类,只设默认值。"""
from .openai_compat import OpenAICompatClient
from core.config import env


class DeepSeekClient(OpenAICompatClient):
    def __init__(self, *, api_key=None, base_url="https://api.deepseek.com",
                 model="deepseek-chat", temperature=0.3, max_tokens=4096, **kwargs):
        # api_key 默认从环境变量取;也允许显式传入(测试/覆盖)
        super().__init__(
            api_key=api_key or env("DEEPSEEK_API_KEY"),
            base_url=base_url, model=model,
            temperature=temperature, max_tokens=max_tokens, **kwargs,
        )
```

- [ ] **Step 4: 写最小实现 `llm/glm_client.py`**

```python
"""GLM 裁判客户端(OpenAI 兼容)。薄子类,默认 GLM-5.2。仅评估环节用,规避自评偏差。"""
from .openai_compat import OpenAICompatClient
from core.config import env


class GLMClient(OpenAICompatClient):
    def __init__(self, *, api_key=None, base_url="https://open.bigmodel.cn/api/paas/v4",
                 model="glm-5.2", temperature=0.0, max_tokens=2048, **kwargs):
        super().__init__(
            api_key=api_key or env("GLM_API_KEY"),
            base_url=base_url, model=model,
            temperature=temperature, max_tokens=max_tokens, **kwargs,
        )
```

- [ ] **Step 5: 跑测试验证通过**

Run: `pytest tests/test_clients_defaults.py -v`
Expected: PASS(5 passed)

- [ ] **Step 6: Commit**

```bash
git add llm/deepseek_client.py llm/glm_client.py tests/test_clients_defaults.py
git commit -m "feat(llm): add DeepSeek (generator) + GLM-5.2 (judge) thin clients"
```

---

### Task 8: `tools/web_search.py` — 博查 Bocha 搜索

对应 spec §3.4。POST 博查 REST,解析 `data.webPages.value[]` → `List[SearchResult]`。返回的是「搜索结果」,**不是** Finding —— 是否采信由 Researcher 在 M2 决定。

**Files:**
- Create: `tools/web_search.py`
- Test: `tests/test_web_search.py`

- [ ] **Step 1: 写失败测试 `tests/test_web_search.py`**

```python
import pytest

from tools.web_search import BochaSearchClient, _parse


class _FakeResp:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


def test_parse_extracts_results():
    data = {"data": {"webPages": {"value": [
        {"name": "A", "url": "https://a", "summary": "sa"},
        {"name": "B", "url": "https://b", "snippet": "sb"},
    ]}}}
    res = _parse(data)
    assert len(res) == 2
    assert res[0].title == "A" and res[0].snippet == "sa"
    assert res[1].snippet == "sb"


def test_parse_handles_empty_shapes():
    assert _parse({}) == []
    assert _parse({"data": {}}) == []
    assert _parse({"data": {"webPages": {}}}) == []


def test_search_sends_bearer_and_query(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return _FakeResp({"data": {"webPages": {"value": [
            {"name": "X", "url": "https://x", "summary": "sx"}]}}})

    monkeypatch.setattr("tools.web_search.httpx.post", fake_post)
    c = BochaSearchClient(api_key="sk-test", count=5)
    res = c.search("hello world")

    assert captured["headers"]["Authorization"] == "Bearer sk-test"
    assert captured["json"] == {"query": "hello world", "count": 5}
    assert len(res) == 1 and res[0].url == "https://x"


def test_search_raises_on_http_error(monkeypatch):
    monkeypatch.setattr("tools.web_search.httpx.post",
                        lambda *a, **k: _FakeResp({}, status=500))
    c = BochaSearchClient(api_key="sk")
    with pytest.raises(Exception):
        c.search("x")
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/test_web_search.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'tools.web_search'`

- [ ] **Step 3: 写最小实现 `tools/web_search.py`**

```python
"""博查 Bocha 网页搜索(REST)。返回 List[SearchResult]。
注意:返回的是搜索结果,不是 Finding —— 是否采信由 Researcher(M2)决定。"""
import httpx

from core.schemas import SearchResult


def _parse(data: dict) -> list[SearchResult]:
    """从博查返回提取结果列表,容忍缺字段/空结构。"""
    items = (((data.get("data") or {}).get("webPages") or {}).get("value")) or []
    results = []
    for it in items:
        results.append(SearchResult(
            title=it.get("name") or it.get("title") or "",
            url=it.get("url", ""),
            snippet=it.get("summary") or it.get("snippet") or "",
        ))
    return results


class BochaSearchClient:
    def __init__(self, api_key, endpoint="https://api.bochaai.com/v1/web-search",
                 count=8, timeout=15):
        self._api_key = api_key
        self._endpoint = endpoint
        self._count = count
        self._timeout = timeout

    def search(self, query: str, count: int | None = None) -> list[SearchResult]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {"query": query, "count": count or self._count}
        resp = httpx.post(self._endpoint, headers=headers, json=payload, timeout=self._timeout)
        resp.raise_for_status()
        return _parse(resp.json())
```

- [ ] **Step 4: 跑测试验证通过**

Run: `pytest tests/test_web_search.py -v`
Expected: PASS(4 passed)

- [ ] **Step 5: Commit**

```bash
git add tools/web_search.py tests/test_web_search.py
git commit -m "feat(tools): add Bocha web search client"
```

---

### Task 9: `tools/web_read.py` — trafilatura 网页提取

对应 spec §3.4。纯本地提取(零网络、零成本),按 `max_chars` 截断正文防爆上下文。

**Files:**
- Create: `tools/web_read.py`
- Test: `tests/test_web_read.py`

- [ ] **Step 1: 写失败测试 `tests/test_web_read.py`**

```python
from tools import web_read


def test_fetch_text_happy(monkeypatch):
    monkeypatch.setattr(web_read.trafilatura, "fetch_url", lambda url: "<html>x</html>")
    monkeypatch.setattr(web_read.trafilatura, "extract",
                        lambda html, with_metadata=False: "正文" * 10)
    out = web_read.fetch_text("https://example.com/a", max_chars=20)
    assert out.startswith("正文")
    assert len(out) <= 20


def test_fetch_text_empty_when_download_fails(monkeypatch):
    monkeypatch.setattr(web_read.trafilatura, "fetch_url", lambda url: None)
    assert web_read.fetch_text("https://example.com/missing") == ""


def test_fetch_text_empty_when_extract_none(monkeypatch):
    monkeypatch.setattr(web_read.trafilatura, "fetch_url", lambda url: "<html></html>")
    monkeypatch.setattr(web_read.trafilatura, "extract",
                        lambda html, with_metadata=False: None)
    assert web_read.fetch_text("https://example.com/x") == ""


def test_fetch_text_no_truncate_when_zero(monkeypatch):
    monkeypatch.setattr(web_read.trafilatura, "fetch_url", lambda url: "h")
    monkeypatch.setattr(web_read.trafilatura, "extract",
                        lambda html, with_metadata=False: "abcdefgh")
    assert web_read.fetch_text("u", max_chars=0) == "abcdefgh"
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/test_web_read.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'tools.web_read'`

- [ ] **Step 3: 写最小实现 `tools/web_read.py`**

```python
"""trafilatura 本地网页正文提取(纯本地、零网络、零成本)。"""
import trafilatura


def fetch_text(url: str, max_chars: int = 8000) -> str:
    """下载并提取正文;失败/空页返回空串。max_chars>0 时截断防爆上下文;0 表示不截断。"""
    downloaded = trafilatura.fetch_url(url)
    if not downloaded:
        return ""
    text = trafilatura.extract(downloaded, with_metadata=False) or ""
    if max_chars:
        text = text[:max_chars]
    return text
```

- [ ] **Step 4: 跑测试验证通过**

Run: `pytest tests/test_web_read.py -v`
Expected: PASS(4 passed)

- [ ] **Step 5: Commit**

```bash
git add tools/web_read.py tests/test_web_read.py
git commit -m "feat(tools): add trafilatura web read"
```

---

### Task 10: `core/tool_registry.py` — 工具注册与分发

对应 spec §4 `ToolRegistry`。外部工具(web_search/web_read)与未来的内部工具(M2 的 `dispatch_research`/`verify_findings`/`write_report`,派发子 agent)统一注册,`schemas()` 导出 OpenAI 兼容的 `tools` 参数。

**Files:**
- Create: `core/tool_registry.py`
- Test: `tests/test_tool_registry.py`

- [ ] **Step 1: 写失败测试 `tests/test_tool_registry.py`**

```python
import pytest

from core.tool_registry import ToolRegistry, ToolNotFoundError


def _echo(text):
    return {"echo": text}


def test_register_and_schemas():
    reg = ToolRegistry()
    reg.register("echo", _echo, description="回显",
                 parameters={"type": "object",
                             "properties": {"text": {"type": "string"}},
                             "required": ["text"]})
    schemas = reg.schemas()
    assert len(schemas) == 1
    assert schemas[0]["type"] == "function"
    assert schemas[0]["function"]["name"] == "echo"
    assert "text" in schemas[0]["function"]["parameters"]["properties"]


def test_execute_passes_kwargs():
    reg = ToolRegistry()
    reg.register("echo", _echo, description="d", parameters={"type": "object"})
    assert reg.execute("echo", {"text": "hi"}) == {"echo": "hi"}


def test_execute_unknown_raises():
    reg = ToolRegistry()
    with pytest.raises(ToolNotFoundError):
        reg.execute("nope", {})


def test_names():
    reg = ToolRegistry()
    reg.register("a", lambda: 1, description="d", parameters={"type": "object"})
    reg.register("b", lambda: 2, description="d", parameters={"type": "object"})
    assert set(reg.names()) == {"a", "b"}
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/test_tool_registry.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'core.tool_registry'`

- [ ] **Step 3: 写最小实现 `core/tool_registry.py`**

```python
"""工具注册 + 分发(spec §4 ToolRegistry)。
外部工具(web_search/web_read)与内部工具(M2 的 dispatch_research/verify_findings/write_report,
派发子 agent)统一注册;schemas() 导出 OpenAI 兼容 tools 参数。"""


class ToolNotFoundError(KeyError):
    """调用了未注册的工具。"""


class ToolRegistry:
    def __init__(self):
        self._tools = {}  # name -> {"fn": callable, "schema": dict}

    def register(self, name, fn, *, description, parameters):
        """注册一个工具。parameters 是 JSON Schema(描述参数给模型看)。"""
        schema = {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": parameters,
            },
        }
        self._tools[name] = {"fn": fn, "schema": schema}

    def schemas(self) -> list[dict]:
        """导出 OpenAI 兼容的 tools 参数,传给 client.chat(tools=...)。"""
        return [t["schema"] for t in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools)

    def execute(self, name: str, arguments: dict):
        """按 name 找到工具,以 arguments 解包为 kwargs 调用。未注册抛 ToolNotFoundError。"""
        if name not in self._tools:
            raise ToolNotFoundError(name)
        return self._tools[name]["fn"](**arguments)
```

- [ ] **Step 4: 跑测试验证通过**

Run: `pytest tests/test_tool_registry.py -v`
Expected: PASS(4 passed)

- [ ] **Step 5: Commit**

```bash
git add core/tool_registry.py tests/test_tool_registry.py
git commit -m "feat(core): add ToolRegistry (registration + dispatch)"
```

---

### Task 11: `core/agent_loop.py` — 核心原语 AgentLoop

对应 spec §4 `AgentLoop`,整个 harness 的心脏。循环:调模型 → 有 `tool_call` 则经 `ToolRegistry` 执行并回填 `tool` 消息、继续循环 → 无 `tool_call` 则返回最终内容。`max_steps` 护栏超限抛 `Escalation`(spec §4「显式 escalation」)。工具执行异常被捕获并回填为 ERROR 文本(让模型自行处理,非致命)。

**Files:**
- Create: `core/agent_loop.py`
- Test: `tests/test_agent_loop.py`

- [ ] **Step 1: 写失败测试 `tests/test_agent_loop.py`**

```python
import pytest

from core.agent_loop import AgentLoop, AgentResult
from core.tool_registry import ToolRegistry
from core.robust import Escalation
from llm.base import LLMClient, LLMResponse, ToolCall


class FakeClient(LLMClient):
    """按脚本顺序返回 LLMResponse,记录每次调用。"""
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def chat(self, *, messages, tools=None, model=None, temperature=None, max_tokens=None):
        self.calls.append({"n_messages": len(messages), "tools": tools})
        return self._responses.pop(0)


def _registry_with_echo():
    reg = ToolRegistry()
    reg.register("echo", lambda text: {"echo": text},
                 description="d", parameters={"type": "object"})
    return reg


def test_no_tools_returns_content():
    c = FakeClient([LLMResponse(content="final answer")])
    loop = AgentLoop(client=c, system_prompt="sys")
    out = loop.run("hi")
    assert isinstance(out, AgentResult)
    assert out.content == "final answer"
    assert len(c.calls) == 1


def test_tool_call_then_final():
    c = FakeClient([
        LLMResponse(content="", tool_calls=[
            ToolCall(id="c1", name="echo", arguments='{"text":"hi"}')]),
        LLMResponse(content="done"),
    ])
    loop = AgentLoop(client=c, system_prompt="sys", registry=_registry_with_echo())
    out = loop.run("do echo")
    assert out.content == "done"
    assert len(c.calls) == 2
    # 第二次调用时,messages 已含 tool 回填 → 比第一次长
    assert c.calls[1]["n_messages"] > c.calls[0]["n_messages"]


def test_bad_arguments_fallback():
    # 模型返回非法 JSON 参数 → safe_parse 兜底 {} → echo 缺 text 抛 → 回填 ERROR → 模型继续
    c = FakeClient([
        LLMResponse(content="", tool_calls=[
            ToolCall(id="c1", name="echo", arguments="{bad json")]),
        LLMResponse(content="recovered"),
    ])
    loop = AgentLoop(client=c, system_prompt="sys", registry=_registry_with_echo())
    out = loop.run("x")
    assert out.content == "recovered"


def test_max_steps_raises_escalation():
    # 模型永远返回 tool_call、永不收敛 → max_steps 护栏触发 Escalation
    looping = LLMResponse(content="", tool_calls=[
        ToolCall(id="c1", name="echo", arguments='{"text":"x"}')])
    c = FakeClient([looping] * 100)
    reg = ToolRegistry()
    reg.register("echo", lambda text: "ok", description="d",
                 parameters={"type": "object"})
    loop = AgentLoop(client=c, system_prompt="sys", registry=reg, max_steps=3)
    with pytest.raises(Escalation):
        loop.run("loop forever")
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/test_agent_loop.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'core.agent_loop'`

- [ ] **Step 3: 写最小实现 `core/agent_loop.py`**

```python
"""AgentLoop —— 核心原语(spec §4)。调模型→有 tool_call 则经 ToolRegistry 执行并回填、
无 tool_call 则返回。max_steps 护栏防无限循环/成本失控(超限抛 Escalation)。
工具执行异常被捕获并回填为 ERROR 文本(非致命,让模型自行处理)。"""
import json
from dataclasses import dataclass, field
from typing import Any, Optional

from llm.base import LLMClient
from .robust import safe_parse_arguments, Escalation


def _to_text(result: Any) -> str:
    """把工具返回值序列化成 tool message 的 content 字符串。"""
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(result)


@dataclass
class AgentResult:
    content: str
    history: list = field(default_factory=list)  # 完整消息历史(调试/审计/M2 上下文隔离参考)
    usage: dict = field(default_factory=dict)


class AgentLoop:
    def __init__(self, *, client: LLMClient, system_prompt: str,
                 registry: Optional[Any] = None, model: Optional[str] = None,
                 temperature: Optional[float] = None,
                 max_tokens: Optional[int] = None,
                 max_steps: int = 12, name: str = "agent"):
        self.client = client
        self.system_prompt = system_prompt
        self.registry = registry
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_steps = max_steps
        self.name = name

    def run(self, user_message: str, context_messages: Optional[list] = None) -> AgentResult:
        messages: list[dict] = [{"role": "system", "content": self.system_prompt}]
        if context_messages:
            messages += context_messages
        messages.append({"role": "user", "content": user_message})

        tools = self.registry.schemas() if self.registry else None

        for _ in range(self.max_steps):
            resp = self.client.chat(
                messages=messages, tools=tools,
                model=self.model, temperature=self.temperature, max_tokens=self.max_tokens,
            )
            assistant_msg: dict = {"role": "assistant", "content": resp.content}
            if resp.tool_calls:
                assistant_msg["tool_calls"] = [
                    {"id": t.id, "type": "function",
                     "function": {"name": t.name, "arguments": t.arguments}}
                    for t in resp.tool_calls
                ]
            messages.append(assistant_msg)

            # 无 tool_call → 模型产出最终结果,返回
            if not resp.tool_calls:
                return AgentResult(content=resp.content, history=messages, usage=resp.usage)

            # 有 tool_call → 逐个执行并回填 tool 消息
            for tc in resp.tool_calls:
                args = safe_parse_arguments(tc.arguments)
                try:
                    result = self.registry.execute(tc.name, args)
                except Exception as e:
                    # 工具执行失败:错误回填给模型,让它自行处理(非致命)
                    result = f"ERROR executing {tc.name}: {e!r}"
                messages.append({"role": "tool", "tool_call_id": tc.id,
                                 "content": _to_text(result)})

        # 用尽 max_steps 仍未收敛 → 显式 escalation,而非静默崩
        raise Escalation(
            f"{self.name} hit max_steps={self.max_steps}",
            context={"name": self.name, "steps": self.max_steps},
        )
```

- [ ] **Step 4: 跑测试验证通过**

Run: `pytest tests/test_agent_loop.py -v`
Expected: PASS(4 passed)

- [ ] **Step 5: Commit**

```bash
git add core/agent_loop.py tests/test_agent_loop.py
git commit -m "feat(core): add AgentLoop primitive (tool loop + max_steps guard)"
```

---

### Task 12: 端到端 smoke 测试 + README + 最终验证

用一个 FakeClient + 双工具 registry,证明「模型发 tool_call → registry 执行 → 回填 → 循环 → 最终返回」整条链路成立。这是本里程碑的**验收点**。同时补 `README.md`。

**Files:**
- Create: `tests/test_smoke_agent_loop.py`
- Create: `README.md`

- [ ] **Step 1: 写 smoke 测试 `tests/test_smoke_agent_loop.py`**

```python
"""端到端 smoke:用 FakeClient 证明 AgentLoop + ToolRegistry 整条链路成立(不烧 API)。"""
from core.agent_loop import AgentLoop
from core.tool_registry import ToolRegistry
from llm.base import LLMClient, LLMResponse, ToolCall


class ScriptedClient(LLMClient):
    def __init__(self, script):
        self._script = list(script)
        self.call_count = 0

    def chat(self, **kw):
        self.call_count += 1
        return self._script.pop(0)


def test_end_to_end_two_tools_then_final():
    # 模拟研究员:先搜索、再读网页、最后综合
    def search(query):
        return [{"title": "T", "url": "https://x", "snippet": "s"}]

    def read(url):
        return "正文内容"

    reg = ToolRegistry()
    reg.register("web_search", search, description="搜索",
                 parameters={"type": "object",
                             "properties": {"query": {"type": "string"}},
                             "required": ["query"]})
    reg.register("web_read", read, description="读网页",
                 parameters={"type": "object",
                             "properties": {"url": {"type": "string"}},
                             "required": ["url"]})

    client = ScriptedClient([
        LLMResponse(content="", tool_calls=[
            ToolCall(id="1", name="web_search", arguments='{"query":"a"}')]),
        LLMResponse(content="", tool_calls=[
            ToolCall(id="2", name="web_read", arguments='{"url":"https://x"}')]),
        LLMResponse(content="研究报告:..."),
    ])
    loop = AgentLoop(client=client, system_prompt="你是研究员",
                     registry=reg, max_steps=10, name="smoke")

    out = loop.run("研究一下 a")

    assert out.content == "研究报告:..."
    assert client.call_count == 3
    roles = [m["role"] for m in out.history]
    assert roles.count("tool") == 2          # 两次工具调用都回填了
    assert roles[-1] == "assistant"          # 最后一条是最终产出
```

- [ ] **Step 2: 跑 smoke 测试验证通过**

Run: `pytest tests/test_smoke_agent_loop.py -v`
Expected: PASS(1 passed)

- [ ] **Step 3: 创建 `README.md`**

````markdown
# AI-PM-Agent · 深度研究型多 Agent 系统

基于 DeepSeek 的**模型自驱**多 Agent 编排系统:接受研究问题 → 规划 / 检索 / 验证 / 撰写 → 产出带引用的研究报告。**自研 harness**(非现成 SDK),核心原语 `AgentLoop` + `ToolRegistry` + 重试 / 兜底解析 / escalation。

> 详细设计见 [spec](docs/superpowers/specs/2026-08-08-deep-research-multi-agent-design.md)。

## 当前里程碑:M1 地基 ✅

已完成:harness 原语(AgentLoop / ToolRegistry / robustness)、LLM 客户端(DeepSeek 生成 + GLM-5.2 裁判)、博查搜索、trafilatura 提取、Pydantic 数据结构。全部单测覆盖,不烧 API。

## 快速开始

```bash
python -m venv .venv && source .venv/Scripts/activate   # Windows Git Bash
pip install -r requirements.txt
cp .env.example .env   # 填入 DEEPSEEK / GLM / BOCHA key(M1 测试不需要)
pytest                  # 跑全部单测
```

## 后续里程碑

- **M2** 四个 agent(orchestrator / researcher / verifier / writer)+ 子 agent 派发 + `main.py` 真实链路
- **M3** 评估体系(9 维指标 + 基准集 + 基线对比 + 上线门槛)
- **M4** Streamlit 实时编排可视化
````

> 说明:README 内含嵌套代码块,故外层用 4 个反引号(````)包裹,写文件时只保留 3 反引号的实际内容。

- [ ] **Step 4: 跑全量测试(里程碑验收)**

Run: `pytest -v`
Expected: PASS —— 全绿,共 50 个用例:
- test_config:4 · test_schemas:7 · test_llm_base:3 · test_robust:8 · test_openai_compat:6
- test_clients_defaults:5 · test_web_search:4 · test_web_read:4 · test_tool_registry:4
- test_agent_loop:4 · test_smoke_agent_loop:1

- [ ] **Step 5: Commit**

```bash
git add tests/test_smoke_agent_loop.py README.md
git commit -m "test: end-to-end smoke for AgentLoop; add README (M1 complete)"
```

---

## M1 完成定义(Definition of Done)

- [ ] 上述 12 个 Task 全部完成,每个都有 commit。
- [ ] `pytest -v` 全绿(50 passed),**无网络调用**(全 mock)。
- [ ] `tests/test_smoke_agent_loop.py` 证明 AgentLoop + ToolRegistry 端到端链路成立。
- [ ] `README.md`、`config.yaml`、`.env.example`、`requirements.txt` 就位。
- [ ] 可进入 M2:四个 agent 的 system prompt + 工具绑定 + 子 agent 派发。

---

## Self-Review(plan 作者自检记录)

**1. Spec 覆盖(本里程碑范围内的 spec 章节):**
- §3.2 数据结构 → Task 3(全部 Pydantic 结构)。✅
- §3.4 外部服务(DeepSeek/GLM/博查/trafilatura)→ Task 7 / Task 8 / Task 9。✅
- §4 Harness(AgentLoop/ToolRegistry/上下文管理/robustness/escalation)→ Task 10 / Task 11 / Task 5。✅ 上下文隔离在 M2 子 agent 派发时体现(M1 数据结构已就位)。
- §7 技术栈 → Task 1 requirements。✅
- §8 项目结构 → File Structure 表(新增 config.py / openai_compat.py 已注明)。✅
- §9 错误处理与测试(护栏 + pytest mock 单测)→ Task 5 / Task 11 / 全 Task。✅

**本里程碑不在范围(spec 其他章节由后续里程碑覆盖):**
- §2/§3.1/§3.3 四个 agent + 工具绑定 + 子 agent 派发 → **M2**。
- §5 评估体系 → **M3**。
- §6 界面 → **M4**。
- §10 简历话术 → 项目收尾时填实测数据。

**2. Placeholder 扫描:** 无 TBD/TODO/"implement later";每个代码步骤都含完整代码;无"参照 Task N"的省略。

**3. 类型一致性核对:** `LLMClient.chat` 签名(messages/tools/model/temperature/max_tokens)在 base(Task 4)、openai_compat(Task 6)、FakeClient(Task 11)三处一致;`ToolCall(id,name,arguments)`、`LLMResponse(content,tool_calls,usage,raw)`、`AgentResult(content,history,usage)`、`ToolRegistry.register/​schemas/​execute/​names`、`safe_parse_arguments`/`retry_with_backoff`/`Escalation`/`TransientError` 跨 Task 名称统一。✅

