import pytest

from tools.web_search import BochaSearchClient, _parse


class _FakeResp:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


def test_parse_extracts_results():
    data = {"data": {"webPages": {"value": [
        {"name": "A", "url": "https://a", "summary": "sa"},
        {"name": "B", "url": "https://b", "snippet": "sb"},
    ]}}}
    res = _parse(data)
    assert len(res) == 2
    assert res[0].title == "A" and res[0].snippet == "sa"
    assert res[1].snippet == "sb"


def test_parse_handles_empty_shapes():
    assert _parse({}) == []
    assert _parse({"data": {}}) == []
    assert _parse({"data": {"webPages": {}}}) == []


def test_search_sends_bearer_and_query(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return _FakeResp({"data": {"webPages": {"value": [
            {"name": "X", "url": "https://x", "summary": "sx"}]}}})

    monkeypatch.setattr("tools.web_search.httpx.post", fake_post)
    c = BochaSearchClient(api_key="sk-test", count=5)
    res = c.search("hello world")

    assert captured["headers"]["Authorization"] == "Bearer sk-test"
    assert captured["json"] == {"query": "hello world", "count": 5}
    assert len(res) == 1 and res[0].url == "https://x"


def test_search_raises_on_http_error(monkeypatch):
    monkeypatch.setattr("tools.web_search.httpx.post",
                        lambda *a, **k: _FakeResp({}, status=500))
    c = BochaSearchClient(api_key="sk")
    with pytest.raises(Exception):
        c.search("x")
