import threading

import pytest

from core.telemetry import AgentRecord, TelemetrySink, compute_cost


def test_sink_records_and_snapshots():
    sink = TelemetrySink()
    sink.record(AgentRecord(name="researcher", steps=4, max_steps=6,
                            prompt_tokens=100, completion_tokens=20,
                            wall_s=1.5, hit_max=False))
    snap = sink.snapshot()
    assert len(snap) == 1
    assert snap[0]["name"] == "researcher"
    assert snap[0]["steps"] == 4


def test_sink_aggregate_by_name_rolls_up_multi_instances():
    sink = TelemetrySink()
    for s in (4, 5, 6):
        sink.record(AgentRecord(name="researcher", steps=s, max_steps=6,
                                prompt_tokens=10, completion_tokens=2,
                                wall_s=1.0, hit_max=False))
    sink.record(AgentRecord(name="writer", steps=1, max_steps=12,
                            prompt_tokens=5, completion_tokens=5,
                            wall_s=0.5, hit_max=False))
    roll = sink.aggregate_by_name()
    assert roll["researcher"]["n"] == 3
    assert roll["researcher"]["total_steps"] == 15
    assert roll["researcher"]["mean_steps"] == 5.0
    assert roll["researcher"]["prompt_tokens"] == 30
    assert roll["researcher"]["hit_max"] == 0
    assert roll["writer"]["n"] == 1


def test_sink_concurrent_record_is_thread_safe():
    sink = TelemetrySink()

    def worker(i):
        sink.record(AgentRecord(name="researcher", steps=4, max_steps=6,
                                prompt_tokens=1, completion_tokens=1,
                                wall_s=0.1, hit_max=False))
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    roll = sink.aggregate_by_name()
    assert roll["researcher"]["n"] == 50
    assert roll["researcher"]["prompt_tokens"] == 50


def test_compute_cost_basic():
    pricing = {"input_per_1m": 0.14, "output_per_1m": 0.28}
    assert compute_cost(1_000_000, 1_000_000, pricing) == pytest.approx(0.42)


def test_compute_cost_none_when_pricing_missing():
    assert compute_cost(100, 50, None) is None
    assert compute_cost(100, 50, {}) is None


from llm.base import LLMClient, LLMResponse
from core.telemetry import CountingClient


class _ScriptedJudge(LLMClient):
    """按顺序返回带 usage 的响应。"""
    def __init__(self, responses):
        self._r = list(responses)

    def chat(self, *, messages, tools=None, model=None, temperature=None, max_tokens=None):
        return self._r.pop(0)


def test_counting_client_delegates_and_accumulates():
    inner = _ScriptedJudge([
        LLMResponse(content="a", usage={"prompt_tokens": 10, "completion_tokens": 5}),
        LLMResponse(content="b", usage={"prompt_tokens": 20, "completion_tokens": 8}),
    ])
    jc = CountingClient(inner)
    assert jc.chat(messages=[]).content == "a"
    assert jc.chat(messages=[]).content == "b"
    snap = jc.snapshot()
    assert snap["calls"] == 2
    assert snap["prompt_tokens"] == 30
    assert snap["completion_tokens"] == 13


def test_counting_client_handles_missing_usage():
    inner = _ScriptedJudge([LLMResponse(content="x")])
    jc = CountingClient(inner)
    jc.chat(messages=[])
    assert jc.snapshot()["calls"] == 1
    assert jc.snapshot()["prompt_tokens"] == 0


def test_counting_client_concurrent_safe():
    inner = _ScriptedJudge([
        LLMResponse(content="x", usage={"prompt_tokens": 1, "completion_tokens": 1})
    ] * 100)
    jc = CountingClient(inner)

    def worker():
        jc.chat(messages=[])
    threads = [threading.Thread(target=worker) for _ in range(100)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    snap = jc.snapshot()
    assert snap["calls"] == 100
    assert snap["prompt_tokens"] == 100


from core.telemetry import build_telemetry, aggregate_telemetry


def _rollup():
    return {
        "researcher": {"name": "researcher", "n": 3, "total_steps": 12, "max_steps": 6,
                       "prompt_tokens": 8000, "completion_tokens": 1500, "wall_s": 18.0,
                       "hit_max": 0, "mean_steps": 4.0},
        "writer": {"name": "writer", "n": 1, "total_steps": 1, "max_steps": 12,
                   "prompt_tokens": 1500, "completion_tokens": 1200, "wall_s": 2.0,
                   "hit_max": 0, "mean_steps": 1.0},
    }


def test_build_telemetry_structure_and_cost():
    pricing = {"deepseek": {"input_per_1m": 0.14, "output_per_1m": 0.28},
               "glm": {"input_per_1m": 0.28, "output_per_1m": 1.12}}
    judge = {"calls": 27, "prompt_tokens": 9000, "completion_tokens": 800}
    t = build_telemetry(_rollup(), judge, judge_wall_s=6.0,
                        question_wall_s=42.0, pricing=pricing)
    assert t["wall_s"] == 42.0
    assert t["generator"]["totals"]["prompt_tokens"] == 9500
    assert t["generator"]["totals"]["completion_tokens"] == 2700
    assert t["generator"]["totals"]["cost_usd"] == pytest.approx(9500 / 1e6 * 0.14 + 2700 / 1e6 * 0.28)
    assert t["judge"]["cost_usd"] == pytest.approx(9000 / 1e6 * 0.28 + 800 / 1e6 * 1.12)
    assert t["judge"]["calls"] == 27
    assert t["cost_usd"]["total"] == pytest.approx(
        t["generator"]["totals"]["cost_usd"] + t["judge"]["cost_usd"])
    assert t["utilization"]["researcher"]["budget_used"] == pytest.approx(4.0 / 6)
    assert t["utilization"]["researcher"]["hit_max"] == 0


def test_build_telemetry_pricing_none_costs_null():
    t = build_telemetry(_rollup(), {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0},
                        judge_wall_s=0.0, question_wall_s=1.0, pricing=None)
    assert t["cost_usd"]["generator"] is None
    assert t["cost_usd"]["judge"] is None
    assert t["cost_usd"]["total"] is None


def test_aggregate_telemetry_two_questions():
    pricing = {"deepseek": {"input_per_1m": 0.14, "output_per_1m": 0.28},
               "glm": {"input_per_1m": 0.28, "output_per_1m": 1.12}}
    t1 = build_telemetry(_rollup(), {"calls": 10, "prompt_tokens": 1000, "completion_tokens": 100},
                         3.0, 40.0, pricing)
    t2 = build_telemetry(_rollup(), {"calls": 20, "prompt_tokens": 2000, "completion_tokens": 200},
                         6.0, 44.0, pricing)
    agg = aggregate_telemetry([t1, t2])
    assert agg["n_questions"] == 2
    assert agg["mean_wall_s"] == 42.0
    assert agg["total_cost_usd"]["total"] == pytest.approx(
        t1["cost_usd"]["total"] + t2["cost_usd"]["total"])
    assert agg["agents"]["researcher"]["mean_steps"] == 4.0


# ---------- Task 5: recorder wiring 到各 agent 构造点 ----------
from agents.researcher import make_researcher
from agents.verifier import make_verifier
from agents.writer import make_writer
from agents.orchestrator import make_orchestrator
from agents.system import build_system
from agents.baseline import build_baseline_system
from core.config import Config


class _DummySearch:
    def search(self, query):
        return []


class _FakeGen(LLMClient):
    def chat(self, **kw):
        raise AssertionError("构造阶段不应调 chat")


def _cfg_for_build():
    return Config({"models": {"generator": {}},
                   "tools": {"web_search": {}, "web_read": {"max_chars": 8000}},
                   "guards": {"agent_max_steps": 12, "research_max_rounds": 3}})


def test_make_researcher_threads_recorder():
    sink = TelemetrySink()
    loop = make_researcher(client=_FakeGen(), search_client=_DummySearch(), recorder=sink)
    assert loop.recorder is sink


def test_make_verifier_threads_recorder():
    sink = TelemetrySink()
    loop = make_verifier(client=_FakeGen(), search_client=_DummySearch(), recorder=sink)
    assert loop.recorder is sink


def test_make_writer_threads_recorder():
    sink = TelemetrySink()
    loop = make_writer(client=_FakeGen(), recorder=sink)
    assert loop.recorder is sink


def test_make_orchestrator_threads_recorder():
    sink = TelemetrySink()
    loop, _ = make_orchestrator(client=_FakeGen(), run_researcher=lambda q: None,
                                run_verifier=lambda f: None, run_writer=lambda m: None,
                                recorder=sink)
    assert loop.recorder is sink


def test_build_system_threads_recorder_to_orchestrator():
    sink = TelemetrySink()
    loop, _ = build_system(_cfg_for_build(), client=_FakeGen(),
                           search_client=_DummySearch(), recorder=sink)
    assert loop.recorder is sink


def test_build_baseline_system_threads_recorder():
    sink = TelemetrySink()
    loop, _ = build_baseline_system(_cfg_for_build(), client=_FakeGen(),
                                    search_client=_DummySearch(), recorder=sink)
    assert loop.recorder is sink


def test_config_has_pricing_section():
    from core.config import load_config
    pricing = load_config()["pricing"]
    assert "deepseek" in pricing and "glm" in pricing
    for m in ("deepseek", "glm"):
        assert pricing[m]["input_per_1m"] > 0
        assert pricing[m]["output_per_1m"] > 0
