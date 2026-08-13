"""UI 控制器:后台线程目标 + trace 录制/回放。纯逻辑可单测,不依赖 Streamlit。"""
import glob
import json
import os
import re

from agents.observe import emit, set_sink, clear_sink
from agents.system import build_system

_TRACE_DIR = os.path.join(os.path.dirname(__file__), "traces")


def run_research(question, cfg, event_sink, telemetry_sink, *, run_fn=None):
    """后台线程目标。返回 Report | None。
    run_fn=None → 生产 build_system(cfg, recorder=telemetry_sink)。
    run_fn(cfg) -> (loop, get_report) → 测试用 fake,不烧 API。"""
    set_sink(event_sink)
    try:
        emit("run_start", None, f"[start] 研究问题:{question}", question=question)
        if run_fn is None:
            loop, get_report = build_system(cfg, recorder=telemetry_sink)
        else:
            loop, get_report = run_fn(cfg)
        loop.run(question)
        report = get_report()
        emit("run_done", None,
             f"[done] {'✓ 报告生成' if report else '✗ 报告未生成'}",
             success=bool(report))
        return report
    except Exception as e:
        emit("error", None, f"[error] 运行失败:{e!r}", reason=repr(e))
        emit("run_done", None, "[done] ✗ 失败", success=False)
        return None
    finally:
        clear_sink()


def slugify(text, maxlen=30):
    s = re.sub(r"[^\w一-鿿]+", "-", text).strip("-")
    return s[:maxlen] or "trace"


def trace_path(question, traces_dir=None):
    d = traces_dir or _TRACE_DIR
    base = slugify(question)
    path = os.path.join(d, f"{base}.json")
    i = 1
    while os.path.exists(path):
        path = os.path.join(d, f"{base}-{i}.json")
        i += 1
    return path


def save_trace(path, question, events, report, telemetry, status):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    data = {
        "question": question,
        "events": events,
        "report": report.model_dump() if report is not None else None,
        "telemetry": telemetry,
        "status": status,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_trace(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def list_traces(traces_dir=None):
    d = traces_dir or _TRACE_DIR
    return sorted(glob.glob(os.path.join(d, "*.json")))
