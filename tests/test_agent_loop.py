import pytest

from core.agent_loop import AgentLoop, AgentResult
from core.tool_registry import ToolRegistry
from core.robust import Escalation
from llm.base import LLMClient, LLMResponse, ToolCall


class FakeClient(LLMClient):
    """按脚本顺序返回 LLMResponse,记录每次调用。"""
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def chat(self, *, messages, tools=None, model=None, temperature=None, max_tokens=None):
        self.calls.append({"n_messages": len(messages), "tools": tools})
        return self._responses.pop(0)


def _registry_with_echo():
    reg = ToolRegistry()
    reg.register("echo", lambda text: {"echo": text},
                 description="d", parameters={"type": "object"})
    return reg


def test_no_tools_returns_content():
    c = FakeClient([LLMResponse(content="final answer")])
    loop = AgentLoop(client=c, system_prompt="sys")
    out = loop.run("hi")
    assert isinstance(out, AgentResult)
    assert out.content == "final answer"
    assert len(c.calls) == 1


def test_tool_call_then_final():
    c = FakeClient([
        LLMResponse(content="", tool_calls=[
            ToolCall(id="c1", name="echo", arguments='{"text":"hi"}')]),
        LLMResponse(content="done"),
    ])
    loop = AgentLoop(client=c, system_prompt="sys", registry=_registry_with_echo())
    out = loop.run("do echo")
    assert out.content == "done"
    assert len(c.calls) == 2
    # 第二次调用时,messages 已含 tool 回填 → 比第一次长
    assert c.calls[1]["n_messages"] > c.calls[0]["n_messages"]


def test_bad_arguments_fallback():
    # 模型返回非法 JSON 参数 → safe_parse 兜底 {} → echo 缺 text 抛 → 回填 ERROR → 模型继续
    c = FakeClient([
        LLMResponse(content="", tool_calls=[
            ToolCall(id="c1", name="echo", arguments="{bad json")]),
        LLMResponse(content="recovered"),
    ])
    loop = AgentLoop(client=c, system_prompt="sys", registry=_registry_with_echo())
    out = loop.run("x")
    assert out.content == "recovered"


def test_max_steps_raises_escalation():
    # 模型永远返回 tool_call、永不收敛 → max_steps 护栏触发 Escalation
    looping = LLMResponse(content="", tool_calls=[
        ToolCall(id="c1", name="echo", arguments='{"text":"x"}')])
    c = FakeClient([looping] * 100)
    reg = ToolRegistry()
    reg.register("echo", lambda text: "ok", description="d",
                 parameters={"type": "object"})
    loop = AgentLoop(client=c, system_prompt="sys", registry=reg, max_steps=3)
    with pytest.raises(Escalation):
        loop.run("loop forever")
