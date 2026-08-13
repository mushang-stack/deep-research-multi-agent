"""类型化事件总线(M4 UI 实时编排可视化用)。
- Event:单条事件(kind/role/text/payload/ts)。
- EventSink:线程安全收集器(同 TelemetrySink 风格);record 时打 ts(monotonic 相对 t0)。
- events_to_json/from_json:序列化往返(trace 录制/回放)。

无 sink 注入时,agents 链路行为完全不变(emit 退化为 observe.progress)。"""
import json
import threading
import time
from dataclasses import dataclass, asdict


@dataclass
class Event:
    kind: str
    role: str | None
    text: str
    payload: dict
    ts: float = 0.0


class EventSink:
    def __init__(self):
        self._lock = threading.Lock()
        self._records: list[Event] = []
        self._t0 = time.monotonic()

    def record(self, event: Event) -> None:
        with self._lock:
            event.ts = time.monotonic() - self._t0
            self._records.append(event)

    def snapshot(self) -> list[dict]:
        with self._lock:
            return [asdict(e) for e in self._records]


def events_to_json(events: list[dict]) -> str:
    return json.dumps(events, ensure_ascii=False)


def events_from_json(s: str) -> list[dict]:
    return json.loads(s)
