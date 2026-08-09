import httpx
import openai
import pytest

from llm.openai_compat import OpenAICompatClient, _TRANSIENT_STATUS
from core.robust import TransientError


# ---- fake openai 对象(模拟 SDK 返回结构,不触网)----
class _FakeFunction:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, id, name, arguments):
        self.id = id
        self.function = _FakeFunction(name, arguments)


class _FakeMessage:
    def __init__(self, content, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _FakeUsage:
    def __init__(self, p, c):
        self.prompt_tokens = p
        self.completion_tokens = c


class _FakeResponse:
    def __init__(self, message, usage=None):
        self.choices = [type("C", (), {"message": message})()]
        self.usage = usage


class _FakeCompletions:
    def __init__(self, side_effects):
        self._side_effects = list(side_effects)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        eff = self._side_effects.pop(0)
        if isinstance(eff, Exception):
            raise eff
        return eff


def _client_with(side_effects):
    completions = _FakeCompletions(side_effects)
    fake = type("O", (), {"chat": type("CH", (), {"completions": completions})()})()
    return OpenAICompatClient(api_key="k", base_url="https://x", model="m",
                              retries=3, backoff=1.5,
                              sleep=lambda s: None, client=fake), completions


def test_transient_status_set():
    assert 503 in _TRANSIENT_STATUS and 429 in _TRANSIENT_STATUS
    assert 400 not in _TRANSIENT_STATUS and 401 not in _TRANSIENT_STATUS


def test_chat_maps_text_response():
    resp = _FakeResponse(_FakeMessage(content="hello"))
    c, completions = _client_with([resp])
    out = c.chat(messages=[{"role": "user", "content": "hi"}])
    assert out.content == "hello"
    assert out.tool_calls == []
    assert out.usage == {}  # usage=None → 空 dict
    assert completions.calls[0]["model"] == "m"


def test_chat_maps_tool_calls_and_usage():
    msg = _FakeMessage(content="", tool_calls=[
        _FakeToolCall(id="c1", name="web_search", arguments='{"query":"x"}'),
    ])
    resp = _FakeResponse(msg, usage=_FakeUsage(10, 5))
    c, _ = _client_with([resp])
    out = c.chat(messages=[{"role": "user", "content": "q"}], tools=[{"type": "function"}])
    assert len(out.tool_calls) == 1
    assert out.tool_calls[0].name == "web_search"
    assert out.tool_calls[0].arguments == '{"query":"x"}'
    assert out.usage == {"prompt_tokens": 10, "completion_tokens": 5}


def test_chat_retries_on_timeout_then_succeeds():
    timeout = openai.APITimeoutError(request=httpx.Request("POST", "https://x"))
    ok = _FakeResponse(_FakeMessage(content="recovered"))
    c, completions = _client_with([timeout, ok])
    out = c.chat(messages=[{"role": "user", "content": "q"}])
    assert out.content == "recovered"
    assert len(completions.calls) == 2  # 第一次超时→重试→第二次成功


def test_chat_retries_exhaust_then_raises_transient():
    timeout = openai.APITimeoutError(request=httpx.Request("POST", "https://x"))
    c, _ = _client_with([timeout, timeout, timeout])
    with pytest.raises(TransientError):
        c.chat(messages=[{"role": "user", "content": "q"}])


def test_chat_does_not_retry_on_non_transient():
    req = httpx.Request("POST", "https://x")
    bad = openai.BadRequestError(
        "bad", response=httpx.Response(400, request=req), body=None)
    c, completions = _client_with([bad])
    with pytest.raises(openai.BadRequestError):
        c.chat(messages=[{"role": "user", "content": "q"}])
    assert len(completions.calls) == 1  # 非瞬时错误不重试
