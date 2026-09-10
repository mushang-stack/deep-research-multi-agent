from agents.researcher import make_researcher
from agents.prompts import RESEARCHER_PROMPT, RESEARCHER_LAST_STEP_NUDGE
from llm.base import LLMClient, LLMResponse, ToolCall
from core.schemas import SearchResult


class FakeClient(LLMClient):
    def __init__(self, responses):
        self._r = list(responses)

    def chat(self, **kw):
        return self._r.pop(0)


class _FakeSearch:
    def search(self, query):
        return [SearchResult(title="T", url="https://x", snippet="s")]


def test_researcher_wiring():
    agent = make_researcher(client=FakeClient([]), search_client=_FakeSearch())
    assert agent.system_prompt == RESEARCHER_PROMPT
    assert agent.name == "researcher"
    assert set(agent.registry.names()) == {"web_search", "web_read"}


def test_researcher_runs_search_then_json():
    client = FakeClient([
        LLMResponse(content="", tool_calls=[
            ToolCall(id="1", name="web_search", arguments='{"query":"x"}')]),
        LLMResponse(content='{"findings": [{"id":"f1","claim":"c","source_url":"https://x"}]}'),
    ])
    agent = make_researcher(client=client, search_client=_FakeSearch())
    out = agent.run("研究 x")
    assert "findings" in out.content
    assert len(out.history) >= 4  # system + user + assistant(tool) + tool + assistant


def test_researcher_last_step_nudge_default_on():
    # F1 收敛修复默认开:工厂自动带预告文案(spec D2)
    loop = make_researcher(client=FakeClient([]), search_client=_FakeSearch())
    assert loop.last_step_nudge == RESEARCHER_LAST_STEP_NUDGE


def test_researcher_nudge_overridable_via_loop_kwargs():
    loop = make_researcher(client=FakeClient([]), search_client=_FakeSearch(),
                           loop_kwargs={"last_step_nudge": None, "on_exhaustion": "partial"})
    assert loop.last_step_nudge is None and loop.on_exhaustion == "partial"
