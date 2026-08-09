"""web 工具(web_search + web_read)绑定的共享 registry 构造。Researcher/Verifier 复用。"""
from core.tool_registry import ToolRegistry
from tools.web_read import fetch_text


def build_web_registry(search_client, *, max_chars: int = 8000) -> ToolRegistry:
    """绑定 web_search(→ search_client.search,model_dump 成 dict)与 web_read(→ fetch_text)。"""
    reg = ToolRegistry()
    reg.register(
        "web_search",
        lambda query: [r.model_dump() for r in search_client.search(query)],
        description="网页搜索:给定 query 返回相关网页列表(标题/URL/摘要)。",
        parameters={"type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"]},
    )
    reg.register(
        "web_read",
        lambda url: fetch_text(url, max_chars=max_chars),
        description="提取指定 URL 的网页正文。",
        parameters={"type": "object",
                    "properties": {"url": {"type": "string"}},
                    "required": ["url"]},
    )
    return reg
