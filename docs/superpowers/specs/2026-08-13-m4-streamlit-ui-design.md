# M4 · Streamlit 实时编排可视化 — 设计文档(Spec)

- **日期:** 2026-08-13
- **作者:** 王宇博
- **状态:** Draft,待 review
- **里程碑:** M4(整体项目最后一个里程碑,对应总体 spec `2026-08-08-deep-research-multi-agent-design.md` §6)

---

## 0. 目标与背景

M1–M3 + M3 后续(基线对比 / verifier 消融 / 运维遥测)全部完成,系统已能在 CLI(`main.py`)跑出带引用报告并产出质量+运维 scorecard。**最后一块拼图是 spec §6 的 demo 卖点:把多 agent 协作过程实时可视化**,让面试官看到模型在动态决策(拆解 → 并行检索 → 验证 → 缺口再检索 → 撰写),而不只是"输入 → 报告"。

**目标用户:** 面试现场 live demo 的招聘方;简历投递前改 public 后的浏览者。

**本项目标:** 交付一个可 live demo、可回放、可测的 Streamlit 单页应用,复用已有 agent 系统(零改决策逻辑)与遥测层。

---

## 1. 范围

### 1.1 做(YAGNI 边界内)

- **实时编排可视化**:输入研究问题 → 多 agent 协作过程实时滚动时间线 → 最终带引用报告。
- **双模式**:① 真实运行(后台线程跑 `build_system().run()`,前端轮询事件)② 回放(从录制的 event trace 回放,零失败风险)。两模式共享同一渲染路径。
- **实时遥测条**:轮询 `TelemetrySink` 显示运行中成本 / Agent 利用率(agent 步数预算)。
- **自动录制**:真实运行完成自动把事件序列 + 报告 + 遥测快照存为 trace JSON,成为回放素材。

### 1.2 不做(YAGNI)

- **不做评估结果仪表盘**(M4 范围投票已定为「实时编排 demo only」)。已有的 scorecard / comparison / 基线对比数据继续走 README + console 表呈现,不入 UI。
- 不做多用户 / 鉴权 / 持久化 DB / 服务端部署(沿用总体 spec YAGNI)。
- 不做步级(每个 tool_call)事件流(噪音大、侵 `core/agent_loop.py`)。
- 不做多页 / 多会话并发;同一时刻只跑一个研究任务。

---

## 2. 三项已定决策(brainstorming 对齐)

1. **范围 = 实时编排 demo**(spec §6),不含评估仪表盘。
2. **双模式 = 真跑 + 回放**,共享一套事件基础设施。
3. **事件机制 = 类型化事件总线**:在叙事边界发结构化事件;`emit()` 取代现有 `progress()` 调用点(保留 stderr 日志行为),零改 agent 决策逻辑。

---

## 3. 架构与分层

沿用项目既有分层(`core/` < `agents/` < `eval/`/`ui/`,参照 `core/telemetry.py` 先例)。**核心纪律:所有可测逻辑沉到纯函数,`app.py` 的 `st.*` 渲染层保持极薄、不单测。**

| 模块 | 职责 | 依赖 |
|---|---|---|
| **`core/events.py`**(新) | `Event` 数据模型 + `EventSink`(线程安全收集器,同 `TelemetrySink` 风格)+ `events_to_json` / `events_from_json` | 无 |
| **`agents/observe.py`**(扩展) | 新增 `emit(kind, role, text, **payload)` + `set_sink` / `clear_sink` / `get_sink`。`emit` 内部:① 调 `progress(text)` 保留 stderr(沿用 `RESEARCH_QUIET`)② 若设了 active sink,构造 `Event` 推入 | `core.events` |
| **`ui/view.py`**(新) | **纯**视图构建器(全单测):`timeline_rows(events)`(时间线行数据,含并行推断)、`render_timeline_md(events)`(→ markdown)、`telemetry_tiles(snapshot, pricing)`(利用率/成本行数据)、`render_telemetry_md(snapshot, pricing)`(→ markdown)、`render_report_markdown(report)`(从 `main.py` 抽出复用) | `core.events`、`core.telemetry` |
| **`ui/controller.py`**(新) | `run_research(question, cfg, event_sink, telemetry_sink, *, run_fn=None) → report\|None`(后台线程目标,`run_fn`/`get_report` 可注入测试)+ `save_trace` / `load_trace` | `agents.system`、`core.events`、`core.telemetry` |
| **`ui/app.py`**(新) | Streamlit 薄壳:模式切换 + 输入 + 后台线程 + 轮询重画 + 失败兜底。只调 `controller` + `view`,不写业务逻辑 | `controller`、`view` |

**零改保证:** `emit()` 只取代 `system.py` / `orchestrator.py` 里**本就有 `progress()`** 的叙事边界调用点;`agents/` 的决策逻辑(AgentLoop、prompt、dispatch 并发、holder 确定性提取)一行不动。`emit()` 对这些调用点而言只是"换个会同时喂 UI 的日志函数",stderr 行为不变。

---

## 4. 事件模型(`core/events.py`)

### 4.1 Event

```python
@dataclass
class Event:
    kind: str            # 词表见 §6
    role: str | None     # "researcher" / "verifier" / "writer" / None(系统级)
    text: str            # 人类可读一句话(同现有 progress 文案语义,UI 时间线直接用)
    payload: dict        # 结构化字段,如 {"findings": 7, "round": 2}
    ts: float            # 相对 run 开始的秒数(EventSink 在 record 时打戳)
```

### 4.2 EventSink(线程安全,researcher 并发 emit 安全)

```python
class EventSink:
    def __init__(self): ...            # t0 = time.monotonic()
    def record(self, ev_partial) -> None  # Lock 下补 ts、append
    def snapshot(self) -> list[dict]:     # Lock 下返回深拷贝 dict 列表(UI 轮询 + 序列化共用)
```

设计要点(对齐 `TelemetrySink`):锁保护内部 list;`snapshot()` 返回 `dict` 列表而非 `Event`(序列化友好、UI 与 trace 落盘共用一条路径);`ts` 由 sink 在 `record` 时打(调用方不知 t0)。

### 4.3 序列化

- `events_to_json(events: list[dict]) -> str` / `events_from_json(s: str) -> list[dict]`:`json.dumps(..., ensure_ascii=False)` 往返;纯函数,单测覆盖。

---

## 5. `observe.emit()` 扩展(`agents/observe.py`)

```python
_active_sink = None  # 模块级;UI 控制器在跑 run 前 set、跑完 clear(单 run 假设)

def set_sink(sink): ...
def clear_sink(): ...
def get_sink(): ...

def emit(kind, role, text, **payload):
    """类型化事件:① progress(text) 打 stderr(RESEARCH_QUIET 静默);
    ② 若设了 active sink,构造 Event 推入。无 sink 时退化为纯日志(不影响 CLI/eval 链路)。"""
    progress(text)
    sink = get_sink()
    if sink is not None:
        sink.record(Event(kind=kind, role=role, text=text, payload=payload, ts=0.0))
```

**向后兼容:** `progress()` 原样保留(CLI `main.py`、未转换的诊断 `progress` 调用点继续用)。`emit()` 无 sink 时等价于 `progress(text)`,故 **eval / CLI 链路行为完全不变**(eval 不设 sink)。active sink 是"运行时可选 taps",与 `recorder` 注入同构。

---

## 6. 事件词表(钉到当前代码行)

> 行号以 `agents/orchestrator.py` / `agents/system.py` 当前版本为准;`writing-plans` 阶段以实际为准微调。

| kind | 触发点 | payload | UI 呈现 |
|---|---|---|---|
| `run_start` | `controller`(`loop.run` 前) | `{question}` | "研究开始" |
| `research_round` | `orchestrator.py:37`(`_dispatch_research` 起) | `{round, max_rounds, n_subquestions, is_gap}` | round=1 → "Orchestrator 拆解:①②③";is_gap=True → "缺口 → 再检索" |
| `research_start` | `system.py:39`(`_run_researcher`,worker 线程) | `{sub_question}`,role=`researcher` | "Researcher-N 检索「…」🔍";多条 ts 相近 → UI 推断"并行 ×N" |
| `research_done` | `system.py:44`(`_run_researcher` 完成) | `{sub_question}`,role=`researcher` | 单 researcher 完成标记 |
| `research_round_done` | `orchestrator.py:40` | `{round, findings, failures}` | "✓ 本轮找到 N 条 findings" |
| `verify_start` | `orchestrator.py:44`(`_verify_findings` 起),role=`verifier` | `{findings}` | "Verifier 复核中" |
| `verify_done` | `orchestrator.py:48` | `{results, verified, rejected}`,role=`verifier` | "⚠ N 条不支撑"(verified/rejected 由 verdict 统计:supported/weak→verified,unsupported→rejected) |
| `write_start` | `orchestrator.py:56`(`_write_report` 起),role=`writer` | `{outline_len, verified_findings}` | "Writer 综合报告中…" |
| `report_ready` | `orchestrator.py:65` | `{sections}` | "📄 报告就绪(N 节)" |
| `warn` | `orchestrator.py:31/34/54/63`(max_rounds / no_findings / write 拒绝 / writer 不可解析) | `{reason}` | 黄色警示行(软问题,非致命) |
| `run_done` | `controller`(`loop.run` 后) | `{success}` | 成功 / 失败终态 |
| `error` | `controller` catch(exception / holder None / 超时) | `{reason}` | 红色横幅(致命) |

**说明:**
- `verify_done` 的 verified/rejected 计数:在 `orchestrator.py:48` 处对 `parse_results()` 返回的 `VerificationResult[]` 按 `.verdict` 统计(轻量,仅 demo 信号)。
- 并行指示符不发专门事件;UI 由 `research_start` 的 ts 聚簇推断(多条 research_start 在任一 research_done 之前 → 并行)。
- 「计划/拆解」不发专门事件;首轮 `research_round` + 各 `research_start` 已自然揭示子问题集。

---

## 7. 控制器(`ui/controller.py`)

### 7.1 `run_research`

```python
def run_research(question, cfg, event_sink, telemetry_sink, *, run_fn=None):
    """后台线程目标。返回 Report | None。
    run_fn=None → 生产:build_system(cfg, recorder=telemetry_sink) → loop, get_report。
    run_fn 注入(loop, get_report) → 测试用 fake,不烧 API。
    """
    set_sink(event_sink)
    try:
        emit("run_start", None, f"[start] 研究问题:{question}", question=question)
        if run_fn is None:
            loop, get_report = build_system(cfg, recorder=telemetry_sink)
        else:
            loop, get_report = run_fn(cfg)
        loop.run(question)
        report = get_report()
        emit("run_done", None, f"[done] {'✓' if report else '✗'} 报告{'生成' if report else '未生成'}",
             success=bool(report))
        return report
    except Exception as e:  # 致命:Escalation / 未预期异常
        emit("error", None, f"[error] 运行失败:{e!r}", reason=repr(e))
        emit("run_done", None, "[done] ✗ 失败", success=False)
        return None
    finally:
        clear_sink()
```

**超时:** 由 `app.py` 的线程 join(timeout)兜底(见 §8),超时发 `error`。

### 7.2 Trace 录制 / 回放

- `save_trace(path, question, events, report, telemetry_snapshot, status)`:写 JSON 到 `ui/traces/<seq>_<slug>.json`(slug = 问题前 ~30 字、非字母数字→`-`)。
- `load_trace(path) -> dict`:读回;`status ∈ {"success","failed"}`。
- Trace schema:
  ```json
  {
    "question": "...",
    "events": [{"kind","role","text","payload","ts"}, ...],
    "report": {"sections":[...], "sources":[...]} | null,
    "telemetry": {...} | null,
    "status": "success" | "failed"
  }
  ```
- **开箱回放素材:** M4 收尾 DoD 真跑捕获的 trace 直接提交进 `ui/traces/`,确保回放模式开箱即用(不依赖用户先真跑)。

---

## 8. Streamlit 应用(`ui/app.py`)

### 8.1 布局

```
┌─ AI-PM-Agent · 深度研究多 Agent 编排 ──────────────────┐
│  模式: ● 真实运行   ○ 回放 [▾ 选 trace]               │
│  研究问题:[____________________________]  [▶ 开始]    │
│                                                        │
│  ── 实时编排 ──────────────  ⏱ 142s   💰 $0.03 ──     │
│  ▸ Orchestrator 拆解:① ② ③                            │
│  ▸ Researcher-1 检索「①…」🔍   (并行 ×3)             │
│  ✓ 本轮找到 7 条 findings                              │
│  ▸ Verifier 复核   ⚠ 2 条不支撑                        │
│  ▸ Orchestrator:缺口 → 再检索                          │
│  ▸ Writer 综合报告中…                                  │
│                                                        │
│  ── Agent 利用率(实时)──                             │
│  researcher  ████████░░  5.2/6 步 (87%)                │
│  verifier    █████░░░░░  6.2/12                        │
│                                                        │
│  ── 研究报告(完成后)──                               │
│  ## 标题  正文…[1]                                     │
│  来源:1. https://… (可点击)                           │
└────────────────────────────────────────────────────────┘
```

### 8.2 真实运行模式(线程 + 轮询)

```python
sink = EventSink(); tele = TelemetrySink(); holder = {}
def target():
    try:
        holder["report"] = run_research(question, cfg, sink, tele)
    except Exception as e:
        holder["error"] = repr(e)
    finally:
        holder["done"] = True
t = threading.Thread(target=target, daemon=True); t.start()

tl_ph = st.empty(); tm_ph = st.empty()
deadline = time.monotonic() + TIMEOUT_S   # 硬上限 ~480s(8min),demo 安全网
while not holder.get("done"):
    if time.monotonic() > deadline:
        holder["error"] = "运行超时(>8min)"; break
    tl_ph.markdown(view.render_timeline_md(sink.snapshot()))
    tm_ph.markdown(view.render_telemetry_md(tele.aggregate_by_name(), pricing))
    t.join(timeout=0.3)   # 等 0.3s 或线程结束
# 终态:成功 → render_report_markdown(report) + save_trace;失败 → 红色横幅
```

- **轮询重画**用 `st.empty()` 原地重写整个 timeline(append 由 `snapshot()` 的全量列表天然实现)。
- **阻塞说明:** 轮询期间页面非交互(单一 demo 任务,可接受);run 结束脚本继续到终态渲染。
- **并行 ×N 推断:** `view.timeline_rows` 扫描 events,把 ts 间距 < 阈值(如 0.5s)且无 `research_done` 隔开的连续 `research_start` 归为一批,标注并行数。

### 8.3 回放模式

- 加载 `ui/traces/*.json` → 下拉选择。
- 按事件 `ts` 顺序喂入**同一** `view.render_timeline_md`;每事件 `time.sleep(delay)`(delay 可调,默认 ~0.15s,提供"立即"档)。
- report/telemetry 直接从 trace 渲染(无需重跑)。

### 8.4 失败 / 健壮性

- 致命(`error` 事件 / holder None / 超时)→ 顶部红色横幅 + 保留已收 timeline,不崩 app。
- DeepSeek / Bocha 429 由现有 `robust` 重试;若 escalade → controller catch → `error` 事件。
- Trace 录制在 `finally` 里尽力落盘(失败 run 也存,供复盘)。

---

## 9. 测试策略(全 mock,不烧 API)

沿用项目"全 mock TDD"纪律(`206` 测试基线,目标 +15~20 → ~225)。**逻辑全在纯函数,`app.py` 不单测。**

| 目标 | 覆盖 | 手段 |
|---|---|---|
| `core/events.py` | Event 构造 / EventSink 线程安全并发 append / snapshot 深拷贝隔离 / `events_to_json`↔`events_from_json` 往返 | 纯函数 + `ThreadPoolExecutor` 并发(同 telemetry 并发测试风格) |
| `observe.emit()` | 设 sink→推 Event;无 sink→退化为纯 stderr;`RESEARCH_QUIET` 静默;`set/clear/get_sink` | `capsys`/cap stderr + `RESEARCH_QUIET`(沿用 `test_observe.py`) |
| `ui/controller.py` | fake `run_fn`(返回 fake loop.run + fake get_report)→ 事件序列正确 + report 透传;异常路径→`error`+`run_done(success=False)`;`save/load_trace` 往返 | 注入 fake,**零 API** |
| `ui/view.py` | `timeline_rows`(并行推断)/ `render_timeline_md`(emoji 标记 ▸/✓/⚠/✗)/ `telemetry_tiles` + `render_telemetry_md`(成本+预算条)/ `render_report_markdown`(复用 main.py 断言) | mock 事件列表 / mock snapshot 断言输出字符串 |
| `ui/app.py` | 仅冒烟 `import app`(不渲染) | `importlib` |

**既有测试不破:** `emit()` 无 sink 时等价 `progress(text)`;eval / CLI 链路不设 sink,行为不变。转换的 `progress`→`emit` 调用点若原有 `test_observe`/`test_orchestrator*` 断言 stderr 文案,按新文案微调(文案语义不变)。

**小重构:** `main.py:render_markdown` 抽到 `ui/view.py:render_report_markdown`,`main.py` 改 import 复用(DRY;`test_main.py` 相应调整)。

---

## 10. 验收标准(DoD)

1. **全 mock 测试绿**(~225),含上述新增;既有 206 不破。
2. **冒烟 import** `ui/app.py` 无错。
3. **真实运行真跑**(`streamlit run ui/app.py`,真实 DeepSeek + 博查 + GLM 不在此模式触发,真跑只走生成链路):输入一题 → 实时 timeline 滚动(拆解/并行检索/验证/撰写可见)→ 报告渲染 + 可点击来源 + 实时遥测条有数;**自动落 trace** 到 `ui/traces/`。
4. **回放验证**:用上一步落的 trace 回放,渲染与真跑一致。
5. **失败兜底验证**:构造失败(如 fake run 抛异常或 holder None)→ 红色横幅 + 不崩。
6. **README** 加 M4 段(运行方式 `streamlit run ui/app.py` + 双模式说明 + 截图位)。

> 成本提示:DoD 真跑单题产品链路 ~$0.05(DeepSeek,沿用 M3 后续遥测实测单价);回放模式零成本。

---

## 11. 风险与对策

| 风险 | 对策 |
|---|---|
| Streamlit 阻塞轮询致页面假死 | 单一 demo 任务可接受;硬超时 8min 安全网;回放模式即时 |
| 真跑偶发 `max_steps` 失败(q002 模式) | 失败兜底 + 回放兜底;DoD 选稳定题录制 trace |
| `emit` 全局 sink 在并发/多 run 下错乱 | 单 run 假设,文档标注;`finally: clear_sink()` |
| 并行推断阈值不准 | 阈值可调;最坏退化为不标并行(不影响正确性) |
| Streamlit 版本 API 差异 | requirements 已 pin `streamlit>=1.30`;`st.empty()`/`st.markdown` 为稳定 API |

---

## 12. 待确认 / 下一步

- [ ] 用户 review 本 spec → `writing-plans` 出 TDD 施工图(Task 粒度:core/events → observe.emit → view → controller → app → 真跑 DoD)。
