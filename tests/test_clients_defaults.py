import pytest

from llm.deepseek_client import DeepSeekClient
from llm.glm_client import GLMClient


def test_deepseek_defaults(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds")
    c = DeepSeekClient()
    assert c.model == "deepseek-chat"
    assert c.temperature == 0.3
    assert c.max_tokens == 4096


def test_deepseek_requires_env(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        DeepSeekClient()


def test_glm_defaults(monkeypatch):
    monkeypatch.setenv("GLM_API_KEY", "sk-glm")
    c = GLMClient()
    assert c.model == "glm-5.2"          # 智谱最新 GLM-5.2,仅评估用
    assert c.temperature == 0.0
    assert c.max_tokens == 2048


def test_glm_requires_env(monkeypatch):
    monkeypatch.delenv("GLM_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GLM_API_KEY"):
        GLMClient()


def test_glm_model_override(monkeypatch):
    monkeypatch.setenv("GLM_API_KEY", "sk-glm")
    c = GLMClient(model="glm-4")          # 可按需覆盖
    assert c.model == "glm-4"
