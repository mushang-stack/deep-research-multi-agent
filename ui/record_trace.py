"""无头录制 trace:python -m ui.record_trace "研究问题"
跑真实 build_system,把事件+报告+遥测存成 trace JSON 供 UI 回放。DoD / 批量录素材用。"""
import sys

from dotenv import load_dotenv

from core.config import load_config
from core.events import EventSink
from core.telemetry import TelemetrySink
from ui.controller import run_research, save_trace, trace_path


def main(argv=None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if not argv:
        print('用法: python -m ui.record_trace "研究问题"', file=sys.stderr)
        return 1
    load_dotenv()
    question = " ".join(argv)
    cfg = load_config()
    sink = EventSink()
    tele = TelemetrySink()
    report = run_research(question, cfg, sink, tele)
    path = trace_path(question)
    save_trace(path, question, sink.snapshot(), report,
               tele.aggregate_by_name(), "success" if report else "failed")
    print(f"[record_trace] trace → {path}  status={'success' if report else 'failed'}",
          file=sys.stderr)
    return 0 if report else 2


if __name__ == "__main__":
    sys.exit(main())
