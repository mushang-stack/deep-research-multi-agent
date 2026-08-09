"""从研究 history 抽取 findings(零侵入:只消费 orchestrator 的 AgentResult.history)。

orchestrator history 里 role=="tool" 消息有三类:dispatch_research(含 findings)、
verify_findings(含 results)、write_report(含 sections)。本模块只收含 findings 键的,
跳过另两类;多轮 dispatch 合并,按 (claim, source_url) 去重。
"""
from core.json_utils import clean_json, json_balanced_substring
from core.robust import safe_parse_arguments


def extract_findings(history: list[dict]) -> list[dict]:
    """history -> [{id,claim,source_url,source_title,excerpt,confidence}, ...]。

    遍历 role=="tool" 消息,解析 content,凡含 findings 键的合并其列表,
    按 (claim, source_url) 去重(保首次出现的完整字段)。
    """
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for msg in history:
        if not isinstance(msg, dict) or msg.get("role") != "tool":
            continue
        data = safe_parse_arguments(json_balanced_substring(clean_json(msg.get("content", ""))))
        if not isinstance(data, dict) or "findings" not in data:
            continue
        findings_list = data.get("findings")
        if not isinstance(findings_list, list):  # findings 非 list(坏 JSON)→ 跳过
            continue
        for f in findings_list:
            if not isinstance(f, dict):
                continue
            key = (f.get("claim", ""), f.get("source_url", ""))
            if key in seen:
                continue
            seen.add(key)
            out.append(f)
    return out
