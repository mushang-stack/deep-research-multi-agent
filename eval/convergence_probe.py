"""收敛三臂定向复现实验(researcher-convergence spec §5)。
臂定义:A=基线(无护栏无修复)/ B=纯截断 6000(隔离截断放大效应)/ C=修复后(F1 预告 + F2 max_steps partial)。
每臂 × 每子问题 N 次单 researcher 真跑,汇总 0-findings 率 / findings 均值 / hit_max / degraded。
用法: python -m eval.convergence_probe [--n 10] [--arms A,B,C] [--out eval/results/convergence-probe.md]
"""
import argparse
import json
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from agents._webtools import build_web_registry
from agents.prompts import RESEARCHER_PROMPT, RESEARCHER_LAST_STEP_NUDGE
from agents.dispatch import parse_findings
from core.agent_loop import AgentLoop
from core.config import load_config, env
from core.robust import Escalation
from core.telemetry import TelemetrySink

SUB_QUESTIONS = [
    "2026 年主流的 AI Agent 评测基准有哪些?各自评测维度与局限?",
    "主流 Agent 开发框架(LangGraph、AutoGen、OpenAI Agents SDK)在架构设计上的核心差异?",
]

ARMS = ("A", "B", "C")


def build_arm_loop(arm: str, *, client, search_client, recorder=None) -> AgentLoop:
    """按臂构造 researcher(直接构造绕过工厂默认,保证臂间唯一差异 = 臂定义)。"""
    kwargs: dict = {"max_steps": 6, "recorder": recorder}
    if arm == "B":
        kwargs["max_tool_result_chars"] = 6000
    elif arm == "C":
        kwargs["last_step_nudge"] = RESEARCHER_LAST_STEP_NUDGE
        kwargs["on_exhaustion"] = "partial"
    return AgentLoop(client=client, system_prompt=RESEARCHER_PROMPT,
                     registry=build_web_registry(search_client),
                     name="researcher", **kwargs)


def run_one(arm: str, sub_question: str, *, client, search_client) -> dict:
    """跑一次单 researcher。Escalation(撞步数/预算)= 发散运行,n_findings 记 0;
    record 在异常分支也必须取——AgentLoop raise 前已记 sink(hit_max/steps),不取则发散统计恒空。
    API 抖动等基础设施失败返回 {"error": ...}:不算发散、聚合时剔除,否则会伪造 0-findings。"""
    sink = TelemetrySink()
    loop = build_arm_loop(arm, client=client, search_client=search_client, recorder=sink)
    try:
        result = loop.run(sub_question)
        rec = (sink.snapshot() or [None])[0]
        return {"arm": arm, "sub_question": sub_question,
                "n_findings": len(parse_findings(result.content)),
                "degraded": result.degraded, "escalated": False, "record": rec}
    except Escalation:
        rec = (sink.snapshot() or [None])[0]
        return {"arm": arm, "sub_question": sub_question, "n_findings": 0,
                "degraded": False, "escalated": True, "record": rec}
    except Exception as e:  # API 抖动等基础设施失败:保留错误,不伪造发散
        return {"arm": arm, "sub_question": sub_question, "error": repr(e)}


def aggregate(runs: list) -> dict:
    errors = sum(1 for r in runs if "error" in r)
    valid = [r for r in runs if "error" not in r]
    n = len(valid)
    recs = [r["record"] for r in valid if r["record"]]
    if n == 0:
        return {"n": 0, "errors": errors, "zero_findings_rate": 0.0, "mean_findings": 0.0,
                "hit_max": 0, "escalated": 0, "degraded": 0, "mean_steps": 0.0}
    return {
        "n": n,
        "errors": errors,
        "zero_findings_rate": sum(1 for r in valid if r["n_findings"] == 0) / n,
        "mean_findings": sum(r["n_findings"] for r in valid) / n,
        "hit_max": sum(1 for x in recs if x["hit_max"]),
        "escalated": sum(1 for r in valid if r["escalated"]),
        "degraded": sum(1 for r in valid if r["degraded"]),
        "mean_steps": sum(x["steps"] for x in recs) / len(recs) if recs else 0.0,
    }


def render_markdown(by_arm: dict, *, n: int) -> str:
    lines = [
        "# Researcher 收敛三臂定向复现实验",
        "",
        f"- 每臂 × {len(SUB_QUESTIONS)} 子问题 × {n} 次,温度 0.3(随机复发需统计样本)",
        "- 臂:A=基线 / B=纯截断 6000 / C=修复后(F1 预告 + F2 max_steps partial)",
        "",
        "| 臂 | n | 0-findings 率 | findings 均值 | hit_max | escalated | degraded | mean_steps | errors |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for arm in ARMS:
        d = by_arm.get(arm)
        if not d:
            continue
        lines.append(f"| {arm} | {d['n']} | {d['zero_findings_rate']:.0%} | {d['mean_findings']:.1f} "
                     f"| {d['hit_max']} | {d['escalated']} | {d['degraded']} | {d['mean_steps']:.1f} "
                     f"| {d['errors']} |")
    lines += ["", "判读:C 臂 0-findings 率应为 0 或明显低于 A(方向性证据;每臂 n=20 不做显著性检验);"
              "B−A 差值 = 截断放大效应(如实记录方向)。errors=API 抖动等基础设施失败,已从比率分母剔除。",
              "耗尽读法:A/B 臂看 hit_max;C 臂救援不记 hit_max,看 degraded(其耗尽被收尾救回)。"]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    load_dotenv()
    for _stream in (sys.stdout, sys.stderr):   # Windows GBK 坑(仓库惯例)
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(prog="python -m eval.convergence_probe")
    parser.add_argument("--n", type=int, default=10, help="每臂每子问题跑几次(默认 10)")
    parser.add_argument("--arms", default="A,B,C", help="要跑的臂(逗号分隔)")
    parser.add_argument("--out", default="eval/results/convergence-probe.md")
    args = parser.parse_args(argv)
    arms = [a.strip().upper() for a in args.arms.split(",") if a.strip()]
    bad = [a for a in arms if a not in ARMS]
    if bad:   # 在 env()/client 构造之前拦下,避免无效参数先烧配置
        parser.error(f"未知臂 {bad}(允许 {','.join(ARMS)})")

    from llm.deepseek_client import DeepSeekClient
    from tools.web_search import BochaSearchClient
    cfg = load_config()
    gen = cfg["models"]["generator"]
    client = DeepSeekClient(base_url=gen["base_url"], model=gen["name"],
                            temperature=gen["temperature"], max_tokens=gen["max_tokens"])
    ws = cfg["tools"]["web_search"]
    search_client = BochaSearchClient(api_key=env("BOCHA_API_KEY"),
                                      endpoint=ws["endpoint"], count=ws["count"])

    pricing = (cfg.get("pricing") or {}).get("deepseek") or {"input_per_1m": 0.14, "output_per_1m": 0.28}
    per_run = 25000 * (0.9 * pricing["input_per_1m"] + 0.1 * pricing["output_per_1m"]) / 1e6
    total = len(arms) * len(SUB_QUESTIONS) * args.n
    print(f"[probe] {total} 次 researcher 真跑(每次 ~25k tokens,估 ~${per_run * total:.2f})", flush=True)
    t0 = time.perf_counter()
    runs = []
    out = Path(args.out)
    jsonl = out.with_suffix(".runs.jsonl")
    with jsonl.open("a", encoding="utf-8") as jf:   # 逐运行落盘:中途崩溃不丢已花钱的数据
        for arm in arms:
            for sq in SUB_QUESTIONS:
                for i in range(args.n):
                    r = run_one(arm, sq, client=client, search_client=search_client)
                    runs.append(r)
                    jf.write(json.dumps(r, ensure_ascii=False) + "\n")
                    jf.flush()
                    if "error" in r:   # 基础设施失败:显示但不中断,聚合时剔除
                        status = f"error:{r['error'][:40]}"
                    else:
                        status = "Escalated" if r["escalated"] else f"{r['n_findings']} findings"
                        if r["degraded"]:
                            status += "(degraded)"
                    print(f"[probe] {arm} · {sq[:18]}… #{i + 1}/{args.n} → {status}", flush=True)
    by_arm = {arm: aggregate([r for r in runs if r["arm"] == arm]) for arm in arms}
    md = render_markdown(by_arm, n=args.n)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(md)
    print(f"[probe] 完成,耗时 {time.perf_counter() - t0:.0f}s → {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
