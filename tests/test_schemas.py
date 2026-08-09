import pytest
from pydantic import ValidationError

from core.schemas import (
    SubQuestion, ResearchPlan, Finding, VerificationResult,
    ReportSection, Report, SearchResult,
)


def test_subquestion_defaults():
    sq = SubQuestion(id="q1", text="什么是 X?")
    assert sq.angle == ""


def test_research_plan():
    plan = ResearchPlan(question="深度研究 A", sub_questions=[
        SubQuestion(id="q1", text="A 的定义", angle="概念"),
    ])
    assert plan.sub_questions[0].angle == "概念"
    assert len(plan.sub_questions) == 1


def test_finding_required_fields():
    f = Finding(id="f1", claim="某结论", source_url="https://example.com/a")
    assert f.source_title == "" and f.excerpt == "" and f.confidence == 0.0


def test_finding_missing_required_raises():
    with pytest.raises(ValidationError):
        Finding(id="f1", claim="x")  # 缺 source_url


def test_verification_result_verdict():
    v = VerificationResult(finding_id="f1", verdict="supported", reason="来源匹配")
    assert v.suggested_query is None


def test_report_with_citations():
    sec = ReportSection(heading="结论", content="...", citations=["f1", "f2"])
    rep = Report(sections=[sec], sources=["https://example.com/a"])
    assert rep.sections[0].citations == ["f1", "f2"]


def test_search_result():
    r = SearchResult(title="t", url="u")
    assert r.snippet == ""
