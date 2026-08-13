"""Writer:综合带引用报告的 agent。无工具(registry=None),只能用传入数据——防幻觉硬保证。"""
from core.agent_loop import AgentLoop
from .prompts import WRITER_PROMPT


def make_writer(*, client, max_steps: int = 12, recorder=None) -> AgentLoop:
    return AgentLoop(
        client=client, system_prompt=WRITER_PROMPT,
        registry=None,  # 无工具
        max_steps=max_steps, name="writer", recorder=recorder,
    )
