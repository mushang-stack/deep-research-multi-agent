# M2 回修:Researcher 收敛修复 — 设计文档(Spec)

- **日期:** 2026-08-10
- **作者:** 王宇博
- **状态:** Draft,待 review
- **对应整体 spec:** [2026-08-08-deep-research-multi-agent-design.md](2026-08-08-deep-research-multi-agent-design.md) §3(Researcher)
- **前置里程碑:** M1 + M2 合并 main;M3 质量 MVP 合并 main(`aa2d9e6`)。M3 真跑把本问题量化为硬阻塞。

---

## 0. 目标与背景

M3 真实冒烟(`python -m eval.run_eval --limit 1`)如实暴露:M2 已知次要问题"researcher 收敛率低"被 M3 量化成**拿不到任何 Grounding 数字**的硬阻塞——三轮研究全部 0 findings → `failed:"no_report"` → 所有质量指标 null。

### 0.1 根因取证(2026-08-10 真跑单 researcher 诊断)

为定位根因,真跑 1 个 researcher(子问题"什么是投机解码?基本原理是什么?",`max_steps=12`),复刻 AgentLoop 逐 step 打印工具调用与结局。结论:**researcher 能收敛,但贪心低效**,且暴露一个记忆未充分记录的放大器。

| 信号 | 证据 |
|---|---|
| researcher **能收敛**(非完全不收敛) | 单跑 step 8 收敛输出 JSON,9 条 finding 解析成功 |
| 但**贪心低效**,余量小 | 3 次 `web_search` + **9 次 `web_read`** = 12 个 tool_call / 7 个 LLM 轮次,离 `max_steps=12`(轮)仅余 5 轮 |
| **放大器①:web_read 高失败率** | 9 次 `web_read` 里 **5 次返回 0 字符(56% 失败)**:知乎×2、infoq、cnblogs、csdb blog 抓不下来(trafilatura 对国内站点成功率低)。模型读到空页倾向继续找别的页 |
| **放大器②:并发放大**(基于推理,未单独取证) | M3 冒烟 8 个并发全军覆没 vs 本次单跑成功——共享 client + 并发抓网页会放大外部失败率,把贪心的 researcher 推过 `max_steps` → `Escalation` → `dispatch_research` 单点容错 → 0 findings |

### 0.2 复合根因(不是单纯 prompt)

1. **主因**:`RESEARCHER_PROMPT` 缺收敛预算——"对最相关的几个结果用 web_read"中"几个"无上限,无"读 N 篇就立即输出"的硬约束 → 贪心读、余量小。
2. **放大器①**:`web_read`(trafilatura)对国内站点抓取成功率低,空页触发更多重试。
3. **放大器②**:`dispatch_research` 并发跑多 researcher,共享 client + 并发抓网页放大失败率。

---

## 1. 里程碑边界

**做(3 处改动,零侵入 AgentLoop):**
- `RESEARCHER_PROMPT` 收敛硬预算(`agents/prompts.py`)
- `web_read` 空失败诊断文本(`tools/web_read.py`)
- `config` 新增 `researcher_max_steps` + 生产链路注入(`config.yaml` + `agents/system.py`)

**不做(留后续 / 范围外):**
- ❌ 改 `core/agent_loop.py`(M2/M3 刻意未动的核心原语;预算感知靠 prompt + config 步数实现,不侵入 loop)
- ❌ 改 verifier / orchestrator(本次只修 researcher;verifier 覆盖不全问题是独立的下次)
- ❌ 加 readability-lxml 等 fallback 提取器(56% 空失败的根治留 M2 二期;本次靠诊断文本 + prompt 跳过 + search snippet 兜底)
- ❌ 单独修并发限流(降 researcher 步数已间接降压;并发死法的精确权重靠 eval 重跑验证)

---

## 2. 改动详述

### 2.1 RESEARCHER_PROMPT 收敛硬预算(`agents/prompts.py`)

把"工作方式"从开放描述改为带**明确预算 + 收敛触发 + 空页处理**:

- **检索预算**:`web_search` 最多 1-2 次(首次搜主问题,必要时补搜 1 次特定角度);`web_read` 最多读 3 篇正文,优先搜索结果中最相关的 2-3 个 URL。
- **收敛触发(关键)**:一旦读到 **≥2 篇有实质正文**的页面,立即停止调用任何工具、输出 JSON——不要为穷尽所有结果而继续 search/read。
- **空页处理**:`web_read` 返回"[抓取失败]"时,该页无内容,**跳过、不重试同 URL、不反复换页**,基于已读到的内容输出。

保留原铁律(claim 必须来自 web_read 实读内容、JSON 格式、id 递增、confidence 自评)。

**为什么是软约束 + 硬护栏配套**:纯 prompt 是软约束,模型不一定严格遵守"读 3 篇"。所以 prompt(让模型主动 4-5 步收尾)+ `researcher_max_steps=6`(硬护栏兜底)两者配套——即使模型想多读,步数也会卡住,而 prompt 让它在卡住前就主动收尾,避免撞 `Escalation`。

### 2.2 web_read 空失败诊断(`tools/web_read.py`)

`fetch_text` 抓空时(下载失败 / extract 返回 None / 空正文)返回诊断文本:

```
"[抓取失败] 无法提取该页正文,请跳过此 URL"
```

而非空串 `""`。

- 放在 `fetch_text` 层 → researcher + verifier 都受益(verifier 的 `web_read` 核对也会遇到同样的空页)。
- **为什么返回文本而非空串**:空串让模型困惑(是没读到?还是读到了空内容?),倾向重试或换页;明确诊断文本给模型**确定性信号**,配合 prompt 的"跳过"指令闭合重试循环。
- 不影响 happy path:有正文时仍返回截断后的正文(`max_chars` 截断逻辑不变)。
- 同步更新 `tests/test_web_read.py` 的 3 个 `== ""` 断言(改为断言诊断文本)。

### 2.3 config:researcher 专用步数(`config.yaml` + `agents/system.py`)

- `config.yaml` 的 `guards` 新增:
  ```yaml
  researcher_max_steps: 6   # 单 researcher 最大循环步数(诊断显示理想路径 4-5 步,6 步有余量)
  ```
- `agents/system.py` 的 `_run_researcher` 注入此值:`make_researcher(..., max_steps=config["guards"].get("researcher_max_steps", max_steps))`(fallback 到 `agent_max_steps`,向后兼容)。
- `orchestrator` / `verifier` / `writer` 仍用 `agent_max_steps=12`(**不动**)。
- `make_researcher` 签名不改(已有 `max_steps` 参数,默认 12)——仅生产链路注入 6,现有 `test_researcher`(默认调用)不受影响。

**为什么 6 步够**:诊断中的理想路径 = 1 search(1 步)+ 2-3 read(DeepSeek 支持单轮并发多 tool_call,乐观 1-2 步)+ 输出(1 步)= 3-5 步。6 步留 1-2 步余量吸收"读到一个空页要补一个 URL"的小幅扰动。并发场景下 6 步也限制单 researcher 的 API 调用数,间接降低共享 client 的并发压力。

---

## 3. 验收(Definition of Done)

### 3.1 单元测试(全 mock,不烧 API)
- ① `tests/test_prompts.py`:加 researcher 收敛预算存在性断言(含"最多"/"跳过"/收敛触发等关键词)。
- ② `tests/test_web_read.py`:3 个空失败断言由 `== ""` 改为断言诊断文本;happy path 与截断断言不变。
- ③ `tests/test_system.py`:加 `_run_researcher` 注入 `researcher_max_steps=6` 的断言(构造的 researcher `max_steps == 6`,orchestrator 仍 12)。
- ④ 现有 141 测试全绿(回归:`test_researcher` / `test_dispatch` / `test_orchestrator_e2e` 等不受影响或顺带验证)。

### 3.2 真实冒烟(收尾手动一次)
- `python -m eval.run_eval`(5 题,用户已更新题库)。
- **成功标准**:
  - 不再出现 `failed:"no_report"`(failures 主因不再是 0 findings)。
  - 多数题(≥3/5)`n_findings > 0`。
  - 平均 Grounding 出**非 null 数字**(本次目标是**解锁指标、收敛稳定**,Grounding 绝对值是后续优化输入,不强制达 0.85 门槛)。
- 真跑产物存 `eval/results/`(每题 JSON + scorecard.json),供后续优化对照。

---

## 4. 风险与回退

| 风险 | 缓解 |
|---|---|
| prompt 软约束:模型不严格遵守"读 3 篇" | `researcher_max_steps=6` 硬护栏兜底 |
| 56% 空失败未根治 → 覆盖率受限于能抓到的页 | 诊断文本 + prompt 跳过 + search snippet 兜底;若 eval 显示覆盖率过低,M2 二期加 readability fallback |
| `researcher_max_steps=6` 过紧,极端子问题不够用 | 诊断理想路径 4-5 步,余量足够;若真跑撞顶,config 调到 7-8 即可(单一配置点) |
| 并发死法未单独取证(API 限流 vs trafilatura 空 vs 纯撞步数) | 三种修法(prompt/步数/诊断文本)对各类死法都有缓解;eval 重跑是最权威的并发验证 |

**回退**:三处改动互相独立但配套生效;若真跑退步,`config.yaml` 把 `researcher_max_steps` 调回 12 即恢复 M2 原行为(prompt + 诊断文本仍保留,只会更好不会更差)。

---

## 5. 测试策略沿用

与 M1/M2/M3 一致:**全 mock TDD**(FakeClient + monkeypatch,不烧 API),每个改动先写失败测试再实现;收尾**手动真跑一次** `python -m eval.run_eval` 验证真实链路。本次新增/改动测试见 §3.1。
