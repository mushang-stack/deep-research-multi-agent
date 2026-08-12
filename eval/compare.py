"""加载多份 scorecard → 算两两 Δ → 对比表 + comparison.json。纯函数 + CLI。
报告两组 Δ(若对应 system 在场):A−B = verifier 价值,A−C = 整套架构价值。"""
import argparse
import json
import sys
from pathlib import Path

_METRICS = [
    ("mean_grounding", "Grounding"),
    ("mean_hallucination", "幻觉率"),
    ("mean_citation", "引用准确率"),
    ("mean_coverage", "覆盖率"),
    ("success_rate", "成功率"),
]


def _delta(a: dict, b: dict) -> dict:
    out = {}
    for key, label in _METRICS:
        va, vb = a.get(key), b.get(key)
        if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
            v = round(va - vb, 3)
            out[label] = 0.0 if v == 0 else v  # 规范化 -0.0 → 0.0,避免渲染 "+-0.0"/"-0.0"
        else:
            out[label] = None
    return out


def compare(scorecards: dict) -> dict:
    """scorecards = {system名: scorecard}。返回 {systems, rows, deltas}。
    rows 每 metric 一行含各 system 值;deltas 含 A-B / A-C(缺系统则跳过)。诚实规则:反超 Δ 为负照实报。"""
    systems = list(scorecards)
    rows = []
    for key, label in _METRICS:
        row = {"key": key, "metric": label}
        for s in systems:
            row[s] = scorecards[s].get(key)
        rows.append(row)
    deltas = {}
    if "multi" in scorecards:
        if "no_verify" in scorecards:
            deltas["A-B (verifier)"] = _delta(scorecards["multi"], scorecards["no_verify"])
        if "baseline" in scorecards:
            deltas["A-C (architecture)"] = _delta(scorecards["multi"], scorecards["baseline"])
    return {"systems": systems, "rows": rows, "deltas": deltas}


def render_table(cmp: dict) -> str:
    systems = cmp["systems"]
    lines = ["=== 基线对比 ==="]
    lines.append("指标".ljust(12) + "".join(s.ljust(14) for s in systems))
    for row in cmp["rows"]:
        cells = []
        for s in systems:
            v = row.get(s)
            cells.append(f"{v:.3f}".ljust(14) if isinstance(v, (int, float)) else "N/A".ljust(14))
        lines.append(row["metric"].ljust(12) + "".join(cells))
    for name, d in cmp["deltas"].items():
        parts = []
        for k, v in d.items():
            if v is None:
                continue
            sign = "+" if v > 0 else ""
            parts.append(f"{k}: {sign}{v}")
        lines.append(f"{name} → " + ", ".join(parts))
    return "\n".join(lines)


def main(argv=None, *, results_dir=None) -> int:
    # Windows 控制台默认 GBK,强制 stdout/stderr UTF-8,避免中文/→ 触发 UnicodeEncodeError(同 run_eval)
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(prog="python -m eval.compare",
                                     description="对比多 system 的 scorecard(Δ + 表)")
    parser.add_argument("--systems", default="multi,no_verify,baseline",
                        help="逗号分隔的 system 名(默认全三档;缺失自动跳过)")
    parser.add_argument("--results-dir", default=None, help="scorecard 根目录(默认 eval/results)")
    args = parser.parse_args(argv)
    base = Path(args.results_dir or results_dir or Path(__file__).resolve().parent / "results")

    scorecards = {}
    for s in [x.strip() for x in args.systems.split(",") if x.strip()]:
        p = base / "scorecard.json" if s == "multi" else base / s / "scorecard.json"
        if not p.exists():
            print(f"[compare] 跳过缺失的 {s}:{p}", file=sys.stderr)
            continue
        try:
            scorecards[s] = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"[compare] {s} 的 scorecard JSON 解析失败({p}):{e}", file=sys.stderr)
            continue
    if not scorecards:
        print("无可用 scorecard。先跑 python -m eval.run_eval --system <multi|no_verify|baseline>。",
              file=sys.stderr)
        return 1

    cmp = compare(scorecards)
    if not cmp["deltas"]:
        print("[compare] 警告:无 multi system 或无可比对象,deltas 为空(数值表仍写入 comparison.json)。",
              file=sys.stderr)
    print(render_table(cmp))
    (base / "comparison.json").write_text(
        json.dumps(cmp, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
