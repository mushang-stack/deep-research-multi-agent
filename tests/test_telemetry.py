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
