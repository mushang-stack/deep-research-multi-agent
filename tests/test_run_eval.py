from core.schemas import Report, ReportSection
from llm.base import LLMResponse

from eval.run_eval import evaluate_question, report_to_text


class FakeGLMClient:
    def __init__(self, responses):
        self._r = list(responses)

    def chat(self, **kw):
        return self._r.pop(0)


def _canned_history_with_findings():
    return [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "1", "type": "function",
             "function": {"name": "dispatch_research",
                          "arguments": '{"sub_questions":["q1"]}'}}]},
        {"role": "tool", "tool_call_id": "1",
         "content": '{"findings":[{"id":"f1","claim":"投机解码加速推理",'
                    '"source_url":"https://x","excerpt":"摘录"}]}'},
    ]


def _canned_report():
    return Report(sections=[ReportSection(heading="结论", content="投机解码加速推理。")])


def test_report_to_text_joins_sections():
    txt = report_to_text(_canned_report())
    assert "结论" in txt and "投机解码加速推理" in txt


def test_evaluate_question_success_path():
    # 1 条 finding → 1 次 finding 裁判;1 个 key_fact → 1 次 key_fact 裁判
    judge = FakeGLMClient([
        LLMResponse(content='{"support":"supported","source_real":true,"reason":""}'),
        LLMResponse(content='{"covered":true,"reason":""}'),
    ])
    item = {"id": "q1", "question": "Q", "key_facts": ["投机解码加速推理"]}
    sc = evaluate_question(item, cfg=None, judge_client=judge,
                           run_one_fn=lambda q: (_canned_report(),
                                                  _canned_history_with_findings()))
    assert sc["id"] == "q1"
    assert sc["success"] is True
    assert sc["failed"] is None
    assert sc["n_findings"] == 1
    assert sc["grounding"] == 1.0
    assert sc["citation"] == 1.0
    assert sc["coverage"] == 1.0
    assert sc["hallucination"] == 0.0


def test_evaluate_question_report_none_marks_failed():
    judge = FakeGLMClient([])  # 不应被调用
    item = {"id": "q2", "question": "Q", "key_facts": ["x"]}
    sc = evaluate_question(item, cfg=None, judge_client=judge,
                           run_one_fn=lambda q: (None, []))
    assert sc["success"] is False
    assert sc["failed"] == "no_report"
    assert sc["grounding"] is None
    # 裁判绝未被调用
    assert judge._r == []


def test_evaluate_question_zero_findings_coverage_only():
    # 有报告但抽不到 findings(N=0):Grounding 不可用,覆盖率仍可算
    judge = FakeGLMClient([
        LLMResponse(content='{"covered":false,"reason":"未提及"}'),
    ])
    item = {"id": "q3", "question": "Q", "key_facts": ["某未覆盖事实"]}
    sc = evaluate_question(item, cfg=None, judge_client=judge,
                           run_one_fn=lambda q: (_canned_report(), []))  # 空 history
    assert sc["success"] is True
    assert sc["n_findings"] == 0
    assert sc["grounding"] is None
    assert sc["grounding_available"] is False
    assert sc["coverage"] == 0.0
