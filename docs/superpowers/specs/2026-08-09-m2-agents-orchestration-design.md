# M2 四 Agent 编排层(Agents + 子 Agent 派发 + main.py)— 设计文档(Spec)

- **日期:** 2026-08-09
- **作者:** 王宇博
- **状态:** Draft,待 review
- **对应整体 spec:** [2026-08-08-deep-research-multi-agent-design.md](2026-08-08-deep-research-multi-agent-design.md) §2 / §3.1 / §3.3 / §4
- **M1 产出:** [2026-08-09-m1-foundation-harness.md](../plans/2026-08-09-m1-foundation-harness.md)(已合并 main,55 测试绿)

---

## 0. 目标与背景

M1 已搭好 harness 地基:`AgentLoop`(核心原语)、`ToolRegistry`、`core/robust.py`(重试/兜底解析/Escalation)、LLM 客户端(DeepSeek 生成 + GLM-5.2 裁判)、博查搜索 + trafilatura 提取、Pydantic 数据结构。全部 mock 测试,不烧 API。

M2 把这些原语**组合成四个有分工的 agent**,实现 Orchestrator 动态决策驱动的并行子 agent 编排,并接通 `main.py` 真实链路。这是 spec 原则 ①(模型决定控制流)与 ②(自研 harness)的落地层。

**M2 范围:** 四个 agent 的 system prompt + 工具绑定 + **子 agent 并行派发** + `main.py` 真实链路 + M1 遗留的 config-wiring 待办闭环。

---

## 1. 里程碑边界

**做:**
- `agents/` 模块:`prompts.py` + `researcher.py` / `verifier.py` / `writer.py` / `orchestrator.py` + `system.py` 工厂
- 子 agent 派发机制:**批量派发 + ThreadPoolExecutor 并发** + 单点容错 + `research_max_rounds` 护栏
- 上下文隔离:子 agent 输出 JSON → 派发层 parse → 精简字符串回填 Orchestrator
- Report holder 硬提取:`write_report` 把 Report 存 holder,系统确定性取
- `main.py` 极薄 CLI + `agents/system.py` 工厂(config wiring)
- 全 mock 测试 + 手动真实冒烟一次

**不做(留后续里程碑):**
- 9 维评估指标 + 基准集 + 基线对比(M3)
- Streamlit 实时编排可视化(M4)
- 真实基准回归测试(M3)

---

## 2. 架构

### 2.1 agents 模块结构

```
agents/
├── prompts.py        # 四个 agent 的 system prompt,集中管理
├── researcher.py     # Researcher: AgentLoop + web_search/web_read
├── verifier.py       # Verifier:   AgentLoop + web_read/web_search(交叉印证)
├── writer.py         # Writer:     AgentLoop + 无工具(防幻觉硬保证)
├── orchestrator.py   # Orchestrator: AgentLoop + 三个内部派发工具
└── system.py         # build_system() 工厂:组装 + config wiring
```

### 2.2 数据流(Orchestrator 用 tool-call 动态决定走哪条路,路径非固定)

```mermaid
flowchart TD
    U[用户问题] --> ORC[Orchestrator AgentLoop<br/>内部工具: dispatch_research / verify_findings / write_report]
    ORC -->|dispatch_research q1,q2,q3| POOL
    subgraph POOL [ThreadPoolExecutor 并发]
      R1[Researcher q1<br/>web_search+web_read]
      R2[Researcher q2]
      R3[Researcher q3]
    end
    R1 & R2 & R3 -->|JSON: Findings[]| MERGE
    MERGE[合并去重 → 精简字符串回填] --> ORC
    ORC -->|verify_findings| VER[Verifier AgentLoop<br/>web_read 交叉印证]
    VER -->|JSON: VerificationResult[]| ORC
    ORC -->|缺口→再dispatch_research / 足够→write_report| DEC{模型决策}
    ORC -->|write_report outline,verified_findings| WRT[Writer AgentLoop<br/>无工具]
    WRT -->|Report 对象| HOLD[(report_holder)]
    WRT -->|Report JSON 回填| ORC
    HOLD --> OUT[最终研究报告(从 holder 取,非 Orchestrator content)]
```

### 2.3 子 agent 派发机制(核心)

Orchestrator 是一个 `AgentLoop`,其 `ToolRegistry` 注册三个**内部工具**——这些工具的 fn 不直接干活,而是派发对应子 agent:

| 内部工具 | fn 行为 | 回填 Orchestrator 的内容 |
|---|---|---|
| `dispatch_research(sub_questions: list)` | `ThreadPoolExecutor` 并发跑 N 个 Researcher,合并去重 Findings | Findings JSON 精简字符串 |
| `verify_findings(findings: list)` | 跑 Verifier | VerificationResult JSON |
| `write_report(outline, verified_findings)` | 跑 Writer(无工具),Report **存 holder** | Report JSON(仅供 Orchestrator 知晓) |

**并行 + 容错:** N 个 Researcher **共享同一个 `DeepSeekClient`**(OpenAI SDK 底层 httpx 线程安全,连接池复用)。单个 Researcher 抛 `Escalation`(max_steps)不阻塞整体——`future.exception()` 捕获后,返回部分 Findings + 失败子问题标记,让 Orchestrator 自行判断是否补检。

### 2.4 上下文隔离落地

子 agent 的 system prompt 被要求最终输出**严格 JSON**(对齐 `core/schemas.py` 的 Pydantic 结构)。派发层工具 fn:`AgentResult.content` → 清洗去 markdown 代码块包裹 → `json.loads` → 构造 Pydantic 对象 → 序列化成**精简字符串**回填 Orchestrator。于是 Orchestrator 上下文只见结构化数据,不见原始网页正文(spec 原则 ③)。坏 JSON 由 M1 已有的 `robust.safe_parse_arguments` 兜底为空结构。

### 2.5 `research_max_rounds=3` 护栏

**不改动 `AgentLoop`**,把轮次计数做进 `dispatch_research` 工具 fn 的**闭包**:每次被调用 `+1`,超过 3 轮时 fn 直接返回"已达最大研究轮次,请直接 write_report",引导模型收敛而非死循环。M1 原语保持纯净,护栏是编排层职责。

### 2.6 Report holder 硬提取(机制级确定性)

最终 Report 的提取**不经过 Orchestrator 的嘴**。`write_report` fn 调 Writer 得到一个 **Pydantic `Report` 对象**(派发层确定性产物,非 LLM 输出),在把它序列化 JSON 回填给 Orchestrator 的**同时**,把该对象存入派发层闭包的 `report_holder`。系统结束后**直接从 holder 取 Report**,零依赖模型转述(spec 原则 ④"机制级防不确定"的延伸)。

- Orchestrator 调过 `write_report` → holder 有 Report → `main` 渲染 markdown
- Orchestrator 从未调 `write_report`(失败 / max_steps 截断) → holder 空 → 抛 `Escalation`(干净的失败信号)
- Orchestrator 的 `content` 退化为"报告已生成"之类的收尾语,不承担产出职责

---

## 3. 四个 Agent 职责契约

| Agent | system prompt 要点 | 输入(user_message) | 工具 | 输出(content,严格 JSON) |
|---|---|---|---|---|
| **Orchestrator** | 规划中枢,动态决策;调 `write_report` 后返回简短收尾语、不再调工具 | 用户问题 | `dispatch_research` / `verify_findings` / `write_report`(内部派发) | 收尾语(真正产出走 holder) |
| **Researcher** | 单子问题检索;**只基于 web_read 读到的真实内容提炼,不得编造** | 单个子问题文本 | `web_search` / `web_read` | `{"findings":[Finding…]}` |
| **Verifier** | 复核来源支撑,用 web_read 交叉印证;弱 / 不支撑时给 `suggested_query` | Findings JSON | `web_read` / `web_search` | `{"results":[VerificationResult…]}` |
| **Writer** | 综合带引用报告;**无工具,只能用传入的 verified_findings**,每论断 cite finding_id | outline + verified_findings | **无(防幻觉硬保证)** | `Report` JSON |

---

## 4. system prompt 契约要点

- **所有子 agent:** 严格 JSON 输出(对齐 schema),不许 markdown 包裹(派发层仍做清洗兜底)。
- **Researcher / Verifier:** 结论必须基于 `web_read` 读到的真实内容,不得凭空生成 claim 或来源。
- **Writer:** 无工具是**结构保证**(ToolRegistry 不注册任何工具,或传入 None);每条论断必须 cite finding_id。
- **Orchestrator:** 拿到 `write_report` 结果后返回简短收尾语、不再调工具;发现验证缺口 → 再 `dispatch_research`(受 §2.5 轮次护栏约束);覆盖足够 → `write_report`。
- 四份 prompt 集中在 `agents/prompts.py`,构造各 `AgentLoop(system_prompt=…)` 时注入,每次 `run()` 自动带。

---

## 5. main.py + config wiring(M1 待办闭环)

### 5.1 `agents/system.py` — `build_system(config, *, client=None, search_client=None) -> Orchestrator`

整个系统的组装点,`main.py` 与测试都调它:

- 从 `config.yaml` 读 `models.generator.{name,temperature,max_tokens}`、`tools.web_search.{endpoint,count}`、`tools.web_read.max_chars`、`guards.{agent_max_steps,research_max_rounds}`,**显式注入**各构造函数
- `client` / `search_client` 可选注入:测试传 `FakeClient` / 假博查;生产传 `None` 时工厂内部按 config + env 构造真实 `DeepSeekClient` / `BochaSearchClient`
- **共享同一个 `DeepSeekClient`** 构造 Researcher / Verifier / Writer / Orchestrator
- 给 Orchestrator 注册三个内部派发工具(fn 闭包持有 round 计数器 + `report_holder`)

### 5.2 config-wiring 解法(零侵入,闭环 M1 待办)

M1 待办:`config.yaml` 的值与各 client 构造函数硬编码默认值"两份"。解法——**生产链路一律走 `build_system` 从 config 注入**(config 成为唯一真相源),client 构造函数的硬编码默认**保留作 fallback**(测试 / 独立构造时仍方便)。**不改 client 代码**,零侵入。

### 5.3 `main.py`(极薄 CLI)

```
python main.py "2026 年大模型推理优化的主流技术路线有哪些?"
```

`main.py` 只做:解析 argv → `load_config()` → `build_system(cfg)` → `orchestrator.run(question)` → 取 `report_holder` 的 Report → 渲染成 markdown(章节 + 可点击引用 + 来源列表)打印 stdout。无参数时打印 usage。组装逻辑全在 `system.py`,`main.py` 不含可测逻辑。

---

## 6. 测试策略(全 mock,沿用 M1 ScriptedClient 模式)

| 测试文件 | 覆盖点 |
|---|---|
| `test_researcher.py` | 工具绑定(web_search→web_read→综合);输出合法 JSON Findings |
| `test_verifier.py` | 工具绑定;VerificationResult 三档 verdict(supported/unsupported/weak) |
| `test_writer.py` | **无工具注册(防幻觉硬保证)**;输入 verified findings → Report |
| `test_orchestrator_dispatch.py` | 派发层核心:并发 N 个 researcher、合并去重、**单个失败不崩(容错)**、`research_max_rounds` 闭包超限引导收敛、上下文隔离(回填精简字符串非原文) |
| `test_orchestrator.py` | Orchestrator `AgentLoop` 整体:tool-call 驱动 dispatch→verify→write 链路 |
| `test_report_holder.py` | `write_report` 存 holder;holder 空(未生成报告)时抛 Escalation |
| `test_system.py` | **直接验收 M1 待办**:`FakeClient` 注入,断言 config 的 model/temperature/max_tokens/max_steps/max_rounds 正确传到 client 与各 agent |

全部 `FakeClient`,不烧 API。**手动冒烟**(不进 pytest):`python main.py "真实问题"`,肉眼确认报告质量即 M2 收尾;README 补一节说明。

---

## 7. 错误处理

- **子 agent 坏 JSON:** `safe_parse_arguments` 兜底为空结构,该子 agent 计"无产出",让 Orchestrator 自行判断是否补检。
- **并发 Researcher 异常:** `future.exception()` 捕获,返回部分 Findings + 失败标记,不阻塞整体。
- **`research_max_rounds` 超限:** 闭包引导模型收敛到 `write_report`,非硬截断。
- **holder 空(从未生成报告):** 抛 `Escalation`,干净的失败信号(spec §9 显式 escalation)。
- **瞬时 API 错误:** 继承 M1 `robust` 层重试(503/超时/429)。

---

## 8. Definition of Done

- [ ] `agents/`(prompts + 四 agent + `system` 工厂)就位
- [ ] `main.py` CLI:`python main.py "问题"` 能真跑出报告(**手动验一次**)
- [ ] `pytest` 全绿(全 mock,预计 +~20 测试,M1 的 55 个保持绿)
- [ ] **M1 config-wiring 待办闭环**:`test_system` 断言 config 值注入 client
- [ ] 上下文隔离可验证:Orchestrator history 的 tool message 是精简结构化字符串,不含原始网页正文
- [ ] Report holder 可验证:系统产出取自 holder,非 Orchestrator content

---

## 9. 已知风险 / 后续迭代点

- **子 agent JSON 输出 fidelity**(spec §10 提到 V4 复杂场景结构化输出 ~60% 成功率的边角):派发层清洗 + `safe_parse` 兜底;真跑若大面积失败,M2 内迭代 prompt 或加结构化输出约束(如 response_format)。
- **并发触发 DeepSeek 限流:** robust 重试层兜底;真跑时观察并发数,必要时给 ThreadPoolExecutor 设 max_workers 上限。

---

## Self-Review(spec 作者自检记录)

**1. Spec 覆盖(本里程碑范围内的整体 spec 章节):**
- §2 四个 agent + 拓扑 → §2.1 模块结构 / §2.2 数据流 / §3 职责契约。✅
- §3.1 端到端数据流 → §2.2 / §2.3 / §2.6。✅
- §3.3 各 agent 工具表 → §3 职责契约表(工具列)。✅
- §4 harness(子 agent 生成 / 上下文管理)→ §2.3 派发机制 / §2.4 上下文隔离 / §2.5 轮次护栏 / §2.6 holder。✅
- §8 项目结构(agents/ + main.py)→ §2.1 / §5。✅
- §9 错误处理(护栏 / escalation)→ §7。✅

**2. Placeholder 扫描:** 无 TBD/TODO/"后续实现";每个设计点都有具体落地路径(文件 / 闭包 / 数据结构)。§9 是已知风险记录,非 placeholder。

**3. 内部一致性:**
- §2.6(holder)与 §3 表(Orchestrator 输出=收尾语)、§6(`test_report_holder`)、§8(DoD holder 项)一致。✅
- §2.5(轮次护栏在闭包)与 §2.3(dispatch_research fn)、§6(`test_orchestrator_dispatch`)一致。✅
- §5.2(零侵入 config wiring)与 M1 待办描述、§6(`test_system` 验收)一致。✅

**4. 歧义检查:** "Orchestrator 最终 content"在 §2.6 与 §3 表明确为"收尾语,非产出",避免与"Report 怎么取"歧义。并行 Researcher 共享 client 在 §2.3 明确,避免误读为各建 client。

**5. Scope 检查:** 聚焦单个实现计划可覆盖的范围(agents + 派发 + main + 测试),评估/UI 明确划到 M3/M4。
