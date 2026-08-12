import json
import threading
import time

from core.config import Config
from core.schemas import Report, ReportSection
from llm.base import LLMClient, LLMResponse

from eval.run_eval import _build_fn_for, evaluate_question, load_benchmark, main, report_to_text


class FakeGLMClient:
    def __init__(self, responses):
        self._r = list(responses)

    def chat(self, **kw):
        return self._r.pop(0)


class ConcurrentFakeJudge:
    """线程安全 fake:记录并发峰值 active,返回固定对称 verdict(不依赖顺序)。
    content 同时含 support/source_real/covered → finding 与 key_fact 两类 parser 都接受。"""
    def __init__(self, content='{"support":"supported","source_real":true,"covered":true,"reason":""}'):
        self._lock = threading.Lock()
        self.state = {"active": 0, "max": 0}
        self._content = content

    def chat(self, **kw):
        with self._lock:
            self.state["active"] += 1
            self.state["max"] = max(self.state["max"], self.state["active"])
        time.sleep(0.05)
        with self._lock:
            self.state["active"] -= 1
        return LLMResponse(content=self._content)


def _history_with_n_findings(n):
    # claim 必须唯一:extract_findings 按 (claim, source_url) 去重
    findings = [{"id": f"f{i}", "claim": f"claim {i}", "source_url": "https://x", "excerpt": "e"}
                for i in range(n)]
    return [{"role": "tool", "content": json.dumps({"findings": findings})}]


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


# ---------- 并发裁判 ----------

def test_evaluate_question_judges_concurrently():
    judge = ConcurrentFakeJudge()
    item = {"id": "q", "question": "Q", "key_facts": []}
    evaluate_question(item, cfg=None, judge_client=judge, judge_concurrency=4,
                     run_one_fn=lambda q: (_canned_report(), _history_with_n_findings(6)))
    assert judge.state["max"] >= 2   # 确实并发(当前串行 max=1 → FAIL 驱动实现)


def test_evaluate_question_respects_concurrency_cap():
    judge = ConcurrentFakeJudge()
    item = {"id": "q", "question": "Q", "key_facts": []}
    evaluate_question(item, cfg=None, judge_client=judge, judge_concurrency=4,
                     run_one_fn=lambda q: (_canned_report(), _history_with_n_findings(8)))
    assert judge.state["max"] <= 4   # 不超上限(max_workers 硬限,确定性)


def test_evaluate_question_concurrent_correctness():
    judge = ConcurrentFakeJudge()
    item = {"id": "q", "question": "Q", "key_facts": ["k1", "k2", "k3"]}
    sc = evaluate_question(item, cfg=None, judge_client=judge, judge_concurrency=4,
                           run_one_fn=lambda q: (_canned_report(), _history_with_n_findings(5)))
    assert sc["n_findings"] == 5
    assert sc["grounding"] == 1.0    # 全 supported + real
    assert sc["coverage"] == 1.0     # 全 covered


def test_main_passes_judge_concurrency_from_config(tmp_path):
    # main 应从 cfg["models"]["judge"]["max_concurrency"] 读并发上限并传入 evaluate_question
    bench = _bench_with(tmp_path, ["q1"])
    out = tmp_path / "out"
    judge = ConcurrentFakeJudge()

    def run_one_many(question):
        report = Report(sections=[ReportSection(heading="H", content="C")])
        return report, _history_with_n_findings(8)

    cfg = Config({"thresholds": {"grounding_min": 0.85},
                  "models": {"judge": {"max_concurrency": 3}}})
    rc = main(["--benchmark", str(bench), "--results", str(out)],
              run_one_fn=run_one_many, judge_client=judge, cfg=cfg)
    assert rc == 0
    assert judge.state["max"] <= 3   # config 的 max_concurrency=3 被尊重
    assert judge.state["max"] >= 2   # 且确实并发(非串行)


# ---------- load_benchmark ----------

def test_load_benchmark_loads_valid_skips_template_and_invalid(tmp_path):
    (tmp_path / "q1.yaml").write_text(
        "id: q1\nquestion: Q\nkey_facts:\n  - 事实\n", encoding="utf-8")
    # _ 前缀模板跳过
    (tmp_path / "_template.yaml").write_text(
        "id: tpl\nquestion: Q\nkey_facts:\n  - 事实\n", encoding="utf-8")
    # 缺 key_facts → 非法,跳过
    (tmp_path / "bad.yaml").write_text("id: bad\nquestion: Q\n", encoding="utf-8")
    items = load_benchmark(tmp_path)
    assert [it["id"] for it in items] == ["q1"]
    assert items[0]["key_facts"] == ["事实"]


# ---------- main CLI ----------

def _bench_with(tmp_path, ids):
    bench = tmp_path / "bench"
    bench.mkdir()
    for i in ids:
        (bench / f"{i}.yaml").write_text(
            f"id: {i}\nquestion: Q{i}\nkey_facts:\n  - 事实\n", encoding="utf-8")
    return bench


def _fake_run_one(question):
    report = Report(sections=[ReportSection(heading="H", content="C")])
    history = [{"role": "tool", "content":
                '{"findings":[{"id":"f1","claim":"c","source_url":"https://x"}]}'}]
    return report, history


def test_main_runs_and_writes_scorecard(tmp_path):
    bench = _bench_with(tmp_path, ["q1"])
    out = tmp_path / "out"
    judge = FakeGLMClient([
        LLMResponse(content='{"support":"supported","source_real":true,"reason":""}'),
        LLMResponse(content='{"covered":true,"reason":""}'),
    ])
    cfg = Config({"thresholds": {"grounding_min": 0.85}})
    rc = main(["--benchmark", str(bench), "--results", str(out)],
              run_one_fn=_fake_run_one, judge_client=judge, cfg=cfg)
    assert rc == 0
    assert (out / "q1.json").exists()
    summary = json.loads((out / "scorecard.json").read_text(encoding="utf-8"))
    assert summary["passed"] is True
    assert summary["mean_grounding"] == 1.0
    assert summary["n_questions"] == 1


def test_main_limit_caps_question_count(tmp_path):
    bench = _bench_with(tmp_path, ["q0", "q1", "q2"])
    out = tmp_path / "out"
    judge = FakeGLMClient([  # 只够 1 题:1 finding + 1 key_fact
        LLMResponse(content='{"support":"supported","source_real":true,"reason":""}'),
        LLMResponse(content='{"covered":true,"reason":""}'),
    ])
    cfg = Config({"thresholds": {"grounding_min": 0.85}})
    rc = main(["--benchmark", str(bench), "--results", str(out), "--limit", "1"],
              run_one_fn=_fake_run_one, judge_client=judge, cfg=cfg)
    assert rc == 0
    assert (out / "q0.json").exists()
    assert not (out / "q1.json").exists()


def test_main_only_filters_to_one(tmp_path):
    bench = _bench_with(tmp_path, ["q0", "q1"])
    out = tmp_path / "out"
    judge = FakeGLMClient([
        LLMResponse(content='{"support":"supported","source_real":true,"reason":""}'),
        LLMResponse(content='{"covered":true,"reason":""}'),
    ])
    cfg = Config({"thresholds": {"grounding_min": 0.85}})
    rc = main(["--benchmark", str(bench), "--results", str(out), "--only", "q1"],
              run_one_fn=_fake_run_one, judge_client=judge, cfg=cfg)
    assert rc == 0
    assert not (out / "q0.json").exists()
    assert (out / "q1.json").exists()


def test_main_no_items_prints_usage(tmp_path, capsys):
    empty = tmp_path / "empty"
    empty.mkdir()
    cfg = Config({"thresholds": {"grounding_min": 0.85}})
    rc = main(["--benchmark", str(empty), "--results", str(tmp_path / "out")], cfg=cfg)
    assert rc == 1
    assert "用法" in capsys.readouterr().err


def test_main_question_error_does_not_abort_run(tmp_path):
    bench = _bench_with(tmp_path, ["good", "bad"])
    out = tmp_path / "out"

    def run_one_flaky(question):
        if "bad" in question:
            raise RuntimeError("agent exploded")
        return _fake_run_one(question)

    judge = FakeGLMClient([
        LLMResponse(content='{"support":"supported","source_real":true,"reason":""}'),
        LLMResponse(content='{"covered":true,"reason":""}'),
    ])
    cfg = Config({"thresholds": {"grounding_min": 0.85}})
    rc = main(["--benchmark", str(bench), "--results", str(out)],
              run_one_fn=run_one_flaky, judge_client=judge, cfg=cfg)
    assert rc == 0  # 整批未中断
    assert (out / "good.json").exists()
    bad_sc = json.loads((out / "bad.json").read_text(encoding="utf-8"))
    assert bad_sc["success"] is False
    assert bad_sc["failed"] == "error"
    summary = json.loads((out / "scorecard.json").read_text(encoding="utf-8"))
    assert summary["n_questions"] == 2


def test_main_runs_questions_concurrently(tmp_path):
    # 题间并发:question_concurrency>1 → 多题并发跑(非串行)
    bench = _bench_with(tmp_path, ["q1", "q2", "q3", "q4"])
    out = tmp_path / "out"
    lock = threading.Lock()
    state = {"active": 0, "max": 0}

    def run_one_slow(question):
        with lock:
            state["active"] += 1
            state["max"] = max(state["max"], state["active"])
        time.sleep(0.1)
        with lock:
            state["active"] -= 1
        return _fake_run_one(question)

    judge = ConcurrentFakeJudge()
    cfg = Config({"thresholds": {"grounding_min": 0.85},
                  "eval": {"question_concurrency": 4}})
    rc = main(["--benchmark", str(bench), "--results", str(out)],
              run_one_fn=run_one_slow, judge_client=judge, cfg=cfg)
    assert rc == 0
    assert state["max"] >= 2  # 题间确实并发(串行 max=1)


# ---------- --system flag (Task 5) ----------


class _FakeGenClient(LLMClient):
    def chat(self, **kw):
        raise AssertionError("build 阶段不应调 chat")


class _NoopSearch:
    def search(self, query):
        return []


def _cfg_for_build():
    return Config({"models": {"generator": {}},
                   "tools": {"web_search": {}, "web_read": {"max_chars": 8000}},
                   "guards": {"agent_max_steps": 12, "research_max_rounds": 3}})


def test_build_fn_for_multi_and_baseline_identity():
    from agents.system import build_system
    from agents.baseline import build_baseline_system
    assert _build_fn_for("multi") is build_system
    assert _build_fn_for("baseline") is build_baseline_system


def test_build_fn_for_no_verify_strips_verify_tool():
    cfg = _cfg_for_build()
    loop_nv, _ = _build_fn_for("no_verify")(cfg, client=_FakeGenClient(), search_client=_NoopSearch())
    assert "verify_findings" not in set(loop_nv.registry.names())
    loop_m, _ = _build_fn_for("multi")(cfg, client=_FakeGenClient(), search_client=_NoopSearch())
    assert "verify_findings" in set(loop_m.registry.names())


def test_build_fn_for_baseline_builds_baseline_toolset():
    # 实际构建 baseline loop(_cfg_for_build 不含 baseline_max_steps → 走 .get 默认,顺带验向后兼容)
    cfg = _cfg_for_build()
    loop, _ = _build_fn_for("baseline")(cfg, client=_FakeGenClient(), search_client=_NoopSearch())
    assert set(loop.registry.names()) == {"web_search", "web_read", "write_report"}
    assert loop.name == "baseline"


def test_build_fn_for_unknown_raises():
    import pytest
    with pytest.raises(ValueError):
        _build_fn_for("bogus")


def test_main_system_baseline_writes_to_subdir(tmp_path):
    bench = _bench_with(tmp_path, ["q1"])
    out = tmp_path / "out"
    judge = FakeGLMClient([
        LLMResponse(content='{"support":"supported","source_real":true,"reason":""}'),
        LLMResponse(content='{"covered":true,"reason":""}'),
    ])
    cfg = Config({"thresholds": {"grounding_min": 0.85}})
    rc = main(["--benchmark", str(bench), "--system", "baseline"],
              results_dir=out, run_one_fn=_fake_run_one, judge_client=judge, cfg=cfg)
    assert rc == 0
    assert (out / "baseline" / "q1.json").exists()
    assert (out / "baseline" / "scorecard.json").exists()


def test_main_system_multi_writes_to_root_no_subdir(tmp_path):
    bench = _bench_with(tmp_path, ["q1"])
    out = tmp_path / "out"
    judge = FakeGLMClient([
        LLMResponse(content='{"support":"supported","source_real":true,"reason":""}'),
        LLMResponse(content='{"covered":true,"reason":""}'),
    ])
    cfg = Config({"thresholds": {"grounding_min": 0.85}})
    rc = main(["--benchmark", str(bench), "--system", "multi"],
              results_dir=out, run_one_fn=_fake_run_one, judge_client=judge, cfg=cfg)
    assert rc == 0
    assert (out / "q1.json").exists()
    assert not (out / "multi").exists()


def test_main_system_baseline_with_explicit_results_flag(tmp_path):
    bench = _bench_with(tmp_path, ["q1"])
    out = tmp_path / "out"
    judge = FakeGLMClient([
        LLMResponse(content='{"support":"supported","source_real":true,"reason":""}'),
        LLMResponse(content='{"covered":true,"reason":""}'),
    ])
    cfg = Config({"thresholds": {"grounding_min": 0.85}})
    rc = main(["--benchmark", str(bench), "--system", "baseline", "--results", str(out)],
              run_one_fn=_fake_run_one, judge_client=judge, cfg=cfg)
    assert rc == 0
    assert (out / "baseline" / "q1.json").exists()
