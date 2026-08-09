"""健壮性层(spec §4 / §9):重试、坏 tool-call 兜底解析、Escalation。
注:本模块不 import openai,保持与 SDK 解耦 —— 瞬时错误由各 client 翻译成 TransientError。"""
import json
import time
from typing import Callable


class TransientError(Exception):
    """瞬时错误(503/超时/连接),可重试。"""


class Escalation(Exception):
    """自主失败:显式上抛而非静默崩。携带 reason + context。"""

    def __init__(self, reason: str, context: dict | None = None):
        super().__init__(reason)
        self.reason = reason
        self.context = context or {}


def retry_with_backoff(
    fn: Callable,
    *,
    retries: int = 3,
    base: float = 1.5,
    sleep: Callable[[float], None] = time.sleep,
    exceptions: tuple = (TransientError,),
):
    """对瞬时错误做指数退避重试;非指定异常直接上抛。"""
    last_exc = None
    for attempt in range(retries):
        try:
            return fn()
        except exceptions as e:
            last_exc = e
            if attempt == retries - 1:
                raise
            sleep(base ** attempt)
    raise last_exc  # 逻辑上不可达


def safe_parse_arguments(raw) -> dict:
    """兜底解析模型返回的 tool-call 参数。
    DeepSeek V4 复杂场景结构化输出成功率约 60%(spec §10),坏参数兜底为空 dict。"""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}
