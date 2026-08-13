"""Streamlit 单页:深度研究多 agent 实时编排可视化。
运行:streamlit run ui/app.py
双模式:真实运行(后台线程 + 事件轮询)/ 回放(从 trace JSON 回放)。"""
import os
import threading
import time

import streamlit as st
from dotenv import load_dotenv

from core.config import load_config
from core.events import EventSink
from core.schemas import Report
from core.telemetry import TelemetrySink
from ui import view
from ui.controller import run_research, save_trace, load_trace, list_traces, trace_path

load_dotenv()
_TIMEOUT_S = 480  # 8 分钟硬上限,demo 安全网

st.set_page_config(page_title="AI-PM-Agent · 深度研究", layout="wide")
cfg = load_config()
PRICING = cfg.get("pricing") or {}

st.title("AI-PM-Agent · 深度研究多 Agent 编排")
mode = st.radio("模式", ["真实运行", "回放"], horizontal=True)


def _render_realtime(sink, tele, elapsed_ph, tl_ph, tm_ph):
    ev = sink.snapshot()
    tl_ph.markdown(view.render_timeline_md(ev))
    tm_ph.markdown(view.render_telemetry_md(tele.aggregate_by_name(), PRICING))
    elapsed_ph.caption(f"⏱ {ev[-1]['ts']:.0f}s  ·  事件 {len(ev)}" if ev else "启动中…")


if mode == "真实运行":
    question = st.text_input("研究问题", value="对比 RAG 与微调的适用场景与工程权衡")
    if st.button("▶ 开始") and question and not st.session_state.get("running"):
        st.session_state["running"] = True
        sink = EventSink()
        tele = TelemetrySink()
        holder = {}

        def target():
            try:
                holder["report"] = run_research(question, cfg, sink, tele)
            except Exception as e:  # controller 已兜底,双保险
                holder["error"] = repr(e)
            finally:
                holder["done"] = True

        threading.Thread(target=target, daemon=True).start()
        elapsed_ph = st.empty(); tl_ph = st.empty(); tm_ph = st.empty()
        deadline = time.monotonic() + _TIMEOUT_S
        while not holder.get("done"):
            if time.monotonic() > deadline:
                holder["error"] = "运行超时(>8min)"
                break
            _render_realtime(sink, tele, elapsed_ph, tl_ph, tm_ph)
            time.sleep(0.3)
        _render_realtime(sink, tele, elapsed_ph, tl_ph, tm_ph)
        st.session_state["running"] = False

        report = holder.get("report")
        if holder.get("error"):
            st.error(f"运行失败:{holder['error']}")
        if report is not None:
            st.markdown("---")
            st.markdown(view.render_report_markdown(report))
            try:
                p = trace_path(question)
                save_trace(p, question, sink.snapshot(), report,
                           tele.aggregate_by_name(), "success")
                st.success(f"已录制 trace → {p}(可在回放模式重放)")
            except Exception as e:
                st.warning(f"trace 录制失败:{e!r}")
        elif not holder.get("error"):
            st.warning("Orchestrator 未产出报告(holder 为空)。")
else:  # 回放
    traces = list_traces()
    if not traces:
        st.info("暂无 trace。先在「真实运行」跑一次(或等 DoD 录制的开箱 trace)。")
    else:
        choice = st.selectbox("选择 trace", traces, format_func=os.path.basename)
        speed = st.select_slider("回放速度", options=["立即", "快", "慢"], value="快")
        if st.button("▶ 回放") and choice:
            data = load_trace(choice)
            delay = {"立即": 0.0, "快": 0.05, "慢": 0.3}[speed]
            tl_ph = st.empty(); tm_ph = st.empty()
            shown = []
            for ev in data["events"]:
                shown.append(ev)
                tl_ph.markdown(view.render_timeline_md(shown))
                if data.get("telemetry"):
                    tm_ph.markdown(view.render_telemetry_md(data["telemetry"], PRICING))
                time.sleep(delay)
            st.markdown("---")
            if data.get("report"):
                st.markdown(view.render_report_markdown(Report(**data["report"])))
            st.caption(f"问题:{data.get('question')}  ·  status: {data.get('status')}")
