# deep-research-multi-agent · 深度研究型多 Agent 系统

基于 DeepSeek 的**模型自驱**多 Agent 编排:输入一个研究问题,系统自主完成 **规划 → 并行检索 → 验证 → 撰写**,产出带引用的研究报告。自研 harness,不套现成 SDK。

## 亮点

- **模型决定控制流** — Orchestrator 运行时动态决策(检索 → 验证 → 缺口 → 再检索 → 收尾),路径与循环次数由模型决定,非固定 pipeline
- **自研 harness** — `AgentLoop` / `ToolRegistry` / 重试退避 / 坏 tool-call 兜底解析 / 步数护栏 / escalation
- **机制级防幻觉** — Writer 无外部工具,只能用已验证 findings 写报告
- **评估驱动** — 4 质量指标 + GLM 独立逐条裁判 + 5 题基准集 + 上线门槛;三档基线对比隔离架构价值
- **实时编排可视化** — Streamlit 双模式(真实运行 + 回放),多 agent 协作过程实时滚动

## 快速开始

```bash
python -m venv .venv && source .venv/Scripts/activate   # Windows Git Bash
pip install -r requirements.txt
cp .env.example .env   # 填 DEEPSEEK / GLM / BOCHA key(测试与回放不需要)
```

**跑 demo(推荐入口):**

```bash
streamlit run ui/app.py
```

- **回放模式**:选择已录制 trace,重放完整编排过程——**无需 API key,零成本**
- **真实运行**:输入问题现场跑一题(~5 min / ~$0.05)

**CLI 与评估:**

```bash
python main.py "对比 RAG 与微调的适用场景"   # 单题带引用报告
python -m eval.run_eval                      # 5 题评估 → scorecard(支持 --system 三档对比)
pytest                                       # 249 单测(全 mock,不烧 API)
```

## 架构

```
用户问题
   ↓
Orchestrator(动态决策:走哪步、循环几次、何时收尾)
   ├─ dispatch_research → Researcher ×N(并行:博查搜索 + 网页提取 → 结构化 findings)
   ├─ verify_findings   → Verifier(复核来源支撑:supported / weak / unsupported)
   └─ write_report      → Writer(无工具,只用已验证 findings → 带引用报告)
```

## 关键结果(真实跑分)

| 指标 | 数值 |
|---|---|
| Grounding(上线门槛 0.85) | **0.932,passed** |
| 多 agent vs 单 agent 基线 | Grounding **+0.089** / 幻觉率 **−0.024** |
| 单题成本 / 时延 | ~$0.05 / ~4.7 min |

> 完整数据、Verifier 消融结论(单独贡献小于预期——如实读数)与运维遥测见[项目详报](docs/PROJECT_REPORT.md)。

## 项目结构

```
core/      自研 harness:AgentLoop、ToolRegistry、robust、events、telemetry
agents/    四 agent 编排:orchestrator / researcher / verifier / writer + 并行派发
tools/     博查搜索 + trafilatura 网页提取(纯国内链路)
eval/      基准集 + GLM 逐条裁判 + 指标 + scorecard + 三档基线对比
ui/        Streamlit 双模式 app(纯视图 / 控制器分层,薄渲染壳)+ trace 回放
main.py    CLI 入口
```

## 文档

- [项目详报:真实数据与工程过程](docs/PROJECT_REPORT.md)
- [整体设计 spec](docs/superpowers/specs/2026-08-08-deep-research-multi-agent-design.md)
- 各阶段 spec / 实施计划见 [`docs/superpowers/`](docs/superpowers/)
