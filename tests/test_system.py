from agents.system import build_system
from core.config import load_config
from llm.base import LLMClient, LLMResponse


def _write_cfg(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(
        "models:\n"
        "  generator: {name: deepseek-chat, base_url: 'https://api.deepseek.com', temperature: 0.3, max_tokens: 4096}\n"
        "tools:\n"
        "  web_search: {endpoint: 'https://api.bochaai.com/v1/web-search', count: 8}\n"
        "  web_read: {max_chars: 8000}\n"
        "guards: {agent_max_steps: 12, research_max_rounds: 3, request_max_retries: 3, request_backoff_base: 1.5}\n",
        encoding="utf-8",
    )
    return load_config(p)


class _StubClient:
    def chat(self, **kw):
        raise AssertionError("build_system 不应调用 client.chat")


def test_build_system_wires_config_to_clients(monkeypatch, tmp_path):
    cfg = _write_cfg(tmp_path)
    captured = {}
    monkeypatch.setattr("agents.system.DeepSeekClient",
                        lambda **kw: captured.setdefault("ds", kw) or _StubClient())
    monkeypatch.setattr("agents.system.BochaSearchClient",
                        lambda **kw: captured.setdefault("bocha", kw))
    monkeypatch.setenv("BOCHA_API_KEY", "sk-b")

    loop, get_report = build_system(cfg)
    # M1 待办验收:config 值确实注入了 client 构造
    assert captured["ds"]["model"] == "deepseek-chat"
    assert captured["ds"]["temperature"] == 0.3
    assert captured["ds"]["max_tokens"] == 4096
    assert captured["bocha"]["endpoint"] == "https://api.bochaai.com/v1/web-search"
    assert captured["bocha"]["count"] == 8
    # guard 注入
    assert loop.max_steps == 12
    assert loop.name == "orchestrator"
    assert get_report() is None


def test_build_system_uses_injected_clients(tmp_path):
    cfg = _write_cfg(tmp_path)

    class FakeClient(LLMClient):
        def chat(self, **kw):
            return LLMResponse(content="{}")

    loop, _ = build_system(cfg, client=FakeClient(), search_client=object())
    assert isinstance(loop.client, FakeClient)  # 注入的 client 被直接采用
