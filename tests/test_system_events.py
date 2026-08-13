# tests/test_system_events.py
import pytest

from agents.system import build_system
from agents.observe import set_sink, clear_sink
from core.events import EventSink
from core.config import Config
from llm.base import LLMClient, LLMResponse


class _DummySearch:
    def search(self, query):
        return []


class _FinalClient(LLMClient):
    """立即返回无 tool_call 的最终内容,让 researcher loop 1 步收敛(不烧真实 API)。"""
    def chat(self, **kw):
        return LLMResponse(content='{"findings":[]}', usage={})


@pytest.fixture
def sink():
    s = EventSink()
    set_sink(s)
    yield s
    clear_sink()


def _cfg():
    return Config({"models": {"generator": {}},
                   "tools": {"web_search": {}, "web_read": {"max_chars": 8000}},
                   "guards": {"agent_max_steps": 12, "research_max_rounds": 3}})


def test_researcher_emits_start_done(sink):
    loop, _ = build_system(_cfg(), client=_FinalClient(), search_client=_DummySearch())
    loop.registry.execute("dispatch_research", {"sub_questions": ["q1"]})
    kinds = [e["kind"] for e in sink.snapshot()]
    assert "research_start" in kinds
    assert "research_done" in kinds
    starts = [e for e in sink.snapshot() if e["kind"] == "research_start"]
    assert starts[0]["role"] == "researcher"
    assert starts[0]["payload"]["sub_question"] == "q1"


def test_researcher_done_has_content_len(sink):
    loop, _ = build_system(_cfg(), client=_FinalClient(), search_client=_DummySearch())
    loop.registry.execute("dispatch_research", {"sub_questions": ["q1"]})
    done = [e for e in sink.snapshot() if e["kind"] == "research_done"][0]
    assert "content_len" in done["payload"]
