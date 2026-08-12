"""系统组装工厂:从 config 构造四个 agent 并接好 Orchestrator 的派发工具。
config wiring:生产链路一律从 config 注入(client 硬编码默认仅作 fallback)→ 零侵入,闭环 M1 待办。"""
from llm.base import LLMClient
from llm.deepseek_client import DeepSeekClient
from tools.web_search import BochaSearchClient
from core.config import Config, env

from .researcher import make_researcher
from .verifier import make_verifier
from .writer import make_writer
from .orchestrator import make_orchestrator
from .observe import progress


def build_system(config: Config, *, client: LLMClient | None = None,
                 search_client=None, verify: bool = True):
    """组装整个系统,返回 (orchestrator_loop, get_report)。

    client / search_client 可注入(测试用 fake);为 None 时按 config + env 构造真实 client。
    """
    gen = config["models"]["generator"]
    client = client or DeepSeekClient(
        base_url=gen["base_url"], model=gen["name"],
        temperature=gen["temperature"], max_tokens=gen["max_tokens"],
    )

    ws = config["tools"]["web_search"]
    search_client = search_client or BochaSearchClient(
        api_key=env("BOCHA_API_KEY"), endpoint=ws["endpoint"], count=ws["count"],
    )

    max_chars = config["tools"]["web_read"]["max_chars"]
    max_steps = config["guards"]["agent_max_steps"]
    researcher_max_steps = config["guards"].get("researcher_max_steps", max_steps)
    research_max_rounds = config["guards"]["research_max_rounds"]

    def _run_researcher(sub_question):
        progress(f"  [researcher] 检索子问题:{sub_question}")
        result = make_researcher(client=client, search_client=search_client,
                                 max_chars=max_chars, max_steps=researcher_max_steps).run(sub_question)
        content = result.content or ""
        progress(f"  [researcher] 完成 → content {len(content)} 字,前 120 字:{content[:120]!r}")
        return result

    def _run_verifier(findings_json):
        progress("  [verifier] 复核 findings 来源支撑")
        return make_verifier(client=client, search_client=search_client,
                             max_chars=max_chars, max_steps=max_steps).run(findings_json)

    def _run_writer(user_message):
        progress("  [writer] 综合带引用报告(无工具,仅用传入 findings)")
        return make_writer(client=client, max_steps=max_steps).run(user_message)

    return make_orchestrator(
        client=client,
        run_researcher=_run_researcher, run_verifier=_run_verifier, run_writer=_run_writer,
        max_steps=max_steps, research_max_rounds=research_max_rounds, verify=verify,
    )
