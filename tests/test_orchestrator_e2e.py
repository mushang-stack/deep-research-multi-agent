import json

from agents.orchestrator import make_orchestrator
from llm.base import LLMClient, LLMResponse, ToolCall


class FakeClient(LLMClient):
    def __init__(self, responses):
        self._r = list(responses)

    def chat(self, **kw):
        return self._r.pop(0)


def _result(content):
    return type("R", (), {"content": content})()


def test_end_to_end_dispatch_verify_write():
    client = FakeClient([
        LLMResponse(content="", tool_calls=[ToolCall(
            id="1", name="dispatch_research",
            arguments='{"sub_questions":["q1","q2"]}')]),
        LLMResponse(content="", tool_calls=[ToolCall(
            id="2", name="verify_findings",
            arguments='{"findings":[{"id":"f1"}]}')]),
        LLMResponse(content="", tool_calls=[ToolCall(
            id="3", name="write_report",
            arguments='{"outline":"O","verified_findings":[{"id":"f1"}]}')]),
        LLMResponse(content="报告已生成"),
    ])
    loop, get_report = make_orchestrator(
        client=client,
        run_researcher=lambda sq: _result(json.dumps({"findings": [{"id": "f1", "claim": sq, "source_url": "https://x"}]})),
        run_verifier=lambda msg: _result('{"results":[{"finding_id":"f1","verdict":"supported"}]}'),
        run_writer=lambda msg: _result('{"sections":[{"heading":"H","content":"C","citations":["f1"]}],"sources":["https://x"]}'),
    )
    out = loop.run("研究问题")

    assert out.content == "报告已生成"
    report = get_report()
    assert report is not None
    assert report.sections[0].citations == ["f1"]
    # 上下文隔离:回填的 tool 消息是结构化 JSON,不是原始网页正文
    tool_msgs = [m for m in out.history if m.get("role") == "tool"]
    assert any("findings" in m["content"] for m in tool_msgs)


def test_finishes_without_write_report_yields_none():
    client = FakeClient([LLMResponse(content="我无法完成")])  # 直接收尾,未调 write_report
    loop, get_report = make_orchestrator(
        client=client,
        run_researcher=lambda sq: _result("{}"),
        run_verifier=lambda msg: _result("{}"),
        run_writer=lambda msg: _result("{}"),
    )
    loop.run("问题")
    assert get_report() is None  # 从未产出报告 → 调用方据此判定失败
