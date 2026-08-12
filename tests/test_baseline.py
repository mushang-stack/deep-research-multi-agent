import json

from llm.base import LLMClient, LLMResponse, ToolCall
from core.schemas import SearchResult
from agents.baseline import make_baseline
from agents.prompts import BASELINE_PROMPT
from eval.trace import extract_findings


class FakeClient(LLMClient):
    def __init__(self, responses):
        self._r = list(responses)

    def chat(self, **kw):
        return self._r.pop(0)


class _FakeSearch:
    def search(self, query):
        return [SearchResult(title="T", url="https://x", snippet="s")]


_REPORT_JSON = '{"sections":[{"heading":"H","content":"C","citations":["f1"]}],"sources":["https://x"]}'
_FINDINGS = [{"id": "f1", "claim": "投机解码加速推理", "source_url": "https://x",
              "source_title": "T", "excerpt": "摘录原文", "confidence": 0.9}]


def test_baseline_wiring_no_verifier():
    loop, get_report = make_baseline(client=FakeClient([]), search_client=_FakeSearch())
    assert loop.name == "baseline"
    assert loop.system_prompt == BASELINE_PROMPT
    assert set(loop.registry.names()) == {"web_search", "web_read", "write_report"}
    assert "verify_findings" not in loop.registry.names()  # 基线无 verifier
    assert get_report() is None


def test_write_report_stores_report_and_returns_findings():
    client = FakeClient([LLMResponse(content=_REPORT_JSON)])  # writer 的唯一一次响应
    loop, get_report = make_baseline(client=client, search_client=_FakeSearch())
    out = loop.registry.execute("write_report", {"outline": "大纲", "findings": _FINDINGS})
    assert "findings" in out
    assert out["findings"][0]["claim"] == "投机解码加速推理"
    report = get_report()
    assert report is not None
    assert report.sections[0].heading == "H"


def test_write_report_empty_findings_refused():
    client = FakeClient([])  # writer 不应被调用
    loop, get_report = make_baseline(client=client, search_client=_FakeSearch())
    out = loop.registry.execute("write_report", {"outline": "x", "findings": []})
    assert "error" in out
    assert get_report() is None


def test_write_report_unparseable_writer_not_stored():
    client = FakeClient([LLMResponse(content="not json")])
    loop, get_report = make_baseline(client=client, search_client=_FakeSearch())
    out = loop.registry.execute("write_report", {"outline": "x", "findings": _FINDINGS})
    assert "error" in out
    assert get_report() is None


def test_findings_extractable_via_same_trace():
    # write_report 返回 {"findings":[...]} → 成为 history 里 role==tool 消息 → 同一 trace 抽得到
    client = FakeClient([LLMResponse(content=_REPORT_JSON)])
    loop, _ = make_baseline(client=client, search_client=_FakeSearch())
    out = loop.registry.execute("write_report", {"outline": "大纲", "findings": _FINDINGS})
    simulated_history = [{"role": "tool", "content": json.dumps(out, ensure_ascii=False)}]
    findings = extract_findings(simulated_history)
    assert len(findings) == 1
    assert findings[0]["claim"] == "投机解码加速推理"


def test_write_report_partial_bad_findings_passes_good_ones():
    partial = [
        {"id": "f1", "claim": "好的", "source_url": "https://x", "source_title": "T",
         "excerpt": "摘录", "confidence": 0.9},
        {"claim": "缺字段"},  # 缺 id/source_url,Finding 校验失败 → 跳过
    ]
    client = FakeClient([LLMResponse(content=_REPORT_JSON)])
    loop, get_report = make_baseline(client=client, search_client=_FakeSearch())
    out = loop.registry.execute("write_report", {"outline": "大纲", "findings": partial})
    assert "findings" in out
    assert len(out["findings"]) == 1  # 只保留那条合法的
    assert get_report() is not None


def test_baseline_loop_runs_and_produces_report_with_traceable_findings():
    # 完整 loop:baseline 直接调 write_report(跳过搜索)→ 内部 writer 成文 → 收尾
    client = FakeClient([
        LLMResponse(content="", tool_calls=[ToolCall(
            id="1", name="write_report",
            arguments=json.dumps({"outline": "大纲", "findings": _FINDINGS}))]),
        LLMResponse(content=_REPORT_JSON),   # writer 的响应
        LLMResponse(content="报告已生成"),    # baseline 收尾
    ])
    loop, get_report = make_baseline(client=client, search_client=_FakeSearch(), max_steps=4)
    result = loop.run("研究 X")
    assert get_report() is not None
    findings = extract_findings(result.history)
    assert len(findings) == 1
