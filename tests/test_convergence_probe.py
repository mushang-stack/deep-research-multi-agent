from eval.convergence_probe import aggregate, build_arm_loop


class _FakeSearch:
    def search(self, query):
        return []


def _fake_client():
    from llm.base import LLMClient, LLMResponse
    class _C(LLMClient):
        def chat(self, **kw):
            return LLMResponse(content="{}")
    return _C()


def test_arm_wiring_differences():
    a = build_arm_loop("A", client=_fake_client(), search_client=_FakeSearch())
    b = build_arm_loop("B", client=_fake_client(), search_client=_FakeSearch())
    c = build_arm_loop("C", client=_fake_client(), search_client=_FakeSearch())
    assert a.max_tool_result_chars is None and a.last_step_nudge is None \
        and a.on_exhaustion == "escalate"
    assert b.max_tool_result_chars == 6000 and b.last_step_nudge is None
    assert c.last_step_nudge is not None and c.on_exhaustion == "partial" \
        and c.max_tool_result_chars is None
    for x in (a, b, c):
        assert x.name == "researcher" and x.max_steps == 6


def test_aggregate_math():
    runs = [
        {"arm": "A", "n_findings": 3, "degraded": False, "escalated": False,
         "record": {"steps": 5, "hit_max": False}},
        {"arm": "A", "n_findings": 0, "degraded": False, "escalated": True,
         "record": {"steps": 6, "hit_max": True}},
        {"arm": "C", "n_findings": 2, "degraded": True, "escalated": False,
         "record": {"steps": 7, "hit_max": False}},
    ]
    out = aggregate(runs)
    assert out["n"] == 3
    assert out["zero_findings_rate"] == 1 / 3
    assert abs(out["mean_findings"] - 5 / 3) < 1e-9
    assert out["hit_max"] == 1 and out["escalated"] == 1 and out["degraded"] == 1
    assert abs(out["mean_steps"] - 6.0) < 1e-9


def test_aggregate_empty():
    assert aggregate([])["n"] == 0


def test_escalated_run_keeps_telemetry_record():
    # Escalation 路径也必须带回 record(hit_max/steps)——否则 A/B 臂发散统计恒 0(仪器校准)
    from llm.base import LLMClient, LLMResponse, ToolCall
    from eval.convergence_probe import run_one

    class _LoopClient(LLMClient):
        def chat(self, **kw):
            return LLMResponse(content="", tool_calls=[
                ToolCall(id="t1", name="web_search", arguments='{"query":"x"}')])

    r = run_one("A", "测试子问题", client=_LoopClient(), search_client=_FakeSearch())
    assert r["escalated"] is True and r["n_findings"] == 0
    assert r["record"] is not None
    assert r["record"]["hit_max"] is True and r["record"]["steps"] == 6


def test_aggregate_excludes_infra_errors():
    runs = [
        {"arm": "A", "n_findings": 3, "degraded": False, "escalated": False,
         "record": {"steps": 5, "hit_max": False}},
        {"arm": "A", "sub_question": "q", "error": "TransientError(...)"},
    ]
    out = aggregate(runs)
    assert out["n"] == 1 and out["errors"] == 1
    assert out["zero_findings_rate"] == 0.0     # 基础设施失败不算发散


def test_run_one_normal_path_counts_findings():
    from llm.base import LLMClient, LLMResponse
    from eval.convergence_probe import run_one

    class _ConvergeClient(LLMClient):
        def chat(self, **kw):
            return LLMResponse(
                content='{"findings": [{"id": "f1", "claim": "c", "source_url": "u"}]}')

    r = run_one("A", "测试", client=_ConvergeClient(), search_client=_FakeSearch())
    assert r["n_findings"] == 1 and r["degraded"] is False and r["escalated"] is False


def test_unknown_arm_rejected():
    import pytest
    from eval.convergence_probe import main
    with pytest.raises(SystemExit):
        main(["--arms", "X", "--n", "1"])
