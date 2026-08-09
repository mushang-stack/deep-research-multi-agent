"""Pydantic 数据结构(spec §3.2)。子 agent 以这些结构化对象回传,做上下文隔离。"""
from typing import Optional

from pydantic import BaseModel, Field


class SubQuestion(BaseModel):
    id: str
    text: str
    angle: str = ""


class ResearchPlan(BaseModel):
    question: str
    sub_questions: list[SubQuestion]


class Finding(BaseModel):
    id: str
    claim: str
    source_url: str
    source_title: str = ""
    excerpt: str = ""
    confidence: float = 0.0


class VerificationResult(BaseModel):
    finding_id: str
    verdict: str  # supported / unsupported / weak
    reason: str = ""
    suggested_query: Optional[str] = None


class ReportSection(BaseModel):
    heading: str
    content: str
    citations: list[str] = Field(default_factory=list)  # finding_id 列表


class Report(BaseModel):
    sections: list[ReportSection]
    sources: list[str] = Field(default_factory=list)


class SearchResult(BaseModel):
    """web_search 的单条返回。非 Finding —— 是否采信由 Researcher 决定(M2)。"""
    title: str
    url: str
    snippet: str = ""
