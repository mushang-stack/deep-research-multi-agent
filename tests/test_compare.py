import json

from eval.compare import compare, render_table


def _sc(grounding, halluc=0.0, citation=1.0, coverage=1.0, success=1.0):
    return {"mean_grounding": grounding, "mean_hallucination": halluc,
            "mean_citation": citation, "mean_coverage": coverage, "success_rate": success}


def test_compare_two_systems_architecture_delta():
    cmp = compare({"multi": _sc(0.932, 0.046), "baseline": _sc(0.760, 0.190)})
    assert "A-C (architecture)" in cmp["deltas"]
    assert "A-B (verifier)" not in cmp["deltas"]
    d = cmp["deltas"]["A-C (architecture)"]
    assert d["Grounding"] == round(0.932 - 0.760, 3)
    assert d["幻觉率"] == round(0.046 - 0.190, 3)


def test_compare_three_systems_both_deltas():
    cmp = compare({"multi": _sc(0.90, 0.05), "no_verify": _sc(0.80, 0.10), "baseline": _sc(0.70, 0.20)})
    assert "A-B (verifier)" in cmp["deltas"]
    assert "A-C (architecture)" in cmp["deltas"]
    assert cmp["deltas"]["A-B (verifier)"]["Grounding"] == round(0.90 - 0.80, 3)


def test_compare_honest_when_baseline_beats_multi():
    # 基线 grounding 反超 → Δ 为负,照实报(不取绝对值、不隐藏)
    cmp = compare({"multi": _sc(0.70), "baseline": _sc(0.90)})
    d = cmp["deltas"]["A-C (architecture)"]
    assert d["Grounding"] == round(0.70 - 0.90, 3)
    assert d["Grounding"] < 0


def test_compare_rows_carry_all_system_values():
    cmp = compare({"multi": _sc(0.9), "baseline": _sc(0.7)})
    g = next(r for r in cmp["rows"] if r["key"] == "mean_grounding")
    assert g["multi"] == 0.9 and g["baseline"] == 0.7


def test_compare_missing_value_yields_none_delta():
    # 某指标为 None(如 N=0 导致 grounding 不可用)→ Δ 记 None,不抛
    cmp = compare({"multi": {"mean_grounding": None}, "baseline": _sc(0.7)})
    assert cmp["deltas"]["A-C (architecture)"]["Grounding"] is None


def test_render_table_contains_labels_and_delta_name():
    cmp = compare({"multi": _sc(0.9, 0.05), "baseline": _sc(0.7, 0.15)})
    txt = render_table(cmp)
    assert "Grounding" in txt and "幻觉率" in txt
    assert "A-C (architecture)" in txt


def test_compare_cli_writes_comparison_json(tmp_path):
    # 造两份 scorecard,跑 CLI → 产 comparison.json
    (tmp_path / "scorecard.json").write_text(json.dumps(_sc(0.9, 0.05)), encoding="utf-8")
    bdir = tmp_path / "baseline"
    bdir.mkdir()
    (bdir / "scorecard.json").write_text(json.dumps(_sc(0.7, 0.15)), encoding="utf-8")
    from eval.compare import main as cmp_main
    rc = cmp_main(["--systems", "multi,baseline", "--results-dir", str(tmp_path)])
    assert rc == 0
    out = json.loads((tmp_path / "comparison.json").read_text(encoding="utf-8"))
    assert "A-C (architecture)" in out["deltas"]
