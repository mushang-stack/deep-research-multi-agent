# tests/test_controller.py
import json

from core.config import Config
from core.events import EventSink
from core.telemetry import TelemetrySink
from core.schemas import Report, ReportSection
from ui.controller import (run_research, save_trace, load_trace, slugify,
                           trace_path, list_traces)


class _FakeLoop:
    def __init__(self, exc=None):
        self.question = None
        self._exc = exc

    def run(self, question):
        self.question = question
        if self._exc:
            raise self._exc


def _fake_run_fn(report=None, exc=None):
    def fn(cfg):
        loop = _FakeLoop(exc=exc)

        def get_report():
            return report

        return loop, get_report
    return fn


def _cfg():
    return Config({})


def test_run_research_success_events_and_report():
    sink = EventSink(); tele = TelemetrySink()
    rep = Report(sections=[ReportSection(heading="H", content="C", citations=[])], sources=[])
    out = run_research("问题", _cfg(), sink, tele, run_fn=_fake_run_fn(report=rep))
    assert out is rep
    kinds = [e["kind"] for e in sink.snapshot()]
    assert kinds[0] == "run_start"
    assert "run_done" in kinds
    done = next(e for e in sink.snapshot() if e["kind"] == "run_done")
    assert done["payload"]["success"] is True


def test_run_research_exception_emits_error_and_returns_none():
    sink = EventSink(); tele = TelemetrySink()
    out = run_research("问题", _cfg(), sink, tele,
                       run_fn=_fake_run_fn(exc=RuntimeError("boom")))
    assert out is None
    kinds = [e["kind"] for e in sink.snapshot()]
    assert "error" in kinds
    done = next(e for e in sink.snapshot() if e["kind"] == "run_done")
    assert done["payload"]["success"] is False


def test_save_load_trace_roundtrip(tmp_path):
    rep = Report(sections=[ReportSection(heading="H", content="C", citations=["f1"])],
                 sources=["https://x"])
    events = [{"kind": "run_start", "role": None, "text": "hi", "payload": {}, "ts": 0.0}]
    path = tmp_path / "t.json"
    save_trace(str(path), "问题", events, rep, {"researcher": {"mean_steps": 5.0}}, "success")
    data = load_trace(str(path))
    assert data["question"] == "问题"
    assert data["events"] == events
    assert data["status"] == "success"
    assert data["report"]["sections"][0]["heading"] == "H"
    # 反序列化报告可重建 Report
    rebuilt = Report(**data["report"])
    assert rebuilt.sections[0].citations == ["f1"]


def test_save_trace_null_report(tmp_path):
    path = tmp_path / "t.json"
    save_trace(str(path), "q", [], None, {}, "failed")
    assert load_trace(str(path))["report"] is None


def test_slugify_sanitizes():
    assert slugify("对比 RAG 与 微调?") == "对比-RAG-与-微调"
    assert slugify("a/b\\c:d") != ""


def test_trace_path_unique(tmp_path):
    p1 = trace_path("问题", traces_dir=str(tmp_path))
    save_trace(p1, "问题", [], None, {}, "failed")
    p2 = trace_path("问题", traces_dir=str(tmp_path))
    assert p1 != p2


def test_list_traces(tmp_path):
    save_trace(str(tmp_path / "a.json"), "q1", [], None, {}, "failed")
    save_trace(str(tmp_path / "b.json"), "q2", [], None, {}, "failed")
    assert len(list_traces(traces_dir=str(tmp_path))) == 2
