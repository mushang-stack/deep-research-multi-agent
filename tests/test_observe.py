from agents.observe import progress


def test_progress_writes_to_stderr(capsys, monkeypatch):
    monkeypatch.delenv("RESEARCH_QUIET", raising=False)
    progress("[test] hello")
    captured = capsys.readouterr()
    assert "[test] hello" in captured.err
    assert captured.out == ""  # 不污染 stdout(报告走 stdout)


def test_progress_silent_when_quiet(capsys, monkeypatch):
    monkeypatch.setenv("RESEARCH_QUIET", "1")
    progress("[test] shh")
    captured = capsys.readouterr()
    assert captured.err == "" and captured.out == ""


from core.events import EventSink
from agents.observe import emit, set_sink, clear_sink, get_sink


def test_emit_without_sink_is_silent_when_quiet(capsys, monkeypatch):
    monkeypatch.setenv("RESEARCH_QUIET", "1")
    emit("run_start", None, "[start] hi", question="q")
    cap = capsys.readouterr()
    assert cap.err == "" and cap.out == ""


def test_emit_without_sink_writes_stderr_when_not_quiet(capsys, monkeypatch):
    monkeypatch.delenv("RESEARCH_QUIET", raising=False)
    emit("run_start", None, "[start] hi", question="q")
    cap = capsys.readouterr()
    assert "[start] hi" in cap.err


def test_emit_with_sink_records_event():
    sink = EventSink()
    set_sink(sink)
    try:
        emit("research_start", "researcher", "检索子问题:q1", sub_question="q1")
    finally:
        clear_sink()
    snap = sink.snapshot()
    assert len(snap) == 1
    assert snap[0]["kind"] == "research_start"
    assert snap[0]["role"] == "researcher"
    assert snap[0]["payload"] == {"sub_question": "q1"}


def test_set_clear_get_sink():
    sink = EventSink()
    assert get_sink() is None
    set_sink(sink)
    assert get_sink() is sink
    clear_sink()
    assert get_sink() is None
