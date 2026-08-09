"""子 agent 派发原语(集中管理,单一职责):
- JSON 解析:子 agent 输出严格 JSON,但常套 markdown 围栏 / 带散文,经 core.json_utils 清洗 + safe_parse_arguments 兜底。
- 并行派发 dispatch_research:ThreadPoolExecutor 并发跑 N 个 researcher,合并 findings,单点容错。"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from core.json_utils import clean_json, json_balanced_substring
from core.robust import safe_parse_arguments
from core.schemas import Finding, VerificationResult, Report


def _items(text: str, key: str) -> list[dict]:
    """从 {"<key>":[...]} 提取列表。坏 JSON → []。"""
    data = safe_parse_arguments(json_balanced_substring(clean_json(text)))
    if not isinstance(data, dict):
        return []
    return data.get(key, []) or []


def parse_findings(text: str) -> list[Finding]:
    out = []
    for it in _items(text, "findings"):
        try:
            out.append(Finding(**it))
        except Exception:
            continue  # 坏项跳过(容错)
    return out


def parse_results(text: str) -> list[VerificationResult]:
    out = []
    for it in _items(text, "results"):
        try:
            out.append(VerificationResult(**it))
        except Exception:
            continue
    return out


def parse_report(text: str) -> Optional[Report]:
    data = safe_parse_arguments(json_balanced_substring(clean_json(text)))
    if not isinstance(data, dict):
        return None
    try:
        return Report(**data)
    except Exception:
        return None


def dispatch_research(sub_questions: list[str], run_one, *, max_workers: int | None = None) -> dict:
    """并发跑 N 个 researcher(每个 sub_question 一个),合并 findings,单点容错。

    run_one(sub_question) -> 对象(需有 .content 属性,JSON 字符串,如 AgentResult)。
    单个 run_one 抛异常(Escalation 等)不阻塞整体,记入 failures。
    返回 {"findings":[...], "failures":[...]}(回填给 Orchestrator 的精简结构)。"""
    findings: list[Finding] = []
    failures: list[dict] = []
    workers = max_workers or max(1, len(sub_questions))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        future_map = {ex.submit(run_one, sq): sq for sq in sub_questions}
        for fut in as_completed(future_map):
            sq = future_map[fut]
            try:
                result = fut.result()
                findings.extend(parse_findings(result.content))
            except Exception as e:  # 单点失败:容错,不阻塞整体
                failures.append({"sub_question": sq, "error": repr(e)})
    # 多 researcher 可能都用 "f1" → 统一重编号,避免 id 冲突
    for i, f in enumerate(findings, 1):
        f.id = f"f{i}"
    return {"findings": [f.model_dump() for f in findings], "failures": failures}
