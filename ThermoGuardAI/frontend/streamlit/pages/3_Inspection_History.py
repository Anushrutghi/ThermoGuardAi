"""Inspection History & Management — searchable history table + individual inspection page."""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, time

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from frontend.streamlit.api_client import ApiClient  # noqa: E402

st.set_page_config(page_title="Inspection History", page_icon="🗂️", layout="wide")

if not st.session_state.get("token"):
    st.warning("Please sign in first.")
    st.stop()

api: ApiClient = st.session_state.api

SEVERITY_COLORS = {"healthy": "#2E8B57", "warning": "#E8A33D", "high": "#E07B39", "critical": "#D64545"}

# Spec: possible causes per fault type (shown in fault analysis)
POSSIBLE_CAUSES: dict[str, list[str]] = {
    "overheating": ["Loose terminal", "Overload", "High resistance connection"],
    "burn_mark": ["Arcing", "Overheating", "Water intrusion"],
    "visible_spark": ["Loose connection", "Short circuit", "Contact erosion"],
    "smoke": ["Insulation breakdown", "Overheating", "Short circuit"],
    "water_intrusion": ["Leaking pipe", "Condensation", "Flooding"],
    "discoloration": ["Prolonged overheating", "UV exposure", "Chemical reaction"],
    "loose_wire": ["Vibration", "Improper torque", "Thermal cycling"],
}


def _fmt_dt(value: str | None) -> str:
    if not value:
        return "—"
    try:
        return datetime.fromisoformat(value).strftime("%d %b %Y %H:%M:%S")
    except ValueError:
        return value


@st.cache_data(ttl=5, show_spinner=False)
def fetch_history(**filters) -> dict:
    return api.list_inspections(**filters)


@st.cache_data(ttl=10, show_spinner=False)
def fetch_detail(inspection_id: int) -> dict:
    return api.get_inspection(inspection_id)


def render_table(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(
        [
            {
                "Inspection ID": r.get("inspection_code") or f"#{r['id']}",
                "Date & Time": _fmt_dt(r.get("started_at")),
                "Inspector": r.get("inspector") or "—",
                "Camera Source": r.get("camera_source") or "—",
                "Panel": r.get("panel_name") or "—",
                "Location": r.get("panel_location") or "—",
                "Components": r.get("component_count", 0),
                "Healthy": (r.get("counts") or {}).get("healthy", 0),
                "Warning": (r.get("counts") or {}).get("warning", 0),
                "Critical": (r.get("counts") or {}).get("critical", 0),
                "Max Temp °C": (r.get("temperature_stats") or {}).get("max_temp"),
                "Health %": round(r.get("health_score", 0), 0),
                "Status": r.get("status", "").upper(),
            }
            for r in rows
        ]
    )
    return df


def render_detail(detail: dict) -> None:
    """Individual inspection page."""
    st.subheader(f"📋 {detail.get('inspection_code') or f'Inspection #{detail['id']}'}")
    c1, c2 = st.columns([3, 2])
    with c1:
        st.markdown(
            f"**Panel:** {detail.get('panel_name') or '—'} ({detail.get('panel_code') or '—'})  "
            f"· **Location:** {detail.get('panel_location') or '—'}"
        )
        st.caption(
            f"Inspector: **{detail.get('inspector') or '—'}** · Started: {_fmt_dt(detail.get('started_at'))} · "
            f"Duration: **{detail.get('duration_s', 0):.0f} s** · Camera: {detail.get('camera_source') or '—'}"
        )
        st.caption(
            f"Software version: {detail.get('software_version') or '—'} · AI model: {detail.get('model_version') or '—'} · "
            f"Mode: {detail.get('mode')} · Status: **{detail.get('status')}**"
        )
    with c2:
        health = detail.get("health_score", 0)
        st.markdown(f"**Overall Health** `{health:.0f}%`" if health else "**Overall Health** `—`")
        st.progress(min(max(health, 0), 100) / 100, text=f"Health score: {health:.0f}%")

    m1, m2, m3, m4, m5 = st.columns(5)
    ts = detail.get("temperature_stats") or {}
    m1.metric("Highest Temperature", f"{ts.get('max_temp')} °C" if ts.get("max_temp") is not None else "—")
    m2.metric("Total Components", detail.get("component_count", 0))
    m3.metric("Faults Found", detail.get("faults_count", len(detail.get("faults", []))))
    m4.metric("Frames", detail.get("frames_processed", 0))
    m5.metric("Avg FPS", f"{detail.get('avg_fps', 0):.1f}")

    detections = detail.get("detections", [])
    faults = detail.get("faults", [])
    incidents = detail.get("incidents", [])
    series = detail.get("temperature_series", [])

    t_comp, t_temp, t_fault, t_inc, t_timeline = st.tabs(
        ["Components", "Temperature Analysis", "Fault Analysis", "Incident Reports", "Timeline"]
    )

    with t_comp:
        if detections:
            fault_by_comp: dict[str, dict] = {}
            for f in faults:
                if f.get("component_label"):
                    fault_by_comp.setdefault(f["component_label"], f)
            comp_rows = []
            for d in detections:
                fault = fault_by_comp.get(d.get("label"))
                comp_rows.append(
                    {
                        "Code": d.get("component_code") or "—",
                        "Component": d.get("label", "").replace("_", " ").title(),
                        "ID": d.get("id"),
                        "Temperature °C": d.get("temperature"),
                        "Status": (d.get("health") or "").upper(),
                        "Confidence": f"{d.get('confidence', 0) * 100:.0f}%",
                        "Fault": (fault.get("fault_type", "").replace("_", " ") if fault else "None").title(),
                    }
                )
            st.dataframe(pd.DataFrame(comp_rows), width="stretch", hide_index=True)
        else:
            st.info("No component records stored for this inspection.")

    with t_temp:
        if series:
            tc1, tc2, tc3 = st.columns(3)
            tc1.metric("Maximum", f"{ts.get('max_temp')} °C" if ts.get("max_temp") is not None else "—")
            tc2.metric("Minimum", f"{ts.get('min_temp')} °C" if ts.get("min_temp") is not None else "—")
            tc3.metric("Average", f"{ts.get('avg_temp')} °C" if ts.get("avg_temp") is not None else "—")
            st.caption("Temperature timeline (per component reading)")
            pivot = pd.DataFrame(series)
            pivot["time"] = pd.to_datetime(pivot["time"])
            chart = pivot.pivot_table(index="time", columns="label", values="temp", aggfunc="mean")
            st.line_chart(chart, height=320)
        else:
            st.info("No temperature readings stored for this inspection.")

    with t_fault:
        if faults:
            for f in faults:
                sev = f.get("severity", "warning")
                color = SEVERITY_COLORS.get(sev, "#888")
                causes = POSSIBLE_CAUSES.get(f.get("fault_type", ""), ["Inspect component for defects"])
                with st.container(border=True):
                    st.markdown(
                        f"**{f.get('fault_type','').replace('_',' ').title()}** — "
                        f"<span style='color:{color};font-weight:600'>{sev.upper()}</span> "
                        f"· confidence {f.get('confidence', 0) * 100:.0f}%"
                        + (f" · component **{f.get('component_label')}**" if f.get("component_label") else "")
                        + (f" · **{f.get('temperature')} °C**" if f.get("temperature") is not None else ""),
                        unsafe_allow_html=True,
                    )
                    st.markdown(f"*{f.get('message')}*")
                    st.markdown("**Possible causes:** " + ", ".join(causes))
                    if f.get("recommendation"):
                        st.markdown(f"**Recommendation:** {f['recommendation']}")
        else:
            st.success("No faults recorded for this inspection.")

    with t_inc:
        if incidents:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Event ID": inc.get("code"),
                            "Fault": inc.get("fault_type", "").replace("_", " ").title(),
                            "Severity": inc.get("severity", "").upper(),
                            "Time": _fmt_dt(inc.get("occurred_at")),
                            "Temperature °C": inc.get("temperature"),
                            "Component": inc.get("component_label") or "—",
                            "Suggested Action": inc.get("suggested_action") or "—",
                        }
                        for inc in incidents
                    ]
                ),
                width="stretch",
                hide_index=True,
            )
            evidence = next((inc.get("screenshot_path") for inc in incidents if inc.get("screenshot_path")), None)
            if evidence:
                url = api.media_url(evidence)
                if url:
                    st.caption("Evidence frame captured at alarm time:")
                    st.image(url, width=420)
        else:
            st.info("No automatic incident reports — no critical faults were detected.")

    with t_timeline:
        events: list[tuple[str, str]] = [(_fmt_dt(detail.get("started_at")), "🚦 Inspection started")]
        if detections:
            first_det = min((d.get("created_at") for d in detections if d.get("created_at")), default=None)
            if first_det:
                events.append((_fmt_dt(first_det), "🔍 Component detection"))
        if series:
            max_row = max(series, key=lambda r: r.get("temp", 0))
            events.append((_fmt_dt(max_row.get("time")), f"🔥 Hotspot detected — {max_row.get('label')} @ {max_row.get('temp')} °C"))
        for alarm in detail.get("alarms", []):
            events.append((_fmt_dt(alarm.get("created_at")), f"🚨 {alarm.get('severity','').upper()} alarm — {alarm.get('message','')[:60]}"))
        for inc in incidents:
            events.append((_fmt_dt(inc.get("occurred_at")), f"📋 Incident {inc.get('code')} — {inc.get('fault_type','')}"))
        if detail.get("report_generated_at"):
            events.append((_fmt_dt(detail["report_generated_at"]), "📄 PDF report generated"))
        if detail.get("ended_at"):
            events.append((_fmt_dt(detail["ended_at"]), "✅ Inspection completed"))
        events = sorted([e for e in events if e[0] != "—"], key=lambda e: e[0])
        for when, what in events:
            st.markdown(f"- `{when}` — {what}")


st.title("🗂️ Inspection History & Management")
st.caption("Every scan — live camera, uploaded image, video or thermal — is stored permanently with a unique ID.")

# ------------------------------------------------------------------ filters
with st.sidebar:
    st.subheader("🔎 Filters")
    search = st.text_input("Search (ID, panel, location, inspector…)", key="hist_search")
    try:
        panels = api.list_panels()
    except Exception:  # noqa: BLE001
        panels = []
    panel_choice = st.selectbox("Panel", ["All"] + [f"{p['name']} ({p['code']})" for p in panels])
    col_a, col_b = st.columns(2)
    status = col_a.selectbox("Status", ["All", "completed", "running", "aborted"])
    mode = col_b.selectbox("Mode", ["All", "quick", "continuous", "manual", "scheduled", "emergency"])
    archived_scope = st.radio("Archive", ["Active", "Archived", "All"], horizontal=True, label_visibility="collapsed")
    d1, d2 = st.columns(2)
    from_d = d1.date_input("From", value=None)
    to_d = d2.date_input("To", value=None)
    col_s, col_o = st.columns(2)
    sort = col_s.selectbox(
        "Sort by",
        ["started_at", "health_score", "max_temp", "risk_score", "component_count", "faults_count", "inspection_code"],
        format_func=lambda s: s.replace("_", " ").title(),
    )
    order = col_o.selectbox("Order", ["desc", "asc"])

filters: dict = {"sort": sort, "order": order, "limit": 500}
if search:
    filters["search"] = search
if status != "All":
    filters["status"] = status
if mode != "All":
    filters["mode"] = mode
if panel_choice != "All":
    filters["panel_id"] = panels[[f"{p['name']} ({p['code']})" for p in panels].index(panel_choice)]["id"]
filters["archived"] = {"Active": False, "Archived": True, "All": None}[archived_scope]
if from_d:
    filters["from_date"] = datetime.combine(from_d, time.min).isoformat()
if to_d:
    filters["to_date"] = datetime.combine(to_d, time.max).isoformat()

try:
    result = fetch_history(**filters)
    items = result.get("items", [])
    total = result.get("total", len(items))

    # header stats
    avg_health = round(sum(i.get("health_score", 0) for i in items) / len(items)) if items else 0
    crit = sum((i.get("counts") or {}).get("critical", 0) for i in items)
    warns = sum((i.get("counts") or {}).get("warning", 0) for i in items)
    h1, h2, h3, h4, h5 = st.columns(5)
    h1.metric("Inspections (filtered)", total)
    h2.metric("Avg Health Score", f"{avg_health}%" if items else "—")
    h3.metric("Critical Components", crit)
    h4.metric("Warnings", warns)
    h5.metric("Highest Temp", f"{max(((i.get('temperature_stats') or {}).get('max_temp') or 0) for i in items):.1f} °C" if items else "—")

    # main table
    df = render_table(items)
    st.dataframe(
        df,
        width="stretch",
        hide_index=True,
        column_config={
            "Health %": st.column_config.ProgressColumn("Health %", min_value=0, max_value=100, format="%d%%"),
            "Max Temp °C": st.column_config.NumberColumn(format="%.1f"),
        },
    )

    # actions
    st.markdown("---")
    a1, a2, a3, a4, a5 = st.columns([2, 1, 1, 1, 1])
    if items:
        options = {f"{i.get('inspection_code') or i['id']} — {_fmt_dt(i.get('started_at'))}"[:60]: i["id"] for i in items}
        sel_label = a1.selectbox("Select inspection", list(options.keys()))
        selected_id = options[sel_label]
        if a2.button("👁️ View detail"):
            st.session_state["hist_selected"] = selected_id
        if a3.button("📄 PDF report", key="hist_pdf"):
            with st.spinner("Generating report…"):
                report = api.generate_report(selected_id)
            st.toast(f"Report generated: {report['title']}")
            st.link_button("⬇ Download PDF", api.report_url(report["id"]), type="primary")
        row = next(i for i in items if i["id"] == selected_id)
        if row.get("archived"):
            if a4.button("♻️ Unarchive"):
                api.unarchive_inspection(selected_id)
                st.rerun()
        elif a4.button("🗄️ Archive"):
            api.archive_inspection(selected_id)
            st.rerun()
        if a5.button("🗑️ Delete", type="secondary"):
            st.session_state["hist_delete_confirm"] = selected_id
        if st.session_state.get("hist_delete_confirm") == selected_id:
            c1, c2 = st.columns([1, 5])
            if c1.button(f"⚠️ Confirm delete #{selected_id}", type="primary"):
                api.delete_inspection(selected_id)
                st.session_state.pop("hist_delete_confirm", None)
                st.success(f"Inspection {selected_id} deleted")
                fetch_history.clear()
                st.rerun()
            if c2.button("Cancel"):
                st.session_state.pop("hist_delete_confirm", None)
                st.rerun()
    else:
        a1.info("No inspections match the current filters.")

    # export
    export_col = st.columns(1)[0]
    with export_col:
        try:
            csv_bytes = api.export_inspections(
                search=search or None,
                status=None if status == "All" else status,
                mode=None if mode == "All" else mode,
                panel_id=filters.get("panel_id"),
                archived=filters.get("archived"),
                from_date=filters.get("from_date"),
                to_date=filters.get("to_date"),
            )
            st.download_button(
                "⬇ Export CSV",
                data=csv_bytes,
                file_name=f"inspection_history_{date.today():%Y%m%d}.csv",
                mime="text/csv",
            )
        except Exception as exc:  # noqa: BLE001
            st.warning(f"CSV export unavailable: {exc}")

    # ------------------------------------------------------------ detail view
    selected = st.session_state.get("hist_selected")
    if selected:
        st.markdown("---")
        try:
            detail = fetch_detail(selected)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not load inspection {selected}: {exc}")
            detail = None
        if detail:
            render_detail(detail)
            if st.button("✖ Close detail"):
                st.session_state.pop("hist_selected", None)
                st.rerun()

except Exception as exc:  # noqa: BLE001
    st.error(f"Could not load history: {exc}")
