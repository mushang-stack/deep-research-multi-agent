from agents.writer import make_writer
from agents.prompts import WRITER_PROMPT
from llm.base import LLMClient, LLMResponse


class FakeClient(LLMClient):
    def __init__(self, responses):
        self._r = list(responses)
        self.last_tools = None

    def chat(self, *, messages, tools=None, **kw):
        self.last_tools = tools
        return self._r.pop(0)


def test_writer_has_no_tools():
    agent = make_writer(client=FakeClient([]))
    assert agent.system_prompt == WRITER_PROMPT
    assert agent.name == "writer"
    assert agent.registry is None  # 无工具:防幻觉硬保证


def test_writer_passes_no_tools_to_client():
    client = FakeClient([
        LLMResponse(content='{"sections": [], "sources": []}'),
    ])
    agent = make_writer(client=client)
    agent.run('{"findings": []}')
    assert client.last_tools is None  # 确认 client 收到 tools=None


def test_writer_outputs_report_json():
    client = FakeClient([
        LLMResponse(content='{"sections":[{"heading":"H","content":"C","citations":["f1"]}],"sources":["https://x"]}'),
    ])
    agent = make_writer(client=client)
    out = agent.run('{"findings": [{"id":"f1"}]}')
    assert "sections" in out.content and "sources" in out.content
