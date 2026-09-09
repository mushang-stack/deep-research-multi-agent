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
class CompactionConfig:
    """P2 上下文压缩配置(config compaction 段 1:1 映射)。本轮只实现 stub 策略。"""
    enabled: bool = False
    threshold_prompt_tokens: int = 30000   # DeepSeek 64k 窗口一半作软上限
    keep_last_n: int = 4
    strategy: str = "stub"                 # 枚举留扩展位(LLM 摘要是未来选项)

    def __post_init__(self):
        if self.keep_last_n < 2:
            raise ValueError(f"keep_last_n 必须 ≥ 2(保护至少一轮完整 tool 结果):{self.keep_last_n}")
        if self.threshold_prompt_tokens < 1:
            raise ValueError(f"threshold_prompt_tokens 必须 ≥ 1:{self.threshold_prompt_tokens}")


def compact_messages(messages: list, *, protected: int, keep_last_n: int) -> tuple[int, int]:
    """启发式 stub 压缩(确定性、零额外 token)。
    protected:受保护前缀长度(system + context_messages + 首个 user),逐字节不动。
    keep_last_n:尾部原样保留条数。中间旧 tool 消息 → '[stub: {tool_name} result, {N} chars]';
    中间旧 assistant content 截 200 字(tool_calls 保留)。只改内容、永不删消息——
    删 tool 消息会破坏 tool_call_id 配对契约(API 直接报错)。
    返回 (存根化 tool 条数, 截断 assistant 条数);已是 stub 的跳过(幂等)。"""
    names = {}
    for m in messages:
        for tc in m.get("tool_calls") or []:
            names[tc["id"]] = tc["function"]["name"]
    middle_end = max(protected, len(messages) - keep_last_n)   # 保护前缀与尾部区不重叠
    n_tool = n_asst = 0
    for m in messages[protected:middle_end]:
        content = m.get("content")
        if m["role"] == "tool":
            if isinstance(content, str) and content.startswith("[stub: "):
                continue                                          # 已存根,幂等跳过
            m["content"] = f"[stub: {names.get(m.get('tool_call_id'), 'tool')} result, {len(content or '')} chars]"
            n_tool += 1
        elif m["role"] == "assistant":
            if isinstance(content, str) and len(content) > 200:
                m["content"] = content[:200]
                n_asst += 1
    return n_tool, n_asst


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
                 compaction: Optional["CompactionConfig"] = None,  # P2 上下文压缩(默认关闭)
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
        if on_exhaustion not in ("escalate", "partial"):
            raise ValueError(f"on_exhaustion 非法:{on_exhaustion!r}(允许 escalate|partial)")
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
                if self.on_exhaustion == "partial" and steps > 0:
                    # steps==0 = 本 agent 尚无任何产出(仅共享池先耗尽场景),无工作可救 → escalate;
                    # partial 路径的 telemetry 由 _forced_wrapup 记一条(degraded=True),此处不双记
                    return self._forced_wrapup(messages, steps, total_prompt,
                                               total_completion, start, compactions)
                wall = time.perf_counter() - start
                self._record(steps, total_prompt, total_completion, wall, hit_max=False,
                             stop_reason="token_budget", compactions=compactions)
                raise Escalation(
                    f"{self.name} exceeded token budget {b.spent}/{b.limit}",
                    context={"name": self.name, "reason": "token_budget",
                             "spent": b.spent, "limit": b.limit},
                )

            # ── P2 压缩:预算已裁决命运(escalate/partial 在上方处理),继续跑才压缩 ──
            if (self.compaction and self.compaction.enabled
                    and last_prompt_tokens > self.compaction.threshold_prompt_tokens):
                n_tool, n_asst = compact_messages(
                    messages, protected=2 + len(context_messages or []),
                    keep_last_n=self.compaction.keep_last_n)
                if n_tool + n_asst > 0:        # 每轮至多一次;幂等无收益不计数
                    compactions += 1
                    if self.on_compact:
                        self.on_compact({
                            "agent": self.name,
                            "threshold": self.compaction.threshold_prompt_tokens,
                            "last_prompt_tokens": last_prompt_tokens,
                            "stubbed_tool_msgs": n_tool,
                            "kept_last": self.compaction.keep_last_n,
                        })

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

    # partial 降级:一次强制收尾调用。上界 = 一次调用 + max_tokens;照常 charge(诚实计数)。
    def _forced_wrapup(self, messages, steps, total_prompt, total_completion,
                       start, compactions) -> AgentResult:
        messages.append({"role": "user", "content":
            "TOKEN_BUDGET_EXHAUSTED: 不再调用任何工具,基于已获取的信息立即输出最终结果。"})
        steps += 1  # 收尾调用顶替本轮迭代(≤ max_steps 恒成立)
        resp = self.client.chat(
            messages=messages, tools=None,     # 不给工具面,机械上杜绝收尾调工具
            model=self.model, temperature=self.temperature, max_tokens=self.max_tokens,
        )
        u = resp.usage or {}
        pt = u.get("prompt_tokens", 0) or 0
        ct = u.get("completion_tokens", 0) or 0
        total_prompt += pt
        total_completion += ct
        if self.budgets:
            for b in self.budgets:
                b.charge(pt, ct)
        # 收尾输出若带 tool_call 一律忽略只取 content;被忽略的 tool_call 无需回填
        messages.append({"role": "assistant", "content": resp.content})
        wall = time.perf_counter() - start
        self._record(steps, total_prompt, total_completion, wall, hit_max=False,
                     degraded=True, stop_reason="token_budget", compactions=compactions)
        return AgentResult(content=resp.content, history=messages, degraded=True,
                           usage={"prompt_tokens": total_prompt,
                                  "completion_tokens": total_completion})

    def _record(self, steps, prompt, completion, wall_s, *, hit_max,
                degraded=False, stop_reason="", compactions=0):
        if self.recorder is not None:
            self.recorder.record(AgentRecord(
                name=self.name, steps=steps, max_steps=self.max_steps,
                prompt_tokens=prompt, completion_tokens=completion,
                wall_s=wall_s, hit_max=hit_max,
                degraded=degraded, stop_reason=stop_reason, compactions=compactions))
