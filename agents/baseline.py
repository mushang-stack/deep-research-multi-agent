"""单 agent 基线(M3 后续 · 模式 C):一个决策 agent + 复用 Writer 子例程。
无 verifier、无 dispatch fan-out、无 orchestrator 拆解。接口与 build_system 一致:(loop, get_report)。
write_report 内部调 make_writer(复用、无工具)把 agent 编译的 findings 综合成 Report,存 holder,
并返回 {"findings":[...]} 进 history 供 eval/trace 抽取 → 三档 trace 通用。"""
import json

from llm.base import LLMClient
from core.agent_loop import AgentLoop
from core.config import Config
from .dispatch import parse_findings, parse_report
from .observe import progress
from .prompts import BASELINE_PROMPT
from .writer import make_writer
from ._webtools import build_web_registry


def make_baseline(*, client, search_client, max_chars: int = 8000,
                  writer_max_steps: int = 12, max_steps: int = 16):
    """返回 (baseline_loop, get_report)。

    单决策 agent 拥有 web_search / web_read / write_report。write_report(outline, findings):
    校验 findings → 调 make_writer 成文 → Report 入 holder → 返回 {"findings":[...], "n_sections":N}。
    """
    holder: dict = {}
    reg = build_web_registry(search_client, max_chars=max_chars)

    def _write_report(outline: str, findings: list) -> dict:
        if not findings:
            progress("[baseline] ✗ write_report 被拒绝:无 findings")
            return {"error": "无 findings,无法生成报告。不要用空 findings 调用 write_report。"}
        # round-trip 经 parse_findings:复用 dispatch 层容错(跳过畸形项),与多 agent 口径一致
        parsed = parse_findings(json.dumps({"findings": findings}, ensure_ascii=False))
        if not parsed:
            progress("[baseline] ✗ write_report:findings 全部不合法")
            return {"error": "findings 全部不合法,请重新编译后重试。"}
        for i, f in enumerate(parsed, 1):
            f.id = f"f{i}"
        msg = json.dumps({"outline": outline,
                          "verified_findings": [f.model_dump() for f in parsed]},
                         ensure_ascii=False)
        result = make_writer(client=client, max_steps=writer_max_steps).run(msg)
        report = parse_report(result.content)
        if report is None:
            snippet = (result.content or "")[:200]
            progress(f"[baseline] ✗ Writer 输出无法解析为 Report。前 200 字:{snippet!r}")
            return {"error": "writer 产出不可解析,请重试或基于现有 findings 重写"}
        progress(f"[baseline] ✓ 报告已生成({len(report.sections)} 节)")
        holder["report"] = report
        return {"findings": [f.model_dump() for f in parsed],
                "n_sections": len(report.sections)}

    reg.register(
        "write_report", _write_report,
        description="基于你编译的 findings 综合带引用报告(系统后台撰写者成文)。调用后用一句话收尾,不再调任何工具。",
        parameters={"type": "object",
                    "properties": {"outline": {"type": "string"},
                                   "findings": {"type": "array", "items": {"type": "object"}}},
                    "required": ["outline", "findings"]},
    )

    loop = AgentLoop(client=client, system_prompt=BASELINE_PROMPT,
                     registry=reg, max_steps=max_steps, name="baseline")

    def get_report():
        return holder.get("report")

    return loop, get_report


def build_baseline_system(config: Config, *, client: LLMClient | None = None, search_client=None):
    """从 config 组装单 agent 基线,返回 (loop, get_report)。签名与 build_system 对齐。"""
    from llm.deepseek_client import DeepSeekClient
    from tools.web_search import BochaSearchClient
    from core.config import env

    gen = config["models"]["generator"]
    client = client or DeepSeekClient(
        base_url=gen["base_url"], model=gen["name"],
        temperature=gen["temperature"], max_tokens=gen["max_tokens"])
    ws = config["tools"]["web_search"]
    search_client = search_client or BochaSearchClient(
        api_key=env("BOCHA_API_KEY"), endpoint=ws["endpoint"], count=ws["count"])
    max_chars = config["tools"]["web_read"]["max_chars"]
    agent_max = config["guards"]["agent_max_steps"]
    baseline_max = config["guards"].get("baseline_max_steps", agent_max)
    return make_baseline(client=client, search_client=search_client,
                         max_chars=max_chars, writer_max_steps=agent_max,
                         max_steps=baseline_max)
