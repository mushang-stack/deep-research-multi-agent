from agents.system import build_system
from agents.observe import set_sink, clear_sink
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


def _write_cfg_guards(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(
        "models:\n"
        "  generator: {name: deepseek-chat, base_url: 'https://api.deepseek.com', temperature: 0.3, max_tokens: 4096}\n"
        "tools:\n"
        "  web_search: {endpoint: 'https://api.bochaai.com/v1/web-search', count: 8}\n"
        "  web_read: {max_chars: 8000}\n"
        "  max_tool_result_chars: 6000\n"
        "guards: {agent_max_steps: 12, research_max_rounds: 3, researcher_max_steps: 6, "
        "request_max_retries: 3, request_backoff_base: 1.5, "
        "run_token_budget: 400000, researcher_token_budget: 60000, "
        "researcher_on_exhaustion: partial}\n"
        "compaction: {enabled: true, threshold_prompt_tokens: 30000, keep_last_n: 4, strategy: stub}\n",
        encoding="utf-8",
    )
    return load_config(p)


class _CapturingResearcherLoop:
    captures = []                       # 每次构造 append 一份 kwargs(D2 共享性断言用)
    def __init__(self, **kw):
        _CapturingResearcherLoop.captured = kw
        _CapturingResearcherLoop.captures.append(kw)
    def run(self, sub_question):
        from core.agent_loop import AgentResult
        return AgentResult(content='{"findings": []}')


def _fake_chat_client():
    class _FakeClient(LLMClient):
        def chat(self, **kw):
            return LLMResponse(content="{}")
    return _FakeClient()


class _ScriptedClient(LLMClient):
    def __init__(self, responses):
        self._r = list(responses)
    def chat(self, **kw):
        return self._r.pop(0)


def _run_once_with_dispatch(cfg, monkeypatch):
    """跑一轮 dispatch_research(工厂是 _run_researcher 闭包内惰性构造,不跑不触发 capture)。
    返回 (researcher loop_kwargs, orchestrator loop)。模式同 test_build_system_injects_researcher_max_steps。"""
    monkeypatch.setattr("agents.system.make_researcher", _CapturingResearcherLoop)
    client = _ScriptedClient([
        LLMResponse(content="", tool_calls=[
            ToolCall(id="1", name="dispatch_research",
                     arguments='{"sub_questions": ["x"]}')]),
        LLMResponse(content="收尾"),
    ])
    loop, _ = build_system(cfg, client=client, search_client=object())
    loop.run("问题")
    return _CapturingResearcherLoop.captured["loop_kwargs"], loop


def test_build_system_default_cfg_all_guards_off(tmp_path, monkeypatch):
    """缺省 config(无新键)→ budgets=None / 截断=None / 压缩=None(逐字节一致回归保证)。"""
    lk, loop = _run_once_with_dispatch(_write_cfg(tmp_path), monkeypatch)
    assert lk["budgets"] is None and lk["on_exhaustion"] == "escalate"
    assert lk["max_tool_result_chars"] is None
    assert lk["compaction"] is None and lk["on_compact"] is None
    assert loop.budgets is None and loop.compaction is None       # orchestrator 同样全关
    assert loop.max_tool_result_chars is None


def test_build_system_wires_budgets_truncation_compaction(tmp_path, monkeypatch):
    lk, loop = _run_once_with_dispatch(_write_cfg_guards(tmp_path), monkeypatch)
    assert [b.limit for b in lk["budgets"]] == [60000, 400000]    # 自身预算 + run 池各一
    assert lk["on_exhaustion"] == "partial"
    assert lk["max_tool_result_chars"] == 6000
    assert lk["compaction"].enabled and lk["compaction"].threshold_prompt_tokens == 30000
    assert callable(lk["on_compact"])
    assert [b.limit for b in loop.budgets] == [400000]            # orchestrator 只挂 run 池
    assert loop.max_tool_result_chars is None                     # orchestrator 不截断(结构化 JSON)
    assert loop.compaction is None and loop.on_compact is None    # orchestrator 免压缩(同因:结构化数据面)


def test_on_compact_wired_to_emit_event(tmp_path, monkeypatch):
    lk, _ = _run_once_with_dispatch(_write_cfg_guards(tmp_path), monkeypatch)
    on_compact = lk["on_compact"]
    events = []
    class _Sink:
        def record(self, e): events.append(e)
    set_sink(_Sink())
    try:
        on_compact({"agent": "researcher", "threshold": 30000,
                    "last_prompt_tokens": 31000, "stubbed_tool_msgs": 2, "kept_last": 4})
    finally:
        clear_sink()
    assert events[0].kind == "compact"
    assert events[0].role == "researcher"
    assert events[0].payload["stubbed_tool_msgs"] == 2


def test_invalid_on_exhaustion_rejected(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(
        "models:\n"
        "  generator: {name: deepseek-chat, base_url: 'https://api.deepseek.com', temperature: 0.3, max_tokens: 4096}\n"
        "tools:\n"
        "  web_search: {endpoint: 'https://api.bochaai.com/v1/web-search', count: 8}\n"
        "  web_read: {max_chars: 8000}\n"
        "guards: {agent_max_steps: 12, research_max_rounds: 3, researcher_on_exhaustion: bogus}\n",
        encoding="utf-8",
    )
    import pytest
    with pytest.raises(ValueError, match="researcher_on_exhaustion"):
        build_system(load_config(p), client=_fake_chat_client(), search_client=object())


def test_researcher_own_budget_per_agent_run_pool_shared(tmp_path, monkeypatch):
    # D2:per-agent 预算独立(每个 researcher 各自的 Budget 对象),仅 run 池共享同一对象
    _CapturingResearcherLoop.captures = []   # 类属性跨测试累积,先重置(前面测试也触发构造)
    monkeypatch.setattr("agents.system.make_researcher", _CapturingResearcherLoop)
    client = _ScriptedClient([
        LLMResponse(content="", tool_calls=[
            ToolCall(id="1", name="dispatch_research",
                     arguments='{"sub_questions": ["x", "y"]}')]),
        LLMResponse(content="收尾"),
    ])
    loop, _ = build_system(_write_cfg_guards(tmp_path), client=client, search_client=object())
    loop.run("问题")
    caps = _CapturingResearcherLoop.captures
    assert len(caps) == 2
    b0, b1 = caps[0]["loop_kwargs"]["budgets"], caps[1]["loop_kwargs"]["budgets"]
    assert b0[0] is not b1[0]   # own 预算:两个独立对象
    assert b0[1] is b1[1]       # run 池:同一共享对象


def test_invalid_compaction_strategy_rejected(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(
        "models:\n"
        "  generator: {name: deepseek-chat, base_url: 'https://api.deepseek.com', temperature: 0.3, max_tokens: 4096}\n"
        "tools:\n"
        "  web_search: {endpoint: 'https://api.bochaai.com/v1/web-search', count: 8}\n"
        "  web_read: {max_chars: 8000}\n"
        "guards: {agent_max_steps: 12, research_max_rounds: 3}\n"
        "compaction: {enabled: true, threshold_prompt_tokens: 30000, keep_last_n: 4, strategy: summarize}\n",
        encoding="utf-8",
    )
    import pytest
    with pytest.raises(ValueError, match="strategy"):
        build_system(load_config(p), client=_fake_chat_client(), search_client=object())
