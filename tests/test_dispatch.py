import threading
import time

from agents.dispatch import (
    clean_json, parse_findings, parse_results, parse_report, dispatch_research,
)


def test_clean_json_strips_markdown_fence():
    assert clean_json('```json\n{"a":1}\n```') == '{"a":1}'
    assert clean_json('{"a":1}') == '{"a":1}'
    assert clean_json("") == ""
    assert clean_json("  ```\n{}\n```  ") == "{}"


def test_parse_findings_valid():
    text = '{"findings": [{"id":"f1","claim":"c","source_url":"https://x"}]}'
    fs = parse_findings(text)
    assert len(fs) == 1 and fs[0].claim == "c"


def test_parse_findings_tolerates_bad_json():
    assert parse_findings("not json at all") == []
    assert parse_findings('{"findings": [{"id":"x"}]}') == []  # 缺 source_url → 跳过坏项


def test_parse_results_and_report():
    rs = parse_results('{"results":[{"finding_id":"f1","verdict":"supported"}]}')
    assert rs[0].verdict == "supported"
    rep = parse_report('{"sections":[{"heading":"H","content":"C"}],"sources":["https://x"]}')
    assert rep is not None and rep.sections[0].heading == "H"
    assert parse_report("garbage") is None


def _result(content):
    return type("R", (), {"content": content})()


def test_dispatch_merges_findings_and_renumbers():
    def run_one(sq):
        return _result(f'{{"findings": [{{"id":"f1","claim":"{sq}","source_url":"https://x"}}]}}')
    out = dispatch_research(["q1", "q2"], run_one)
    assert [f["id"] for f in out["findings"]] == ["f1", "f2"]  # 去重重编号
    assert {f["claim"] for f in out["findings"]} == {"q1", "q2"}
    assert out["failures"] == []


def test_dispatch_tolerates_single_failure():
    def run_one(sq):
        if sq == "bad":
            raise RuntimeError("boom")
        return _result(f'{{"findings": [{{"id":"f1","claim":"{sq}","source_url":"https://x"}}]}}')
    out = dispatch_research(["good", "bad"], run_one)
    assert len(out["findings"]) == 1
    assert len(out["failures"]) == 1 and out["failures"][0]["sub_question"] == "bad"


def test_dispatch_runs_concurrently():
    lock = threading.Lock()
    state = {"active": 0, "max": 0}

    def run_one(sq):
        with lock:
            state["active"] += 1
            state["max"] = max(state["max"], state["active"])
        time.sleep(0.05)
        with lock:
            state["active"] -= 1
        return _result('{"findings":[]}')

    dispatch_research(["q1", "q2", "q3"], run_one)
    assert state["max"] >= 2  # 确实并发(非串行)
