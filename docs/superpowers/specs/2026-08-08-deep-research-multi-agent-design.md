# 深度研究型多 Agent 系统 — 设计文档(Spec)

- **日期:** 2026-08-08
- **作者:** 王宇博
- **状态:** Draft,待 review
- **项目代号:** `deepseek-research-agent`

---

## 0. 项目目标与背景

本项目是一个**模型自驱的多 Agent 编排系统**,接受用户的研究问题,产出一份带引用的结构化研究报告。

**它存在的真正目的:补足简历上"agent 原生"这块缺口。** 作者现有两个项目均为传统 CV/视频 AI(医学图像去噪、YOLO+LSTM 视频分类),技能栏虽写"精通提示词工程",但缺少任何 LLM/agent 项目做实证。本项目要直接证明:理解 agent loop、多 agent 编排、工具调用、agent 行为评估、防幻觉设计、成本/延迟权衡。

**目标用户:** AI 产品经理岗位的招聘方(面试时需能 live demo + 讲清工程权衡)。

**不做的事(YAGNI):** 不做多用户/鉴权、不做生产部署、不做分布式、不做通用 agent 框架。只做"深度研究"这一个场景,做到可演示、可评估、有故事。

---

## 1. 设计原则

1. **模型决定控制流(agent,而非 workflow)** — 走哪条路、循环几次、何时停,由 Orchestrator 在运行时用 tool-call 决定,不在代码里写死流水线。
2. **自研 harness** — 自己实现 agent loop、工具分发、子 agent 调度、上下文隔离,不用现成 SDK 把逻辑藏起来。
3. **上下文隔离** — 子 agent 产出以结构化数据(Pydantic)回传,不把原始网页文本灌进 Orchestrator 上下文。
4. **机制级防幻觉** — Writer 无外部工具,只能用已验证 Findings 写报告,从结构上杜绝编造。
5. **评估驱动** — 复刻作者医学项目"多维评估体系 + 测试集 + 基线对比 + 上线门槛"的方法论。

---

## 2. 架构

### 2.1 四个 Agent

| Agent | 职责 | 关键点 |
|---|---|---|
| **① Orchestrator 规划者** | 拆解问题→出研究计划→**运行时决定**检索/验证/撰写/继续循环 | 模型决定控制流的载体 |
| **② Researcher 检索者**(可并行 N 个) | 用 web_search + web_read 找资料,带来源返回 Findings | 并行多路,效率与覆盖 |
| **③ Verifier 验证者** | 复核结论是否有来源支撑、来源真伪、有无矛盾/幻觉,弱则打回 | 报告可信度的核心 |
| **④ Writer 撰写者** | 把已验证 Findings 综合成结构化带引用报告 | **无外部工具**,只能用传入数据 |

### 2.2 拓扑:Orchestrator 为动态中枢

```
        用户问题
           ↓
     ┌─────────────┐
     │ Orchestrator│  ← 运行时动态决策(tool-call 驱动)
     └─────┬───────┘
   dispatch_research │ verify_findings │ write_report
      ┌────┴────────┴─────┐
   Researcher(s)       Verifier        Writer
   (可并行 ×N)
```

**与固定 workflow 的本质区别:** 不是"检索→验证→撰写"一条道。Orchestrator 可能 `检索→验证→发现缺口→再检索→验证→撰写`,也可能某些问题直接 `检索→撰写`。路径与循环次数由模型决定。

---

## 3. 数据流与工具链

### 3.1 端到端数据流

```
用户问题
  ↓
[Orchestrator] → ResearchPlan {子问题列表 + 角度}
  │ dispatch_research(sub_question)
  ↓
[Researcher×N] → web_search → web_read → 提取
  │ 回传 Findings[] {claim, source_url, excerpt, confidence}   ← 结构化,非原文
  ↓
[Orchestrator] 收集 Findings → verify_findings(findings)
  ↓
[Verifier] → web_read 复核 → 回传 VerificationResult[]
            {finding_id, verdict: supported/unsupported/weak, reason}
  ↓
[Orchestrator] 看 VerificationResult:
  ├─ 缺口/不支撑 → 回 dispatch_research(动态循环)
  └─ 覆盖足够 → write_report(outline, verified_findings)
  ↓
[Writer] → 只用已验证 Findings 写带引用报告 → Report
  ↓
用户拿到带引用的研究报告
```

### 3.2 核心数据结构(Pydantic)

- `ResearchPlan`: `{question, sub_questions: [{id, text, angle}]}`
- `Finding`: `{id, claim, source_url, source_title, excerpt, confidence}`
- `VerificationResult`: `{finding_id, verdict, reason, suggested_query?}`
- `Report`: `{sections: [{heading, content, citations: [finding_id]}], sources: [...]}`

### 3.3 各 Agent 工具

| Agent | 外部工具 | 内部工具(派发子 agent) |
|---|---|---|
| Orchestrator | — | `dispatch_research`、`verify_findings`、`write_report` |
| Researcher | `web_search`、`web_read` | — |
| Verifier | `web_read`、`web_search`(交叉印证) | — |
| Writer | **无** | — |

### 3.4 外部服务(纯国内方案,避免网络问题)

| 能力 | 选型 | 说明 |
|---|---|---|
| 生成模型 | **DeepSeek V4** | OpenAI 兼容 API,function-calling |
| 裁判模型 | **智谱 GLM** | 仅评估环节用,规避"自评自"偏差 |
| 搜索 | **博查 Bocha**(REST) | 为 AI 设计,中文覆盖好 |
| 网页提取 | **trafilatura**(本地库) | 纯本地、零网络、零成本 |

---

## 4. Harness(自研部分 — harness engineering)

- **`AgentLoop`**:核心原语。调 DeepSeek → 有 tool_call 则执行工具、回填结果、继续循环 → 无 tool_call(产出最终结果)则返回。
- **`ToolRegistry`**:工具注册 + 分发;内部工具(`dispatch_research` 等)映射到子 agent 派发。
- **子 agent 生成**:Researcher/Verifier/Writer 各自是一个独立 `AgentLoop`,有独立工具与隔离上下文。
- **上下文管理**:子 agent 以结构化对象回传,Orchestrator 上下文保持干净。
- **健壮性层(`core/robust.py`)**:
  - 503 / 超时重试(DeepSeek 高峰期不稳定)
  - 坏 tool-call 兜底解析(应对 V4 结构化输出 ~60% 成功率的边角问题)
  - 最大步数 / 最大研究轮次护栏(防无限循环与成本失控)
  - 显式 escalation 路径(自主失败时打 flag,而非静默崩)

---

## 5. 评估体系(简历核心放大器)

方法论 1:1 复刻作者医学项目:**多维指标 + 测试集 + 基线对比 + 上线门槛**。

### 5.1 质量指标(产出好不好)

| 维度 | 定义 | 测量方法 |
|---|---|---|
| **Grounding 事实准确率**(头号指标) | claims 被来源支撑的比例 | GLM-as-judge:给 claim+来源,判 supported/partial/unsupported |
| **引用准确性** | 引用真实、URL 可达、内容匹配 | URL 存活检查 + 内容比对 |
| **覆盖率** | 覆盖 Planner 拆出的子问题比例 | 报告章节 vs ResearchPlan 对照 |
| **幻觉率** | 无来源支撑 claim 占比(含编造来源) | Grounding 补集 + 编造来源检测 |

**裁判:** 生成用 DeepSeek、裁判用 **GLM**,规避模型自我偏好偏差。

### 5.2 Agent 运维指标(效率/成本,共 5 项)

| 指标 | 定义 | 测量方法 |
|---|---|---|
| **① Agent Success Rate** | 基准题集中成功产出有效报告的任务占比 | 每题记 status(success/failed)+ 失败原因分类;建议同时报"完成率"与"质量成功率(过 Grounding 门槛)" |
| **② Agent Latency** | 提交问题→报告返回的端到端墙钟时间 | 分阶段计时(plan/research/verify/write)+ 总时长与占比 |
| **③ Agent Cost per Task** | 每个研究任务的服务侧总成本(¥) | DeepSeek token×单价 + 博查调用数×单价;**只计服务链路,不计评估链路(GLM 裁判开销另算)** |
| **④ Task Automation Rate**(任务自动化率) | 任务自主完成程度,3 档:完全自动 / 人工修改 / 人工接管 | 对基准集每份报告做**人工 grading pass** 归档;头号指标 **Automation Rate = 完全自动/总数**(如 70/100 = 70%),并报三档分布;escalation 打点作为"人工接管"的代理信号 |
| **⑤ Agent Utilization**(各 Agent 利用率与有效性) | 每个 agent 是否"物尽其用",用于删并低价值角色的 PM 决策 | 对 4 个 agent 分别统计:**调用次数 / 成功率(产出是否被采用或过校验)/ token 与成本占比**;附带 **检索有效率**=被引用进报告的 web 调用占比(Researcher 专属) |

> ④⑤ 已定:④ = 3 档自主度 + Automation Rate 头号指标(人工 grading 测);⑤ = 各 agent 调用次数/成功率/成本占比,驱动"删并低价值角色"的 PM 决策。

### 5.3 评估方法论

- **基准测试集:** 20-30 道研究题,每道带可验证关键事实/参考答案(对应医学项目的"166 张测试集")。
- **基线对比:** baseline = 单 agent 无 verifier 版;证明多 agent + verifier 在 Grounding/幻觉率上超越 baseline(对应"核心指标超越基线")。
- **上线门槛:** 如 Grounding ≥ 85% 才算可上线(对应"划定上线边界")。
- **评估执行:** `eval/run_eval.py` 跑全集 → 出分到 `eval/results/` → 汇总 CSV/图。

---

## 6. 界面(Streamlit,实时编排可视化)

Demo 卖点:不只"输入→报告",而是**把多 agent 协作过程实时可视化**,让面试官看到模型在动态决策。

```
┌──────────────────────────────────────────┐
│  [输入框] 研究问题                 [开始] │
├──────────────────────────────────────────┤
│  ▸ Orchestrator 拆解中:研究计划 ①②③      │
│  ▸ Researcher-1 检索"①"...  🔍           │
│  ▸ Researcher-2 检索"②"...  🔍  (并行)   │
│  ✓ 找到 7 条 Findings                     │
│  ▸ Verifier 复核... ⚠ 2 条不支撑          │
│  ▸ Orchestrator: 缺口 → 再检索"③"        │
│  ▸ Writer 综合报告中...                   │
├──────────────────────────────────────────┤
│  📄 最终研究报告(带可点击引用)            │
└──────────────────────────────────────────┘
```

---

## 7. 技术栈

| 层 | 选型 |
|---|---|
| 语言 | Python |
| 生成模型 | DeepSeek V4 |
| 裁判模型 | 智谱 GLM |
| 搜索 | 博查 Bocha(REST) |
| 网页提取 | trafilatura(本地) |
| Harness | 自研 |
| 数据结构 | Pydantic |
| 界面 | Streamlit |
| 测试 | pytest |

---

## 8. 项目结构

```
deepseek-research-agent/
├── README.md
├── .env.example            # API key 模板
├── requirements.txt
├── config.yaml             # 模型名、阈值、最大轮次
├── core/                   # ← harness engineering
│   ├── agent_loop.py       #   核心原语
│   ├── tool_registry.py    #   工具注册 + 分发
│   ├── robust.py           #   重试/兜底/超时/护栏/escalation
│   └── schemas.py          #   Pydantic 数据结构
├── agents/                 # 四个 agent(system prompt + 工具绑定)
│   ├── orchestrator.py
│   ├── researcher.py
│   ├── verifier.py
│   └── writer.py
├── tools/
│   ├── web_search.py       #   博查
│   └── web_read.py         #   trafilatura
├── llm/
│   ├── deepseek_client.py  #   生成
│   └── glm_client.py       #   裁判
├── eval/
│   ├── benchmark.jsonl     #   20-30 题 + 参考答案
│   ├── judge.py            #   GLM-as-judge
│   ├── metrics.py          #   4 质量指标 + 5 运维指标
│   ├── run_eval.py         #   跑全集 + 出分
│   └── results/
├── ui/
│   └── app.py              #   Streamlit 实时编排可视化
└── main.py                 #   CLI 入口
```

---

## 9. 错误处理与测试

**护栏(`core/robust.py`):** 503 重试、坏 tool-call 兜底解析、超时、最大步数/最大研究轮次、显式 escalation。

**测试(pytest):** `AgentLoop` 用 mock 模型客户端做单元测试(不烧 API);eval benchmark 同时充当集成测试。

---

## 10. 简历与面试话术(附录)

简历 bullet 示例(风格对齐作者现有简历):

> **【基于 DeepSeek 的深度研究多 Agent 系统】** 项目负责人 / 独立开发 2026.08-2026.10
> - **架构设计与技术选型:** 主导从 0 搭建模型自驱的多 Agent 编排框架(规划-检索-验证-撰写四角色),自研 agent loop 与工具调度层,确立"Orchestrator 动态决策 + Writer 无工具防幻觉"的核心架构,明确 agent 式编排与固定工作流的差异化路径。
> - **工程难点与权衡:** 针对 DeepSeek V4 工具调用稳定性问题(复杂场景结构化输出成功率约 60%),设计重试/兜底解析/escalation 的健壮性层;采用结构化数据隔离子 Agent 上下文,保障多轮决策质量。
> - **评估体系与基线超越:** 复刻多维评估方法论,搭建 Grounding/引用准确率/覆盖率/幻觉率 + 任务成功率/延迟/成本/自动化率/利用率共 9 维指标体系,基于 20-30 题基准集划线;引入验证 Agent 后事实准确率从 X% 提升至 Y%、幻觉率下降 Z%,全面超越单 Agent 基线。

(X/Y/Z 待实测后填入真实数据)

---

## 11. 待确认 / 下一步

- [x] 确认 ④ Task Automation Rate、⑤ Agent Utilization 的定义(见 5.2)
- [ ] 确认 spec 整体无误后,进入实现规划(writing-plans)
