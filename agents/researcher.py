"""Researcher:单子问题检索 agent(AgentLoop + web 工具)。"""
from core.agent_loop import AgentLoop
from ._webtools import build_web_registry
from .prompts import RESEARCHER_PROMPT, RESEARCHER_LAST_STEP_NUDGE


def make_researcher(*, client, search_client, max_chars: int = 8000,
                    max_steps: int = 12, recorder=None,
                    loop_kwargs: dict | None = None) -> AgentLoop:
    kwargs = {"last_step_nudge": RESEARCHER_LAST_STEP_NUDGE,   # F1 收敛修复默认开(loop_kwargs 可覆盖)
              **(loop_kwargs or {})}
    return AgentLoop(
        client=client, system_prompt=RESEARCHER_PROMPT,
        registry=build_web_registry(search_client, max_chars=max_chars),
        max_steps=max_steps, name="researcher", recorder=recorder,
        **kwargs,
    )
