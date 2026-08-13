import pytest

from core.agent_loop import AgentLoop, AgentResult
from core.tool_registry import ToolRegistry
from core.robust import Escalation
from core.telemetry import TelemetrySink
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


def test_message_history_shape_matches_openai_api():
    # 消息历史必须兼容 OpenAI/DeepSeek API:assistant.tool_calls 形状 + tool 回填 + tool_call_id 匹配
    c = FakeClient([
        LLMResponse(content="", tool_calls=[
            ToolCall(id="c1", name="echo", arguments='{"text":"hi"}')]),
        LLMResponse(content="done"),
    ])
    loop = AgentLoop(client=c, system_prompt="sys", registry=_registry_with_echo())
    out = loop.run("do echo")
    hist = out.history
    # 顺序:[system, user, assistant(tool_calls), tool, assistant(final)]
    assert hist[0]["role"] == "system"
    assert hist[1]["role"] == "user" and hist[1]["content"] == "do echo"
    assert hist[2]["role"] == "assistant"
    assert hist[2]["tool_calls"][0]["type"] == "function"
    assert hist[2]["tool_calls"][0]["id"] == "c1"
    assert hist[2]["tool_calls"][0]["function"]["name"] == "echo"
    assert hist[2]["tool_calls"][0]["function"]["arguments"] == '{"text":"hi"}'
    assert hist[3]["role"] == "tool"
    assert hist[3]["tool_call_id"] == "c1"          # 必须匹配 assistant 的 tool_calls[].id
    assert hist[4]["role"] == "assistant" and hist[4]["content"] == "done"


def test_multiple_tool_calls_in_one_turn():
    # 单轮多个 tool_call:每个都必须执行并回填(API 要求每个 tool_call 都有对应 tool 消息)
    def add(a, b):
        return a + b
    reg = ToolRegistry()
    reg.register("add", add, description="d", parameters={"type": "object"})
    c = FakeClient([
        LLMResponse(content="", tool_calls=[
            ToolCall(id="t1", name="add", arguments='{"a":1,"b":2}'),
            ToolCall(id="t2", name="add", arguments='{"a":10,"b":20}'),
        ]),
        LLMResponse(content="done"),
    ])
    loop = AgentLoop(client=c, system_prompt="sys", registry=reg)
    out = loop.run("add twice")
    tool_msgs = [m for m in out.history if m["role"] == "tool"]
    assert len(tool_msgs) == 2
    assert [m["tool_call_id"] for m in tool_msgs] == ["t1", "t2"]   # 顺序保留
    assert tool_msgs[0]["content"] == "3"
    assert tool_msgs[1]["content"] == "30"


def test_context_messages_injected_before_user():
    c = FakeClient([LLMResponse(content="ok")])
    loop = AgentLoop(client=c, system_prompt="sys")
    out = loop.run("q", context_messages=[{"role": "user", "content": "prior context"}])
    # 第一次 chat 收到 [system, context-user, user] = 3 条
    assert c.calls[0]["n_messages"] == 3
    hist = out.history
    assert hist[0]["role"] == "system"
    assert hist[1]["content"] == "prior context"      # context 注入在 user 之前
    assert hist[2]["role"] == "user" and hist[2]["content"] == "q"


def test_escalation_carries_context():
    looping = LLMResponse(content="", tool_calls=[
        ToolCall(id="c1", name="echo", arguments='{"text":"x"}')])
    c = FakeClient([looping] * 100)
    reg = ToolRegistry()
    reg.register("echo", lambda text: "ok", description="d", parameters={"type": "object"})
    loop = AgentLoop(client=c, system_prompt="sys", registry=reg, max_steps=3, name="researcher")
    with pytest.raises(Escalation) as ei:
        loop.run("loop")
    assert ei.value.context["name"] == "researcher"
    assert ei.value.context["steps"] == 3


def test_usage_aggregates_across_steps():
    # 3 步:前两步 tool_call,第三步收敛 → usage 求和(原 bug 只留末步)
    c = FakeClient([
        LLMResponse(content="", usage={"prompt_tokens": 100, "completion_tokens": 10},
                    tool_calls=[ToolCall(id="c1", name="echo", arguments='{"text":"a"}')]),
        LLMResponse(content="", usage={"prompt_tokens": 200, "completion_tokens": 20},
                    tool_calls=[ToolCall(id="c2", name="echo", arguments='{"text":"b"}')]),
        LLMResponse(content="done", usage={"prompt_tokens": 300, "completion_tokens": 30}),
    ])
    loop = AgentLoop(client=c, system_prompt="sys", registry=_registry_with_echo())
    out = loop.run("x")
    assert out.usage == {"prompt_tokens": 600, "completion_tokens": 60}


def test_recorder_records_on_converge():
    c = FakeClient([
        LLMResponse(content="", usage={"prompt_tokens": 100, "completion_tokens": 10},
                    tool_calls=[ToolCall(id="c1", name="echo", arguments='{"text":"a"}')]),
        LLMResponse(content="done", usage={"prompt_tokens": 50, "completion_tokens": 5}),
    ])
    sink = TelemetrySink()
    loop = AgentLoop(client=c, system_prompt="sys", registry=_registry_with_echo(),
                     max_steps=12, name="researcher", recorder=sink)
    loop.run("x")
    snap = sink.snapshot()
    assert len(snap) == 1
    assert snap[0]["name"] == "researcher"
    assert snap[0]["steps"] == 2
    assert snap[0]["max_steps"] == 12
    assert snap[0]["prompt_tokens"] == 150
    assert snap[0]["hit_max"] is False


def test_recorder_records_hit_max_before_escalation():
    looping = LLMResponse(content="", usage={"prompt_tokens": 10, "completion_tokens": 1},
                          tool_calls=[ToolCall(id="c1", name="echo", arguments='{"text":"x"}')])
    c = FakeClient([looping] * 100)
    reg = ToolRegistry()
    reg.register("echo", lambda text: "ok", description="d", parameters={"type": "object"})
    sink = TelemetrySink()
    loop = AgentLoop(client=c, system_prompt="sys", registry=reg, max_steps=3,
                     name="researcher", recorder=sink)
    with pytest.raises(Escalation):
        loop.run("loop")
    snap = sink.snapshot()
    assert len(snap) == 1
    assert snap[0]["hit_max"] is True
    assert snap[0]["steps"] == 3


def test_recorder_none_backward_compat():
    c = FakeClient([LLMResponse(content="ok")])
    loop = AgentLoop(client=c, system_prompt="sys")       # 不传 recorder
    assert loop.run("hi").content == "ok"
