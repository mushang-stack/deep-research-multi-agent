"""系统组装工厂:从 config 构造四个 agent 并接好 Orchestrator 的派发工具。
config wiring:生产链路一律从 config 注入(client 硬编码默认仅作 fallback)→ 零侵入,闭环 M1 待办。"""
from llm.base import LLMClient
from llm.deepseek_client import DeepSeekClient
from tools.web_search import BochaSearchClient
from core.config import Config, env

from core.budget import Budget
from core.agent_loop import CompactionConfig

from .researcher import make_researcher
from .verifier import make_verifier
from .writer import make_writer
from .orchestrator import make_orchestrator
from .observe import progress, emit


def build_system(config: Config, *, client: LLMClient | None = None,
                 search_client=None, verify: bool = True, recorder=None):
    """组装整个系统,返回 (orchestrator_loop, get_report)。

    client / search_client 可注入(测试用 fake);为 None 时按 config + env 构造真实 client。
    recorder:可选 TelemetrySink,注入四个 agent 做运维遥测。
    """
    gen = config["models"]["generator"]
    client = client or DeepSeekClient(
        base_url=gen["base_url"], model=gen["name"],
        temperature=gen["temperature"], max_tokens=gen["max_tokens"],
    )

    ws = config["tools"]["web_search"]
    search_client = search_client or BochaSearchClient(
        api_key=env("BOCHA_API_KEY"), endpoint=ws["endpoint"], count=ws["count"],
    )

    max_chars = config["tools"]["web_read"]["max_chars"]
    guards = config["guards"]
    max_steps = guards["agent_max_steps"]
    researcher_max_steps = guards.get("researcher_max_steps", max_steps)
    research_max_rounds = guards["research_max_rounds"]

    # ── v2.1 护栏接线(P1 预算 / 循环层截断 / P2 压缩);缺省全关 ──
    run_pool = Budget(guards["run_token_budget"]) if guards.get("run_token_budget") else None

    def _budgets_for(kind: str):
        limit = guards.get(f"{kind}_token_budget")
        bs = ([Budget(limit)] if limit else []) + ([run_pool] if run_pool else [])
        return bs or None

    researcher_on_exhaustion = guards.get("researcher_on_exhaustion", "escalate")
    if researcher_on_exhaustion not in ("escalate", "partial"):
        raise ValueError(f"researcher_on_exhaustion 非法:{researcher_on_exhaustion!r}(允许 escalate|partial)")

    trunc = config["tools"].get("max_tool_result_chars")

    comp_cfg = config.get("compaction") or {}
    if comp_cfg.get("enabled") and comp_cfg.get("strategy", "stub") != "stub":
        raise ValueError(f"compaction.strategy 非法:{comp_cfg.get('strategy')!r}(本轮只实现 stub)")
    compaction = CompactionConfig(
        enabled=True,   # 只在 enabled 时构造,此处恒真(构造出即启用)
        threshold_prompt_tokens=comp_cfg.get("threshold_prompt_tokens", 30000),
        keep_last_n=comp_cfg.get("keep_last_n", 4),
        strategy=comp_cfg.get("strategy", "stub"),
    ) if comp_cfg.get("enabled") else None   # 未启用 → None,循环零路径

    def _on_compact(info: dict) -> None:
        emit("compact", info["agent"],
             f"  [{info['agent']}] 上下文压缩:prompt {info['last_prompt_tokens']} > "
             f"阈值 {info['threshold']},存根化 {info['stubbed_tool_msgs']} 条 tool 消息"
             f"(保留末尾 {info['kept_last']} 条)", **info)

    common_loop_kwargs = {"max_tool_result_chars": trunc,
                          "compaction": compaction,
                          "on_compact": _on_compact if compaction else None}
    # orchestrator 免循环层截断与压缩(Task 3/6 质量审发现):其工具结果是结构化 JSON
    # (dispatch_research 合并 findings / verify 复核结果)——截断破坏 JSON 完整性;存根化直接抹掉
    # 整轮结果(eval trace 静默丢 findings 污染 off/on 对比;verify 判定只存在于 tool 消息,老化即永久
    # 丢失,模型从此无法过滤 supported findings)。截断/压缩防御纵深针对 researcher/verifier 的原始网页文本
    orchestrator_loop_kwargs = {"budgets": _budgets_for("orchestrator")}

    def _run_researcher(sub_question):
        emit("research_start", "researcher",
             f"  [researcher] 检索子问题:{sub_question}",
             sub_question=sub_question)
        result = make_researcher(client=client, search_client=search_client,
                                 max_chars=max_chars, max_steps=researcher_max_steps,
                                 recorder=recorder,
                                 loop_kwargs={**common_loop_kwargs,
                                              "budgets": _budgets_for("researcher"),
                                              "on_exhaustion": researcher_on_exhaustion}).run(sub_question)
        content = result.content or ""
        emit("research_done", "researcher",
             f"  [researcher] 完成 → content {len(content)} 字",
             sub_question=sub_question, content_len=len(content),
             degraded=result.degraded)
        return result

    def _run_verifier(findings_json):
        progress("  [verifier] 复核 findings 来源支撑")
        return make_verifier(client=client, search_client=search_client,
                             max_chars=max_chars, max_steps=max_steps,
                             recorder=recorder,
                             loop_kwargs={**common_loop_kwargs,
                                          "budgets": _budgets_for("verifier")}).run(findings_json)

    def _run_writer(user_message):
        progress("  [writer] 综合带引用报告(无工具,仅用传入 findings)")
        return make_writer(client=client, max_steps=max_steps,
                           recorder=recorder,
                           loop_kwargs={**common_loop_kwargs,
                                        "budgets": _budgets_for("writer")}).run(user_message)

    return make_orchestrator(
        client=client,
        run_researcher=_run_researcher, run_verifier=_run_verifier, run_writer=_run_writer,
        max_steps=max_steps, research_max_rounds=research_max_rounds, verify=verify,
        recorder=recorder,
        loop_kwargs=orchestrator_loop_kwargs,
    )
