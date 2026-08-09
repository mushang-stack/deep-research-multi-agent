# M3 评估体系(质量 MVP:GLM 逐条裁判 + 4 质量指标)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 零侵入地给 M2 系统加一套评估管线——从研究 history 抽 findings、用 GLM-5.2 逐条裁判、算出 4 个质量指标(Grounding/引用准确率/覆盖率/幻觉率),跑基准集出 scorecard 并立 Grounding ≥ 0.85 上线门槛。

**Architecture:** 评估链路只**消费** M2 产物(`build_system` 返回的 `get_report()` 拿 Report、`loop.run()` 返回的 `AgentResult.history` 拿 findings),**不改** `agents/`、`core/`、`llm/`、`tools/` 的运行时行为。唯一例外是一次纯函数重构:把 `agents/dispatch.py` 里两段 JSON 清洗逻辑(`clean_json` + `_json_substring`)抽到新增的 `core/json_utils.py`,供 `eval/trace.py` 与 `eval/judge.py` 复用(行为不变,由现有 `test_dispatch.py` 守护)。评估分四层:`trace.py`(抽 findings,纯解析)→ `judge.py`(封装 `GLMClient` 逐条裁判,小输出)→ `metrics.py`(纯函数算分)→ `run_eval.py`(编排 + CLI)。全 mock TDD,收尾手动真跑一次。

**Tech Stack:** Python 3.11+、pydantic v2、pyyaml 6.0.3(已装,`config.py` 已用)、pytest。复用 M1/M2 的 `core/`(robust/schemas/config)、`llm/`(GLMClient)、`agents/`(system/orchestrator/dispatch)。

**对应 spec:** [docs/superpowers/specs/2026-08-09-m3-evaluation-quality-mvp-design.md](../specs/2026-08-09-m3-evaluation-quality-mvp-design.md)

---

## 里程碑边界(本 plan 范围)

**做:** `eval/`(trace + judge + metrics + run_eval + benchmark 题库格式)、4 质量指标 + 附带成功率、GLM 逐条裁判(每 finding 一次 + 每 key_fact 一次)、题库 `{id,question,key_facts}` + 模板 + 1 道真实种子题、上线门槛判定(平均 Grounding ≥ 0.85)、全 mock 测试 + 收尾手动真跑。

**不做(留 M3 后续 / M4):** 5 运维指标(成本/延迟/Automation Rate/Agent Utilization)、单 agent 基线对比、Streamlit dashboard、统计显著性 / 多裁判投票 / 一致性校准。

**验收(对应 spec §7 DoD):**
- `pytest -q` 全绿(M1+M2 的 96 保持 + M3 新增,全 mock 不烧 API)
- `test_metrics.py` 证明 4 指标公式 + 门槛判定 + N=0 不可用
- `test_judge.py` 证明坏 JSON(围栏 / 散文包 JSON)被清洗兜底(吸取 M2 冒烟踩过的 JSON 解析坑)
- 手动 `python -m eval.run_eval` 真跑出真实 scorecard
- 题库模板就位 + 至少 1 道真实种子题

---

## File Structure

仓库根 = 项目根。新增/改动文件如下:

| 文件 | 职责 | 本里程碑创建 |
|---|---|---|
| `core/json_utils.py` | 纯函数 JSON 清洗:`clean_json`(去 markdown 围栏)+ `json_balanced_substring`(从散文里抠平衡 JSON)——从 `agents/dispatch.py` 抽公共 | Task 1 |
| `agents/dispatch.py` | **改动(纯重构):** 改为从 `core.json_utils` 导入上述两个函数,删除本地副本。行为不变 | Task 1 |
| `eval/trace.py` | `extract_findings(history)` -> list[dict]:从 orchestrator history 的 tool 消息抽 findings,按 (claim, source_url) 去重 | Task 2 |
| `eval/metrics.py` | `compute_question_metrics` + `aggregate`:纯函数,由裁判 verdict 算 4 指标 + 门槛 | Task 3 |
| `eval/judge.py` | `judge_finding` / `judge_key_fact`:封装 GLMClient 逐条裁判,小输出 + 坏 JSON 兜底 | Task 4 |
| `eval/run_eval.py` | `run_one` / `evaluate_question` / `load_benchmark` / `aggregate` 调用 / `main(argv)` CLI | Task 5/6 |
| `eval/benchmark/README.md` | 题库格式说明 + 模板 | Task 7 |
| `eval/benchmark/_template.yaml` | 单题模板 | Task 7 |
| `eval/benchmark/q001-speculative-decoding.yaml` | 1 道真实种子题(可扩充) | Task 7 |
| `tests/test_trace.py` / `test_metrics.py` / `test_judge.py` / `test_run_eval.py` | 全 mock 测试 | Task 2/3/4/5/6 |

> **设计说明(对 spec 的落地细化):**
> 1. **JSON 清洗抽公共(spec §4.1/§4.2 「复用/抽公共」):** `eval/trace.py` 与 `eval/judge.py` 都要洗模型/裁判输出的 JSON。与其跨模块 import `agents.dispatch` 的 `_` 私有函数(代码味),也不如各自重写(违反 DRY),把这两个**纯函数**抽到 `core/json_utils.py` 单一职责模块。这是一次**行为不变的重构**:`agents/dispatch.py` 改为 import 复用,由现有 `tests/test_dispatch.py`(含围栏/散文/坏 JSON 共 10 个用例)守护,96 测试不受影响。spec 的「零侵入」指不改 agent 运行时行为——纯函数提取 + import 重指向满足此约束。
> 2. **eval 测试注入 `run_one` 缝隙(spec §4.4 「可注入」):** `evaluate_question` 内部调用 `run_one`(经 `build_system` 跑全系统)。为让 eval 单测**聚焦自身逻辑**(trace→judge→metrics→scorecard 装配)且不重新驱动整条 agent 链(M2 `test_orchestrator_e2e.py` 已覆盖),`evaluate_question` 接受可注入的 `run_one_fn`;真实链路正确性由 M2 e2e + 收尾手动真跑共同保证。CLI `main` 同样开放 `run_one_fn` / `judge_client` / `cfg` / `results_dir` 注入,使端到端装配可全 mock 测。

---

## 数据契约(跨 Task 名称锁定,后续 Task 不得改名)

**finding 裁判 verdict(dict):**
```python
{"support": "supported" | "partial" | "unsupported",
 "source_real": bool,
 "reason": str,
 "parse_failed": bool}   # 仅裁判输出不可解析时 True;此时 support="unsupported", source_real=False
```

**key_fact 裁判 verdict(dict):**
```python
{"covered": bool,
 "reason": str,
 "parse_failed": bool}   # 仅裁判输出不可解析时 True;此时 covered=False
```

**单题 scorecard(`evaluate_question` 返回,dict):**
```python
{"id": str, "question": str,
 "success": bool,                # report 非 None 即 True
 "failed": None | "no_report",
 "n_findings": int, "n_key_facts": int,
 "grounding": float | None,      # None = 该题无 finding,不可用
 "citation": float | None,       # 引用准确率
 "coverage": float | None,       # 无 key_fact 时 None
 "hallucination": float | None,
 "grounding_available": bool,    # n_findings > 0
 "judge_parse_failures": int}
```

**汇总 scorecard(`aggregate` 返回,dict):**
```python
{"n_questions": int, "n_success": int, "success_rate": float,
 "mean_grounding": float | None,   # 仅在 grounding 非 None 的题上取均值
 "mean_citation": float | None, "mean_coverage": float | None, "mean_hallucination": float | None,
 "grounding_questions": int,       # 参与均值的有效题数
 "grounding_min": float,
 "passed": bool,                   # mean_grounding is not None and mean_grounding >= grounding_min
 "total_judge_parse_failures": int}
```

**指标公式(spec §3,N=该题 findings 数,M=该题 key_facts 数):**
- Grounding = Σ(supported=1.0, partial=0.5, unsupported=0.0) / N
- 引用准确率 citation = (source_real 为 True 的 findings) / N
- 覆盖率 coverage = (covered 为 True 的 key_facts) / M
- 幻觉率 hallucination = (support=unsupported **或** source_real 非 True 的 findings) / N
- 成功率 success = report 非 None(runner 层免费统计,与运维 Agent Success Rate 不同口径)

---

## 约定

- **TDD:** 每个 Task 先写失败测试 → 跑红 → 写最小实现 → 跑绿 → 提交。Task 1 是纯重构(无新行为),用现有 `test_dispatch.py` 作安全网。
- **commit 粒度:** 每个 Task 一次 commit,conventional commits(`feat(eval):` / `refactor(core):` / `test(eval):` / `docs(eval):`)。
- **不烧 API:** 所有测试用 FakeClient(M1/M2 已建立的模式),绝不真实调 GLM/DeepSeek/博查。收尾手动真跑才用真 key。
- **Python 3.11+:** 用 `str | None` 等新语法。
- **零侵入:** Task 2-7 不改 `agents/`、`llm/`、`tools/`;Task 1 是 `core/` 纯函数新增 + `agents/dispatch.py` import 重指向(行为不变)。

---

### Task 1: 抽 JSON 清洗到 `core/json_utils.py`(纯重构,行为不变)

把 `agents/dispatch.py` 的 `clean_json` 与 `_json_substring` 原样搬到新模块 `core/json_utils.py`(后者去掉 `_` 前缀、改名为 `json_balanced_substring` 以示公开共享),`agents/dispatch.py` 改为导入复用。由现有 `tests/test_dispatch.py` 守护,证明行为不变。

**Files:**
- Create: `core/json_utils.py`
- Modify: `agents/dispatch.py`(删除两个函数定义,改为 import;内部 `_json_substring(` → `json_balanced_substring(`)
- 守护测试(已存在): `tests/test_dispatch.py`

- [ ] **Step 1: 跑基线,确认现有 dispatch 测试全绿**

Run: `python -m pytest tests/test_dispatch.py -q`
Expected: PASS(10 个用例全绿)。这是重构前的安全网。

- [ ] **Step 2: 创建 `core/json_utils.py`**

```python
"""JSON 清洗工具(纯函数)。供 agents.dispatch 与 eval.* 共用。

模型/裁判输出严格 JSON,但常套 markdown 围栏或在 JSON 前后输出思考散文。
这里两步清洗:
- clean_json: 去 ```json ... ``` 围栏与首尾空白。
- json_balanced_substring: 从散文里抠第一个平衡的 {...} 子串。
"""
import re


def clean_json(text: str) -> str:
    """去除 markdown 代码块包裹(```json ... ```)与首尾空白。"""
    if not text:
        return ""
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*\n?", "", s)
        s = re.sub(r"\n?```\s*$", "", s)
    return s.strip()


def json_balanced_substring(text: str) -> str:
    """从文本中抠出第一个平衡的 JSON 对象子串。

    模型(尤其 DeepSeek)常在 JSON 前后输出思考散文("我已经收集了…让我整理…{json}")。
    clean_json 只去围栏,去不掉这种散文 → json.loads 整段必失败。这里用大括号深度
    扫描(识别字符串字面量与转义),把第一个平衡的 {...} 抠出来。找不到则原样返回,
    交由 safe_parse_arguments 兜底成 {}。纯 JSON / 围栏 JSON 不受影响(首字符即 {)。
    """
    start = text.find("{")
    if start == -1:
        return text
    depth = 0
    in_str = False
    escaped = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
    return text  # 不平衡:原样返回,让上层兜底
```

- [ ] **Step 3: 改 `agents/dispatch.py`——删本地副本、改 import、改内部调用**

把文件顶部的两个函数定义(`clean_json` 与 `_json_substring`)整段删除,并新增 import;把 `_items` 与 `parse_report` 内部的 `_json_substring(` 两处调用改为 `json_balanced_substring(`。

修改后 `agents/dispatch.py` 顶部应为:

```python
"""子 agent 派发原语(集中管理,单一职责):
- JSON 清洗/解析:子 agent 输出严格 JSON,但模型常套 markdown 代码块/带杂字,这里清洗+兜底。
- 并行派发 dispatch_research:ThreadPoolExecutor 并发跑 N 个 researcher,合并 findings,单点容错。"""
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from core.json_utils import clean_json, json_balanced_substring
from core.robust import safe_parse_arguments
from core.schemas import Finding, VerificationResult, Report
```

(原 `def clean_json` 与 `def _json_substring` 两段整段删除——逻辑已搬到 `core/json_utils.py`。)

`_items` 改为(只改第一行调用名):

```python
def _items(text: str, key: str) -> list[dict]:
    """从 {"<key>":[...]} 提取列表。坏 JSON → []。"""
    data = safe_parse_arguments(json_balanced_substring(clean_json(text)))
    if not isinstance(data, dict):
        return []
    return data.get(key, []) or []
```

`parse_report` 改为(只改第二行调用名):

```python
def parse_report(text: str) -> Optional[Report]:
    data = safe_parse_arguments(json_balanced_substring(clean_json(text)))
    if not isinstance(data, dict):
        return None
    try:
        return Report(**data)
    except Exception:
        return None
```

> 注:`tests/test_dispatch.py` 里的 `from agents.dispatch import clean_json` 仍可用——`clean_json` 通过 import 进入了 `agents.dispatch` 的命名空间。

- [ ] **Step 4: 跑 dispatch 测试 + 全量测试,确认零回归**

Run: `python -m pytest tests/test_dispatch.py -q`
Expected: PASS(10 用例全绿,证明行为不变)。

Run: `python -m pytest -q`
Expected: PASS(M1+M2 共 96 个全绿)。

- [ ] **Step 5: Commit**

```bash
git add core/json_utils.py agents/dispatch.py
git commit -m "refactor(core): extract JSON cleaners to core/json_utils for eval reuse"
```

---

### Task 2: `eval/trace.py` — 从 history 抽 findings

`extract_findings(history) -> list[dict]`:遍历 orchestrator 的 `AgentResult.history`,取 `role=="tool"` 消息,经 `clean_json` + `json_balanced_substring` + `safe_parse_arguments` 解析 content;凡含 `findings` 键的,合并其 findings(每条是 `{id,claim,source_url,source_title,excerpt,confidence}` dict);按 `(claim, source_url)` 去重。

> **为什么只取含 `findings` 键的 tool 消息:** orchestrator history 里只有三类 tool 消息——`dispatch_research`(content 有 `findings`)、`verify_findings`(content 有 `results`)、`write_report`(content 有 `sections`)。用「含 findings 键」过滤,天然只收 dispatch 产物,跳过另两类。子 agent 的 web_search/web_read 消息在各自独立 history 里,不在 orchestrator history 中,无需处理。

**Files:**
- Create: `eval/trace.py`
- Test: `tests/test_trace.py`

- [ ] **Step 1: 写失败测试 `tests/test_trace.py`**

```python
from eval.trace import extract_findings


def _tool(content, cid="1"):
    return {"role": "tool", "tool_call_id": cid, "content": content}


def test_extract_findings_from_dispatch_tool_message():
    history = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "问题"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "1", "type": "function",
             "function": {"name": "dispatch_research",
                          "arguments": '{"sub_questions":["q1"]}'}}]},
        _tool('{"findings":[{"id":"f1","claim":"投机解码加速推理",'
              '"source_url":"https://x","excerpt":"摘录"}],"failures":[]}'),
        {"role": "assistant", "content": "报告已生成"},
    ]
    fs = extract_findings(history)
    assert len(fs) == 1
    assert fs[0]["claim"] == "投机解码加速推理"
    assert fs[0]["source_url"] == "https://x"
    assert fs[0]["excerpt"] == "摘录"


def test_extract_findings_skips_non_findings_tool_messages():
    # verify_findings(results) 与 write_report(sections) 的 tool 消息必须被跳过
    history = [
        _tool('{"results":[{"finding_id":"f1","verdict":"supported"}]}', "1"),
        _tool('{"sections":[{"heading":"H","content":"C"}],"sources":["https://x"]}', "2"),
        _tool('{"findings":[{"id":"f1","claim":"c","source_url":"https://x"}]}', "3"),
    ]
    fs = extract_findings(history)
    assert len(fs) == 1 and fs[0]["claim"] == "c"


def test_extract_findings_dedups_by_claim_and_source():
    # 多轮 dispatch 重复返回同一条 → 按 (claim, source_url) 去重
    dup = ('{"findings":[{"id":"f1","claim":"同一条","source_url":"https://x"},'
           '{"id":"f2","claim":"同一条","source_url":"https://x"}]}')
    history = [_tool(dup, "1"), _tool(dup, "2")]
    fs = extract_findings(history)
    assert len(fs) == 1  # 两次 dispatch × 各 2 条同内容 → 去重后 1 条


def test_extract_findings_merges_across_dispatch_rounds():
    history = [
        _tool('{"findings":[{"id":"f1","claim":"A","source_url":"https://a"}]}', "1"),
        _tool('{"findings":[{"id":"f1","claim":"B","source_url":"https://b"}]}', "2"),
    ]
    fs = extract_findings(history)
    assert {f["claim"] for f in fs} == {"A", "B"}


def test_extract_findings_tolerates_garbage_content():
    history = [
        _tool("not json at all", "1"),
        _tool('{"findings":[{"id":"f1","claim":"c","source_url":"https://x"}]}', "2"),
    ]
    fs = extract_findings(history)
    assert len(fs) == 1  # 坏消息跳过,好的照收


def test_extract_findings_empty_history():
    assert extract_findings([]) == []
```

- [ ] **Step 2: 跑测试,确认失败**

Run: `python -m pytest tests/test_trace.py -q`
Expected: FAIL(`ModuleNotFoundError: No module named 'eval.trace'`)。

- [ ] **Step 3: 写最小实现 `eval/trace.py`**

```python
"""从研究 history 抽取 findings(零侵入:只消费 orchestrator 的 AgentResult.history)。

orchestrator history 里 role=="tool" 消息有三类:dispatch_research(含 findings)、
verify_findings(含 results)、write_report(含 sections)。本模块只收含 findings 键的,
跳过另两类;多轮 dispatch 合并,按 (claim, source_url) 去重。
"""
from core.json_utils import clean_json, json_balanced_substring
from core.robust import safe_parse_arguments


def extract_findings(history: list[dict]) -> list[dict]:
    """history -> [{id,claim,source_url,source_title,excerpt,confidence}, ...]。

    遍历 role=="tool" 消息,解析 content,凡含 findings 键的合并其列表,
    按 (claim, source_url) 去重(保首次出现的完整字段)。
    """
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for msg in history:
        if not isinstance(msg, dict) or msg.get("role") != "tool":
            continue
        data = safe_parse_arguments(json_balanced_substring(clean_json(msg.get("content", ""))))
        if not isinstance(data, dict) or "findings" not in data:
            continue
        for f in data.get("findings") or []:
            if not isinstance(f, dict):
                continue
            key = (f.get("claim", ""), f.get("source_url", ""))
            if key in seen:
                continue
            seen.add(key)
            out.append(f)
    return out
```

- [ ] **Step 4: 跑测试,确认通过**

Run: `python -m pytest tests/test_trace.py -q`
Expected: PASS(6 用例全绿)。

- [ ] **Step 5: Commit**

```bash
git add eval/trace.py tests/test_trace.py
git commit -m "feat(eval): extract findings from orchestrator history"
```

---

### Task 3: `eval/metrics.py` — 纯函数算 4 指标 + 门槛

`compute_question_metrics(findings_verdicts, key_fact_verdicts, report_produced) -> dict` 与 `aggregate(scorecards, grounding_min) -> dict`。无 IO、无 LLM,纯计算 → 易单测。

**Files:**
- Create: `eval/metrics.py`
- Test: `tests/test_metrics.py`

- [ ] **Step 1: 写失败测试 `tests/test_metrics.py`**

```python
from eval.metrics import compute_question_metrics, aggregate


# ---------- compute_question_metrics ----------

def test_grounding_weights_supported_partial_unsupported():
    verdicts = [
        {"support": "supported", "source_real": True},
        {"support": "partial", "source_real": True},
        {"support": "unsupported", "source_real": False},
    ]
    m = compute_question_metrics(verdicts, [], report_produced=True)
    # (1.0 + 0.5 + 0.0) / 3
    assert m["grounding"] == (1.0 + 0.5 + 0.0) / 3
    assert m["grounding_available"] is True


def test_citation_is_fraction_source_real_true():
    verdicts = [
        {"support": "supported", "source_real": True},
        {"support": "supported", "source_real": False},
    ]
    m = compute_question_metrics(verdicts, [], report_produced=True)
    assert m["citation"] == 0.5


def test_coverage_is_fraction_covered():
    kfs = [{"covered": True}, {"covered": False}, {"covered": True}]
    m = compute_question_metrics([], kfs, report_produced=True)
    assert m["coverage"] == 2 / 3


def test_hallucination_counts_unsupported_or_fake_source():
    verdicts = [
        {"support": "supported", "source_real": True},   # 不计
        {"support": "unsupported", "source_real": True},  # 计(unsupported)
        {"support": "supported", "source_real": False},   # 计(假源)
    ]
    m = compute_question_metrics(verdicts, [], report_produced=True)
    assert m["hallucination"] == 2 / 3


def test_no_findings_marks_grounding_unavailable_but_coverage_ok():
    # N=0:Grounding/引用/幻觉不可用,覆盖率仍可算(spec §3 / §6)
    m = compute_question_metrics([], [{"covered": True}, {"covered": False}],
                                 report_produced=True)
    assert m["grounding"] is None
    assert m["citation"] is None
    assert m["hallucination"] is None
    assert m["grounding_available"] is False
    assert m["coverage"] == 0.5


def test_no_key_facts_marks_coverage_none():
    m = compute_question_metrics(
        [{"support": "supported", "source_real": True}], [], report_produced=True)
    assert m["coverage"] is None
    assert m["grounding"] == 1.0


def test_report_none_marks_failed_no_report():
    m = compute_question_metrics([], [], report_produced=False)
    assert m["success"] is False
    assert m["failed"] == "no_report"
    assert m["grounding"] is None


def test_report_produced_success_true():
    m = compute_question_metrics(
        [{"support": "supported", "source_real": True}], [], report_produced=True)
    assert m["success"] is True
    assert m["failed"] is None


def test_parse_failures_counted():
    verdicts = [
        {"support": "unsupported", "source_real": False, "parse_failed": True},
        {"support": "supported", "source_real": True},
    ]
    kfs = [{"covered": False, "parse_failed": True}]
    m = compute_question_metrics(verdicts, kfs, report_produced=True)
    assert m["judge_parse_failures"] == 2


# ---------- aggregate ----------

def test_aggregate_mean_grounding_over_n_gt_zero_only():
    sc = [
        {"id": "q1", "success": True, "grounding": 1.0, "citation": 1.0,
         "coverage": 1.0, "hallucination": 0.0, "judge_parse_failures": 0},
        {"id": "q2", "success": True, "grounding": None, "citation": None,
         "coverage": 0.0, "hallucination": None, "judge_parse_failures": 0},  # N=0,排除
    ]
    agg = aggregate(sc, grounding_min=0.85)
    assert agg["mean_grounding"] == 1.0          # 只在 q1 上取均值
    assert agg["grounding_questions"] == 1
    assert agg["n_questions"] == 2
    assert agg["n_success"] == 2
    assert agg["success_rate"] == 1.0


def test_aggregate_passed_threshold():
    sc = [{"id": "q1", "success": True, "grounding": 0.9, "citation": None,
           "coverage": None, "hallucination": None, "judge_parse_failures": 0}]
    assert aggregate(sc, grounding_min=0.85)["passed"] is True
    assert aggregate(sc, grounding_min=0.95)["passed"] is False


def test_aggregate_no_grounding_available_fails():
    # 所有题都 N=0 → mean_grounding None → passed False
    sc = [{"id": "q1", "success": True, "grounding": None, "citation": None,
           "coverage": None, "hallucination": None, "judge_parse_failures": 0}]
    agg = aggregate(sc, grounding_min=0.85)
    assert agg["mean_grounding"] is None
    assert agg["passed"] is False
```

- [ ] **Step 2: 跑测试,确认失败**

Run: `python -m pytest tests/test_metrics.py -q`
Expected: FAIL(`ModuleNotFoundError: No module named 'eval.metrics'`)。

- [ ] **Step 3: 写最小实现 `eval/metrics.py`**

```python
"""质量指标纯函数(spec §3)。由裁判 verdict 算分,无 IO / 无 LLM。

每题先算(compute_question_metrics),再跨题取均值(aggregate)。
N = 该题 findings 数,M = 该题 key_facts 数。
"""
from typing import Optional

_SUPPORT_SCORE = {"supported": 1.0, "partial": 0.5}  # 其余(含 unsupported)→ 0.0


def _mean(xs: list[float]) -> Optional[float]:
    return sum(xs) / len(xs) if xs else None


def compute_question_metrics(findings_verdicts: list[dict],
                             key_fact_verdicts: list[dict],
                             report_produced: bool) -> dict:
    """单题 5 数 + 不可用标注。verdict 形状见 plan「数据契约」。"""
    n = len(findings_verdicts)
    m = len(key_fact_verdicts)

    parse_fails = sum(1 for v in findings_verdicts if v.get("parse_failed")) \
                + sum(1 for v in key_fact_verdicts if v.get("parse_failed"))

    if n > 0:
        grounding = sum(_SUPPORT_SCORE.get(v.get("support"), 0.0)
                        for v in findings_verdicts) / n
        citation = sum(1 for v in findings_verdicts
                       if v.get("source_real") is True) / n
        hallucination = sum(1 for v in findings_verdicts
                            if v.get("support") == "unsupported"
                            or v.get("source_real") is not True) / n
    else:
        grounding = citation = hallucination = None

    coverage = (sum(1 for v in key_fact_verdicts if v.get("covered") is True) / m
                if m > 0 else None)

    return {
        "success": bool(report_produced),
        "failed": None if report_produced else "no_report",
        "n_findings": n,
        "n_key_facts": m,
        "grounding": grounding,
        "citation": citation,
        "coverage": coverage,
        "hallucination": hallucination,
        "grounding_available": n > 0,
        "judge_parse_failures": parse_fails,
    }


def aggregate(scorecards: list[dict], grounding_min: float) -> dict:
    """跨题均值 + 门槛判定。Grounding 均值只在 grounding 非 None 的题上取。"""
    n_q = len(scorecards)
    n_success = sum(1 for s in scorecards if s.get("success"))
    g_vals = [s["grounding"] for s in scorecards if s.get("grounding") is not None]
    mean_grounding = _mean(g_vals)

    return {
        "n_questions": n_q,
        "n_success": n_success,
        "success_rate": n_success / n_q if n_q else 0.0,
        "mean_grounding": mean_grounding,
        "mean_citation": _mean([s["citation"] for s in scorecards
                                if s.get("citation") is not None]),
        "mean_coverage": _mean([s["coverage"] for s in scorecards
                                if s.get("coverage") is not None]),
        "mean_hallucination": _mean([s["hallucination"] for s in scorecards
                                     if s.get("hallucination") is not None]),
        "grounding_questions": len(g_vals),
        "grounding_min": grounding_min,
        "passed": bool(mean_grounding is not None and mean_grounding >= grounding_min),
        "total_judge_parse_failures": sum(s.get("judge_parse_failures", 0)
                                          for s in scorecards),
    }
```

- [ ] **Step 4: 跑测试,确认通过**

Run: `python -m pytest tests/test_metrics.py -q`
Expected: PASS(12 用例全绿)。

- [ ] **Step 5: Commit**

```bash
git add eval/metrics.py tests/test_metrics.py
git commit -m "feat(eval): pure quality-metric functions + threshold gate"
```

---

### Task 4: `eval/judge.py` — GLM 逐条裁判(小输出 + 坏 JSON 兜底)

封装 M1 的 `GLMClient`(`llm/glm_client.py`,模型 glm-5.2)。两类裁判,每次输出极小(一个 verdict 对象),最大化 JSON 解析成功率。坏 JSON 用 `clean_json` + `json_balanced_substring` + `safe_parse_arguments` 清洗;仍失败则重试(默认 2 次),最终失败按 spec §6 保守计为 unsupported / not covered,并打 `parse_failed=True` 供 metrics 统计。

> **瞬时错误重试由 GLMClient 自带:** `OpenAICompatClient.chat` 内部已对 503/超时/429 走 `retry_with_backoff`(M1 已实现),judge.py 不重复造。本模块的重试仅针对「输出不可解析」这一类。

**Files:**
- Create: `eval/judge.py`
- Test: `tests/test_judge.py`

- [ ] **Step 1: 写失败测试 `tests/test_judge.py`**

```python
from llm.base import LLMResponse

from eval.judge import judge_finding, judge_key_fact


class FakeGLMClient:
    """脚本化 LLMResponse 队列,模拟 GLM 裁判(可故意返回坏 JSON)。"""
    def __init__(self, responses):
        self._r = list(responses)

    def chat(self, **kw):
        return self._r.pop(0)


# ---------- judge_finding ----------

def test_judge_finding_parses_clean_json():
    client = FakeGLMClient([LLMResponse(content='{"support":"supported",'
                                          '"source_real":true,"reason":"摘录支撑"}')])
    v = judge_finding(claim="c", excerpt="e", source_url="https://x", client=client)
    assert v["support"] == "supported"
    assert v["source_real"] is True
    assert v.get("parse_failed") is not True


def test_judge_finding_strips_markdown_fence():
    client = FakeGLMClient([LLMResponse(
        content='```json\n{"support":"partial","source_real":false,"reason":""}\n```')])
    v = judge_finding(claim="c", excerpt="e", source_url="https://x", client=client)
    assert v["support"] == "partial"
    assert v["source_real"] is False


def test_judge_finding_extracts_json_from_prose():
    # M2 冒烟踩过的坑:模型在 JSON 前输出散文
    client = FakeGLMClient([LLMResponse(
        content='好的,我来判定。该摘录明确支撑论断。\n\n'
                '{"support":"supported","source_real":true,"reason":"支撑"}')])
    v = judge_finding(claim="c", excerpt="e", source_url="https://x", client=client)
    assert v["support"] == "supported" and v["source_real"] is True


def test_judge_finding_parse_failure_retries_then_unsupported():
    # 连续两次不可解析 → 第三次也坏 → 计 unsupported + parse_failed(默认 retries=2 共 3 次)
    bad = LLMResponse(content="完全不是 JSON 的散文")
    client = FakeGLMClient([bad, bad, bad])
    v = judge_finding(claim="c", excerpt="e", source_url="https://x", client=client)
    assert v["support"] == "unsupported"
    assert v["source_real"] is False
    assert v["parse_failed"] is True


def test_judge_finding_parse_failure_recovers_on_retry():
    bad = LLMResponse(content="散文")
    good = LLMResponse(content='{"support":"supported","source_real":true,"reason":""}')
    client = FakeGLMClient([bad, good])  # 第一次坏,第二次好
    v = judge_finding(claim="c", excerpt="e", source_url="https://x", client=client)
    assert v["support"] == "supported"
    assert v.get("parse_failed") is not True


# ---------- judge_key_fact ----------

def test_judge_key_fact_parses_clean_json():
    client = FakeGLMClient([LLMResponse(content='{"covered":true,"reason":"已陈述"}')])
    v = judge_key_fact(key_fact="某关键事实", report_text="报告含某关键事实。",
                       client=client)
    assert v["covered"] is True
    assert v.get("parse_failed") is not True


def test_judge_key_fact_strips_fence():
    client = FakeGLMClient([LLMResponse(
        content='```json\n{"covered":false,"reason":"未提及"}\n```')])
    v = judge_key_fact(key_fact="某关键事实", report_text="报告。", client=client)
    assert v["covered"] is False


def test_judge_key_fact_parse_failure_counts_not_covered():
    bad = LLMResponse(content="散文")
    client = FakeGLMClient([bad, bad, bad])
    v = judge_key_fact(key_fact="某关键事实", report_text="报告。", client=client)
    assert v["covered"] is False
    assert v["parse_failed"] is True
```

- [ ] **Step 2: 跑测试,确认失败**

Run: `python -m pytest tests/test_judge.py -q`
Expected: FAIL(`ModuleNotFoundError: No module named 'eval.judge'`)。

- [ ] **Step 3: 写最小实现 `eval/judge.py`**

```python
"""GLM 逐条裁判(spec §4.2)。封装 GLMClient,小输出(一个 verdict),坏 JSON 清洗兜底。

两类裁判:
- judge_finding: 判 excerpt 是否支撑 claim(support)+ 来源是否真实(source_real)
- judge_key_fact: 判报告全文是否覆盖某关键事实(covered)

输出不可解析时重试(parse_retries 次);最终失败按 spec §6 保守计为
unsupported / not covered 并打 parse_failed=True。瞬时网络错误的重试由
GLMClient(OpenAICompatClient)内部 retry_with_backoff 负责,本模块不重复。
"""
from core.json_utils import clean_json, json_balanced_substring
from core.robust import safe_parse_arguments
from llm.base import LLMResponse

_FINDING_SYSTEM = """你是严格的来源核查裁判。给定一条研究发现的论断(claim)、其引用的原文摘录(excerpt)、来源链接(source_url),判定两件事:
1. support: excerpt 是否支撑 claim?取值 "supported"(明确支撑)/ "partial"(部分支撑或间接)/ "unsupported"(不支撑或无关)。
2. source_real: source_url 是否像真实、相关、可达的来源(非编造 / 非死链 / 非无关)?布尔。

仅输出一行 JSON,不要任何解释或 markdown:
{"support": "supported" | "partial" | "unsupported", "source_real": true | false, "reason": "一句依据"}"""

_KEYFACT_SYSTEM = """你是严格的覆盖度裁判。给定一个关键事实(key_fact)与一份研究报告全文(report),判定报告是否覆盖该关键事实(明确陈述或可直接推出)。
仅输出一行 JSON,不要任何解释或 markdown:
{"covered": true | false, "reason": "一句依据"}"""

_VALID_SUPPORT = {"supported", "partial", "unsupported"}


def _ask(client, system: str, user: str, parser, parse_retries: int = 2):
    """调一次裁判并解析;输出不可解析时重试,最多 parse_retries+1 次。

    返回 parser 的结果(dict);全部失败返回 None(由调用方兜底成保守 verdict)。
    """
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user}]
    for _ in range(parse_retries + 1):
        resp = client.chat(messages=messages, temperature=0.0, max_tokens=512)
        content = resp.content if isinstance(resp, LLMResponse) else str(resp)
        parsed = parser(content)
        if parsed is not None:
            return parsed
    return None


def _parse_obj(content: str) -> dict | None:
    data = safe_parse_arguments(json_balanced_substring(clean_json(content)))
    return data if isinstance(data, dict) else None


def judge_finding(*, claim: str, excerpt: str, source_url: str,
                  client, parse_retries: int = 2) -> dict:
    user = f"claim: {claim}\nexcerpt: {excerpt}\nsource_url: {source_url}"

    def _parse(content):
        d = _parse_obj(content)
        if not d or d.get("support") not in _VALID_SUPPORT \
                or not isinstance(d.get("source_real"), bool):
            return None
        return {"support": d["support"], "source_real": d["source_real"],
                "reason": str(d.get("reason", ""))}

    v = _ask(client, _FINDING_SYSTEM, user, _parse, parse_retries)
    if v is None:
        return {"support": "unsupported", "source_real": False,
                "reason": "judge output unparseable", "parse_failed": True}
    v["parse_failed"] = False
    return v


def judge_key_fact(*, key_fact: str, report_text: str,
                   client, parse_retries: int = 2) -> dict:
    user = f"key_fact: {key_fact}\n\nreport:\n{report_text}"

    def _parse(content):
        d = _parse_obj(content)
        if not d or not isinstance(d.get("covered"), bool):
            return None
        return {"covered": d["covered"], "reason": str(d.get("reason", ""))}

    v = _ask(client, _KEYFACT_SYSTEM, user, _parse, parse_retries)
    if v is None:
        return {"covered": False, "reason": "judge output unparseable",
                "parse_failed": True}
    v["parse_failed"] = False
    return v
```

- [ ] **Step 4: 跑测试,确认通过**

Run: `python -m pytest tests/test_judge.py -q`
Expected: PASS(8 用例全绿——含围栏/散文/重试/兜底)。

- [ ] **Step 5: Commit**

```bash
git add eval/judge.py tests/test_judge.py
git commit -m "feat(eval): GLM per-finding/per-key-fact judge with bad-JSON fallback"
```

---

### Task 5: `eval/run_eval.py`——`run_one` + `evaluate_question` 装配

`run_one` 经 `build_system` 跑一次系统,返回 `(report, history)`;`evaluate_question` 串起 run_one → extract_findings → 逐条裁判 → compute_question_metrics,返回单题 scorecard。`evaluate_question` 开放 `run_one_fn` 注入缝隙,使 eval 单测聚焦自身装配、不重新驱动整条 agent 链(M2 e2e 已覆盖)。

**Files:**
- Create: `eval/run_eval.py`
- Test: `tests/test_run_eval.py`

- [ ] **Step 1: 写失败测试 `tests/test_run_eval.py`(本步覆盖装配 + report=None 分支)**

```python
from core.schemas import Report, ReportSection
from llm.base import LLMResponse

from eval.run_eval import evaluate_question, report_to_text


class FakeGLMClient:
    def __init__(self, responses):
        self._r = list(responses)

    def chat(self, **kw):
        return self._r.pop(0)


def _canned_history_with_findings():
    return [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "1", "type": "function",
             "function": {"name": "dispatch_research",
                          "arguments": '{"sub_questions":["q1"]}'}}]},
        {"role": "tool", "tool_call_id": "1",
         "content": '{"findings":[{"id":"f1","claim":"投机解码加速推理",'
                    '"source_url":"https://x","excerpt":"摘录"}]}'},
    ]


def _canned_report():
    return Report(sections=[ReportSection(heading="结论", content="投机解码加速推理。")])


def test_report_to_text_joins_sections():
    txt = report_to_text(_canned_report())
    assert "结论" in txt and "投机解码加速推理" in txt


def test_evaluate_question_success_path():
    # 1 条 finding → 1 次 finding 裁判;1 个 key_fact → 1 次 key_fact 裁判
    judge = FakeGLMClient([
        LLMResponse(content='{"support":"supported","source_real":true,"reason":""}'),
        LLMResponse(content='{"covered":true,"reason":""}'),
    ])
    item = {"id": "q1", "question": "Q", "key_facts": ["投机解码加速推理"]}
    sc = evaluate_question(item, cfg=None, judge_client=judge,
                           run_one_fn=lambda q: (_canned_report(),
                                                  _canned_history_with_findings()))
    assert sc["id"] == "q1"
    assert sc["success"] is True
    assert sc["failed"] is None
    assert sc["n_findings"] == 1
    assert sc["grounding"] == 1.0
    assert sc["citation"] == 1.0
    assert sc["coverage"] == 1.0
    assert sc["hallucination"] == 0.0


def test_evaluate_question_report_none_marks_failed():
    judge = FakeGLMClient([])  # 不应被调用
    item = {"id": "q2", "question": "Q", "key_facts": ["x"]}
    sc = evaluate_question(item, cfg=None, judge_client=judge,
                           run_one_fn=lambda q: (None, []))
    assert sc["success"] is False
    assert sc["failed"] == "no_report"
    assert sc["grounding"] is None
    # 裁判绝未被调用
    assert judge._r == []


def test_evaluate_question_zero_findings_coverage_only():
    # 有报告但抽不到 findings(N=0):Grounding 不可用,覆盖率仍可算
    judge = FakeGLMClient([
        LLMResponse(content='{"covered":false,"reason":"未提及"}'),
    ])
    item = {"id": "q3", "question": "Q", "key_facts": ["某未覆盖事实"]}
    sc = evaluate_question(item, cfg=None, judge_client=judge,
                           run_one_fn=lambda q: (_canned_report(), []))  # 空 history
    assert sc["success"] is True
    assert sc["n_findings"] == 0
    assert sc["grounding"] is None
    assert sc["grounding_available"] is False
    assert sc["coverage"] == 0.0
```

- [ ] **Step 2: 跑测试,确认失败**

Run: `python -m pytest tests/test_run_eval.py -q`
Expected: FAIL(`ModuleNotFoundError: No module named 'eval.run_eval'`)。

- [ ] **Step 3: 写最小实现 `eval/run_eval.py`(本步只含 run_one / report_to_text / evaluate_question;CLI 与 load_benchmark 在 Task 6 加)**

```python
"""评估编排 + CLI(spec §4.4)。逐题:跑系统 → 抽 trace → 逐条裁判 → 算分 → scorecard。

零侵入:只消费 build_system 的产物(get_report() 的 Report、loop.run() 的 history)。
"""
from agents.system import build_system
from core.schemas import Report

from .judge import judge_finding, judge_key_fact
from .metrics import compute_question_metrics
from .trace import extract_findings


def report_to_text(report: Report) -> str:
    """把 Report 拼成裁判可读的纯文本(heading + content)。"""
    parts = []
    for sec in report.sections:
        parts.append(f"## {sec.heading}\n{sec.content}")
    return "\n\n".join(parts)


def run_one(question: str, cfg, *, client=None, search_client=None):
    """跑一次系统,返回 (report, history)。report 可能为 None。"""
    loop, get_report = build_system(cfg, client=client, search_client=search_client)
    result = loop.run(question)
    return get_report(), result.history


def evaluate_question(item: dict, cfg, *, judge_client,
                      client=None, search_client=None, run_one_fn=None):
    """单题评估 → scorecard。

    run_one_fn 可注入(测试用,跳过真实 agent 链);为 None 时用真实 run_one。
    """
    question = item["question"]
    runner = run_one_fn or (lambda q: run_one(q, cfg, client=client,
                                              search_client=search_client))
    report, history = runner(question)

    if report is None:
        return {"id": item["id"], "question": question,
                **compute_question_metrics([], [], report_produced=False)}

    findings = extract_findings(history)
    finding_verdicts = [
        judge_finding(claim=f.get("claim", ""), excerpt=f.get("excerpt", ""),
                      source_url=f.get("source_url", ""), client=judge_client)
        for f in findings
    ]
    text = report_to_text(report)
    key_fact_verdicts = [
        judge_key_fact(key_fact=kf, report_text=text, client=judge_client)
        for kf in item.get("key_facts", [])
    ]
    return {"id": item["id"], "question": question,
            **compute_question_metrics(finding_verdicts, key_fact_verdicts,
                                       report_produced=True)}
```

- [ ] **Step 4: 跑测试,确认通过**

Run: `python -m pytest tests/test_run_eval.py -q`
Expected: PASS(4 用例全绿)。

- [ ] **Step 5: Commit**

```bash
git add eval/run_eval.py tests/test_run_eval.py
git commit -m "feat(eval): per-question evaluation wiring (run_one + judge + metrics)"
```

---

### Task 6: `eval/run_eval.py`——`load_benchmark` + `main(argv)` CLI

题库加载(校验 `{id,question,key_facts}`,跳过 `_` 前缀模板与非法题)、CLI(argparse:`--limit`/`--only`/`--benchmark`/`--results`)、逐题评估写 `eval/results/<id>.json`、`aggregate` 汇总写 `scorecard.json`、打印人类可读摘要。`main` 开放 `cfg`/`judge_client`/`run_one_fn`/`benchmark_dir`/`results_dir` 注入,使端到端装配可全 mock 测。

**Files:**
- Modify: `eval/run_eval.py`(在 Task 5 基础上追加 `load_benchmark` / `_print_summary` / `main` + `if __name__` guard + 新 import)
- Modify: `tests/test_run_eval.py`(追加 CLI / load_benchmark 测试)

- [ ] **Step 1: 在 `tests/test_run_eval.py` 追加 CLI / load_benchmark 失败测试**

在文件顶部 import 区追加:

```python
import json

from core.config import Config
from eval.run_eval import load_benchmark, main
```

在文件末尾追加:

```python
# ---------- load_benchmark ----------

def test_load_benchmark_loads_valid_skips_template_and_invalid(tmp_path):
    (tmp_path / "q1.yaml").write_text(
        "id: q1\nquestion: Q\nkey_facts:\n  - 事实\n", encoding="utf-8")
    # _ 前缀模板跳过
    (tmp_path / "_template.yaml").write_text(
        "id: tpl\nquestion: Q\nkey_facts:\n  - 事实\n", encoding="utf-8")
    # 缺 key_facts → 非法,跳过
    (tmp_path / "bad.yaml").write_text("id: bad\nquestion: Q\n", encoding="utf-8")
    items = load_benchmark(tmp_path)
    assert [it["id"] for it in items] == ["q1"]
    assert items[0]["key_facts"] == ["事实"]


# ---------- main CLI ----------

def _bench_with(tmp_path, ids):
    bench = tmp_path / "bench"
    bench.mkdir()
    for i in ids:
        (bench / f"{i}.yaml").write_text(
            f"id: {i}\nquestion: Q{i}\nkey_facts:\n  - 事实\n", encoding="utf-8")
    return bench


def _fake_run_one(question):
    report = Report(sections=[ReportSection(heading="H", content="C")])
    history = [{"role": "tool", "content":
                '{"findings":[{"id":"f1","claim":"c","source_url":"https://x"}]}'}]
    return report, history


def test_main_runs_and_writes_scorecard(tmp_path):
    bench = _bench_with(tmp_path, ["q1"])
    out = tmp_path / "out"
    judge = FakeGLMClient([
        LLMResponse(content='{"support":"supported","source_real":true,"reason":""}'),
        LLMResponse(content='{"covered":true,"reason":""}'),
    ])
    cfg = Config({"thresholds": {"grounding_min": 0.85}})
    rc = main(["--benchmark", str(bench), "--results", str(out)],
              run_one_fn=_fake_run_one, judge_client=judge, cfg=cfg)
    assert rc == 0
    assert (out / "q1.json").exists()
    summary = json.loads((out / "scorecard.json").read_text(encoding="utf-8"))
    assert summary["passed"] is True
    assert summary["mean_grounding"] == 1.0
    assert summary["n_questions"] == 1


def test_main_limit_caps_question_count(tmp_path):
    bench = _bench_with(tmp_path, ["q0", "q1", "q2"])
    out = tmp_path / "out"
    judge = FakeGLMClient([  # 只够 1 题:1 finding + 1 key_fact
        LLMResponse(content='{"support":"supported","source_real":true,"reason":""}'),
        LLMResponse(content='{"covered":true,"reason":""}'),
    ])
    cfg = Config({"thresholds": {"grounding_min": 0.85}})
    rc = main(["--benchmark", str(bench), "--results", str(out), "--limit", "1"],
              run_one_fn=_fake_run_one, judge_client=judge, cfg=cfg)
    assert rc == 0
    assert (out / "q0.json").exists()
    assert not (out / "q1.json").exists()


def test_main_no_items_prints_usage(tmp_path, capsys):
    empty = tmp_path / "empty"
    empty.mkdir()
    cfg = Config({"thresholds": {"grounding_min": 0.85}})
    rc = main(["--benchmark", str(empty), "--results", str(tmp_path / "out")], cfg=cfg)
    assert rc == 1
    assert "用法" in capsys.readouterr().err
```

- [ ] **Step 2: 跑测试,确认失败**

Run: `python -m pytest tests/test_run_eval.py -q`
Expected: FAIL(`ImportError: cannot import name 'load_benchmark'`)。

- [ ] **Step 3: 在 `eval/run_eval.py` 顶部追加 import,文件末尾追加 `load_benchmark` / `_print_summary` / `main` / guard**

顶部 import 区(在现有 import 之后)追加:

```python
import argparse
import json
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

from core.config import load_config
from llm.glm_client import GLMClient

from .metrics import aggregate

_EVAL_DIR = Path(__file__).resolve().parent
```

文件末尾追加:

```python
def load_benchmark(benchmark_dir) -> list[dict]:
    """加载题库目录下所有 <id>.yaml(跳过 _ 前缀模板与非法题)。

    每题校验:{id:str, question:str, key_facts:list[str] 非空}。非法题跳过并告警,不中断。
    """
    out = []
    for p in sorted(Path(benchmark_dir).glob("*.yaml")):
        if p.name.startswith("_"):
            continue
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception:
            print(f"[eval] 跳过无法解析的题:{p.name}", file=sys.stderr)
            continue
        if not (isinstance(data, dict)
                and isinstance(data.get("id"), str) and data["id"]
                and isinstance(data.get("question"), str) and data["question"]
                and isinstance(data.get("key_facts"), list) and data["key_facts"]
                and all(isinstance(x, str) for x in data["key_facts"])):
            print(f"[eval] 跳过非法题(缺 id/question/key_facts):{p.name}", file=sys.stderr)
            continue
        out.append({"id": data["id"], "question": data["question"],
                    "key_facts": data["key_facts"]})
    return out


def _print_summary(summary: dict) -> None:
    g = summary["mean_grounding"]
    gstr = f"{g:.3f}" if g is not None else "N/A(无可用题)"
    flag = "✓ 过线" if summary["passed"] else "✗ 未过线"
    print(f"\n=== 质量 scorecard ===")
    print(f"题数 {summary['n_questions']}  成功 {summary['n_success']}  "
          f"成功率 {summary['success_rate']:.2%}")
    print(f"Grounding 均值 {gstr} (门槛 {summary['grounding_min']},"
          f"有效题 {summary['grounding_questions']})  {flag}")
    for k, label in (("mean_citation", "引用准确率"), ("mean_coverage", "覆盖率"),
                     ("mean_hallucination", "幻觉率")):
        v = summary[k]
        print(f"{label} 均值 " + (f"{v:.3f}" if v is not None else "N/A"))
    if summary["total_judge_parse_failures"]:
        print(f"⚠ 裁判输出不可解析累计 {summary['total_judge_parse_failures']} 次")


def main(argv=None, *, benchmark_dir=None, run_one_fn=None,
         judge_client=None, cfg=None, results_dir=None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(
        prog="python -m eval.run_eval",
        description="质量评估:跑基准集 → GLM 逐条裁判 → scorecard + 上线门槛")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 题")
    parser.add_argument("--only", default=None, help="只跑指定 id 的题")
    parser.add_argument("--benchmark", default=None, help="题库目录(默认 eval/benchmark)")
    parser.add_argument("--results", default=None, help="结果输出目录(默认 eval/results)")
    args = parser.parse_args(argv)

    cfg = cfg or load_config()
    bench_dir = args.benchmark or benchmark_dir or (_EVAL_DIR / "benchmark")
    res_dir = Path(args.results or results_dir or (_EVAL_DIR / "results"))

    items = load_benchmark(bench_dir)
    if args.only:
        items = [it for it in items if it["id"] == args.only]
    if args.limit is not None:
        items = items[:args.limit]
    if not items:
        print("用法: python -m eval.run_eval [--limit N] [--only ID] "
              "[--benchmark DIR] [--results DIR]\n"
              "题库目录无可用题(检查 eval/benchmark/*.yaml,至少一道 {id,question,key_facts})。",
              file=sys.stderr)
        return 1

    judge_client = judge_client or GLMClient()
    res_dir.mkdir(parents=True, exist_ok=True)
    scorecards = []
    for it in items:
        sc = evaluate_question(it, cfg, judge_client=judge_client, run_one_fn=run_one_fn)
        (res_dir / f"{it['id']}.json").write_text(
            json.dumps(sc, ensure_ascii=False, indent=2), encoding="utf-8")
        scorecards.append(sc)

    summary = aggregate(scorecards, cfg["thresholds"]["grounding_min"])
    (res_dir / "scorecard.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _print_summary(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 跑测试,确认通过**

Run: `python -m pytest tests/test_run_eval.py -q`
Expected: PASS(8 用例全绿:Task 5 的 4 个 + 本 Task 的 4 个)。

- [ ] **Step 5: Commit**

```bash
git add eval/run_eval.py tests/test_run_eval.py
git commit -m "feat(eval): benchmark loader + CLI (scorecard, threshold, results dump)"
```

---

### Task 7: 题库格式 + 模板 + 1 道真实种子题

立 `eval/benchmark/` 目录:格式说明 README、单题模板 `_template.yaml`(runner 跳过 `_` 前缀)、1 道真实种子题 `q001-speculative-decoding.yaml`(项目贯穿示例)。本 Task 无代码,纯文档/数据。

**Files:**
- Create: `eval/benchmark/README.md`
- Create: `eval/benchmark/_template.yaml`
- Create: `eval/benchmark/q001-speculative-decoding.yaml`

- [ ] **Step 1: 创建 `eval/benchmark/README.md`**

````markdown
# 基准题库

每道题一个 `.yaml` 文件,供 `python -m eval.run_eval` 逐题深研 + GLM 裁判 + 算 4 质量指标。

## 格式

```yaml
id: q001                          # 必填,唯一,用作结果文件名
question: "一句话研究问题"          # 必填,系统将深研它
key_facts:                         # 必填,非空,每条是可被报告覆盖的可核查事实
  - "关键事实 1"
  - "关键事实 2"
```

## 规则

- 文件名以 `_` 开头的(如 `_template.yaml`)会被 runner **跳过**——用作模板,不计入评估。
- 缺 `id` / `question` / `key_facts`(或 key_facts 为空 / 含非字符串)的题会被 runner **跳过并告警**,不中断全集。
- `key_facts` 是**人写的金标准**:其质量直接决定覆盖率指标上限,务必写具体、可核查的事实,而非泛泛的方向。
- 建议种子题控制在 5 道左右,以控制真跑成本(每题 = 一次完整研究 + N+M 次 GLM 裁判)。

## 跑

```bash
python -m eval.run_eval                 # 跑全集
python -m eval.run_eval --limit 1       # 只跑第一题
python -m eval.run_eval --only q001     # 只跑指定 id
```

结果写到 `eval/results/<id>.json` 与 `eval/results/scorecard.json`(已 gitignore)。
````

- [ ] **Step 2: 创建 `eval/benchmark/_template.yaml`**

```yaml
# 单题模板:复制本文件改名 <id>.yaml,填好后即是一道基准题。
# 文件名以 _ 开头会被 runner 跳过,故本文件不参与评估。
id: q000
question: "在这里写一句话研究问题(系统将深研它)"
key_facts:
  - "关键事实 1:具体、可核查、可被报告覆盖"
  - "关键事实 2:由人写的金标准,决定覆盖率上限"
```

- [ ] **Step 3: 创建真实种子题 `eval/benchmark/q001-speculative-decoding.yaml`**

```yaml
id: q001
question: "DeepSeek-V3 如何用投机解码(speculative decoding)加速推理?"
key_facts:
  - "投机解码用一个小型草稿模型生成候选 token,再由目标大模型批量校验"
  - "DeepSeek-V3 在其技术报告中报告了投机解码带来的解码加速"
```

> **真跑前请人工核实这两条 key_facts:** 它们是覆盖率的金标准。本项目贯穿示例围绕 DeepSeek 投机解码,上述两条为合理起点;真跑前请对照 DeepSeek-V3 公开技术报告确认措辞,并可继续补充更多关键事实以提高覆盖率判别力(spec §8:金标准质量决定覆盖率上限)。

- [ ] **Step 4: 跑 runner 用种子题冒烟(全 mock,验证题库被正确加载)**

用注入的 `run_one_fn` + FakeGLM 跑一次,确认 `load_benchmark` 收到 `q001`(不烧 API):

Run(在仓库根):
```bash
python -c "from eval.run_eval import load_benchmark; \
print([i['id'] for i in load_benchmark('eval/benchmark')])"
```
Expected: 输出 `['q001']`(`_template.yaml` 被跳过)。

- [ ] **Step 5: Commit**

```bash
git add eval/benchmark/README.md eval/benchmark/_template.yaml eval/benchmark/q001-speculative-decoding.yaml
git commit -m "docs(eval): benchmark format + template + seed question"
```

---

### Task 8: 收尾——全量测试 + 手动真跑出真实 scorecard(DoD)

最后确认全绿,并用真 GLM + 真系统手动跑一次,肉眼确认 scorecard 合理(spec §7 DoD)。本 Task 是验证步骤,不写新代码。

- [ ] **Step 1: 全量测试全绿**

Run: `python -m pytest -q`
Expected: PASS(M1+M2 共 96 保持 + M3 新增:`test_trace` 6 + `test_metrics` 12 + `test_judge` 8 + `test_run_eval` 8 = 34 个 → 合计 130)。

> 若数量对不上,先核各自 `pytest tests/test_<x>.py -q` 的用例数再定位。

- [ ] **Step 2: 确认 .env 有真 key**

确保仓库根 `.env`(已 gitignore)含:
```
DEEPSEEK_API_KEY=...    # 系统生成模型
BOCHA_API_KEY=...       # web 检索
GLM_API_KEY=...         # 裁判模型
```
缺一不可:DEEPSEEK/BOCHA 跑研究链路,GLM 跑裁判。

- [ ] **Step 3: 单题真跑(控成本先用 --limit 1)**

Run: `python -m eval.run_eval --limit 1`
Expected:
- stderr 打印研究进度(Orchestrator/Researcher/Verifier/Writer,因 `RESEARCH_QUIET` 未设)。
- stdout 打印 `=== 质量 scorecard ===`:题数 1、成功率、Grounding 均值、过线/未过线、引用/覆盖/幻觉均值。
- 生成 `eval/results/q001.json`(逐题)与 `eval/results/scorecard.json`(汇总)。
- 肉眼确认:Grounding 等有合理数值(非全 N/A),门槛判定与数值一致。

- [ ] **Step 4: (可选)跑全集种子题**

种子题扩充到 ~5 道后:`python -m eval.run_eval`,确认汇总 scorecard 的平均 Grounding 与门槛判定合理。

- [ ] **Step 5: 提交收尾(若有结果需留存,单独 force-add;否则 results 已 gitignore 不提交)**

```bash
git status   # 确认 eval/results/ 未被加入(已 gitignore)
# 如需留存真实 scorecard 供回看:
# git add -f eval/results/scorecard.json
# git commit -m "chore(eval): record real M3 quality-MVP scorecard"
```

---

## Self-Review(本 plan 作者自检)

**1. Spec 覆盖(逐条对照 spec):**
- spec §1「做」trace 抽取 + GLM 逐条裁判 + 4 指标 + runner/CLI + 题库 + 门槛 → Task 2(trace)/ Task 4(judge)/ Task 3(metrics)/ Task 5+6(runner+CLI)/ Task 7(题库)/ Task 3+6(门槛)。✅
- spec §2.2「从 history 抽 findings,零侵入」→ Task 2 `extract_findings` 只消费 history;Task 1 的重构是纯函数提取不改 agent 运行时行为。✅
- spec §3 四指标公式 + N=0 不可用 + 门槛 → Task 3 `compute_question_metrics`/`aggregate` + `test_metrics` 逐公式覆盖。✅
- spec §3.1 引用准确率 finding 层口径 → Task 3 `citation` 按 source_real 计。✅
- spec §4 组件职责(trace/judge/metrics/run_eval)→ Task 2/4/3/5+6 一一对应,文件结构与 spec §4 一致。✅
- spec §5 测试矩阵(4 文件全 mock + 收尾手动真跑)→ Task 2/3/4/5+6 各自测试 + Task 8 真跑。✅
- spec §6 错误处理(report=None / N=0 / 裁判不可解析 / 非法题)→ Task 5(report=None)/ Task 3+5(N=0)/ Task 4(parse_failed 兜底)+ Task 3 计数 / Task 6 `load_benchmark` 跳过非法题。✅
- spec §7 DoD 7 条 → Task 8 逐条对应(零侵入 / 全绿 / metrics 公式 / judge 坏 JSON / 真跑 / 题库+种子 / 可进 M3 后续)。✅

**2. Placeholder 扫描:** 无 TBD/TODO;每步含完整代码或确切命令 + 预期输出;种子题 key_facts 为可运行真实内容(非占位),并标注真跑前人工核实。✅

**3. 类型 / 名称一致性:**
- verdict 字段(`support`/`source_real`/`covered`/`parse_failed`)在 Task 4(产出)、Task 3(消费)、数据契约三处一致。✅
- scorecard 字段(`grounding`/`citation`/`coverage`/`hallucination`/`grounding_available`/`judge_parse_failures`/`success`/`failed`)在 Task 3(产出)、Task 5(evaluate_question 合并 id/question)、Task 6(aggregate 消费 + 写 JSON)一致。✅
- `extract_findings` / `judge_finding` / `judge_key_fact` / `compute_question_metrics` / `aggregate` / `run_one` / `evaluate_question` / `load_benchmark` / `report_to_text` / `main` 签名跨 Task 一致(`run_one_fn` 缝隙在 Task 5 定义、Task 6 CLI 注入)。✅
- 重构命名:`json_balanced_substring`(Task 1 新公开名)在 Task 1(dispatch 改调用)+ Task 2(trace)+ Task 4(judge `_parse_obj`)三处使用一致。✅

**4. 风险复核:**
- Task 1 重构有 `test_dispatch.py`(10 用例)+ 全量 96 守护,Step 4 显式跑两遍证零回归。✅
- eval 测试不烧 API:Task 5/6 用 `run_one_fn` + `FakeGLMClient` + 注入 `cfg`,真 GLMClient 仅 Task 8 手动真跑时构造(且在 items 非空检查之后,空题库不会触发)。✅
- `python -m eval.run_eval` 可运行:eval 有 `__init__.py`,run_eval 有 `__main__` guard,相对 import(`from .judge` 等)在 `-m` 下成立。✅
