# M2 回修:Researcher 收敛修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 researcher 在并发派发场景下稳定收敛输出 findings,解锁 eval 5 题的 Grounding 指标(不再 `no_report` / 0 findings)。

**Architecture:** 三处零侵入改动,不碰 `core/agent_loop.py`(M2/M3 刻意未动的核心原语):① `RESEARCHER_PROMPT` 加收敛硬预算(检索次数上限 + 收敛触发 + 空页跳过);② `tools/web_read.py` 的 `fetch_text` 抓空时返回诊断文本而非空串(给模型确定性信号,闭合重试循环);③ `config.yaml` 新增 `researcher_max_steps: 6` + `agents/system.py` 生产链路注入(prompt 让模型主动 4-5 步收尾,6 步是硬护栏兜底)。全 mock TDD,收尾手动真跑 5 题。

**Tech Stack:** Python 3.11+、pydantic v2、pyyaml、pytest。复用 M1/M2 的 `core/`(config/agent_loop)、`agents/`(prompts/system/researcher)、`tools/`(web_read)。

**对应 spec:** [docs/superpowers/specs/2026-08-10-m2-researcher-convergence-fix-design.md](../specs/2026-08-10-m2-researcher-convergence-fix-design.md)

---

## 里程碑边界(本 plan 范围)

**做:** `tools/web_read.py`(空失败诊断)+ `agents/prompts.py`(RESEARCHER_PROMPT 收敛预算)+ `config.yaml` + `agents/system.py`(researcher_max_steps 注入)+ 对应测试。

**不做:** 改 AgentLoop / verifier / orchestrator;加 readability fallback;单独修并发限流。

**验收(对应 spec §3 DoD):**
- `pytest -q` 全绿(141 现有 + 新增,全 mock 不烧 API)
- `test_web_read` 证明空失败返回诊断文本;`test_prompts` 证明收敛预算关键词;`test_system` 证明 researcher 注入 `max_steps=6`、orchestrator 仍 12
- 手动 `python -m eval.run_eval`(5 题):不再 `no_report`,≥3/5 题 findings>0,平均 Grounding 非 null

---

## File Structure

| 文件 | 职责 | 本 plan 改动 | Task |
|---|---|---|---|
| `tools/web_read.py` | trafilatura 正文提取 | `fetch_text` 抓空返回诊断文本(非空串) | Task 1 |
| `tests/test_web_read.py` | web_read 单测 | 2 个空失败断言由 `== ""` 改 `startswith("[抓取失败]")` | Task 1 |
| `agents/prompts.py` | 四 agent system prompt | RESEARCHER_PROMPT 加收敛硬预算 | Task 2 |
| `tests/test_prompts.py` | prompt 断言 | 加 researcher 收敛预算关键词断言 | Task 2 |
| `config.yaml` | 单一配置源 | `guards` 加 `researcher_max_steps: 6` | Task 3 |
| `agents/system.py` | 系统组装工厂 | `_run_researcher` 注入 `researcher_max_steps` | Task 3 |
| `tests/test_system.py` | build_system wiring 测 | `_write_cfg` 加字段 + 新增 researcher 注入断言 | Task 3 |
| `scripts/diag_researcher.py` | (Phase 1 诊断脚本,一次性) | 删除清理 | Task 4 |

---

## Task 1: web_read 空失败诊断文本

**Files:**
- Modify: `tests/test_web_read.py`
- Modify: `tools/web_read.py`

- [ ] **Step 1: 改测试断言(让它先 fail)**

打开 `tests/test_web_read.py`,把两个空失败测试的断言由 `== ""` 改为诊断文本。把 `test_fetch_text_empty_when_download_fails` 改名为 `test_fetch_text_diagnostic_when_download_fails`、`test_fetch_text_empty_when_extract_none` 改名为 `test_fetch_text_diagnostic_when_extract_none`:

```python
def test_fetch_text_diagnostic_when_download_fails(monkeypatch):
    monkeypatch.setattr(web_read.trafilatura, "fetch_url", lambda url: None)
    out = web_read.fetch_text("https://example.com/missing")
    assert out.startswith("[抓取失败]")


def test_fetch_text_diagnostic_when_extract_none(monkeypatch):
    monkeypatch.setattr(web_read.trafilatura, "fetch_url", lambda url: "<html></html>")
    monkeypatch.setattr(web_read.trafilatura, "extract",
                        lambda html, with_metadata=False: None)
    out = web_read.fetch_text("https://example.com/x")
    assert out.startswith("[抓取失败]")
```

> `test_fetch_text_happy` 与 `test_fetch_text_no_truncate_when_zero` 不动(有正文路径不变)。

- [ ] **Step 2: 跑测试,确认 fail**

Run: `cd e:/record/Agent && .venv/Scripts/python.exe -m pytest tests/test_web_read.py -v`
Expected: 两个 diagnostic 测试 FAIL(当前 `fetch_text` 仍返回 `""`,`startswith("[抓取失败]")` 不成立)。

- [ ] **Step 3: 改 fetch_text 返回诊断文本**

把 `tools/web_read.py` 整体替换为:

```python
"""trafilatura 本地网页正文提取(纯本地、零网络、零成本)。"""
import trafilatura

# 抓取失败时的诊断文本(而非空串):给 researcher/verifier 明确信号,
# 配合 prompt 的"跳过空页"指令闭合重试循环(M2 回修:web_read 56% 空失败的放大器)。
_FETCH_FAILED = "[抓取失败] 无法提取该页正文,请跳过此 URL"


def fetch_text(url: str, max_chars: int = 8000) -> str:
    """下载并提取正文;下载失败/提取为空返回诊断文本。max_chars>0 时截断防爆上下文;0 表示不截断。"""
    downloaded = trafilatura.fetch_url(url)
    if not downloaded:
        return _FETCH_FAILED
    text = trafilatura.extract(downloaded, with_metadata=False) or ""
    if not text:
        return _FETCH_FAILED
    if max_chars:
        text = text[:max_chars]
    return text
```

- [ ] **Step 4: 跑测试,确认 pass**

Run: `cd e:/record/Agent && .venv/Scripts/python.exe -m pytest tests/test_web_read.py -v`
Expected: 4 个测试全 PASS(2 diagnostic + happy + no_truncate)。

- [ ] **Step 5: Commit**

```bash
cd e:/record/Agent && git add tools/web_read.py tests/test_web_read.py && git commit -m "$(cat <<'EOF'
fix(tools): web_read 抓空返回诊断文本而非空串

闭合 researcher 读到空页的重试循环(诊断显示 56% 国内站点抓空);
配合 prompt 跳过指令,给模型确定性信号。

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: RESEARCHER_PROMPT 收敛硬预算

**Files:**
- Modify: `tests/test_prompts.py`
- Modify: `agents/prompts.py`

- [ ] **Step 1: 加收敛预算断言(让它先 fail)**

在 `tests/test_prompts.py` 末尾追加:

```python
def test_researcher_prompt_has_convergence_budget():
    """M2 回修:researcher prompt 必须含收敛预算(检索上限 + 收敛触发 + 空页跳过)。"""
    assert "最多" in RESEARCHER_PROMPT          # 检索次数上限措辞
    assert "跳过" in RESEARCHER_PROMPT          # 空页处理
    assert "立即" in RESEARCHER_PROMPT          # 收敛触发(读到几篇就停)
```

- [ ] **Step 2: 跑测试,确认 fail**

Run: `cd e:/record/Agent && .venv/Scripts/python.exe -m pytest tests/test_prompts.py::test_researcher_prompt_has_convergence_budget -v`
Expected: FAIL(当前 prompt 无"最多"/"跳过"/"立即")。

- [ ] **Step 3: 重写 RESEARCHER_PROMPT**

在 `agents/prompts.py` 中,把整个 `RESEARCHER_PROMPT = """..."""`(第 21-36 行)替换为:

```python
RESEARCHER_PROMPT = """你是检索者(Researcher),针对单个研究子问题找资料。

可用工具:
- web_search(query): 网页搜索,返回搜索结果列表(标题+URL+摘要)。
- web_read(url): 提取指定 URL 的正文。

工作方式(严格遵守步数预算,果断收敛):
1. 用 web_search 搜该子问题。最多搜 1-2 次:首次搜主问题;仅当结果明显偏题时补搜 1 次更具体的词。
2. 从搜索结果里挑最相关的 2-3 个 URL,用 web_read 读正文。最多读 3 篇。
3. 收敛规则:一旦你读到 2 篇及以上有实质正文的页面,立即停止调用任何工具,基于已读内容提炼 Findings 并输出 JSON。不要为了穷尽所有结果继续搜索或读取。
4. 空页处理:如果 web_read 返回"[抓取失败]",说明该页抓不到正文——直接跳过它,不要重试同一个 URL,也不要换别的 URL 反复试。用已读到的内容即可输出。

铁律:每条 Finding 的 claim 必须来自你 web_read 实际读到的内容,source_url 必须是真实访问过的 URL。绝不允许编造 claim 或来源。

完成后,只输出如下严格 JSON(不要 markdown 代码块、不要任何额外文字):
{"findings": [{"id": "f1", "claim": "结论陈述", "source_url": "https://...", "source_title": "来源标题", "excerpt": "支撑原文摘录", "confidence": 0.0到1.0}]}
id 用 f1、f2... 递增。confidence 是你对这条 claim 被来源支撑程度的自评。"""
```

> ORCHESTRATOR/VERIFIER/WRITER 三个 prompt 不动。

- [ ] **Step 4: 跑测试,确认 pass**

Run: `cd e:/record/Agent && .venv/Scripts/python.exe -m pytest tests/test_prompts.py -v`
Expected: 全 PASS(含新断言 + 现有 `findings`/`web_search`/`web_read` 关键词断言,新 prompt 均保留)。

- [ ] **Step 5: Commit**

```bash
cd e:/record/Agent && git add agents/prompts.py tests/test_prompts.py && git commit -m "$(cat <<'EOF'
fix(agents): RESEARCHER_PROMPT 加收敛硬预算

检索上限(搜1-2/读≤3)+ 收敛触发(读≥2篇即输出)+ 空页跳过;
配合 researcher_max_steps 硬护栏,治贪心不收敛。

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: config 注入 researcher_max_steps

**Files:**
- Modify: `tests/test_system.py`
- Modify: `config.yaml`
- Modify: `agents/system.py`

- [ ] **Step 1: 改测试(让它先 fail)**

(a) 在 `tests/test_system.py` 的 `_write_cfg` 中,把 guards 那行加上 `researcher_max_steps: 6`:

```python
        "guards: {agent_max_steps: 12, research_max_rounds: 3, researcher_max_steps: 6, request_max_retries: 3, request_backoff_base: 1.5}\n"
```

(b) 在 `tests/test_system.py` 末尾追加新测试。需在文件顶部 import 处补 `ToolCall` —— 把第 3 行 `from llm.base import LLMClient, LLMResponse` 改为:

```python
from llm.base import LLMClient, LLMResponse, ToolCall
```

(c) 追加测试:

```python
def test_build_system_injects_researcher_max_steps(tmp_path):
    """researcher 用专用步数(默认 6),orchestrator 仍用 agent_max_steps(12)。"""
    cfg = _write_cfg(tmp_path)
    captured = {}

    class _FakeResearcherLoop:
        def __init__(self, **kw):
            captured.update(kw)
        def run(self, sub_question):
            from core.agent_loop import AgentResult
            return AgentResult(content='{"findings": []}')

    import agents.system as system_mod
    orig_maker = system_mod.make_researcher
    system_mod.make_researcher = _FakeResearcherLoop  # monkeypatch:捕获 max_steps
    try:
        class _FakeClient(LLMClient):
            def __init__(self, responses):
                self._r = list(responses)
            def chat(self, **kw):
                return self._r.pop(0)

        client = _FakeClient([
            LLMResponse(content="", tool_calls=[
                ToolCall(id="1", name="dispatch_research",
                         arguments='{"sub_questions": ["x"]}')]),
            LLMResponse(content="收尾"),  # 第二轮无 tool_call → orchestrator 收敛
        ])
        loop, _ = build_system(cfg, client=client, search_client=object())
        loop.run("问题")
    finally:
        system_mod.make_researcher = orig_maker

    assert captured["max_steps"] == 6        # researcher 注入了专用步数
    assert loop.max_steps == 12              # orchestrator 仍用 agent_max_steps
```

> 说明:`_FakeResearcherLoop` 替身捕获 `make_researcher` 的 kwargs;`_FakeClient` 第一轮触发一次 `dispatch_research`(从而调 `_run_researcher` → 被捕获),第二轮 plain content 让 orchestrator 收敛。无需真 client/真 key(都注入)。

- [ ] **Step 2: 跑测试,确认 fail**

Run: `cd e:/record/Agent && .venv/Scripts/python.exe -m pytest tests/test_system.py::test_build_system_injects_researcher_max_steps -v`
Expected: FAIL —— `_run_researcher` 当前传的是 `max_steps=max_steps`(=12),`captured["max_steps"]` 为 12 而非 6。

- [ ] **Step 3: config.yaml 加字段**

在 `config.yaml` 的 `guards:` 段,`agent_max_steps` 行下方加一行(保持注释风格):

```yaml
guards:
  agent_max_steps: 12              # 单 agent 最大循环步数(防无限循环/成本失控)
  researcher_max_steps: 6          # 单 researcher 步数(M2 回修:诊断显示理想路径 4-5 步,6 步有余量)
  research_max_rounds: 3           # Orchestrator 最大研究轮次(M2 用)
  request_max_retries: 3           # 瞬时错误重试次数
  request_backoff_base: 1.5        # 指数退避基数(秒)
```

- [ ] **Step 4: system.py 注入**

在 `agents/system.py` 的 `build_system` 内,把(第 32-34 行附近)

```python
    max_chars = config["tools"]["web_read"]["max_chars"]
    max_steps = config["guards"]["agent_max_steps"]
    research_max_rounds = config["guards"]["research_max_rounds"]
```

改为(加一行 `researcher_max_steps`):

```python
    max_chars = config["tools"]["web_read"]["max_chars"]
    max_steps = config["guards"]["agent_max_steps"]
    researcher_max_steps = config["guards"].get("researcher_max_steps", max_steps)
    research_max_rounds = config["guards"]["research_max_rounds"]
```

然后在 `_run_researcher` 闭包里,把 `max_steps=max_steps` 改为 `max_steps=researcher_max_steps`:

```python
    def _run_researcher(sub_question):
        progress(f"  [researcher] 检索子问题:{sub_question}")
        result = make_researcher(client=client, search_client=search_client,
                                 max_chars=max_chars, max_steps=researcher_max_steps).run(sub_question)
        content = result.content or ""
        progress(f"  [researcher] 完成 → content {len(content)} 字,前 120 字:{content[:120]!r}")
        return result
```

> `_run_verifier` / `_run_writer` / `make_orchestrator` 的 `max_steps=max_steps` **不动**(仍 12)。

- [ ] **Step 5: 跑测试,确认 pass**

Run: `cd e:/record/Agent && .venv/Scripts/python.exe -m pytest tests/test_system.py -v`
Expected: 全 PASS(新注入断言 + 现有 wiring 断言;`loop.max_steps == 12` 仍成立)。

- [ ] **Step 6: Commit**

```bash
cd e:/record/Agent && git add config.yaml agents/system.py tests/test_system.py && git commit -m "$(cat <<'EOF'
feat(config): researcher 专用步数 researcher_max_steps=6

system.py 生产链路注入;orchestrator/verifier/writer 仍用 agent_max_steps=12。
与 prompt 收敛预算配套(软约束 + 硬护栏)。

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: 全量回归 + 真实冒烟验证

**Files:**
- 删除: `scripts/diag_researcher.py`(Phase 1 一次性诊断脚本,不进仓库)

- [ ] **Step 1: 全量单测回归**

Run: `cd e:/record/Agent && .venv/Scripts/python.exe -m pytest -q`
Expected: 全绿(141 现有 + Task 1/2/3 新增,全 mock 不烧 API)。特别注意 `test_dispatch` / `test_orchestrator_e2e` / `test_researcher` 不受影响。

- [ ] **Step 2: 清理诊断脚本**

```bash
cd e:/record/Agent && rm -f scripts/diag_researcher.py && rmdir scripts 2>/dev/null || true
```

> 若 `scripts/` 目录由此空了,一并删掉;删不掉(非空)无妨。

- [ ] **Step 3: 真实冒烟(5 题,真调 DeepSeek + 博查 + GLM-5.2)**

Run: `cd e:/record/Agent && .venv/Scripts/python.exe -m eval.run_eval 2>&1 | tee /tmp/eval_smoke.log`
(Windows Git Bash 下 `/tmp` 可用;或把日志重定向到仓库外临时文件。)

Expected(对应 spec §3.2 成功标准):
- 不再出现 `failed:"no_report"` 为主的失败
- ≥3/5 题 `n_findings > 0`
- `scorecard.json` 的 `mean_grounding` 为**非 null 数字**

打开 `eval/results/scorecard.json` 与各 `eval/results/q*.json` 核对。把真实数字回填给用户(这是后续优化的输入)。

- [ ] **Step 4: 如真跑产物有变更,按需 commit**

`eval/results/` 下的 JSON 会被真跑覆盖。若需留存本次基线:

```bash
cd e:/record/Agent && git add eval/results/ && git commit -m "$(cat <<'EOF'
chore(eval): M2 researcher 收敛修复后的 5 题真实冒烟产物

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

> 若策略是不进仓库(留本地),跳过此步。诊断脚本删除已在 Step 2 完成,无需额外 commit(它从未被 add)。

---

## Self-Review(写完后自查)

**1. Spec 覆盖:**
- spec §2.1 RESEARCHER_PROMPT 收敛预算 → Task 2 ✓
- spec §2.2 web_read 诊断文本 → Task 1 ✓
- spec §2.3 config researcher_max_steps 注入 → Task 3 ✓
- spec §3.1 单测 ①②③ → Task 2(prompts)/ Task 1(web_read)/ Task 3(system)✓
- spec §3.2 真实冒烟 → Task 4 ✓
- spec §1 "不做" 边界(AgentLoop/verifier/readability/并发)→ plan 全程未碰 ✓

**2. Placeholder 扫描:** 无 TBD/TODO;每步含完整代码;commit message 完整。✓

**3. 类型/命名一致性:**
- `_FETCH_FAILED` 在 `web_read.py` 定义、`test_web_read` 用 `startswith("[抓取失败]")` 断言(不绑私有名,稳)✓
- `researcher_max_steps` 在 config/system/test 三处一致 ✓
- `make_researcher(*, client, search_client, max_chars, max_steps)` 签名不变,Task 3 仅改传入值 ✓
- `test_system` 用 `ToolCall`(Task 3 Step 1 补 import)✓

无遗漏,plan 可执行。
