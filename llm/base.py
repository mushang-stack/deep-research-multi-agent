"""LLM 客户端统一接口。AgentLoop 仅依赖此抽象,故可用 FakeClient 单测。"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # 模型返回的原始 JSON 字符串(可能非法,由 robust.safe_parse_arguments 兜底)


@dataclass
class LLMResponse:
    content: str  # 助手文本(有 tool_calls 时可能为空)
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict = field(default_factory=dict)  # {prompt_tokens, completion_tokens}
    raw: Any = None  # 保留原始返回,便于调试


class LLMClient(ABC):
    """生成模型客户端抽象。DeepSeek/GLM 各实现一份 chat。"""

    @abstractmethod
    def chat(
        self,
        *,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        ...
