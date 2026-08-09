from llm.base import LLMClient, LLMResponse, ToolCall


def test_toolcall_and_response_construction():
    tc = ToolCall(id="call_1", name="web_search", arguments='{"query":"x"}')
    assert tc.name == "web_search"

    resp = LLMResponse(content="hello")
    assert resp.content == "hello"
    assert resp.tool_calls == []
    assert resp.usage == {}


def test_response_with_tool_calls():
    resp = LLMResponse(content="", tool_calls=[
        ToolCall(id="c1", name="web_search", arguments='{"query":"a"}'),
    ])
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].arguments == '{"query":"a"}'


def test_llmclient_is_abstract():
    import pytest
    # ABC 有未实现的 abstractmethod → 实例化即抛 TypeError(在 ABCMeta.__call__ 阶段)
    with pytest.raises(TypeError):
        LLMClient()
