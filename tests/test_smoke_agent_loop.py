"""端到端 smoke:用 FakeClient 证明 AgentLoop + ToolRegistry 整条链路成立(不烧 API)。"""
from core.agent_loop import AgentLoop
from core.tool_registry import ToolRegistry
from llm.base import LLMClient, LLMResponse, ToolCall


class ScriptedClient(LLMClient):
    def __init__(self, script):
        self._script = list(script)
        self.call_count = 0

    def chat(self, **kw):
        self.call_count += 1
        return self._script.pop(0)


def test_end_to_end_two_tools_then_final():
    # 模拟研究员:先搜索、再读网页、最后综合
    def search(query):
        return [{"title": "T", "url": "https://x", "snippet": "s"}]

    def read(url):
        return "正文内容"

    reg = ToolRegistry()
    reg.register("web_search", search, description="搜索",
                 parameters={"type": "object",
                             "properties": {"query": {"type": "string"}},
                             "required": ["query"]})
    reg.register("web_read", read, description="读网页",
                 parameters={"type": "object",
                             "properties": {"url": {"type": "string"}},
                             "required": ["url"]})

    client = ScriptedClient([
        LLMResponse(content="", tool_calls=[
            ToolCall(id="1", name="web_search", arguments='{"query":"a"}')]),
        LLMResponse(content="", tool_calls=[
            ToolCall(id="2", name="web_read", arguments='{"url":"https://x"}')]),
        LLMResponse(content="研究报告:..."),
    ])
    loop = AgentLoop(client=client, system_prompt="你是研究员",
                     registry=reg, max_steps=10, name="smoke")

    out = loop.run("研究一下 a")

    assert out.content == "研究报告:..."
    assert client.call_count == 3
    roles = [m["role"] for m in out.history]
    assert roles.count("tool") == 2          # 两次工具调用都回填了
    assert roles[-1] == "assistant"          # 最后一条是最终产出
