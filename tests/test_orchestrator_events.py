import json
import pytest

from agents.orchestrator import make_orchestrator
from agents.observe import set_sink, clear_sink
from core.events import EventSink
from llm.base import LLMClient


class _NoChat(LLMClient):
    def chat(self, **kw):
        raise AssertionError("工具执行不应调生成模型")


def _result(content):
    return type("R", (), {"content": content})()


@pytest.fixture
def sink():
    s = EventSink()
    set_sink(s)
    yield s
    clear_sink()


def _kinds(sink):
    return [e["kind"] for e in sink.snapshot()]


def test_research_round_events(sink):
    def run_researcher(sq):
        return _result(json.dumps({"findings": [{"id": "f1", "claim": sq, "source_url": "https://x"}]}))
    loop, _ = make_orchestrator(client=_NoChat(), run_researcher=run_researcher,
                                run_verifier=lambda f: _result("{}"), run_writer=lambda m: _result("{}"))
    loop.registry.execute("dispatch_research", {"sub_questions": ["q1", "q2"]})
    kinds = _kinds(sink)
    assert "research_round" in kinds and "research_round_done" in kinds
    rr = next(e for e in sink.snapshot() if e["kind"] == "research_round")
    assert rr["payload"]["round"] == 1
    assert rr["payload"]["n_subquestions"] == 2
    assert rr["payload"]["is_gap"] is False
    rrd = next(e for e in sink.snapshot() if e["kind"] == "research_round_done")
    assert rrd["payload"]["findings"] == 2
    assert rrd["payload"]["failures"] == 0


def test_research_round_gap_flag(sink):
    loop, _ = make_orchestrator(
        client=_NoChat(),
        run_researcher=lambda sq: _result('{"findings":[{"id":"f1","claim":"c","source_url":"https://x"}]}'),
        run_verifier=lambda f: _result("{}"), run_writer=lambda m: _result("{}"),
        research_max_rounds=3)
    loop.registry.execute("dispatch_research", {"sub_questions": ["q1"]})
    loop.registry.execute("dispatch_research", {"sub_questions": ["q2"]})
    rounds = [e for e in sink.snapshot() if e["kind"] == "research_round"]
    assert rounds[0]["payload"]["is_gap"] is False
    assert rounds[1]["payload"]["is_gap"] is True


def test_verify_done_tally(sink):
    def run_verifier(findings_json):
        return _result(json.dumps({"results": [
            {"finding_id": "f1", "verdict": "supported"},
            {"finding_id": "f2", "verdict": "supported"},
            {"finding_id": "f3", "verdict": "unsupported"},
            {"finding_id": "f4", "verdict": "weak"},
        ]}))
    loop, _ = make_orchestrator(client=_NoChat(), run_researcher=lambda sq: _result("{}"),
                                run_verifier=run_verifier, run_writer=lambda m: _result("{}"))
    loop.registry.execute("verify_findings",
                          {"findings": [{"id": "f1"}, {"id": "f2"}, {"id": "f3"}, {"id": "f4"}]})
    kinds = _kinds(sink)
    assert "verify_start" in kinds and "verify_done" in kinds
    vd = next(e for e in sink.snapshot() if e["kind"] == "verify_done")
    assert vd["payload"]["supported"] == 2
    assert vd["payload"]["unsupported"] == 1
    assert vd["payload"]["weak"] == 1
    assert vd["role"] == "verifier"


def test_write_and_report_events(sink):
    def run_writer(msg):
        return _result('{"sections":[{"heading":"H","content":"C","citations":["f1"]}],"sources":["https://x"]}')
    loop, _ = make_orchestrator(client=_NoChat(), run_researcher=lambda sq: _result("{}"),
                                run_verifier=lambda f: _result("{}"), run_writer=run_writer)
    loop.registry.execute("write_report", {"outline": "大纲", "verified_findings": [{"id": "f1"}]})
    kinds = _kinds(sink)
    assert "write_start" in kinds and "report_ready" in kinds
    rr = next(e for e in sink.snapshot() if e["kind"] == "report_ready")
    assert rr["payload"]["sections"] == 1


def test_warn_on_empty_findings_refused(sink):
    loop, _ = make_orchestrator(client=_NoChat(), run_researcher=lambda sq: _result("{}"),
                                run_verifier=lambda f: _result("{}"),
                                run_writer=lambda m: _result("{}"))
    loop.registry.execute("write_report", {"outline": "x", "verified_findings": []})
    warns = [e for e in sink.snapshot() if e["kind"] == "warn"]
    assert len(warns) == 1
    assert warns[0]["payload"]["reason"] == "write_refused_no_findings"
