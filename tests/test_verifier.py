from agents.verifier import make_verifier
from agents.prompts import VERIFIER_PROMPT
from llm.base import LLMClient, LLMResponse


class FakeClient(LLMClient):
    def __init__(self, responses):
        self._r = list(responses)

    def chat(self, **kw):
        return self._r.pop(0)


class _FakeSearch:
    def search(self, query):
        return []


def test_verifier_wiring():
    agent = make_verifier(client=FakeClient([]), search_client=_FakeSearch())
    assert agent.system_prompt == VERIFIER_PROMPT
    assert agent.name == "verifier"
    assert set(agent.registry.names()) == {"web_search", "web_read"}


def test_verifier_outputs_results_json():
    client = FakeClient([
        LLMResponse(content='{"results": [{"finding_id":"f1","verdict":"supported"}]}'),
    ])
    agent = make_verifier(client=client, search_client=_FakeSearch())
    out = agent.run('{"findings": [{"id":"f1"}]}')
    assert "results" in out.content and "supported" in out.content
