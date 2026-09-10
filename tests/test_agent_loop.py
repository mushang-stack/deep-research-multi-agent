import pytest

from core.agent_loop import AgentLoop, AgentResult, CompactionConfig, compact_messages
from core.tool_registry import ToolRegistry
from core.budget import Budget
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


def _tc(id_):
    return ToolCall(id=id_, name="echo", arguments='{"text":"x"}')


def test_budget_escalate_at_next_iteration_top():
    # limit=150:r1 花 110(<150,继续)→ r2 花后 220(≥150)→ 顶部检查点在 r3 前 → Escalation
    c = FakeClient([
        LLMResponse(content="", usage={"prompt_tokens": 100, "completion_tokens": 10},
                    tool_calls=[_tc("c1")]),
        LLMResponse(content="", usage={"prompt_tokens": 100, "completion_tokens": 10},
                    tool_calls=[_tc("c2")]),
        LLMResponse(content="never reached"),
    ])
    sink = TelemetrySink()
    loop = AgentLoop(client=c, system_prompt="sys", registry=_registry_with_echo(),
                     max_steps=10, name="researcher", recorder=sink,
                     budgets=[Budget(limit=150)])
    with pytest.raises(Escalation) as ei:
        loop.run("q")
    assert len(c.calls) == 2                     # 第 3 次调用被检查点拦下(FakeClient.calls 是 list)
    assert "exceeded token budget 220/150" in str(ei.value)
    assert ei.value.context == {"name": "researcher", "reason": "token_budget",
                                "spent": 220, "limit": 150}
    assert sink.snapshot()[0]["stop_reason"] == "token_budget"
    assert sink.snapshot()[0]["degraded"] is False
    assert sink.snapshot()[0]["hit_max"] is False and sink.snapshot()[0]["steps"] == 2


def test_budget_exceeded_but_final_answer_returns_normally():
    # 预算在最终轮才超:模型已给最终回答 → 正常返回,不降级(活干完了)
    c = FakeClient([
        LLMResponse(content="", usage={"prompt_tokens": 80, "completion_tokens": 10},
                    tool_calls=[_tc("c1")]),
        LLMResponse(content="done", usage={"prompt_tokens": 80, "completion_tokens": 10}),
    ])
    loop = AgentLoop(client=c, system_prompt="sys", registry=_registry_with_echo(),
                     budgets=[Budget(limit=100)])
    out = loop.run("q")
    assert out.content == "done" and out.degraded is False   # spent 180 ≥ 100 但已收敛


def test_dual_budget_charges_both_and_either_triggers():
    # 自身预算 5000(不超)+ run 池 150(超)→ 对两者各 charge 一次,池超即触发
    own, pool = Budget(limit=5000), Budget(limit=150)
    c = FakeClient([
        LLMResponse(content="", usage={"prompt_tokens": 100, "completion_tokens": 10},
                    tool_calls=[_tc("c1")]),
        LLMResponse(content="", usage={"prompt_tokens": 100, "completion_tokens": 10},
                    tool_calls=[_tc("c2")]),
    ])
    loop = AgentLoop(client=c, system_prompt="sys", registry=_registry_with_echo(),
                     budgets=[own, pool])
    with pytest.raises(Escalation) as ei:
        loop.run("q")
    assert own.spent == 220 and pool.spent == 220     # 同一次调用对两者各 charge
    assert ei.value.context["limit"] == 150           # 上报的是超限的那只(run 池)


def test_no_budgets_default_off_behavior_unchanged():
    # 缺省不传 budgets → 全程无预算路径(回归保证)
    c = FakeClient([
        LLMResponse(content="", tool_calls=[_tc("c1")]),
        LLMResponse(content="done"),
    ])
    loop = AgentLoop(client=c, system_prompt="sys", registry=_registry_with_echo())
    assert loop.run("q").content == "done"


def test_budget_partial_forced_wrapup():
    # r1 花 200 超 limit=150 → 顶部检查点走 partial:追加收尾 user 消息,恰好一次收尾调用
    c = FakeClient([
        LLMResponse(content="", usage={"prompt_tokens": 150, "completion_tokens": 50},
                    tool_calls=[_tc("c1")]),
        LLMResponse(content='{"findings":[{"claim":"部分结论"}]}',
                    usage={"prompt_tokens": 300, "completion_tokens": 40}),
    ])
    sink = TelemetrySink()
    loop = AgentLoop(client=c, system_prompt="sys", registry=_registry_with_echo(),
                     max_steps=10, name="researcher", recorder=sink,
                     budgets=[Budget(limit=150)], on_exhaustion="partial")
    out = loop.run("q")
    assert out.degraded is True
    assert out.content.startswith('{"findings"')          # 救回部分产出
    assert len(c.calls) == 2                              # 恰好一次收尾调用
    assert c.calls[1]["tools"] is None                    # 收尾不给工具面(机械保证)
    # 收尾 user 消息追加在 tool 消息之后(契约:tool_call 后必须先回填 tool 消息)
    roles = [(m["role"], m.get("tool_call_id")) for m in out.history]
    assert roles == [("system", None), ("user", None), ("assistant", None),
                     ("tool", "c1"), ("user", None), ("assistant", None)]
    assert out.history[4]["content"].startswith("TOKEN_BUDGET_EXHAUSTED")
    # 收尾调用照常 charge(诚实计数):150+50 + 300+40 = 540
    assert loop.budgets[0].spent == 540
    assert out.usage == {"prompt_tokens": 450, "completion_tokens": 90}
    assert len(sink.snapshot()) == 1                      # 单条 telemetry 记录(partial 不双记)
    snap = sink.snapshot()[0]
    assert snap["degraded"] is True and snap["stop_reason"] == "token_budget"
    assert snap["steps"] == 2 and snap["hit_max"] is False


def test_partial_wrapup_tool_call_ignored():
    # 收尾输出仍带 tool_call → 一律忽略只取 content,不再有后续调用
    c = FakeClient([
        LLMResponse(content="", usage={"prompt_tokens": 100, "completion_tokens": 10},
                    tool_calls=[_tc("c1")]),
        LLMResponse(content="收尾文本", usage={"prompt_tokens": 100, "completion_tokens": 10},
                    tool_calls=[_tc("c2")]),      # 收尾响应违规带 tool_call
    ])
    loop = AgentLoop(client=c, system_prompt="sys", registry=_registry_with_echo(),
                     budgets=[Budget(limit=100)], on_exhaustion="partial")
    out = loop.run("q")
    assert out.content == "收尾文本" and out.degraded is True
    assert len(c.calls) == 2                              # 忽略 tool_call,无第三次调用
    assert out.history[-1] == {"role": "assistant", "content": "收尾文本"}   # 未回填 tool


def test_invalid_on_exhaustion_rejected_at_loop_level():
    # on_exhaustion 非法值在构造时即拒(纵深防御:config 层 Task 8 还会再验一次)
    with pytest.raises(ValueError, match="on_exhaustion"):
        AgentLoop(client=FakeClient([]), system_prompt="sys",
                  on_exhaustion="partail")


def test_partial_steps0_shared_pool_exhausted_escalates():
    # 共享池先被别的 researcher 耗尽 → 本 agent steps==0 无工作可救 → escalate(而非空手收尾)
    pool = Budget(limit=10)
    pool.charge(10, 0)                                  # 池已被耗尽
    c = FakeClient([])                                  # 不应发生任何模型调用
    sink = TelemetrySink()
    loop = AgentLoop(client=c, system_prompt="sys", registry=_registry_with_echo(),
                     name="researcher", recorder=sink,
                     budgets=[pool], on_exhaustion="partial")
    with pytest.raises(Escalation) as ei:
        loop.run("q")
    assert len(c.calls) == 0
    assert ei.value.context["reason"] == "token_budget"
    snap = sink.snapshot()[0]
    assert snap["steps"] == 0 and snap["degraded"] is False
    assert snap["stop_reason"] == "token_budget"


def test_tool_result_truncated_with_marker():
    reg = ToolRegistry()
    reg.register("big", lambda: "L" * 100, description="d", parameters={"type": "object"})
    c = FakeClient([
        LLMResponse(content="", tool_calls=[ToolCall(id="c1", name="big", arguments="{}")]),
        LLMResponse(content="done"),
    ])
    loop = AgentLoop(client=c, system_prompt="sys", registry=reg,
                     max_tool_result_chars=10)
    out = loop.run("q")
    tool_msg = next(m for m in out.history if m["role"] == "tool")
    assert tool_msg["content"] == "L" * 10 + "…[truncated 10/100 chars]"


def test_tool_result_exactly_at_limit_not_truncated():
    reg = ToolRegistry()
    reg.register("big", lambda: "L" * 10, description="d", parameters={"type": "object"})
    c = FakeClient([
        LLMResponse(content="", tool_calls=[ToolCall(id="c1", name="big", arguments="{}")]),
        LLMResponse(content="done"),
    ])
    loop = AgentLoop(client=c, system_prompt="sys", registry=reg,
                     max_tool_result_chars=10)
    out = loop.run("q")
    tool_msg = next(m for m in out.history if m["role"] == "tool")
    assert tool_msg["content"] == "L" * 10          # 恰好等于阈值不截


def test_truncation_off_by_default():
    reg = ToolRegistry()
    reg.register("big", lambda: "L" * 100, description="d", parameters={"type": "object"})
    c = FakeClient([
        LLMResponse(content="", tool_calls=[ToolCall(id="c1", name="big", arguments="{}")]),
        LLMResponse(content="done"),
    ])
    loop = AgentLoop(client=c, system_prompt="sys", registry=reg)   # 不传 → 不截断
    out = loop.run("q")
    tool_msg = next(m for m in out.history if m["role"] == "tool")
    assert tool_msg["content"] == "L" * 100


def test_compaction_stubs_middle_tool_message():
    # r1 pt=50(<100 不触发)→ r2 pt=250(>100)→ 顶部检查点压缩:t1(中间)存根化,t2(末尾)原样
    on_compact_calls = []
    c = FakeClient([
        LLMResponse(content="", usage={"prompt_tokens": 50, "completion_tokens": 5},
                    tool_calls=[ToolCall(id="c1", name="echo",
                                         arguments='{"text":"' + "L" * 100 + '"}')]),
        LLMResponse(content="", usage={"prompt_tokens": 250, "completion_tokens": 5},
                    tool_calls=[ToolCall(id="c2", name="echo",
                                         arguments='{"text":"' + "R" * 100 + '"}')]),
        LLMResponse(content="done", usage={"prompt_tokens": 100, "completion_tokens": 5}),
    ])
    sink = TelemetrySink()
    loop = AgentLoop(client=c, system_prompt="sys", registry=_registry_with_echo(),
                     recorder=sink, name="researcher",
                     compaction=CompactionConfig(enabled=True,
                                                 threshold_prompt_tokens=100,
                                                 keep_last_n=2),
                     on_compact=on_compact_calls.append)
    out = loop.run("q")
    t1 = next(m for m in out.history if m.get("tool_call_id") == "c1")
    t2 = next(m for m in out.history if m.get("tool_call_id") == "c2")
    assert t1["content"].startswith("[stub: echo result, ") and t1["content"].endswith(" chars]")
    assert t2["content"].startswith('{"echo": "RRR')      # keep_last_n 内原样
    assert len(on_compact_calls) == 1
    info = on_compact_calls[0]
    assert info["agent"] == "researcher" and info["threshold"] == 100
    assert info["stubbed_tool_msgs"] == 1 and info["kept_last"] == 2
    assert info["last_prompt_tokens"] == 250
    assert sink.snapshot()[0]["compactions"] == 1


def test_compaction_protects_prefix_pairs_and_truncates_assistant():
    # 前缀(system+user)逐字节不动;assistant 长内容截 200;tool_call_id 配对完整
    on_compact_calls = []
    c = FakeClient([
        LLMResponse(content="分" * 250, usage={"prompt_tokens": 50, "completion_tokens": 5},
                    tool_calls=[ToolCall(id="c1", name="echo",
                                         arguments='{"text":"' + "L" * 100 + '"}')]),
        LLMResponse(content="", usage={"prompt_tokens": 250, "completion_tokens": 5},
                    tool_calls=[ToolCall(id="c2", name="echo",
                                         arguments='{"text":"' + "R" * 100 + '"}')]),
        LLMResponse(content="done", usage={"prompt_tokens": 100, "completion_tokens": 5}),
    ])
    loop = AgentLoop(client=c, system_prompt="sys", registry=_registry_with_echo(),
                     compaction=CompactionConfig(enabled=True,
                                                 threshold_prompt_tokens=100,
                                                 keep_last_n=2),
                     on_compact=on_compact_calls.append)
    out = loop.run("q")
    hist = out.history
    assert hist[0] == {"role": "system", "content": "sys"}       # 前缀未动
    assert hist[1] == {"role": "user", "content": "q"}
    a1 = hist[2]
    assert a1["content"] == "分" * 200                            # assistant 截 200,tool_calls 保留
    assert a1["tool_calls"][0]["id"] == "c1"
    t1 = hist[3]
    assert t1["content"].startswith("[stub: echo result, ")
    # 配对契约:每个 assistant.tool_calls[].id 都有对应 tool 消息(只改内容不删消息)
    tool_ids = {m["tool_call_id"] for m in hist if m["role"] == "tool"}
    asst_ids = {tc["id"] for m in hist if m["role"] == "assistant"
                for tc in m.get("tool_calls") or []}
    assert asst_ids <= tool_ids
    assert on_compact_calls[0]["stubbed_tool_msgs"] == 1         # 只数 tool 存根


def test_compaction_below_threshold_never_triggers():
    on_compact_calls = []
    c = FakeClient([
        LLMResponse(content="", usage={"prompt_tokens": 50, "completion_tokens": 5},
                    tool_calls=[ToolCall(id="c1", name="echo",
                                         arguments='{"text":"' + "L" * 100 + '"}')]),
        LLMResponse(content="done", usage={"prompt_tokens": 60, "completion_tokens": 5}),
    ])
    loop = AgentLoop(client=c, system_prompt="sys", registry=_registry_with_echo(),
                     compaction=CompactionConfig(enabled=True,
                                                 threshold_prompt_tokens=100,
                                                 keep_last_n=2),
                     on_compact=on_compact_calls.append)
    out = loop.run("q")
    assert on_compact_calls == []                                 # 全程低于阈值
    t1 = next(m for m in out.history if m.get("tool_call_id") == "c1")
    assert t1["content"].startswith('{"echo": "LLL')              # 原样


def test_compact_messages_idempotent_and_never_deletes():
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "分" * 250,
         "tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "echo", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "L" * 500},
        {"role": "assistant", "content": "done"},
    ]
    n_before = len(msgs)
    n_tool, n_asst = compact_messages(msgs, protected=2, keep_last_n=1)
    assert (n_tool, n_asst) == (1, 1) and len(msgs) == n_before   # 只改内容不删消息
    n_tool2, n_asst2 = compact_messages(msgs, protected=2, keep_last_n=1)
    assert (n_tool2, n_asst2) == (0, 0)                           # 幂等:二次无收益


def test_compaction_config_validates_footguns():
    with pytest.raises(ValueError, match="keep_last_n"):
        CompactionConfig(enabled=True, threshold_prompt_tokens=100, keep_last_n=1)
    with pytest.raises(ValueError, match="threshold_prompt_tokens"):
        CompactionConfig(enabled=True, threshold_prompt_tokens=0)


def test_budget_crossed_on_final_iteration_escalates_as_token_budget():
    # 预算恰在最后允许迭代超限:循环退出后由 post-loop 检查裁决 → token_budget(非 max_steps)
    c = FakeClient([
        LLMResponse(content="", usage={"prompt_tokens": 100, "completion_tokens": 10},
                    tool_calls=[_tc("c1")]),
        LLMResponse(content="", usage={"prompt_tokens": 100, "completion_tokens": 10},
                    tool_calls=[_tc("c2")]),
    ])
    sink = TelemetrySink()
    loop = AgentLoop(client=c, system_prompt="sys", registry=_registry_with_echo(),
                     max_steps=2, name="researcher", recorder=sink,
                     budgets=[Budget(limit=150)])
    with pytest.raises(Escalation) as ei:
        loop.run("q")
    assert ei.value.context["reason"] == "token_budget"       # 不是 max_steps
    assert "exceeded token budget 220/150" in str(ei.value)
    snap = sink.snapshot()[0]
    assert snap["stop_reason"] == "token_budget" and snap["hit_max"] is False


def test_budget_crossed_on_final_iteration_partial_salvages():
    # 同场景 + partial → 强制收尾救回,而非 max_steps escalation 丢工作
    c = FakeClient([
        LLMResponse(content="", usage={"prompt_tokens": 100, "completion_tokens": 10},
                    tool_calls=[_tc("c1")]),
        LLMResponse(content="", usage={"prompt_tokens": 100, "completion_tokens": 10},
                    tool_calls=[_tc("c2")]),
        LLMResponse(content='{"findings":[{"claim":"救回"}]}',
                    usage={"prompt_tokens": 200, "completion_tokens": 20}),
    ])
    sink = TelemetrySink()
    loop = AgentLoop(client=c, system_prompt="sys", registry=_registry_with_echo(),
                     max_steps=2, budgets=[Budget(limit=150)], on_exhaustion="partial",
                     recorder=sink)
    out = loop.run("q")
    assert out.degraded is True and out.content.startswith('{"findings"')
    assert sink.snapshot()[0]["stop_reason"] == "token_budget"   # 钉 post-loop partial 语义,防与 F2 分支 reorder
    assert len(c.calls) == 3                                  # 2 轮 + 1 次收尾


def test_max_steps_without_budget_unchanged():
    # 无预算时 max_steps 路径原样(回归)
    looping = LLMResponse(content="", tool_calls=[_tc("c1")])
    c = FakeClient([looping] * 100)
    loop = AgentLoop(client=c, system_prompt="sys", registry=_registry_with_echo(),
                     max_steps=3)
    with pytest.raises(Escalation, match="hit max_steps"):
        loop.run("q")


def test_max_steps_partial_forced_wrapup_salvages():
    # max_steps 耗尽 + partial → 不再 Escalation 丢工作,MAX_STEPS_REACHED 收尾救回
    c = FakeClient([
        LLMResponse(content="", usage={"prompt_tokens": 100, "completion_tokens": 10},
                    tool_calls=[_tc("c1")]),
        LLMResponse(content="", usage={"prompt_tokens": 100, "completion_tokens": 10},
                    tool_calls=[_tc("c2")]),
        LLMResponse(content='{"findings":[{"claim":"救回"}]}',
                    usage={"prompt_tokens": 300, "completion_tokens": 30}),
    ])
    sink = TelemetrySink()
    loop = AgentLoop(client=c, system_prompt="sys", registry=_registry_with_echo(),
                     max_steps=2, name="researcher", recorder=sink,
                     on_exhaustion="partial")
    out = loop.run("q")
    assert out.degraded is True
    assert out.content.startswith('{"findings"')
    assert len(c.calls) == 3                          # 2 轮循环 + 1 次收尾
    assert c.calls[2]["tools"] is None                # 收尾不给工具面
    assert out.history[-2]["content"].startswith("MAX_STEPS_REACHED")
    snap = sink.snapshot()
    assert len(snap) == 1
    assert snap[0]["degraded"] is True and snap[0]["stop_reason"] == "max_steps"
    assert snap[0]["steps"] == 3 and snap[0]["hit_max"] is False   # 诚实计数:2+1


def test_max_steps_escalate_agent_unchanged():
    # 未配 partial 的 agent:max_steps 耗尽照旧 Escalation(D1 语义不动)
    looping = LLMResponse(content="", usage={"prompt_tokens": 10, "completion_tokens": 1},
                          tool_calls=[_tc("c1")])
    c = FakeClient([looping] * 100)
    sink = TelemetrySink()
    loop = AgentLoop(client=c, system_prompt="sys", registry=_registry_with_echo(),
                     max_steps=3, name="verifier", recorder=sink)
    with pytest.raises(Escalation, match="hit max_steps"):
        loop.run("q")
    snap = sink.snapshot()[0]
    assert snap["hit_max"] is True and snap["stop_reason"] == "max_steps"
    assert snap["degraded"] is False and snap["steps"] == 3


def test_last_step_nudge_injected_on_final_iteration():
    # max_steps=3:前两轮 tool_call,进入第 3 轮(最后一轮)前注入预告;第 3 轮收敛
    c = FakeClient([
        LLMResponse(content="", tool_calls=[_tc("c1")]),
        LLMResponse(content="", tool_calls=[_tc("c2")]),
        LLMResponse(content="done"),
    ])
    loop = AgentLoop(client=c, system_prompt="sys", registry=_registry_with_echo(),
                     max_steps=3, last_step_nudge="LAST_STEP: 立即输出 Findings JSON。")
    out = loop.run("q")
    assert len(c.calls) == 3                          # 零额外调用
    roles = [m["role"] for m in out.history]
    assert roles == ["system", "user", "assistant", "tool",
                     "assistant", "tool", "user", "assistant"]
    assert out.history[6]["content"] == "LAST_STEP: 立即输出 Findings JSON。"
    assert out.degraded is False and out.content == "done"


def test_last_step_nudge_not_injected_when_converges_early():
    # 第 2 轮(max_steps-1 之前)已收敛 → 无预告
    c = FakeClient([
        LLMResponse(content="", tool_calls=[_tc("c1")]),
        LLMResponse(content="done"),
    ])
    loop = AgentLoop(client=c, system_prompt="sys", registry=_registry_with_echo(),
                     max_steps=5, last_step_nudge="LAST_STEP: 立即输出。")
    out = loop.run("q")
    assert not any(m["role"] == "user" and m["content"].startswith("LAST_STEP")
                   for m in out.history)


def test_last_step_nudge_skipped_on_first_iteration():
    # max_steps=1:首轮无"上一轮" → 不注入(第 1 次调用只见 [system, user])
    c = FakeClient([
        LLMResponse(content="", tool_calls=[_tc("c1")]),
    ])
    loop = AgentLoop(client=c, system_prompt="sys", registry=_registry_with_echo(),
                     max_steps=1, last_step_nudge="LAST_STEP: 立即输出。")
    with pytest.raises(Escalation, match="hit max_steps"):
        loop.run("q")
    assert c.calls[0]["n_messages"] == 2              # 无预告消息


def test_last_step_nudge_none_by_default():
    # 缺省 None → 全程无预告(其他 agent 零扰动)
    c = FakeClient([
        LLMResponse(content="", tool_calls=[_tc("c1")]),
        LLMResponse(content="", tool_calls=[_tc("c2")]),
        LLMResponse(content="done"),
    ])
    loop = AgentLoop(client=c, system_prompt="sys", registry=_registry_with_echo(),
                     max_steps=3)
    out = loop.run("q")
    assert not any(m["role"] == "user" and m["content"].startswith("LAST_STEP")
                   for m in out.history)
