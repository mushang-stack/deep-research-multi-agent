# AI-PM-Agent · 深度研究型多 Agent 系统

基于 DeepSeek 的**模型自驱**多 Agent 编排系统:接受研究问题 → 规划 / 检索 / 验证 / 撰写 → 产出带引用的研究报告。**自研 harness**(非现成 SDK),核心原语 `AgentLoop` + `ToolRegistry` + 重试 / 兜底解析 / escalation。

> 详细设计见 [spec](docs/superpowers/specs/2026-08-08-deep-research-multi-agent-design.md)。

## 当前状态:M3 评估体系完成,真实基线过线 ✅

| 里程碑 | 内容 | 状态 |
|---|---|---|
| **M1 地基** | harness(`AgentLoop`/`ToolRegistry`/robust)+ LLM client(DeepSeek 生成 + GLM-5.2 裁判)+ 博查搜索 + trafilatura 提取 + Pydantic 数据结构 | ✅ |
| **M2 编排** | 四 agent(orchestrator / researcher / verifier / writer)+ 子 agent `ThreadPoolExecutor` 并行派发 + 上下文隔离(JSON 清洗/parse/精简回填)+ Report holder 硬提取(最终报告不经 Orchestrator 转述) | ✅ |
| **M3 评估体系** | 4 质量指标(Grounding / 引用准确率 / 覆盖率 / 幻觉率)+ GLM 逐条裁判 + 5 题基准集 + 上线门槛 | ✅ |

**真实基线(5 题)**:mean_grounding = **0.932**(门槛 0.85,**passed**),引用准确率 0.954,覆盖率 0.60,幻觉率 0.046。153 单测全绿(全 mock 不烧 API)。

### 三阶段质量修复(评估驱动)

1. **researcher 收敛**(M2 回修):收敛硬预算(搜 ≤2 次 / 读 ≤3 篇 / 读 ≥2 篇有正文即输出)+ `web_read` 空失败返回诊断文本 + `researcher_max_steps=6` → 每题稳定 24-37 findings(对比修复前 0)。
2. **提分过线**:逐条诊断 verdict 定位 grounding 低主因(claim 混入 excerpt 外的模型补充)→ claim 严格 = excerpt 直接改写 + `parse_failed` 兜底改 partial + 裁判 JSON 强化 → mean_grounding 0.795 → 0.932(+0.137)。
3. **健壮性**:0 findings 时不调 writer(避免空报告);并发裁判 + 题间并发。

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

- **M3 后续**:5 维运维指标(成功率 / 延迟 / 成本 / Automation Rate / Agent Utilization,需全链路遥测层)+ 单 agent 基线对比。
- **M4**:Streamlit 实时编排可视化。
