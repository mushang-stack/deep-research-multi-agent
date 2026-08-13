from ui.view import (render_report_markdown, timeline_rows, render_timeline_md,
                     telemetry_tiles, render_telemetry_md, _bar)
from core.schemas import Report, ReportSection


def test_render_report_markdown_sections_and_sources_links():
    rep = Report(
        sections=[ReportSection(heading="结论", content="某结论。", citations=["f1", "f2"])],
        sources=["https://a", "https://b"],
    )
    md = render_report_markdown(rep)
    assert "## 结论" in md
    assert "某结论。" in md
    assert "## 来源" in md
    assert "[https://a](https://a)" in md  # 可点击链接


def test_timeline_rows_basic_and_icons():
    events = [
        {"kind": "run_start", "role": None, "text": "[start] hi", "payload": {}, "ts": 0.0},
        {"kind": "report_ready", "role": None, "text": "报告就绪", "payload": {"sections": 3}, "ts": 1.0},
    ]
    rows = timeline_rows(events)
    assert rows[0]["icon"] == "▶"
    assert rows[1]["icon"] == "📄"
    assert rows[1]["payload"]["sections"] == 3


def test_timeline_parallel_inference():
    # 3 个 research_start 在 0.5s 内 → 首个标 parallel=3
    events = [
        {"kind": "research_start", "role": "researcher", "text": "[researcher] q1", "payload": {}, "ts": 0.1},
        {"kind": "research_start", "role": "researcher", "text": "[researcher] q2", "payload": {}, "ts": 0.2},
        {"kind": "research_start", "role": "researcher", "text": "[researcher] q3", "payload": {}, "ts": 0.3},
        {"kind": "research_done", "role": "researcher", "text": "[researcher] done", "payload": {}, "ts": 2.0},
    ]
    rows = timeline_rows(events)
    assert rows[0]["parallel"] == 3
    assert rows[1]["parallel"] is None
    assert rows[3]["parallel"] is None


def test_render_timeline_md_strips_tag_and_renders():
    events = [{"kind": "research_start", "role": "researcher",
               "text": "  [researcher] 检索子问题:q1", "payload": {}, "ts": 0.1}]
    md = render_timeline_md(events)
    assert "🔍" in md
    assert "检索子问题:q1" in md
    assert "[researcher]" not in md  # 前导 tag 已剥


def test_bar_half():
    assert _bar(0.5) == "█████░░░░░"
    assert _bar(0.0) == "░░░░░░░░░░"
    assert _bar(1.0) == "██████████"


def test_telemetry_tiles_budget_and_cost():
    rollup = {"researcher": {"name": "researcher", "n": 2, "total_steps": 10, "max_steps": 6,
                             "prompt_tokens": 8000, "completion_tokens": 1500,
                             "wall_s": 12.0, "hit_max": 0, "mean_steps": 5.0}}
    pricing = {"deepseek": {"input_per_1m": 0.14, "output_per_1m": 0.28}}
    tiles = telemetry_tiles(rollup, pricing)
    t = tiles["researcher"]
    assert t["mean_steps"] == 5.0
    assert t["max_steps"] == 6
    assert abs(t["budget_used"] - 5.0 / 6) < 1e-9
    assert t["cost_usd"] is not None and t["cost_usd"] > 0


def test_render_telemetry_md_contains_bar_and_pct():
    rollup = {"writer": {"name": "writer", "n": 1, "total_steps": 1, "max_steps": 12,
                         "prompt_tokens": 100, "completion_tokens": 50,
                         "wall_s": 1.0, "hit_max": 0, "mean_steps": 1.0}}
    md = render_telemetry_md(rollup, {"deepseek": {"input_per_1m": 0.14, "output_per_1m": 0.28}})
    assert "█" in md
    assert "writer" in md
    assert "%" in md
