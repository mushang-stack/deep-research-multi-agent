"""评估编排 + CLI(spec §4.4)。逐题:跑系统 → 抽 trace → 逐条裁判 → 算分 → scorecard。

零侵入:只消费 build_system 的产物(get_report() 的 Report、loop.run() 的 history)。
"""
import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml
from dotenv import load_dotenv

from agents.system import build_system
from core.config import load_config
from core.schemas import Report
from llm.glm_client import GLMClient

from .judge import judge_finding, judge_key_fact
from .metrics import aggregate, compute_question_metrics
from .trace import extract_findings

_EVAL_DIR = Path(__file__).resolve().parent


def report_to_text(report: Report) -> str:
    """把 Report 拼成裁判可读的纯文本(heading + content)。"""
    parts = []
    for sec in report.sections:
        parts.append(f"## {sec.heading}\n{sec.content}")
    return "\n\n".join(parts)


def run_one(question: str, cfg, *, client=None, search_client=None):
    """跑一次系统,返回 (report, history)。report 可能为 None。"""
    loop, get_report = build_system(cfg, client=client, search_client=search_client)
    result = loop.run(question)
    return get_report(), result.history


def evaluate_question(item: dict, cfg, *, judge_client,
                      client=None, search_client=None, run_one_fn=None,
                      judge_concurrency: int = 10):
    """单题评估 → scorecard。

    run_one_fn 可注入(测试用,跳过真实 agent 链);为 None 时用真实 run_one。
    judge_concurrency: judge_finding/judge_key_fact 的并发上限(GLM-5.2 = 10)。
    """
    question = item["question"]
    runner = run_one_fn or (lambda q: run_one(q, cfg, client=client,
                                              search_client=search_client))
    report, history = runner(question)

    if report is None:
        return {"id": item["id"], "question": question,
                **compute_question_metrics([], [], report_produced=False)}

    findings = extract_findings(history)
    text = report_to_text(report)
    # 并发裁判:GLM-5.2 并发上限内,pool.map 保序,单条异常仍冒泡到 main 单题兜底
    with ThreadPoolExecutor(max_workers=judge_concurrency) as pool:
        finding_verdicts = list(pool.map(
            lambda f: judge_finding(claim=f.get("claim", ""),
                                    excerpt=f.get("excerpt", ""),
                                    source_url=f.get("source_url", ""),
                                    client=judge_client),
            findings))
        key_fact_verdicts = list(pool.map(
            lambda kf: judge_key_fact(key_fact=kf, report_text=text,
                                      client=judge_client),
            item.get("key_facts", [])))
    return {"id": item["id"], "question": question,
            **compute_question_metrics(finding_verdicts, key_fact_verdicts,
                                       report_produced=True)}


def load_benchmark(benchmark_dir) -> list[dict]:
    """加载题库目录下所有 <id>.yaml(跳过 _ 前缀模板与非法题)。

    每题校验:{id:str, question:str, key_facts:list[str] 非空}。非法题跳过并告警,不中断。
    """
    out = []
    for p in sorted(Path(benchmark_dir).glob("*.yaml")):
        if p.name.startswith("_"):
            continue
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception:
            print(f"[eval] 跳过无法解析的题:{p.name}", file=sys.stderr)
            continue
        if not (isinstance(data, dict)
                and isinstance(data.get("id"), str) and data["id"]
                and isinstance(data.get("question"), str) and data["question"]
                and isinstance(data.get("key_facts"), list) and data["key_facts"]
                and all(isinstance(x, str) for x in data["key_facts"])):
            print(f"[eval] 跳过非法题(缺 id/question/key_facts):{p.name}", file=sys.stderr)
            continue
        out.append({"id": data["id"], "question": data["question"],
                    "key_facts": data["key_facts"]})
    return out


def _print_summary(summary: dict) -> None:
    g = summary["mean_grounding"]
    gstr = f"{g:.3f}" if g is not None else "N/A(无可用题)"
    flag = "✓ 过线" if summary["passed"] else "✗ 未过线"
    print(f"\n=== 质量 scorecard ===")
    print(f"题数 {summary['n_questions']}  成功 {summary['n_success']}  "
          f"成功率 {summary['success_rate']:.2%}")
    print(f"Grounding 均值 {gstr} (门槛 {summary['grounding_min']},"
          f"有效题 {summary['grounding_questions']})  {flag}")
    for k, label in (("mean_citation", "引用准确率"), ("mean_coverage", "覆盖率"),
                     ("mean_hallucination", "幻觉率")):
        v = summary[k]
        print(f"{label} 均值 " + (f"{v:.3f}" if v is not None else "N/A"))
    if summary["total_judge_parse_failures"]:
        print(f"⚠ 裁判输出不可解析累计 {summary['total_judge_parse_failures']} 次")


def main(argv=None, *, benchmark_dir=None, run_one_fn=None,
         judge_client=None, cfg=None, results_dir=None) -> int:
    load_dotenv()
    # Windows 控制台默认 GBK,强制 stdout/stderr UTF-8,避免 ✓/✗/⚠ 等 Unicode 字符 UnicodeEncodeError
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(
        prog="python -m eval.run_eval",
        description="质量评估:跑基准集 → GLM 逐条裁判 → scorecard + 上线门槛")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 题")
    parser.add_argument("--only", default=None, help="只跑指定 id 的题")
    parser.add_argument("--benchmark", default=None, help="题库目录(默认 eval/benchmark)")
    parser.add_argument("--results", default=None, help="结果输出目录(默认 eval/results)")
    args = parser.parse_args(argv)

    cfg = cfg or load_config()
    bench_dir = args.benchmark or benchmark_dir or (_EVAL_DIR / "benchmark")
    res_dir = Path(args.results or results_dir or (_EVAL_DIR / "results"))

    items = load_benchmark(bench_dir)
    if args.only:
        items = [it for it in items if it["id"] == args.only]
    if args.limit is not None:
        items = items[:args.limit]
    if not items:
        print("用法: python -m eval.run_eval [--limit N] [--only ID] "
              "[--benchmark DIR] [--results DIR]\n"
              "题库目录无可用题(检查 eval/benchmark/*.yaml,至少一道 {id,question,key_facts})。",
              file=sys.stderr)
        return 1

    judge_client = judge_client or GLMClient()
    judge_concurrency = cfg.get("models", {}).get("judge", {}).get("max_concurrency", 10)
    question_concurrency = cfg.get("eval", {}).get("question_concurrency", 1)
    res_dir.mkdir(parents=True, exist_ok=True)

    def _eval_one(it):
        try:
            sc = evaluate_question(it, cfg, judge_client=judge_client,
                                  run_one_fn=run_one_fn, judge_concurrency=judge_concurrency)
        except Exception as e:
            # 单题异常不中断整批(spec §6 精神):记为失败继续
            print(f"[eval] 题 {it['id']} 评估异常,记为失败继续:{e!r}", file=sys.stderr)
            sc = {"id": it["id"], "question": it["question"], "success": False,
                  "failed": "error", "error": repr(e),
                  "n_findings": 0, "n_key_facts": len(it.get("key_facts", [])),
                  "grounding": None, "citation": None, "coverage": None,
                  "hallucination": None, "grounding_available": False,
                  "judge_parse_failures": 0}
        (res_dir / f"{it['id']}.json").write_text(
            json.dumps(sc, ensure_ascii=False, indent=2), encoding="utf-8")
        return sc

    # 题间并发(question_concurrency=1 等价串行,向后兼容);pool.map 保序
    with ThreadPoolExecutor(max_workers=question_concurrency) as pool:
        scorecards = list(pool.map(_eval_one, items))

    summary = aggregate(scorecards, cfg["thresholds"]["grounding_min"])
    (res_dir / "scorecard.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _print_summary(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
