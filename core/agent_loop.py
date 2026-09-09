"""AgentLoop —— 核心原语(spec §4)。调模型→有 tool_call 则经 ToolRegistry 执行并回填、
无 tool_call 则返回。max_steps 护栏防无限循环/成本失控(超限抛 Escalation)。
工具执行异常被捕获并回填为 ERROR 文本(非致命,让模型自行处理)。"""
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence

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
    degraded: bool = False                       # P1:partial 强制收尾产出(仅预算耗尽路径)


class AgentLoop:
    def __init__(self, *, client: LLMClient, system_prompt: str,
                 registry: Optional[Any] = None, model: Optional[str] = None,
                 temperature: Optional[float] = None,
                 max_tokens: Optional[int] = None,
                 max_steps: int = 12, name: str = "agent",
                 recorder=None,
                 budgets: Optional[Sequence] = None,      # list[Budget] | None;任一超限触发 on_exhaustion
                 on_exhaustion: str = "escalate",         # "escalate" | "partial"
                 max_tool_result_chars: Optional[int] = None,   # 循环层工具结果截断;None=不截断
                 compaction: Optional["CompactionConfig"] = None,  # P2,Task 6 实现(字符串前向引用,现在不存在也不报错)
                 on_compact: Optional[Callable[[dict], None]] = None):
        self.client = client
        self.system_prompt = system_prompt
        self.registry = registry
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_steps = max_steps
        self.name = name
        self.recorder = recorder
        self.budgets = list(budgets) if budgets else None
        self.on_exhaustion = on_exhaustion
        self.max_tool_result_chars = max_tool_result_chars
        self.compaction = compaction
        self.on_compact = on_compact

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
        compactions = 0          # NEW(P2 计数,Task 6 使用)
        last_prompt_tokens = 0   # NEW(压缩触发信号:上轮响应 usage.prompt_tokens)

        for _ in range(self.max_steps):
            # NEW ── 护栏检查点:上轮 tool 已回填、本轮 chat 前 ──
            # 模型已给最终回答的轮次在循环体内直接 return,到不了这里 → 活干完不降级
            if self.budgets and any(b.exceeded for b in self.budgets):
                b = next(b for b in self.budgets if b.exceeded)
                wall = time.perf_counter() - start
                self._record(steps, total_prompt, total_completion, wall, hit_max=False,
                             stop_reason="token_budget", compactions=compactions)
                if self.on_exhaustion == "partial":          # Task 4 实现
                    return self._forced_wrapup(messages, steps, total_prompt,
                                               total_completion, start, compactions)
                raise Escalation(
                    f"{self.name} exceeded token budget {b.spent}/{b.limit}",
                    context={"name": self.name, "reason": "token_budget",
                             "spent": b.spent, "limit": b.limit},
                )

            steps += 1
            resp = self.client.chat(
                messages=messages, tools=tools,
                model=self.model, temperature=self.temperature, max_tokens=self.max_tokens,
            )
            u = resp.usage or {}
            pt = u.get("prompt_tokens", 0) or 0
            ct = u.get("completion_tokens", 0) or 0
            total_prompt += pt
            total_completion += ct
            last_prompt_tokens = pt                   # NEW
            if self.budgets:                          # NEW:响应后收费(诚实计数)
                for b in self.budgets:
                    b.charge(pt, ct)

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
                self._record(steps, total_prompt, total_completion, wall, hit_max=False,
                             compactions=compactions)
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
                content = _to_text(result)
                if self.max_tool_result_chars and len(content) > self.max_tool_result_chars:
                    m = self.max_tool_result_chars
                    content = f"{content[:m]}…[truncated {m}/{len(content)} chars]"
                messages.append({"role": "tool", "tool_call_id": tc.id,
                                 "content": content})

        # 用尽 max_steps 仍未收敛 → 上报后显式 escalation(sink 先记,不丢)
        wall = time.perf_counter() - start
        self._record(steps, total_prompt, total_completion, wall, hit_max=True,
                     stop_reason="max_steps", compactions=compactions)
        raise Escalation(
            f"{self.name} hit max_steps={self.max_steps}",
            context={"name": self.name, "steps": self.max_steps},
        )

    def _record(self, steps, prompt, completion, wall_s, *, hit_max,
                degraded=False, stop_reason="", compactions=0):
        if self.recorder is not None:
            self.recorder.record(AgentRecord(
                name=self.name, steps=steps, max_steps=self.max_steps,
                prompt_tokens=prompt, completion_tokens=completion,
                wall_s=wall_s, hit_max=hit_max,
                degraded=degraded, stop_reason=stop_reason, compactions=compactions))
