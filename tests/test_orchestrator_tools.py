import json

from agents.orchestrator import make_orchestrator
from agents.prompts import ORCHESTRATOR_PROMPT, NO_VERIFY_ORCHESTRATOR_PROMPT
from llm.base import LLMClient, LLMResponse


class FakeClient(LLMClient):
    def __init__(self, responses):
        self._r = list(responses)

    def chat(self, **kw):
        return self._r.pop(0)


def _result(content):
    return type("R", (), {"content": content})()


def _make(run_researcher, run_verifier, run_writer, *, research_max_rounds=3):
    return make_orchestrator(
        client=FakeClient([]),
        run_researcher=run_researcher, run_verifier=run_verifier, run_writer=run_writer,
        research_max_rounds=research_max_rounds,
    )


def test_orchestrator_wiring():
    loop, get_report = _make(lambda sq: _result('{"findings":[]}'),
                              lambda f: _result('{"results":[]}'),
                              lambda m: _result('{"sections":[],"sources":[]}'))
    assert loop.system_prompt == ORCHESTRATOR_PROMPT
    assert loop.name == "orchestrator"
    assert set(loop.registry.names()) == {"dispatch_research", "verify_findings", "write_report"}
    assert get_report() is None  # 初始无报告


def test_dispatch_research_calls_runner_and_merges():
    seen = []

    def run_researcher(sq):
        seen.append(sq)
        return _result(json.dumps({"findings": [{"id": "f1", "claim": sq, "source_url": "https://x"}]}))

    loop, _ = _make(run_researcher, lambda f: _result('{}'), lambda m: _result('{}'))
    out = loop.registry.execute("dispatch_research", {"sub_questions": ["q1", "q2"]})
    assert set(seen) == {"q1", "q2"}
    assert len(out["findings"]) == 2
    assert out["failures"] == []


def test_dispatch_research_round_guard():
    # 有 findings 的前提下,达 max_rounds → 引导 write_report 收尾
    loop, _ = _make(lambda sq: _result('{"findings":[{"id":"f1","claim":"c","source_url":"https://x"}]}'),
                    lambda f: _result('{}'), lambda m: _result('{}'),
                    research_max_rounds=2)
    # 前两轮正常派发
    assert "findings" in loop.registry.execute("dispatch_research", {"sub_questions": ["q1"]})
    assert "findings" in loop.registry.execute("dispatch_research", {"sub_questions": ["q2"]})
    # 第三轮超限 + 累计有 findings → max_rounds_reached(请 write_report 收尾)
    out = loop.registry.execute("dispatch_research", {"sub_questions": ["q3"]})
    assert out["status"] == "max_rounds_reached"


def test_dispatch_research_no_findings_at_max_rounds():
    # 0 findings + 达 max_rounds → 提示不要 write_report(避免空报告)
    loop, _ = _make(lambda sq: _result('{"findings":[]}'),
                    lambda f: _result('{}'), lambda m: _result('{}'),
                    research_max_rounds=2)
    loop.registry.execute("dispatch_research", {"sub_questions": ["q1"]})  # 轮1, 0 findings
    loop.registry.execute("dispatch_research", {"sub_questions": ["q2"]})  # 轮2, 0 findings
    out = loop.registry.execute("dispatch_research", {"sub_questions": ["q3"]})  # 轮3 超限 + 累计 0
    assert out["status"] == "no_findings"
    assert "write_report" in out["message"] or "不要" in out["message"]  # 提示不写报告


def test_verify_findings_parses_results():
    def run_verifier(findings_json):
        data = json.loads(findings_json)
        assert data["findings"] == [{"id": "f1"}]
        return _result('{"results":[{"finding_id":"f1","verdict":"supported"}]}')

    loop, _ = _make(lambda sq: _result('{}'), run_verifier, lambda m: _result('{}'))
    out = loop.registry.execute("verify_findings", {"findings": [{"id": "f1"}]})
    assert out["results"][0]["verdict"] == "supported"


def test_write_report_stores_in_holder():
    def run_writer(msg):
        return _result('{"sections":[{"heading":"H","content":"C","citations":["f1"]}],"sources":["https://x"]}')

    loop, get_report = _make(lambda sq: _result('{}'), lambda f: _result('{}'), run_writer)
    out = loop.registry.execute("write_report",
                                {"outline": "大纲", "verified_findings": [{"id": "f1"}]})
    assert out["sections"][0]["heading"] == "H"
    report = get_report()
    assert report is not None  # holder 硬提取
    assert report.sections[0].citations == ["f1"]


def test_write_report_unparseable_not_stored():
    loop, get_report = _make(lambda sq: _result('{}'), lambda f: _result('{}'),
                             lambda m: _result("not json"))
    out = loop.registry.execute("write_report",
                                {"outline": "x", "verified_findings": [{"id": "f1"}]})
    assert "error" in out
    assert get_report() is None


def test_write_report_empty_findings_refused():
    # 0 verified findings → 拒绝(不调 writer,避免空报告);holder 保持 None
    writer_called = []
    loop, get_report = _make(lambda sq: _result('{}'), lambda f: _result('{}'),
                             lambda m: (writer_called.append(1), _result('{}'))[1])
    out = loop.registry.execute("write_report", {"outline": "x", "verified_findings": []})
    assert "error" in out
    assert get_report() is None
    assert writer_called == []  # writer 未被调用


def test_orchestrator_verify_false_strips_verify_tool():
    loop, get_report = make_orchestrator(
        client=FakeClient([]),
        run_researcher=lambda sq: _result('{"findings":[]}'),
        run_verifier=lambda f: _result('{}'),
        run_writer=lambda m: _result('{}'),
        verify=False,
    )
    assert loop.system_prompt == NO_VERIFY_ORCHESTRATOR_PROMPT
    assert "verify_findings" not in set(loop.registry.names())
    assert set(loop.registry.names()) == {"dispatch_research", "write_report"}
    assert get_report() is None


def test_orchestrator_verify_true_default_keeps_verify_tool():
    # 默认 verify=True:行为与现有完全一致(回归保护)
    loop, _ = make_orchestrator(
        client=FakeClient([]),
        run_researcher=lambda sq: _result('{}'),
        run_verifier=lambda f: _result('{}'),
        run_writer=lambda m: _result('{}'),
    )
    assert loop.system_prompt == ORCHESTRATOR_PROMPT
    assert "verify_findings" in set(loop.registry.names())
