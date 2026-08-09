import pytest
from core.config import Config, load_config, env


def test_load_config_reads_yaml(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "models:\n  generator:\n    name: deepseek-chat\n    temperature: 0.3\n",
        encoding="utf-8",
    )
    cfg = load_config(cfg_file)
    assert cfg["models"]["generator"]["name"] == "deepseek-chat"
    assert cfg["models"]["generator"]["temperature"] == 0.3


def test_config_get_returns_default():
    cfg = Config({"a": 1})
    assert cfg.get("a") == 1
    assert cfg.get("missing", "fallback") == "fallback"


def test_env_raises_when_missing(monkeypatch):
    monkeypatch.delenv("FAKE_KEY", raising=False)
    with pytest.raises(RuntimeError, match="FAKE_KEY"):
        env("FAKE_KEY")


def test_env_returns_value(monkeypatch):
    monkeypatch.setenv("FAKE_KEY", "sk-abc")
    assert env("FAKE_KEY") == "sk-abc"
