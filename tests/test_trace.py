from eval.trace import extract_findings


def _tool(content, cid="1"):
    return {"role": "tool", "tool_call_id": cid, "content": content}


def test_extract_findings_from_dispatch_tool_message():
    history = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "问题"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "1", "type": "function",
             "function": {"name": "dispatch_research",
                          "arguments": '{"sub_questions":["q1"]}'}}]},
        _tool('{"findings":[{"id":"f1","claim":"投机解码加速推理",'
              '"source_url":"https://x","excerpt":"摘录"}],"failures":[]}'),
        {"role": "assistant", "content": "报告已生成"},
    ]
    fs = extract_findings(history)
    assert len(fs) == 1
    assert fs[0]["claim"] == "投机解码加速推理"
    assert fs[0]["source_url"] == "https://x"
    assert fs[0]["excerpt"] == "摘录"


def test_extract_findings_skips_non_findings_tool_messages():
    # verify_findings(results) 与 write_report(sections) 的 tool 消息必须被跳过
    history = [
        _tool('{"results":[{"finding_id":"f1","verdict":"supported"}]}', "1"),
        _tool('{"sections":[{"heading":"H","content":"C"}],"sources":["https://x"]}', "2"),
        _tool('{"findings":[{"id":"f1","claim":"c","source_url":"https://x"}]}', "3"),
    ]
    fs = extract_findings(history)
    assert len(fs) == 1 and fs[0]["claim"] == "c"


def test_extract_findings_dedups_by_claim_and_source():
    # 多轮 dispatch 重复返回同一条 → 按 (claim, source_url) 去重
    dup = ('{"findings":[{"id":"f1","claim":"同一条","source_url":"https://x"},'
           '{"id":"f2","claim":"同一条","source_url":"https://x"}]}')
    history = [_tool(dup, "1"), _tool(dup, "2")]
    fs = extract_findings(history)
    assert len(fs) == 1  # 两次 dispatch × 各 2 条同内容 → 去重后 1 条


def test_extract_findings_merges_across_dispatch_rounds():
    history = [
        _tool('{"findings":[{"id":"f1","claim":"A","source_url":"https://a"}]}', "1"),
        _tool('{"findings":[{"id":"f1","claim":"B","source_url":"https://b"}]}', "2"),
    ]
    fs = extract_findings(history)
    assert {f["claim"] for f in fs} == {"A", "B"}


def test_extract_findings_tolerates_garbage_content():
    history = [
        _tool("not json at all", "1"),
        _tool('{"findings":[{"id":"f1","claim":"c","source_url":"https://x"}]}', "2"),
    ]
    fs = extract_findings(history)
    assert len(fs) == 1  # 坏消息跳过,好的照收


def test_extract_findings_empty_history():
    assert extract_findings([]) == []
