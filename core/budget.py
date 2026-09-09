"""P1 预算护栏:线程安全 token 预算对象。
Lock 计数沿用 TelemetrySink 的线程安全模式(dispatch 的 ThreadPoolExecutor 并发 researcher
共享 run 池时保证计数正确)。"""
import threading
from dataclasses import dataclass, field


@dataclass
class Budget:
    limit: int   # prompt+completion 累计上限
    spent: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def charge(self, prompt_tokens: int | None, completion_tokens: int | None) -> None:
        with self._lock:
            self.spent += (prompt_tokens or 0) + (completion_tokens or 0)

    @property
    def exceeded(self) -> bool:
        return self.spent >= self.limit
