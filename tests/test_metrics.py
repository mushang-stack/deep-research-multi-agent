from eval.metrics import compute_question_metrics, aggregate


# ---------- compute_question_metrics ----------

def test_grounding_weights_supported_partial_unsupported():
    verdicts = [
        {"support": "supported", "source_real": True},
        {"support": "partial", "source_real": True},
        {"support": "unsupported", "source_real": False},
    ]
    m = compute_question_metrics(verdicts, [], report_produced=True)
    # (1.0 + 0.5 + 0.0) / 3
    assert m["grounding"] == (1.0 + 0.5 + 0.0) / 3
    assert m["grounding_available"] is True


def test_citation_is_fraction_source_real_true():
    verdicts = [
        {"support": "supported", "source_real": True},
        {"support": "supported", "source_real": False},
    ]
    m = compute_question_metrics(verdicts, [], report_produced=True)
    assert m["citation"] == 0.5


def test_coverage_is_fraction_covered():
    kfs = [{"covered": True}, {"covered": False}, {"covered": True}]
    m = compute_question_metrics([], kfs, report_produced=True)
    assert m["coverage"] == 2 / 3


def test_hallucination_counts_unsupported_or_fake_source():
    verdicts = [
        {"support": "supported", "source_real": True},   # not counted
        {"support": "unsupported", "source_real": True},  # counted (unsupported)
        {"support": "supported", "source_real": False},   # counted (fake source)
    ]
    m = compute_question_metrics(verdicts, [], report_produced=True)
    assert m["hallucination"] == 2 / 3


def test_no_findings_marks_grounding_unavailable_but_coverage_ok():
    # N=0: grounding/citation/hallucination unavailable; coverage still computable (spec 3/6)
    m = compute_question_metrics([], [{"covered": True}, {"covered": False}],
                                 report_produced=True)
    assert m["grounding"] is None
    assert m["citation"] is None
    assert m["hallucination"] is None
    assert m["grounding_available"] is False
    assert m["coverage"] == 0.5


def test_no_key_facts_marks_coverage_none():
    m = compute_question_metrics(
        [{"support": "supported", "source_real": True}], [], report_produced=True)
    assert m["coverage"] is None
    assert m["grounding"] == 1.0


def test_report_none_marks_failed_no_report():
    m = compute_question_metrics([], [], report_produced=False)
    assert m["success"] is False
    assert m["failed"] == "no_report"
    assert m["grounding"] is None


def test_report_produced_success_true():
    m = compute_question_metrics(
        [{"support": "supported", "source_real": True}], [], report_produced=True)
    assert m["success"] is True
    assert m["failed"] is None


def test_parse_failures_counted():
    verdicts = [
        {"support": "unsupported", "source_real": False, "parse_failed": True},
        {"support": "supported", "source_real": True},
    ]
    kfs = [{"covered": False, "parse_failed": True}]
    m = compute_question_metrics(verdicts, kfs, report_produced=True)
    assert m["judge_parse_failures"] == 2


# ---------- aggregate ----------

def test_aggregate_mean_grounding_over_n_gt_zero_only():
    sc = [
        {"id": "q1", "success": True, "grounding": 1.0, "citation": 1.0,
         "coverage": 1.0, "hallucination": 0.0, "judge_parse_failures": 0},
        {"id": "q2", "success": True, "grounding": None, "citation": None,
         "coverage": 0.0, "hallucination": None, "judge_parse_failures": 0},  # N=0, excluded
    ]
    agg = aggregate(sc, grounding_min=0.85)
    assert agg["mean_grounding"] == 1.0          # only average over q1
    assert agg["grounding_questions"] == 1
    assert agg["n_questions"] == 2
    assert agg["n_success"] == 2
    assert agg["success_rate"] == 1.0


def test_aggregate_passed_threshold():
    sc = [{"id": "q1", "success": True, "grounding": 0.9, "citation": None,
           "coverage": None, "hallucination": None, "judge_parse_failures": 0}]
    assert aggregate(sc, grounding_min=0.85)["passed"] is True
    assert aggregate(sc, grounding_min=0.95)["passed"] is False


def test_aggregate_no_grounding_available_fails():
    # all questions N=0 -> mean_grounding None -> passed False
    sc = [{"id": "q1", "success": True, "grounding": None, "citation": None,
           "coverage": None, "hallucination": None, "judge_parse_failures": 0}]
    agg = aggregate(sc, grounding_min=0.85)
    assert agg["mean_grounding"] is None
    assert agg["passed"] is False


def test_aggregate_empty_scorecards():
    # 空基准集(全部题加载失败/被跳过)→ 无除零,passed False
    agg = aggregate([], grounding_min=0.85)
    assert agg["n_questions"] == 0
    assert agg["success_rate"] == 0.0
    assert agg["mean_grounding"] is None
    assert agg["passed"] is False
    assert agg["total_judge_parse_failures"] == 0
