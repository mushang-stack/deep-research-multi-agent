"""纯视图构建器(M4 UI)。全函数无副作用,单测覆盖;app.py 只做 st.* 渲染。"""
import re

from core.telemetry import compute_cost

_PARALLEL_THRESHOLD = 0.5  # 秒;连续 research_start ts 跨度小于此 → 视为并行

_ICONS = {
    "run_start": "▶", "research_round": "🧩", "research_start": "🔍",
    "research_done": "·", "research_round_done": "✓", "verify_start": "🔎",
    "verify_done": "✓", "write_start": "✍", "report_ready": "📄",
    "warn": "⚠", "run_done": "✓", "error": "✗",
}


def render_report_markdown(report) -> str:
    """渲染 Report → markdown(从 main.py 抽出复用)。来源为可点击 markdown 链接。"""
    lines = []
    for sec in report.sections:
        lines.append(f"## {sec.heading}\n")
        lines.append(sec.content)
        if sec.citations:
            lines.append("\n\n*引用: " + ", ".join(sec.citations) + "*")
        lines.append("\n")
    if report.sources:
        lines.append("## 来源\n")
        for i, s in enumerate(report.sources, 1):
            lines.append(f"{i}. [{s}]({s})")
    return "\n".join(lines)


def _clean_text(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^\[[^\]]*\]\s*", "", text)   # 去前导 [tag]
    text = re.sub(r"^[⚠✗✓✅❌]\s*", "", text)     # 去前导状态符(避免与 _ICONS 双重显示)
    return text


def _parallel_badges(events):
    """{event_index: 并行数} 给每批连续 research_start(ts 跨度 < 阈值)的首个。"""
    badges = {}
    i = 0
    while i < len(events):
        if events[i]["kind"] != "research_start":
            i += 1
            continue
        j = i
        while (j < len(events) and events[j]["kind"] == "research_start"
               and events[j]["ts"] - events[i]["ts"] < _PARALLEL_THRESHOLD):
            j += 1
        n = j - i
        if n > 1:
            badges[i] = n
        i = j
    return badges


def _icon_for(e) -> str:
    """run_done 的图标按 payload.success 取 ✓/✗(其余按 _ICONS)。避免失败显示绿勾。"""
    if e["kind"] == "run_done":
        return "✓" if e.get("payload", {}).get("success") else "✗"
    return _ICONS.get(e["kind"], "·")


def timeline_rows(events):
    """events: list[dict](EventSink.snapshot / trace)。返回展示行列表。"""
    badges = _parallel_badges(events)
    rows = []
    for idx, e in enumerate(events):
        rows.append({
            "icon": _icon_for(e),
            "role": e.get("role"),
            "text": e.get("text", ""),
            "kind": e["kind"],
            "payload": e.get("payload", {}),
            "parallel": badges.get(idx),
        })
    return rows


def render_timeline_md(events) -> str:
    rows = timeline_rows(events)
    lines = ["**实时编排**"]  # 事件用 \n\n 分段(Streamlit 会折叠段内单个换行)
    for r in rows:
        role_tag = f"**{r['role']}** " if r["role"] else ""
        extra = ""
        p = r["payload"]
        if r["kind"] == "research_round" and p.get("is_gap"):
            extra = "  ⟲ 缺口→再检索"
        elif r["kind"] == "research_round_done":
            extra = f"  → {p.get('findings', 0)} 条 findings"
        elif r["kind"] == "verify_done" and p.get("unsupported", 0):
            extra = f"  ⚠ {p['unsupported']} 条不支撑"
        elif r["kind"] == "report_ready":
            extra = f"  → {p.get('sections', 0)} 节"
        par = f"  ｜并行 ×{r['parallel']}" if r["parallel"] else ""
        lines.append(f"{r['icon']} {role_tag}{_clean_text(r['text'])}{extra}{par}")
    return "\n\n".join(lines)


def _bar(frac) -> str:
    frac = max(0.0, min(1.0, frac))
    filled = int(round(frac * 10))
    return "█" * filled + "░" * (10 - filled)


def telemetry_tiles(rollup, pricing):
    """rollup: TelemetrySink.aggregate_by_name()。pricing: cfg['pricing']。
    返回 {name: {mean_steps, max_steps, budget_used, cost_usd}}。"""
    deepseek_p = (pricing or {}).get("deepseek")
    out = {}
    for name, d in rollup.items():
        ms = d.get("mean_steps", 0.0)
        mx = d.get("max_steps", 0) or 0
        cost = compute_cost(d.get("prompt_tokens", 0), d.get("completion_tokens", 0), deepseek_p)
        out[name] = {"mean_steps": ms, "max_steps": mx,
                     "budget_used": (ms / mx) if mx else 0.0,
                     "cost_usd": cost}
    return out


def render_telemetry_md(rollup, pricing) -> str:
    tiles = telemetry_tiles(rollup, pricing)
    if not tiles:
        return ""
    lines = ["**Agent 利用率(实时)**"]  # 同 timeline:每行自成段落
    for name, t in tiles.items():
        pct = int(round(t["budget_used"] * 100))
        cost = f"  💰 ${t['cost_usd']:.4f}" if t["cost_usd"] is not None else ""
        lines.append(f"`{name}`  {_bar(t['budget_used'])}  "
                     f"{t['mean_steps']:.1f}/{t['max_steps']} 步 ({pct}%){cost}")
    return "\n\n".join(lines)
