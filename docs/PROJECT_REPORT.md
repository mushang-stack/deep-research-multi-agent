# 项目详报 · deep-research-multi-agent(真实数据与工程过程)

> 本文件是项目的完整实况记录(里程碑 / 真实跑分 / 消融结论 / 遥测读数)。
> 仓库主页(简介 + 使用指南)见 [README](../README.md)。

基于 DeepSeek 的**模型自驱**多 Agent 编排系统:接受研究问题 → 规划 / 检索 / 验证 / 撰写 → 产出带引用的研究报告。**自研 harness**(非现成 SDK),核心原语 `AgentLoop` + `ToolRegistry` + 重试 / 兜底解析 / escalation。

## 当前状态:全部里程碑完成(M1–M4)✅

| 里程碑 | 内容 | 状态 |
|---|---|---|
| **M1 地基** | harness(`AgentLoop`/`ToolRegistry`/robust)+ LLM client(DeepSeek 生成 + GLM-5.2 裁判)+ 博查搜索 + trafilatura 提取 + Pydantic 数据结构 | ✅ |
| **M2 编排** | 四 agent(orchestrator / researcher / verifier / writer)+ 子 agent `ThreadPoolExecutor` 并行派发 + 上下文隔离(JSON 清洗/parse/精简回填)+ Report holder 硬提取(最终报告不经 Orchestrator 转述) | ✅ |
| **M3 评估体系** | 4 质量指标(Grounding / 引用准确率 / 覆盖率 / 幻觉率)+ GLM 逐条裁判 + 5 题基准集 + 上线门槛 | ✅ |
| **M3 后续 · 基线对比** | 三档系统(multi / no_verify 消融 / 单 agent baseline)+ `--system` 切换 + 三方对比 + Verifier 消融归因 | ✅ |
| **M3 后续 · 运维遥测** | 延迟/成本/利用率三柱埋点(`TelemetrySink` 注入 `AgentLoop` + `CountingClient` 裁判计数)+ pricing 成本估算 + 每题 telemetry/scorecard 聚合/控制台表 | ✅ |
| **M4 · 实时编排可视化** | Streamlit 双模式(真实运行 + 回放)+ 类型化事件总线(`core/events` + `observe.emit` 取代 progress 调用点,零改 agent 决策逻辑)+ 实时利用率/成本条 + trace 自动录制/回放 | ✅ |

**真实基线(5 题)**:mean_grounding = **0.932**(门槛 0.85,**passed**),引用准确率 0.954,覆盖率 0.60,幻觉率 0.046。**基线对比见下节(多 agent+verifier 架构 Grounding +0.089、幻觉率 −0.024 超越单 agent)**。249 单测全绿(全 mock 不烧 API)。

### 三阶段质量修复(评估驱动)

1. **researcher 收敛**(M2 回修):收敛硬预算(搜 ≤2 次 / 读 ≤3 篇 / 读 ≥2 篇有正文即输出)+ `web_read` 空失败返回诊断文本 + `researcher_max_steps=6` → 每题稳定 24-37 findings(对比修复前 0)。
2. **提分过线**:逐条诊断 verdict 定位 grounding 低主因(claim 混入 excerpt 外的模型补充)→ claim 严格 = excerpt 直接改写 + `parse_failed` 兜底改 partial + 裁判 JSON 强化 → mean_grounding 0.795 → 0.932(+0.137)。
3. **健壮性**:0 findings 时不调 writer(避免空报告);并发裁判 + 题间并发。

### 基线对比与 Verifier 消融(M3 后续)

在同一套 eval 管线(同 DeepSeek 生成 / 同博查工具 / 同 GLM-5.2 裁判 / 同 5 题基准)上跑三档,隔离 **Verifier 单独价值(A−B)** 与 **整套架构价值(A−C)**:

| 配置 | 编排 | Verifier | Grounding | 幻觉率 | 引用准确率 | 覆盖率 | 成功率 |
|---|:---:|:---:|---|---|---|---|---|
| **A multi**(完整系统) | ✅ | ✅ | **0.974** | **0.046** | 0.954 | 0.60 | 80%(4/5) |
| **B no_verify**(消融) | ✅ | ❌ | 0.950 | 0.048 | 0.952 | 0.52 | 100% |
| **C baseline**(单 agent) | ❌ | ❌ | 0.885 | 0.070 | 0.930 | 0.64 | 100% |
| **Δ A−B**(Verifier 价值) | | | +0.024 | −0.001 | +0.001 | +0.08 | −0.20 |
| **Δ A−C**(架构价值) | | | **+0.089** | **−0.024** | +0.024 | −0.04 | −0.20 |

**结论(如实读数,含反直觉发现):**
- **架构价值成立**:多 agent + verifier 在 Grounding(+0.089)与幻觉率(−0.024)上明确超越单 agent 基线 —— 核心卖点站得住。
- **Verifier 的单独贡献比预期小**:A−B 的 Grounding 仅 +0.024、幻觉率基本持平(−0.001)。说明准确率的主要增益来自**多 agent 编排(并行检索 + 规划)**,Verifier 是过滤层面的"锦上添花",非主力。这是个有用的工程发现(Verifier 的 ROI 低于先验)。
- **多 agent 有可靠性代价**:A 在 q002 触及 `orchestrator max_steps=12` 护栏 Escalation → 成功率 80% < B/C 的 100%。更复杂的模型驱动控制流偶发不收敛,是准确率的 tradeoff。
- **覆盖率:基线反而最高(0.64)**:单 agent 无收敛硬预算、读得更宽。5 题小样本,不武断归因。

> 口径:A 的质量分在 4 道成功题上取均值(q002 失败、无 findings,按 spec 不计入);B/C 在 5 题。5 题为趋势性证据,非统计证明。复跑:`python -m eval.run_eval --system {multi,no_verify,baseline}` 三遍 → `python -m eval.compare`。

### 提速

- **裁判并发**:GLM 逐条裁判 `ThreadPoolExecutor`(上限 10),单题裁判 ~5min → ~30s。
- **题间并发**:`eval.question_concurrency`(默认 1 串行向后兼容;3 时 5 题 ~40min → ~14min,但并发峰值偶发限流,按 DeepSeek 配额调)。

### 运维遥测(延迟 / 成本 / 利用率)

系统在 eval 管线上内置运维可观测,每次 `python -m eval.run_eval` 除质量 scorecard 外,
额外产出**延迟/成本/利用率三柱**报告(逐题 `telemetry` 字段 + scorecard 聚合 + 控制台表)。

**埋点设计(单点覆盖,最小侵入):**
- **生成链路**:`TelemetrySink` 注入 `AgentLoop`(全仓唯一执行原语,自带 agent 名
  `orchestrator/researcher/verifier/writer`)。`run()` 内累加各步 token、计步、
  `perf_counter` 计墙钟,收敛/触顶都上报 —— **一处覆盖四柱 agent**。
- **裁判链路**:`CountingClient` 包装 GLM judge client,Lock 下累加 usage 与调用次数,
  **零改 judge 契约**。
- 顺修一个 bug:`AgentResult.usage` 原只留最后一步,多步 researcher 漏算约 4/5 token,
  现改为跨步聚合(由专门单测锁定)。

**三柱口径:**

| 柱 | 指标 | 口径 |
|---|---|---|
| 延迟 | `wall_s` | 整批 / 单题 / 单 agent 角色(researcher 按单个计,不叠加并发) |
| 成本 | `cost_usd` | token/1M × 单价;**产品链路(DeepSeek)与评估链路(GLM)分列** |
| 利用率 | `budget_used` | `steps / max_steps`(researcher 理想 4-5 / 上限 6);附触顶(Escalation)次数 |

**成本估算说明:** 单价在 `config.yaml` 的 `pricing` 段,取公开标价(截至 2026-08,近似,
可在 config 调整):DeepSeek-chat(V4-Flash)取 cache-miss 保守价 input \$0.14 / output
\$0.28 per 1M;GLM-5.2 实标未公开,暂按同族 GLM-4.5(¥2/¥8 per 1M)@7.2 换算 input \$0.28 /
output \$1.12。**这是量级估算而非计费依据。**

真实样例(multi 5 题,2026-08-13 真跑,题间并发 3):

> 5 题中 4 题成功(q002 触 `orchestrator max_steps` 护栏 Escalation,与质量基线同模式);
> 质量仍过线:mean_grounding **0.970**、引用 0.939、覆盖 0.65、幻觉 0.061。
> telemetry 在 4 道成功题上聚合(失败题无 telemetry)。

| 柱 | 数值 |
|---|---|
| **延迟** | 整批墙钟 **~33 min**(1979.6s,5 题并发跑);单题均值 **282s**(~4.7 min) |
| **成本** | 产品(DeepSeek)**\$0.1995** + 评估(GLM)**\$0.0709** = **\$0.2705**;单题产品成本 ~\$0.05 |
| **利用率** | researcher **5.2/6 步(87%)** · verifier 6.2/12(52%) · orchestrator 6.0/12(50%) · writer 1.0/12(8%) |

**读数:** researcher 步数预算占用 87%(贴近 `researcher_max_steps=6` 上限,收敛偏紧);
writer 一步成文(确定性子例程);评估链路 GLM 成本约占产品成本的 1/3。

## 架构

四个 agent(模型决定控制流,非固定 pipeline):

- **Orchestrator**(规划中枢):动态拆解子问题 → `dispatch_research` → `verify_findings` → `write_report`;`research_max_rounds` 护栏;0 findings 时拒绝写报告。
- **Researcher**(可并行):单子问题检索,收敛硬预算 + 空页跳过。
- **Verifier**:复核 findings 来源支撑,标 supported/unsupported/weak。
- **Writer**(无工具,防幻觉):综合 verified findings 成带引用报告,全面覆盖不省略。

核心原语:`AgentLoop`(步数护栏 / escalation)+ `ToolRegistry` + 重试退避 / 兜底 JSON 解析 + Report holder(机制级确定性提取)。

## 快速开始

```bash
python -m venv .venv && source .venv/Scripts/activate   # Windows Git Bash
pip install -r requirements.txt
cp .env.example .env   # 填入 DEEPSEEK / GLM / BOCHA key(M1 测试不需要)
pytest                  # 全单测(全 mock,不烧 API)
streamlit run ui/app.py # 实时编排 demo(回放模式开箱即用,无需 API key;见 M4 节)
```

## 手动真实冒烟(会产生少量 API 费用)

```bash
python main.py "2026 年大模型推理优化的主流技术路线有哪些?"   # 单题带引用报告
python -m eval.run_eval                                       # 5 题评估 → scorecard(默认串行)
```

`config.yaml` 是单一配置源(模型 / 工具参数 / 护栏阈值 / 并发上限)。

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

## 项目状态与可选后续

**M1–M4 全部完成**(2026-08-08 spec → 2026-08-14 M4 收口),249 单测全绿,各阶段真实跑数据见上各节。可选后续方向(基于上述真实读数,非承诺):

- **成功率**:multi 档 orchestrator 偶发触 `max_steps` 护栏(80% vs 基线 100%),可在护栏 / 提示词层继续调优。
- **覆盖率**:mean 0.60,受 researcher 检索面与 agent 不感知 key_facts 的本质限制。
- **Verifier ROI**:消融显示单独贡献小(Grounding +0.024),可评估简化或与检索层合并的取舍。

## 附录:v2.1 Loop 内功升级——预算护栏 + 上下文压缩(2026-09-09)

M4 之后的循环层专项:步数护栏之外补上 token/成本维度、上下文只增不减的压缩、以及撞护栏时"救回已花掉的工作"。全部能力挂 config 开关、**默认关闭时循环行为与现状逐字节一致**(遥测输出按设计新增增量字段;249 → **278 单测**,零扰动回归)。真实 off/on 对照实验(5 题 ×3 轮)完整数据见 `eval/results/guards-comparison.md`,此处是结论与每个能力的三句话讲解。

### 实验结论(诚实版)

- **机制全部验证 ✓**:预算检测/诚实计数/熔断/escalation/容错按设计工作——熔断消息 `orchestrator exceeded token budget 721184/700000` 就是计数现场的直接证据;完成题质量三轮稳定(grounding 0.90–0.95,不倒退)。
- **护栏是成本上限,不是节省**:off 侧单题自然花费 151k–447k token、run 间方差 ±2×;400k 池切在分布带正中(60% 完成率),700k(+57% 余量)仍死于单轮轨迹膨胀(721k)。其正确用法 = 灾难保险丝(余量 ≥2× 观测最大),而非日常调节旋钮。
- **意外收获的真实发现**:紧预算与缺口驱动编排互噬(子 agent 产出变薄 → orchestrator 补偿性多轮 → 总花费反升,直至池杀);researcher 步数不收敛(M2 旧疾)以轨迹级概率随机复发,同配置两轮结果天壤之别——这是比 token 预算更深的问题,留给 prompt/步数层。
- **压缩与 partial 本题集零触发**(阈值对 64k 窗口是宽安全位),正确性由 29 个新增单测背书;价值区在更长任务。

### 每个能力三句话

- **Token 预算护栏(P1)**:每个 agent 注入独立 `Budget`(线程安全,并发 researcher 共享 run 池时各计费一次),循环顶部检查点在"上轮工具已回填、下轮调用前"裁决——模型已给最终回答的轮次天然不降级。因为步数护栏区分不了"每步读长文"和"每步只搜一次",成本失控需要 token 维度的真值。验证 = 29 个单测(精确触发数学/双预算各 charge/并发计数)+ 三轮真实 eval 的熔断现场。
- **partial 强制收尾(D1 细则)**:researcher 预算耗尽时不直接丢工作,而是追加一条"禁止再调工具"的收尾指令、做恰好一次模型调用(输出若带 tool_call 一律忽略),`degraded=True` 返回,输出仍经 `parse_findings` 逐项容错。因为超限时刻模型处于 tool_call 中间态,直接返回最后内容常不可解析,等于白降级。验证 = 单测钉死恰好一次/契约安全/诚实计数;steps==0(共享池先耗尽、零工作可救)时强制走 escalate——那是质量审抓出的"收尾比丢弃更糟"边界。
- **上下文压缩(P2)**:上轮响应的 `usage.prompt_tokens`(免费真值,免 tokenizer)超阈值时,把中间旧 tool 消息替换为 `[stub: ...]` 存根、旧 assistant 内容截 200 字,保护 system/首条 user 前缀与最近 `keep_last_n` 条。**只改内容、永不删消息**——删 tool 消息会破坏 `tool_call_id` 配对契约,是压缩实现最易踩的坑。验证 = 单测覆盖幂等/前缀逐字节保护/配对完整;经 `on_compact` 回调发 `compact` 事件,UI 无需改动即可收到通知(另为该事件补了 🗜 图标)。
- **双层截断**:工具层 `web_read.max_chars=8000`(正文提取时)+ 循环层 `max_tool_result_chars`(任何工具回填时),互为防御纵深;orchestrator 豁免两层——其工具结果是结构化 JSON(dispatch 合并 findings / verify 判定),截断/存根化会破坏数据完整性并污染 eval trace(这是质量审驱动的设计修订)。验证 = 边界单测 + eval trace 完整性对照。

### 附录补记:researcher 收敛稳定性修复(2026-09-09,同日续)

v2.1 三轮 eval 暴露的真瓶颈:researcher 随机不收敛(同配置两轮 0 findings vs 35 findings),根因是**零松弛算术**——收敛规则(搜≤2+读≤3)加输出步恰好铺满 `researcher_max_steps=6`,任何一次浪费(空页/补搜)都让交卷挤不进日程,且步数护栏耗尽即 Escalation 全部丢弃。

**修复(双保险,默认开)**:F1 最后一步预告(`last_step_nudge`,进入最后一轮且未收敛时注入交卷指令,零额外调用)+ F2 步数耗尽走强制收尾(v2.1 的 partial 原语外溢到步数护栏,`MAX_STEPS_REACHED` 话术)。配套:降级空手的 researcher 由 dispatch 显式记 `degraded_rescue_empty` 失败条目(不留隐形缺口)。

**三臂定向复现实验**(60 次单 researcher 真跑,`eval/results/convergence-probe.md`,原始逐次数据 `.runs.jsonl`):

| 臂 | n | 0-findings 率 | findings 均值 | escalated |
|---|---|---|---|---|
| A 基线 | 20 | 10%(2/20) | 8.2 | 2 |
| B 纯截断 6000 | 20 | 15%(3/20) | 8.0 | 3 |
| C 修复后 | 20 | **0%(0/20)** | 8.9 | 0 |

- 修复生效:C 臂零发散且 findings 均值最高;天然发散率被量化为 10%(解释了完整 eval 的偶发 no_report)。
- **修复后完整 eval(默认开)**:5/5 成功、grounding 0.939、**0/31 个 researcher 撞顶**(修复前 mean_steps 5.52/6 贴顶)、单题 191s/$0.034(修复前 325s/$0.048)——更快更省更稳。
- **诚实注记**:① 截断放大效应方向为正(+5pp)但 1 个事件之差属噪声级,未证实;② F2 兜底在实验与 eval 中零触发——F1 预告在边界轨迹上直接推动收敛,兜底未出场(其有效性由单测背书,含 F1/F2 联合路径测试);③ nudge 注入无计数器,"预告推动收敛"与"自然恰好末步收敛"无法从遥测区分;④ `degraded_rescue_empty` 覆盖"空 findings"与"输出不可解析"两种尾部(处置相同);⑤ 救援路径 steps=max_steps+1 属诚实计数,utilization 的 budget_used 可 >100%;⑥ baseline 臂刻意不带修复——预告"不要再调用任何工具"对 baseline 有害(其收尾动作恰是 write_report 工具调用)。
