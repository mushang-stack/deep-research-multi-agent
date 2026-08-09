"""共享 pytest fixtures。各 Task 按需在此追加。"""
import pytest


@pytest.fixture(autouse=True)
def _quiet_progress(monkeypatch):
    """默认关闭 agents.observe.progress 的 stderr 输出,保持单测输出干净。
    真实冒烟(python main.py)不受影响。"""
    monkeypatch.setenv("RESEARCH_QUIET", "1")
