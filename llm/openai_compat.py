"""OpenAI 兼容客户端:DeepSeek/GLM 共用。负责 SDK 异常翻译→TransientError、重试、响应映射。"""
import time

import openai
from openai import OpenAI

from .base import LLMClient, LLMResponse, ToolCall
from core.robust import retry_with_backoff, TransientError

# 瞬时状态码:重试;其余(400/401/403/404…)直接上抛
_TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})


class OpenAICompatClient(LLMClient):
    def __init__(self, *, api_key, base_url, model, temperature=0.3, max_tokens=4096,
                 retries=3, backoff=1.5, sleep=time.sleep, client=None):
        # client 可注入:测试传 fake,生产时由各子类(DeepSeek/GLM)用真实 OpenAI
        self._client = client or OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._retries = retries
        self._backoff = backoff
        self._sleep = sleep

    def chat(self, *, messages, tools=None, model=None, temperature=None, max_tokens=None):
        kwargs = {
            "model": model or self.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.temperature,
            "max_tokens": max_tokens or self.max_tokens,
        }
        if tools:
            kwargs["tools"] = tools

        def _call():
            try:
                return self._client.chat.completions.create(**kwargs)
            except openai.APITimeoutError as e:
                raise TransientError("timeout") from e
            except openai.APIConnectionError as e:
                raise TransientError("connection") from e
            except openai.APIStatusError as e:
                if e.status_code in _TRANSIENT_STATUS:
                    raise TransientError(f"status {e.status_code}") from e
                raise  # 非瞬时(400/401/403/404…)直接上抛,不重试

        resp = retry_with_backoff(_call, retries=self._retries,
                                  base=self._backoff, sleep=self._sleep)
        msg = resp.choices[0].message
        tool_calls = [
            ToolCall(id=tc.id, name=tc.function.name, arguments=tc.function.arguments or "{}")
            for tc in (msg.tool_calls or [])
        ]
        usage = {}
        if getattr(resp, "usage", None):
            usage = {
                "prompt_tokens": resp.usage.prompt_tokens,
                "completion_tokens": resp.usage.completion_tokens,
            }
        return LLMResponse(content=msg.content or "", tool_calls=tool_calls, usage=usage, raw=resp)
