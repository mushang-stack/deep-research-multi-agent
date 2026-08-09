from agents._webtools import build_web_registry
from core.schemas import SearchResult


class _FakeSearch:
    def __init__(self, results):
        self._r = results
        self.queries = []

    def search(self, query):
        self.queries.append(query)
        return self._r


def test_registry_has_both_tools():
    reg = build_web_registry(_FakeSearch([]))
    assert set(reg.names()) == {"web_search", "web_read"}


def test_web_search_calls_client_and_dumps(monkeypatch):
    fake = _FakeSearch([SearchResult(title="T", url="https://x", snippet="s")])
    reg = build_web_registry(fake)
    out = reg.execute("web_search", {"query": "量子计算"})
    assert fake.queries == ["量子计算"]
    assert out == [{"title": "T", "url": "https://x", "snippet": "s"}]


def test_web_read_calls_fetch_text(monkeypatch):
    from agents import _webtools
    monkeypatch.setattr(_webtools, "fetch_text", lambda url, max_chars=8000: "正文")
    reg = build_web_registry(_FakeSearch([]), max_chars=20)
    out = reg.execute("web_read", {"url": "https://x"})
    assert out == "正文"


def test_web_search_schema_is_function():
    reg = build_web_registry(_FakeSearch([]))
    s = reg.schemas()
    names = {x["function"]["name"] for x in s}
    assert names == {"web_search", "web_read"}
