"""子 agent 派发原语(集中管理,单一职责):
- JSON 清洗/解析:子 agent 输出严格 JSON,但模型常套 markdown 代码块/带杂字,这里清洗+兜底。
- 并行派发 dispatch_research:ThreadPoolExecutor 并发跑 N 个 researcher,合并 findings,单点容错。"""
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from core.robust import safe_parse_arguments
from core.schemas import Finding, VerificationResult, Report


def clean_json(text: str) -> str:
    """去除 markdown 代码块包裹(```json ... ```)与首尾空白。"""
    if not text:
        return ""
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*\n?", "", s)
        s = re.sub(r"\n?```\s*$", "", s)
    return s.strip()


def _json_substring(text: str) -> str:
    """从文本中抠出第一个平衡的 JSON 对象子串。

    模型(尤其 DeepSeek)常在 JSON 前后输出思考散文("我已经收集了…让我整理…{json}")。
    clean_json 只去围栏,去不掉这种散文 → json.loads 整段必失败。这里用大括号深度
    扫描(识别字符串字面量与转义),把第一个平衡的 {...} 抠出来。找不到则原样返回,
    交由 safe_parse_arguments 兜底成 {}。纯 JSON / 围栏 JSON 不受影响(首字符即 {)。"""
    start = text.find("{")
    if start == -1:
        return text
    depth = 0
    in_str = False
    escaped = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
    return text  # 不平衡:原样返回,让上层兜底


def _items(text: str, key: str) -> list[dict]:
    """从 {"<key>":[...]} 提取列表。坏 JSON → []。"""
    data = safe_parse_arguments(_json_substring(clean_json(text)))
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
    data = safe_parse_arguments(_json_substring(clean_json(text)))
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
