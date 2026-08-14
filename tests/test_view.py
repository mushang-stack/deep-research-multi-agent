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


def test_clean_text_strips_leading_status_glyph():
    # warn/report_ready 文案自带 ⚠/✓;_clean_text 剥之,避免与 _ICONS 双重显示
    rows = timeline_rows([
        {"kind": "warn", "role": None, "text": "[orchestrator] ⚠ 已达最大轮次",
         "payload": {"reason": "x"}, "ts": 0.0},
        {"kind": "report_ready", "role": None, "text": "[orchestrator] ✓ 报告已生成",
         "payload": {"sections": 1}, "ts": 0.0},
    ])
    md = render_timeline_md([{"kind": "warn", "role": None,
                              "text": "[orchestrator] ⚠ 已达最大轮次", "payload": {}, "ts": 0.0}])
    assert "⚠ ⚠" not in md          # 不重复
    assert "已达最大轮次" in md


def test_parallel_single_and_interleaved_no_badge():
    # 单个 research_start → 无并行标记;被其他事件隔开 → 不并组
    events = [
        {"kind": "research_start", "role": "researcher", "text": "a", "payload": {}, "ts": 0.1},
        {"kind": "verify_start", "role": "verifier", "text": "v", "payload": {}, "ts": 0.2},
        {"kind": "research_start", "role": "researcher", "text": "b", "payload": {}, "ts": 0.3},
    ]
    rows = timeline_rows(events)
    assert rows[0]["parallel"] is None
    assert rows[2]["parallel"] is None


def test_bar_clamps_out_of_range():
    assert _bar(1.5) == "██████████"   # 上界
    assert _bar(-0.3) == "░░░░░░░░░░"  # 下界


def test_telemetry_tiles_max_steps_zero_and_no_pricing():
    rollup = {"x": {"name": "x", "max_steps": 0, "mean_steps": 0.0,
                    "prompt_tokens": 0, "completion_tokens": 0}}
    tiles = telemetry_tiles(rollup, None)
    assert tiles["x"]["budget_used"] == 0.0   # 除零保护
    assert tiles["x"]["cost_usd"] is None     # 无 pricing


def test_render_telemetry_md_empty_rollup_returns_empty():
    assert render_telemetry_md({}, {"deepseek": {"input_per_1m": 0.14, "output_per_1m": 0.28}}) == ""


def test_run_done_icon_reflects_success():
    # 失败的 run_done 不应显示绿勾 ✓(跨层:_ICONS 按 kind,_clean_text 剥 ✗)
    ok = timeline_rows([{"kind": "run_done", "role": None, "text": "[done] ✓ 报告生成",
                         "payload": {"success": True}, "ts": 0.0}])
    fail = timeline_rows([{"kind": "run_done", "role": None, "text": "[done] ✗ 失败",
                           "payload": {"success": False}, "ts": 0.0}])
    assert ok[0]["icon"] == "✓"
    assert fail[0]["icon"] == "✗"


def test_render_timeline_md_one_event_per_paragraph():
    # Streamlit markdown 会把段内单个 \n 折叠成空格 → 每个事件须自成一段落(\n\n 分隔)
    events = [
        {"kind": "run_start", "role": None, "text": "[start] AAA", "payload": {}, "ts": 0.0},
        {"kind": "write_start", "role": "writer", "text": "[writer] BBB", "payload": {}, "ts": 0.1},
    ]
    md = render_timeline_md(events)
    parts = md.split("\n\n")
    hits = [p for p in parts if "AAA" in p or "BBB" in p]
    assert len(hits) == 2  # 两个事件各成一段,不粘连成一大段


def test_render_telemetry_md_one_agent_per_paragraph():
    rollup = {
        "researcher": {"name": "researcher", "max_steps": 6, "mean_steps": 5.0,
                       "prompt_tokens": 100, "completion_tokens": 20},
        "writer": {"name": "writer", "max_steps": 12, "mean_steps": 1.0,
                   "prompt_tokens": 50, "completion_tokens": 10},
    }
    md = render_telemetry_md(rollup, {"deepseek": {"input_per_1m": 0.14, "output_per_1m": 0.28}})
    parts = md.split("\n\n")
    hits = [p for p in parts if "researcher" in p or "writer" in p]
    assert len(hits) == 2
