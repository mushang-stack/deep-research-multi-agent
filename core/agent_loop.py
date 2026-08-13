"""AgentLoop —— 核心原语(spec §4)。调模型→有 tool_call 则经 ToolRegistry 执行并回填、
无 tool_call 则返回。max_steps 护栏防无限循环/成本失控(超限抛 Escalation)。
工具执行异常被捕获并回填为 ERROR 文本(非致命,让模型自行处理)。"""
import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from llm.base import LLMClient
from .robust import safe_parse_arguments, Escalation
from .telemetry import AgentRecord


def _to_text(result: Any) -> str:
    """把工具返回值序列化成 tool message 的 content 字符串。"""
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(result)


@dataclass
class AgentResult:
    content: str
    history: list = field(default_factory=list)  # 完整消息历史(调试/审计/M2 上下文隔离参考)
    usage: dict = field(default_factory=dict)


class AgentLoop:
    def __init__(self, *, client: LLMClient, system_prompt: str,
                 registry: Optional[Any] = None, model: Optional[str] = None,
                 temperature: Optional[float] = None,
                 max_tokens: Optional[int] = None,
                 max_steps: int = 12, name: str = "agent",
                 recorder=None):
        self.client = client
        self.system_prompt = system_prompt
        self.registry = registry
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_steps = max_steps
        self.name = name
        self.recorder = recorder

    def run(self, user_message: str, context_messages: Optional[list] = None) -> AgentResult:
        messages: list[dict] = [{"role": "system", "content": self.system_prompt}]
        if context_messages:
            messages += context_messages
        messages.append({"role": "user", "content": user_message})

        tools = self.registry.schemas() if self.registry else None
        start = time.perf_counter()
        total_prompt = 0
        total_completion = 0
        steps = 0

        for _ in range(self.max_steps):
            steps += 1
            resp = self.client.chat(
                messages=messages, tools=tools,
                model=self.model, temperature=self.temperature, max_tokens=self.max_tokens,
            )
            u = resp.usage or {}
            total_prompt += u.get("prompt_tokens", 0) or 0
            total_completion += u.get("completion_tokens", 0) or 0

            assistant_msg: dict = {"role": "assistant", "content": resp.content}
            if resp.tool_calls:
                assistant_msg["tool_calls"] = [
                    {"id": t.id, "type": "function",
                     "function": {"name": t.name, "arguments": t.arguments}}
                    for t in resp.tool_calls
                ]
            messages.append(assistant_msg)

            # 无 tool_call → 模型产出最终结果,返回
            if not resp.tool_calls:
                wall = time.perf_counter() - start
                self._record(steps, total_prompt, total_completion, wall, hit_max=False)
                return AgentResult(
                    content=resp.content, history=messages,
                    usage={"prompt_tokens": total_prompt,
                           "completion_tokens": total_completion})

            # 有 tool_call → 逐个执行并回填 tool 消息
            for tc in resp.tool_calls:
                args = safe_parse_arguments(tc.arguments)
                try:
                    result = self.registry.execute(tc.name, args)
                except Exception as e:
                    # 工具执行失败:错误回填给模型,让它自行处理(非致命)
                    result = f"ERROR executing {tc.name}: {e!r}"
                messages.append({"role": "tool", "tool_call_id": tc.id,
                                 "content": _to_text(result)})

        # 用尽 max_steps 仍未收敛 → 上报后显式 escalation(sink 先记,不丢)
        wall = time.perf_counter() - start
        self._record(steps, total_prompt, total_completion, wall, hit_max=True)
        raise Escalation(
            f"{self.name} hit max_steps={self.max_steps}",
            context={"name": self.name, "steps": self.max_steps},
        )

    def _record(self, steps, prompt, completion, wall_s, *, hit_max):
        if self.recorder is not None:
            self.recorder.record(AgentRecord(
                name=self.name, steps=steps, max_steps=self.max_steps,
                prompt_tokens=prompt, completion_tokens=completion,
                wall_s=wall_s, hit_max=hit_max))
