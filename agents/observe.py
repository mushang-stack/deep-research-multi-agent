"""轻量进度日志:打印到 stderr(与 stdout 的最终报告分流,真实冒烟时终端实时可见编排过程)。
pytest 默认捕获 stderr,单测输出不受影响;设 RESEARCH_QUIET=1 可关闭(测试用,见 conftest)。"""
import os
import sys


def progress(msg: str) -> None:
    """把一行进度打到 stderr。RESEARCH_QUIET=1 时静默。运行时读环境变量(非导入时),
    以便测试 fixture 能动态开关。"""
    if os.environ.get("RESEARCH_QUIET") == "1":
        return
    print(msg, file=sys.stderr, flush=True)


from core.events import Event

_active_sink = None


def set_sink(sink):
    global _active_sink
    _active_sink = sink


def clear_sink():
    global _active_sink
    _active_sink = None


def get_sink():
    return _active_sink


def emit(kind, role, text, **payload):
    """类型化事件:① progress(text) 打 stderr(RESEARCH_QUIET 静默);
    ② 若设了 active sink,构造 Event 推入。无 sink 时退化为纯日志(不影响 CLI/eval 链路)。"""
    progress(text)
    sink = get_sink()
    if sink is not None:
        sink.record(Event(kind=kind, role=role, text=text, payload=payload))
