import json
import pytest

from core.robust import (
    TransientError, Escalation, retry_with_backoff, safe_parse_arguments,
)


def test_retry_succeeds_first_try():
    calls = []
    def fn():
        calls.append(1)
        return "ok"
    assert retry_with_backoff(fn, retries=3, base=1.0, sleep=lambda s: None) == "ok"
    assert len(calls) == 1


def test_retry_recovers_after_transient():
    calls = []
    def fn():
        calls.append(1)
        if len(calls) < 3:
            raise TransientError("503")
        return "ok"
    sleeps = []
    out = retry_with_backoff(fn, retries=5, base=2.0, sleep=sleeps.append)
    assert out == "ok"
    assert len(calls) == 3
    # 指数退避:base^0=1, base^1=2
    assert sleeps == [1.0, 2.0]


def test_retry_exhausts_then_reraises():
    def fn():
        raise TransientError("always")
    with pytest.raises(TransientError):
        retry_with_backoff(fn, retries=3, base=1.0, sleep=lambda s: None)


def test_retry_does_not_swallow_non_transient():
    def fn():
        raise ValueError("boom")
    with pytest.raises(ValueError):
        retry_with_backoff(fn, retries=3, base=1.0, sleep=lambda s: None,
                           exceptions=(TransientError,))


def test_safe_parse_valid_json():
    assert safe_parse_arguments('{"query": "x"}') == {"query": "x"}


def test_safe_parse_malformed_returns_empty():
    # V4 复杂场景结构化输出偶发非法 JSON;兜底为空 dict 而非崩溃
    assert safe_parse_arguments("{not json") == {}


def test_safe_parse_none_returns_empty():
    assert safe_parse_arguments(None) == {}


def test_escalation_carries_context():
    e = Escalation("hit max_steps", context={"agent": "researcher"})
    assert e.reason == "hit max_steps"
    assert e.context["agent"] == "researcher"
