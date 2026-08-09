from agents.prompts import (
    ORCHESTRATOR_PROMPT, RESEARCHER_PROMPT, VERIFIER_PROMPT, WRITER_PROMPT,
)


def test_all_prompts_nonempty():
    for p in (ORCHESTRATOR_PROMPT, RESEARCHER_PROMPT, VERIFIER_PROMPT, WRITER_PROMPT):
        assert isinstance(p, str) and len(p) > 50


def test_researcher_demands_json_and_real_sources():
    assert "findings" in RESEARCHER_PROMPT
    assert "web_search" in RESEARCHER_PROMPT and "web_read" in RESEARCHER_PROMPT


def test_verifier_outputs_results_verdicts():
    assert "results" in VERIFIER_PROMPT
    for v in ("supported", "unsupported", "weak"):
        assert v in VERIFIER_PROMPT


def test_writer_has_no_tools_and_cites():
    assert "finding_id" in WRITER_PROMPT
    # Writer 无工具:prompt 里不应承诺任何工具
    assert "web_search" not in WRITER_PROMPT and "web_read" not in WRITER_PROMPT


def test_orchestrator_knows_dispatch_tools_and_wrapup():
    for t in ("dispatch_research", "verify_findings", "write_report"):
        assert t in ORCHESTRATOR_PROMPT
