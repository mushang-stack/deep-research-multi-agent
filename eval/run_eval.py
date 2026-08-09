"""评估编排 + CLI(spec §4.4)。逐题:跑系统 → 抽 trace → 逐条裁判 → 算分 → scorecard。

零侵入:只消费 build_system 的产物(get_report() 的 Report、loop.run() 的 history)。
"""
from agents.system import build_system
from core.schemas import Report

from .judge import judge_finding, judge_key_fact
from .metrics import compute_question_metrics
from .trace import extract_findings


def report_to_text(report: Report) -> str:
    """把 Report 拼成裁判可读的纯文本(heading + content)。"""
    parts = []
    for sec in report.sections:
        parts.append(f"## {sec.heading}\n{sec.content}")
    return "\n\n".join(parts)


def run_one(question: str, cfg, *, client=None, search_client=None):
    """跑一次系统,返回 (report, history)。report 可能为 None。"""
    loop, get_report = build_system(cfg, client=client, search_client=search_client)
    result = loop.run(question)
    return get_report(), result.history


def evaluate_question(item: dict, cfg, *, judge_client,
                      client=None, search_client=None, run_one_fn=None):
    """单题评估 → scorecard。

    run_one_fn 可注入(测试用,跳过真实 agent 链);为 None 时用真实 run_one。
    """
    question = item["question"]
    runner = run_one_fn or (lambda q: run_one(q, cfg, client=client,
                                              search_client=search_client))
    report, history = runner(question)

    if report is None:
        return {"id": item["id"], "question": question,
                **compute_question_metrics([], [], report_produced=False)}

    findings = extract_findings(history)
    finding_verdicts = [
        judge_finding(claim=f.get("claim", ""), excerpt=f.get("excerpt", ""),
                      source_url=f.get("source_url", ""), client=judge_client)
        for f in findings
    ]
    text = report_to_text(report)
    key_fact_verdicts = [
        judge_key_fact(key_fact=kf, report_text=text, client=judge_client)
        for kf in item.get("key_facts", [])
    ]
    return {"id": item["id"], "question": question,
            **compute_question_metrics(finding_verdicts, key_fact_verdicts,
                                       report_produced=True)}
