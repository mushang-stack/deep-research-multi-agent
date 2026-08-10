"""trafilatura 本地网页正文提取(纯本地、零网络、零成本)。"""
import trafilatura

# 抓取失败时的诊断文本(而非空串):给 researcher/verifier 明确信号,
# 配合 prompt 的"跳过空页"指令闭合重试循环(M2 回修:web_read 56% 空失败的放大器)。
_FETCH_FAILED = "[抓取失败] 无法提取该页正文,请跳过此 URL"


def fetch_text(url: str, max_chars: int = 8000) -> str:
    """下载并提取正文;下载失败/提取为空返回诊断文本。max_chars>0 时截断防爆上下文;0 表示不截断。"""
    downloaded = trafilatura.fetch_url(url)
    if not downloaded:
        return _FETCH_FAILED
    text = trafilatura.extract(downloaded, with_metadata=False) or ""
    if not text:
        return _FETCH_FAILED
    if max_chars:
        text = text[:max_chars]
    return text
