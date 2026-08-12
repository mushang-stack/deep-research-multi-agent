from agents.system import build_system
from core.config import load_config
from llm.base import LLMClient, LLMResponse, ToolCall


def _write_cfg(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(
        "models:\n"
        "  generator: {name: deepseek-chat, base_url: 'https://api.deepseek.com', temperature: 0.3, max_tokens: 4096}\n"
        "tools:\n"
        "  web_search: {endpoint: 'https://api.bochaai.com/v1/web-search', count: 8}\n"
        "  web_read: {max_chars: 8000}\n"
        "guards: {agent_max_steps: 12, research_max_rounds: 3, researcher_max_steps: 6, request_max_retries: 3, request_backoff_base: 1.5}\n",
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


def test_build_system_injects_researcher_max_steps(tmp_path, monkeypatch):
    """researcher 用专用步数(默认 6),orchestrator 仍用 agent_max_steps(12)。"""
    cfg = _write_cfg(tmp_path)
    captured = {}

    class _FakeResearcherLoop:
        def __init__(self, **kw):
            captured.update(kw)
        def run(self, sub_question):
            from core.agent_loop import AgentResult
            return AgentResult(content='{"findings": []}')

    monkeypatch.setattr("agents.system.make_researcher", _FakeResearcherLoop)

    class _FakeClient(LLMClient):
        def __init__(self, responses):
            self._r = list(responses)
        def chat(self, **kw):
            return self._r.pop(0)

    client = _FakeClient([
        LLMResponse(content="", tool_calls=[
            ToolCall(id="1", name="dispatch_research",
                     arguments='{"sub_questions": ["x"]}')]),
        LLMResponse(content="收尾"),  # 第二轮无 tool_call → orchestrator 收敛
    ])
    loop, _ = build_system(cfg, client=client, search_client=object())
    loop.run("问题")

    assert captured["max_steps"] == 6        # researcher 注入了专用步数
    assert loop.max_steps == 12              # orchestrator 仍用 agent_max_steps


class _FakeClientForVerify(LLMClient):
    def chat(self, **kw):
        raise AssertionError("build 阶段不应调 chat")


class _FakeSearch:
    def search(self, query):
        return []


def test_build_system_verify_false_strips_verify_tool(tmp_path):
    cfg = _write_cfg(tmp_path)
    loop, _ = build_system(cfg, client=_FakeClientForVerify(), search_client=_FakeSearch(), verify=False)
    assert "verify_findings" not in set(loop.registry.names())


def test_build_system_verify_true_default_keeps_verify_tool(tmp_path):
    cfg = _write_cfg(tmp_path)
    loop, _ = build_system(cfg, client=_FakeClientForVerify(), search_client=_FakeSearch())
    assert "verify_findings" in set(loop.registry.names())
