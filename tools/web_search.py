"""博查 Bocha 网页搜索(REST)。返回 List[SearchResult]。
注意:返回的是搜索结果,不是 Finding —— 是否采信由 Researcher(M2)决定。"""
import httpx

from core.schemas import SearchResult


def _parse(data: dict) -> list[SearchResult]:
    """从博查返回提取结果列表,容忍缺字段/空结构。"""
    items = (((data.get("data") or {}).get("webPages") or {}).get("value")) or []
    results = []
    for it in items:
        results.append(SearchResult(
            title=it.get("name") or it.get("title") or "",
            url=it.get("url", ""),
            snippet=it.get("summary") or it.get("snippet") or "",
        ))
    return results


class BochaSearchClient:
    def __init__(self, api_key, endpoint="https://api.bochaai.com/v1/web-search",
                 count=8, timeout=15):
        self._api_key = api_key
        self._endpoint = endpoint
        self._count = count
        self._timeout = timeout

    def search(self, query: str, count: int | None = None) -> list[SearchResult]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {"query": query, "count": count or self._count}
        resp = httpx.post(self._endpoint, headers=headers, json=payload, timeout=self._timeout)
        resp.raise_for_status()
        return _parse(resp.json())
