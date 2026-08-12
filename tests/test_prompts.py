from agents.prompts import (
    BASELINE_PROMPT, ORCHESTRATOR_PROMPT, RESEARCHER_PROMPT, VERIFIER_PROMPT, WRITER_PROMPT,
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


def test_researcher_prompt_has_convergence_budget():
    """M2 回修:researcher prompt 必须含收敛预算(检索上限 + 收敛触发 + 空页跳过)。"""
    assert "最多" in RESEARCHER_PROMPT          # 检索次数上限措辞
    assert "跳过" in RESEARCHER_PROMPT          # 空页处理
    assert "立即" in RESEARCHER_PROMPT          # 收敛触发(读到几篇就停)


def test_researcher_prompt_claims_strictly_from_excerpt():
    """提分(grounding):claim 必须是 excerpt 直接改写、禁补充 excerpt 外内容;excerpt 为原文原句。
    诊断根因:researcher 的 claim 混入 excerpt 外的模型补充(方法论/升华)→ judge 判 partial → grounding 低。"""
    assert "直接改写" in RESEARCHER_PROMPT       # claim = excerpt 改写(非概括升华)
    assert "不要添加" in RESEARCHER_PROMPT       # 禁止补充 excerpt 外内容
    assert "原文原句" in RESEARCHER_PROMPT       # excerpt 要求:原文直接复制


def test_writer_prompt_requires_full_coverage():
    """coverage:report 必须全面覆盖所有 verified findings 的核心论点,不省略细节。"""
    assert "省略" in WRITER_PROMPT
    assert "每一条" in WRITER_PROMPT or "所有" in WRITER_PROMPT


def test_baseline_prompt_nonempty_and_mentions_tools():
    assert isinstance(BASELINE_PROMPT, str) and len(BASELINE_PROMPT) > 100
    assert "web_search" in BASELINE_PROMPT
    assert "web_read" in BASELINE_PROMPT
    assert "write_report" in BASELINE_PROMPT
