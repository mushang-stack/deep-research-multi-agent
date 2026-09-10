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
pytest                                       # 300 单测(全 mock,不烧 API)
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

## Loop 内功:预算护栏与上下文压缩

循环层的两道护栏与一项收敛修复:护栏全部挂 `config.yaml` 开关、默认关闭(关闭时循环行为与不加完全一致,遥测输出按设计新增 degraded/压缩等增量字段);收敛双保险是质量修复、默认开启。回归由 300 个测试守门:

- **Token 预算护栏(P1)**——步数护栏之外补上成本维度。`run_token_budget` 整场共享池 + 各 agent 独立预算(每次调用新建),同一调用对两者各计费一次;耗尽时默认 `escalate` 上抛,researcher 单独配 `partial`:追加一条"禁止再调工具"的收尾指令、做恰好一次强制收尾调用,把已花掉的检索工作救回部分 findings(其输出仍经 `parse_findings` 逐项容错,坏项跳过、救回的 findings 正常合流)。
- **上下文压缩(P2)**——上轮响应的 `usage.prompt_tokens`(免费的上下文真值,免 tokenizer)超过 `threshold_prompt_tokens` 时,把中间旧 tool 消息替换为 `[stub: ...]` 存根、旧 assistant 内容截断,保护 system/首条 user 前缀与最近 `keep_last_n` 条。只改内容、永不删消息——删 tool 消息会破坏 `tool_call_id` 配对契约,是压缩实现最易踩的坑。
- **截断的双层设计**——`tools.web_read.max_chars` 是工具层截断(网页正文提取时),`tools.max_tool_result_chars` 是循环层截断(工具回填时;orchestrator 豁免截断与压缩——其工具结果是结构化 JSON,截断/存根化会破坏数据完整性),互为防御纵深。
- **收敛双保险(默认开)**——最后一步预告(进入最后一轮仍未收敛时注入交卷指令,零额外调用)+ 步数耗尽强制收尾(复用 v2.1 partial 原语)。根因是零松弛算术:收敛规则(搜≤2+读≤3)加输出步恰好铺满 6 步上限,随机发散率 10%;三臂定向实验(基线/纯截断/修复后各 20 次)修复臂 **0/20 发散**,完整 eval **0/31 个 researcher 撞顶**(修复前贴顶运行)。

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

- [产品 PRD:v2.0 产品化规划](docs/PRD.md)
- [可交互原型(Figma)· v2.0](https://www.figma.com/proto/cfVP05FJVll5Hba2yV09sq/%E6%B7%B1%E5%BA%A6%E7%A0%94%E7%A9%B6%E5%8A%A9%E6%89%8B-%C2%B7-v2.0-%E4%BA%A4%E4%BA%92%E5%8E%9F%E5%9E%8B?node-id=0-1)
- [项目详报:真实数据与工程过程](docs/PROJECT_REPORT.md)
