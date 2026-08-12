# AI-PM-Agent · 深度研究型多 Agent 系统

基于 DeepSeek 的**模型自驱**多 Agent 编排系统:接受研究问题 → 规划 / 检索 / 验证 / 撰写 → 产出带引用的研究报告。**自研 harness**(非现成 SDK),核心原语 `AgentLoop` + `ToolRegistry` + 重试 / 兜底解析 / escalation。

> 详细设计见 [spec](docs/superpowers/specs/2026-08-08-deep-research-multi-agent-design.md)。

## 当前状态:M3 完成 + 单 agent 基线对比与 Verifier 消融 ✅

| 里程碑 | 内容 | 状态 |
|---|---|---|
| **M1 地基** | harness(`AgentLoop`/`ToolRegistry`/robust)+ LLM client(DeepSeek 生成 + GLM-5.2 裁判)+ 博查搜索 + trafilatura 提取 + Pydantic 数据结构 | ✅ |
| **M2 编排** | 四 agent(orchestrator / researcher / verifier / writer)+ 子 agent `ThreadPoolExecutor` 并行派发 + 上下文隔离(JSON 清洗/parse/精简回填)+ Report holder 硬提取(最终报告不经 Orchestrator 转述) | ✅ |
| **M3 评估体系** | 4 质量指标(Grounding / 引用准确率 / 覆盖率 / 幻觉率)+ GLM 逐条裁判 + 5 题基准集 + 上线门槛 | ✅ |
| **M3 后续 · 基线对比** | 三档系统(multi / no_verify 消融 / 单 agent baseline)+ `--system` 切换 + 三方对比 + Verifier 消融归因 | ✅ |

**真实基线(5 题)**:mean_grounding = **0.932**(门槛 0.85,**passed**),引用准确率 0.954,覆盖率 0.60,幻觉率 0.046。**基线对比见下节(多 agent+verifier 架构 Grounding +0.089、幻觉率 −0.024 超越单 agent)**。180 单测全绿(全 mock 不烧 API)。

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
```

## 手动真实冒烟(会产生少量 API 费用)

```bash
python main.py "2026 年大模型推理优化的主流技术路线有哪些?"   # 单题带引用报告
python -m eval.run_eval                                       # 5 题评估 → scorecard(默认串行)
```

`config.yaml` 是单一配置源(模型 / 工具参数 / 护栏阈值 / 并发上限)。

## 后续里程碑

- **M3 后续 · 基线对比** ✅:单 agent 基线 + Verifier 消融已落地(见上节)。
- **M3 后续 · 运维遥测**:5 维运维指标(延迟 / 成本 / Automation Rate / Agent Utilization,需全链路遥测层)。成功率已在 eval 附带统计。
- **M4**:Streamlit 实时编排可视化。
