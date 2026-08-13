# tests/test_events.py
import threading
import time

import pytest

from core.events import Event, EventSink, events_to_json, events_from_json


def test_event_defaults():
    e = Event(kind="x", role=None, text="t", payload={"a": 1})
    assert e.kind == "x"
    assert e.payload == {"a": 1}
    assert e.ts == 0.0


def test_sink_records_and_snapshots():
    sink = EventSink()
    sink.record(Event(kind="run_start", role=None, text="hi", payload={}))
    snap = sink.snapshot()
    assert len(snap) == 1
    assert snap[0]["kind"] == "run_start"
    assert snap[0]["ts"] >= 0.0


def test_sink_ts_monotonic_increasing():
    sink = EventSink()
    sink.record(Event(kind="a", role=None, text="", payload={}))
    time.sleep(0.01)
    sink.record(Event(kind="b", role=None, text="", payload={}))
    s = sink.snapshot()
    assert s[1]["ts"] > s[0]["ts"]


def test_sink_concurrent_record_thread_safe():
    sink = EventSink()

    def worker():
        sink.record(Event(kind="research_start", role="researcher", text="x", payload={}))

    threads = [threading.Thread(target=worker) for _ in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(sink.snapshot()) == 50


def test_snapshot_is_isolated_copy():
    sink = EventSink()
    sink.record(Event(kind="a", role=None, text="", payload={}))
    snap = sink.snapshot()
    snap.clear()
    assert len(sink.snapshot()) == 1  # 外部改动不影响内部


def test_events_json_roundtrip():
    events = [{"kind": "x", "role": "researcher", "text": "t", "payload": {"n": 3}, "ts": 1.5}]
    s = events_to_json(events)
    back = events_from_json(s)
    assert back == events
    assert '"kind": "x"' in s  # ensure_ascii=False 仍合法
