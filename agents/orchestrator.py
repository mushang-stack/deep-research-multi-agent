"""Orchestrator:规划中枢。AgentLoop + 三个内部派发工具。
子 agent runner 注入(生产由 system.py 绑定共享 client,测试注入 fake)。
状态用闭包:report_holder(机制级确定性提取)、round_counter(轮次护栏)。"""
import json

from core.agent_loop import AgentLoop
from core.tool_registry import ToolRegistry
from .dispatch import dispatch_research, parse_results, parse_report
from .observe import emit
from .prompts import ORCHESTRATOR_PROMPT, NO_VERIFY_ORCHESTRATOR_PROMPT


def make_orchestrator(*, client, run_researcher, run_verifier, run_writer,
                      max_steps: int = 12, research_max_rounds: int = 3,
                      verify: bool = True, recorder=None,
                      loop_kwargs: dict | None = None):
    """返回 (orchestrator_loop, get_report)。

    run_researcher(sub_question) -> AgentResult   (content = Findings JSON)
    run_verifier(findings_json)  -> AgentResult   (content = Results JSON)
    run_writer(user_message)     -> AgentResult   (content = Report JSON)
    get_report() -> Report | None   (从 holder 取;None 表示从未成功 write_report)
    """
    holder: dict = {}
    round_counter: dict = {"n": 0}
    findings_total: dict = {"n": 0}  # 累计所有轮次 findings(判断是否空研究)

    def _dispatch_research(sub_questions: list[str]) -> dict:
        round_counter["n"] += 1
        if round_counter["n"] > research_max_rounds:
            if findings_total["n"] == 0:
                emit("warn", None,
                     f"[orchestrator] ⚠ 已达最大研究轮次({research_max_rounds})且未获得任何 findings",
                     reason="max_rounds_no_findings")
                return {"status": "no_findings",
                        "message": "已达最大研究轮次且未获得任何 findings。不要调用 write_report,直接用一句话结束(说明未能获取到资料)。"}
            emit("warn", None,
                 f"[orchestrator] ⚠ 已达最大研究轮次({research_max_rounds}),请直接 write_report 收尾",
                 reason="max_rounds_reached")
            return {"status": "max_rounds_reached",
                    "message": "已达最大研究轮次,请直接 write_report,不要再检索。"}
        emit("research_round", None,
             f"[orchestrator] dispatch_research 第 {round_counter['n']}/{research_max_rounds} 轮,派发 {len(sub_questions)} 个子问题",
             round=round_counter["n"], max_rounds=research_max_rounds,
             n_subquestions=len(sub_questions), is_gap=round_counter["n"] > 1)
        out = dispatch_research(sub_questions, run_researcher)
        findings_total["n"] += len(out["findings"])
        emit("research_round_done", None,
             f"[orchestrator] dispatch_research 完成 → {len(out['findings'])} 条 findings,{len(out['failures'])} 个失败子问题",
             round=round_counter["n"], findings=len(out["findings"]), failures=len(out["failures"]))
        return out

    def _verify_findings(findings: list[dict]) -> dict:
        emit("verify_start", "verifier",
             f"[orchestrator] verify_findings → 复核 {len(findings)} 条 findings",
             findings=len(findings))
        msg = json.dumps({"findings": findings}, ensure_ascii=False)
        result = run_verifier(msg)
        results = parse_results(result.content)
        supported = sum(1 for r in results if r.verdict == "supported")
        weak = sum(1 for r in results if r.verdict == "weak")
        unsupported = sum(1 for r in results if r.verdict == "unsupported")
        emit("verify_done", "verifier",
             f"[orchestrator] verify_findings 完成 → {len(results)} 条结果(supported {supported}/weak {weak}/unsupported {unsupported})",
             results=len(results), supported=supported, weak=weak, unsupported=unsupported)
        return {"results": [r.model_dump() for r in results]}

    def _write_report(outline: str, verified_findings: list[dict]) -> dict:
        if not verified_findings:
            # 防御:无 verified findings 时不调 writer(避免空报告;博查耗尽等场景曾暴露此路径)
            emit("warn", None,
                 "[orchestrator] ✗ write_report 被拒绝:无 verified findings(避免空报告)",
                 reason="write_refused_no_findings")
            return {"error": "无 verified findings,无法生成报告。不要用空 findings 调用 write_report。"}
        emit("write_start", "writer",
             f"[orchestrator] write_report → 让 Writer 综合报告(outline {len(outline)} 字,{len(verified_findings)} 条 verified findings)",
             outline_len=len(outline), verified_findings=len(verified_findings))
        msg = json.dumps({"outline": outline, "verified_findings": verified_findings},
                         ensure_ascii=False)
        result = run_writer(msg)
        report = parse_report(result.content)
        if report is None:
            snippet = (result.content or "")[:200]
            emit("warn", None,
                 f"[orchestrator] ✗ Writer 输出无法解析为 Report。原始 content 前 200 字:{snippet!r}",
                 reason="writer_unparseable")
            return {"error": "writer 产出不可解析,请重试或基于现有 findings 重写"}
        emit("report_ready", None,
             f"[orchestrator] ✓ 报告已生成({len(report.sections)} 节),存入 holder",
             sections=len(report.sections))
        holder["report"] = report  # 机制级确定性提取
        return report.model_dump()

    prompt = ORCHESTRATOR_PROMPT if verify else NO_VERIFY_ORCHESTRATOR_PROMPT

    reg = ToolRegistry()
    reg.register(
        "dispatch_research", _dispatch_research,
        description="对一组子问题并发检索,返回 {findings:[...], failures:[...]}。",
        parameters={"type": "object",
                    "properties": {"sub_questions": {"type": "array", "items": {"type": "string"}}},
                    "required": ["sub_questions"]},
    )
    if verify:
        reg.register(
            "verify_findings", _verify_findings,
            description="复核一批 findings 是否有来源支撑,返回 {results:[{finding_id,verdict,reason,...}]}。",
            parameters={"type": "object",
                        "properties": {"findings": {"type": "array", "items": {"type": "object"}}},
                        "required": ["findings"]},
        )
    reg.register(
        "write_report", _write_report,
        description="基于已验证 findings 综合带引用报告。调用后用一句话收尾,不再调任何工具。",
        parameters={"type": "object",
                    "properties": {"outline": {"type": "string"},
                                   "verified_findings": {"type": "array", "items": {"type": "object"}}},
                    "required": ["outline", "verified_findings"]},
    )

    loop = AgentLoop(client=client, system_prompt=prompt,
                     registry=reg, max_steps=max_steps, name="orchestrator",
                     recorder=recorder,
                     **(loop_kwargs or {}))

    def get_report():
        return holder.get("report")

    return loop, get_report
