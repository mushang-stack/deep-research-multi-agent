import threading
import time

from agents.dispatch import (
    parse_findings, parse_results, parse_report, dispatch_research,
)


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


def test_parse_findings_extracts_json_from_prose():
    # DeepSeek 常在 JSON 前输出思考散文("我已经收集了…让我整理…")——解析层必须把 JSON 抠出来
    text = ('我已经收集了丰富的资料。现在让我整理这些发现,形成结构化的 JSON 输出。\n\n'
            '基于读取的内容整理如下。\n\n'
            '{"findings": [{"id":"f1","claim":"投机解码可加速推理","source_url":"https://x"}]}')
    fs = parse_findings(text)
    assert len(fs) == 1 and fs[0].claim == "投机解码可加速推理"


def test_parse_results_extracts_json_from_prose():
    text = '复核完成,结论如下:\n\n{"results":[{"finding_id":"f1","verdict":"supported"}]}'
    rs = parse_results(text)
    assert len(rs) == 1 and rs[0].verdict == "supported"


def test_parse_report_extracts_json_from_prose():
    text = '好的,这是报告。\n\n{"sections":[{"heading":"H","content":"C"}],"sources":["https://x"]}'
    rep = parse_report(text)
    assert rep is not None and rep.sections[0].heading == "H"


def test_parse_findings_extracts_json_from_fenced_prose():
    # 散文 + 围栏 JSON 的组合也应被正确提取
    text = '思考中…\n```json\n{"findings":[{"id":"f1","claim":"c","source_url":"https://x"}]}\n```'
    fs = parse_findings(text)
    assert len(fs) == 1 and fs[0].claim == "c"


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
