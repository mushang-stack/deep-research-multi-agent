import json

from agents.orchestrator import make_orchestrator
from agents.prompts import ORCHESTRATOR_PROMPT
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
    loop, _ = _make(lambda sq: _result('{"findings":[]}'),
                    lambda f: _result('{}'), lambda m: _result('{}'),
                    research_max_rounds=2)
    # 前两轮正常派发
    assert "findings" in loop.registry.execute("dispatch_research", {"sub_questions": ["q1"]})
    assert "findings" in loop.registry.execute("dispatch_research", {"sub_questions": ["q2"]})
    # 第三轮超限 → 引导收敛(不再派发)
    out = loop.registry.execute("dispatch_research", {"sub_questions": ["q3"]})
    assert out["status"] == "max_rounds_reached"


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
    out = loop.registry.execute("write_report", {"outline": "x", "verified_findings": []})
    assert "error" in out
    assert get_report() is None
