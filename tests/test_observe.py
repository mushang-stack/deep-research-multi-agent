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
