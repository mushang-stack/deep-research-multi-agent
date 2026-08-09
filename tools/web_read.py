"""trafilatura 本地网页正文提取(纯本地、零网络、零成本)。"""
import trafilatura


def fetch_text(url: str, max_chars: int = 8000) -> str:
    """下载并提取正文;失败/空页返回空串。max_chars>0 时截断防爆上下文;0 表示不截断。"""
    downloaded = trafilatura.fetch_url(url)
    if not downloaded:
        return ""
    text = trafilatura.extract(downloaded, with_metadata=False) or ""
    if max_chars:
        text = text[:max_chars]
    return text
