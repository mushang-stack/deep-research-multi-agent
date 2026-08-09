"""Verifier:复核 Findings 来源支撑的 agent(AgentLoop + web 工具,交叉印证)。"""
from core.agent_loop import AgentLoop
from ._webtools import build_web_registry
from .prompts import VERIFIER_PROMPT


def make_verifier(*, client, search_client, max_chars: int = 8000,
                  max_steps: int = 12) -> AgentLoop:
    return AgentLoop(
        client=client, system_prompt=VERIFIER_PROMPT,
        registry=build_web_registry(search_client, max_chars=max_chars),
        max_steps=max_steps, name="verifier",
    )
