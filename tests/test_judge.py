from llm.base import LLMResponse

from eval.judge import judge_finding, judge_key_fact


class FakeGLMClient:
    """脚本化 LLMResponse 队列,模拟 GLM 裁判(可故意返回坏 JSON)。"""
    def __init__(self, responses):
        self._r = list(responses)

    def chat(self, **kw):
        return self._r.pop(0)


# ---------- judge_finding ----------

def test_judge_finding_parses_clean_json():
    client = FakeGLMClient([LLMResponse(content='{"support":"supported",'
                                          '"source_real":true,"reason":"摘录支撑"}')])
    v = judge_finding(claim="c", excerpt="e", source_url="https://x", client=client)
    assert v["support"] == "supported"
    assert v["source_real"] is True
    assert v.get("parse_failed") is False


def test_judge_finding_strips_markdown_fence():
    client = FakeGLMClient([LLMResponse(
        content='```json\n{"support":"partial","source_real":false,"reason":""}\n```')])
    v = judge_finding(claim="c", excerpt="e", source_url="https://x", client=client)
    assert v["support"] == "partial"
    assert v["source_real"] is False


def test_judge_finding_extracts_json_from_prose():
    # M2 冒烟踩过的坑:模型在 JSON 前输出散文
    client = FakeGLMClient([LLMResponse(
        content='好的,我来判定。该摘录明确支撑论断。\n\n'
                '{"support":"supported","source_real":true,"reason":"支撑"}')])
    v = judge_finding(claim="c", excerpt="e", source_url="https://x", client=client)
    assert v["support"] == "supported" and v["source_real"] is True


def test_judge_finding_parse_failure_counts_partial():
    # 连续不可解析 → 计 partial + parse_failed(parse_failed 是裁判技术失败,
    # 非 finding 真无支撑;取中性 partial=0.5 而非归零。source_real 仍兜底 false 防幻觉)
    bad = LLMResponse(content="完全不是 JSON 的散文")
    client = FakeGLMClient([bad, bad, bad])
    v = judge_finding(claim="c", excerpt="e", source_url="https://x", client=client)
    assert v["support"] == "partial"
    assert v["source_real"] is False
    assert v["parse_failed"] is True


def test_finding_system_prompt_enforces_strict_json():
    """parse_failed 是 grounding 损失源,prompt 须强化 JSON:禁思考过程、reason 纯文本。"""
    from eval.judge import _FINDING_SYSTEM
    assert "思考" in _FINDING_SYSTEM          # 禁止思考过程
    assert "纯文本" in _FINDING_SYSTEM        # reason 限纯文本(防破坏 JSON)


def test_judge_finding_parse_failure_recovers_on_retry():
    bad = LLMResponse(content="散文")
    good = LLMResponse(content='{"support":"supported","source_real":true,"reason":""}')
    client = FakeGLMClient([bad, good])  # 第一次坏,第二次好
    v = judge_finding(claim="c", excerpt="e", source_url="https://x", client=client)
    assert v["support"] == "supported"
    assert v.get("parse_failed") is False


# ---------- judge_key_fact ----------

def test_judge_key_fact_parses_clean_json():
    client = FakeGLMClient([LLMResponse(content='{"covered":true,"reason":"已陈述"}')])
    v = judge_key_fact(key_fact="某关键事实", report_text="报告含某关键事实。",
                       client=client)
    assert v["covered"] is True
    assert v.get("parse_failed") is False


def test_judge_key_fact_strips_fence():
    client = FakeGLMClient([LLMResponse(
        content='```json\n{"covered":false,"reason":"未提及"}\n```')])
    v = judge_key_fact(key_fact="某关键事实", report_text="报告。", client=client)
    assert v["covered"] is False


def test_judge_key_fact_parse_failure_counts_not_covered():
    bad = LLMResponse(content="散文")
    client = FakeGLMClient([bad, bad, bad])
    v = judge_key_fact(key_fact="某关键事实", report_text="报告。", client=client)
    assert v["covered"] is False
    assert v["parse_failed"] is True
