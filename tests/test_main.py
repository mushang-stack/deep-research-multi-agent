from main import main
from ui.view import render_report_markdown
from core.schemas import Report, ReportSection


def test_render_markdown_sections_and_sources():
    rep = Report(
        sections=[ReportSection(heading="结论", content="某结论。", citations=["f1", "f2"])],
        sources=["https://a", "https://b"],
    )
    md = render_report_markdown(rep)
    assert "## 结论" in md
    assert "某结论。" in md
    assert "f1" in md and "f2" in md
    assert "## 来源" in md
    assert "https://a" in md and "https://b" in md
    assert "[https://a](https://a)" in md


def test_main_no_args_prints_usage(capsys):
    assert main([]) == 1
    assert "用法" in capsys.readouterr().out
