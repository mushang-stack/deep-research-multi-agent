"""Researcher:单子问题检索 agent(AgentLoop + web 工具)。"""
from core.agent_loop import AgentLoop
from ._webtools import build_web_registry
from .prompts import RESEARCHER_PROMPT


def make_researcher(*, client, search_client, max_chars: int = 8000,
                    max_steps: int = 12) -> AgentLoop:
    return AgentLoop(
        client=client, system_prompt=RESEARCHER_PROMPT,
        registry=build_web_registry(search_client, max_chars=max_chars),
        max_steps=max_steps, name="researcher",
    )
