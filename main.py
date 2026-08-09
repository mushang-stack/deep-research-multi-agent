"""CLI 入口:python main.py "研究问题"。
组装系统 → 跑 Orchestrator → 从 holder 取 Report(机制级确定性)→ 渲染 markdown 打印。"""
import sys

from dotenv import load_dotenv

from core.config import load_config
from agents.system import build_system
from agents.observe import progress

load_dotenv()  # 加载 .env 到环境变量(真实冒烟需 DEEPSEEK/BOCHA key)


def render_markdown(report) -> str:
    lines = []
    for sec in report.sections:
        lines.append(f"## {sec.heading}\n")
        lines.append(sec.content)
        if sec.citations:
            lines.append("\n\n*引用: " + ", ".join(sec.citations) + "*")
        lines.append("\n")
    if report.sources:
        lines.append("## 来源\n")
        for i, s in enumerate(report.sources, 1):
            lines.append(f"{i}. {s}")
    return "\n".join(lines)


def main(argv=None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if not argv:
        print('用法: python main.py "研究问题"')
        return 1
    question = " ".join(argv)
    cfg = load_config()
    loop, get_report = build_system(cfg)
    progress(f"[start] 研究问题:{question}")
    loop.run(question)
    report = get_report()
    if report is None:
        progress("[done] ✗ Orchestrator 未成功产出 write_report(holder 为空)")
        print("未能生成报告(Orchestrator 未产出 write_report)。", file=sys.stderr)
        return 2
    progress(f"[done] ✓ 报告生成完成({len(report.sections)} 节)")
    print(render_markdown(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
