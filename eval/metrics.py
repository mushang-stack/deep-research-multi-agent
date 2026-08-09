"""质量指标纯函数(spec §3)。由裁判 verdict 算分,无 IO / 无 LLM。

每题先算(compute_question_metrics),再跨题取均值(aggregate)。
N = 该题 findings 数,M = 该题 key_facts 数。
"""
from typing import Optional

_SUPPORT_SCORE = {"supported": 1.0, "partial": 0.5}  # 其余(含 unsupported)→ 0.0


def _mean(xs: list[float]) -> Optional[float]:
    return sum(xs) / len(xs) if xs else None


def compute_question_metrics(findings_verdicts: list[dict],
                             key_fact_verdicts: list[dict],
                             report_produced: bool) -> dict:
    """单题 5 数 + 不可用标注。verdict 形状见 plan「数据契约」。"""
    n = len(findings_verdicts)
    m = len(key_fact_verdicts)

    parse_fails = sum(1 for v in findings_verdicts if v.get("parse_failed")) \
                + sum(1 for v in key_fact_verdicts if v.get("parse_failed"))

    if n > 0:
        grounding = sum(_SUPPORT_SCORE.get(v.get("support"), 0.0)
                        for v in findings_verdicts) / n
        citation = sum(1 for v in findings_verdicts
                       if v.get("source_real") is True) / n
        hallucination = sum(1 for v in findings_verdicts
                            if v.get("support") == "unsupported"
                            or v.get("source_real") is not True) / n
    else:
        grounding = citation = hallucination = None

    coverage = (sum(1 for v in key_fact_verdicts if v.get("covered") is True) / m
                if m > 0 else None)

    return {
        "success": bool(report_produced),
        "failed": None if report_produced else "no_report",
        "n_findings": n,
        "n_key_facts": m,
        "grounding": grounding,
        "citation": citation,
        "coverage": coverage,
        "hallucination": hallucination,
        "grounding_available": n > 0,
        "judge_parse_failures": parse_fails,
    }


def aggregate(scorecards: list[dict], grounding_min: float) -> dict:
    """跨题均值 + 门槛判定。Grounding 均值只在 grounding 非 None 的题上取。"""
    n_q = len(scorecards)
    n_success = sum(1 for s in scorecards if s.get("success"))
    g_vals = [s["grounding"] for s in scorecards if s.get("grounding") is not None]
    mean_grounding = _mean(g_vals)

    return {
        "n_questions": n_q,
        "n_success": n_success,
        "success_rate": n_success / n_q if n_q else 0.0,
        "mean_grounding": mean_grounding,
        "mean_citation": _mean([s["citation"] for s in scorecards
                                if s.get("citation") is not None]),
        "mean_coverage": _mean([s["coverage"] for s in scorecards
                                if s.get("coverage") is not None]),
        "mean_hallucination": _mean([s["hallucination"] for s in scorecards
                                     if s.get("hallucination") is not None]),
        "grounding_questions": len(g_vals),
        "grounding_min": grounding_min,
        "passed": bool(mean_grounding is not None and mean_grounding >= grounding_min),
        "total_judge_parse_failures": sum(s.get("judge_parse_failures", 0)
                                          for s in scorecards),
    }
