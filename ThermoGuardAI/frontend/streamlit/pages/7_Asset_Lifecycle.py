"""Asset Lifecycle Management — panel & component lifecycle, RUL, reliability, costs."""
from __future__ import annotations

import os
import sys
from datetime import date, datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from frontend.streamlit.api_client import ApiClient  # noqa: E402

st.set_page_config(page_title="Asset Lifecycle", page_icon="🏭", layout="wide")

if not st.session_state.get("token"):
    st.warning("Please sign in first.")
    st.stop()

api: ApiClient = st.session_state.api

RELIABILITY_COLORS = {"healthy": "#2E8B57", "warning": "#E8A33D", "high": "#E07B39", "critical": "#D64545"}


def _fmt_dt(value) -> str:  # noqa: ANN001
    if not value:
        return "—"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value
    return value.strftime("%d %b %Y")


def _reliability_color(score: float) -> str:
    if score >= 80:
        return "#2E8B57"
    if score >= 60:
        return "#E8A33D"
    if score >= 40:
        return "#E07B39"
    return "#D64545"


def render_component_detail(detail: dict) -> None:
    st.markdown("---")
    st.subheader(f"🔩 {detail.get('code') or '—'} · {detail.get('label', '').replace('_', ' ').title()}")
    st.caption(
        f"Panel: **{detail.get('panel_name') or '—'}** ({detail.get('panel_code') or '—'}) · "
        f"Location: {detail.get('panel_location') or '—'} · Type: {detail.get('component_type')}"
    )

    c1, c2, c3, c4, c5 = st.columns(5)
    reliability = detail.get("reliability_score", 0)
    c1.metric("Reliability", f"{reliability:.0f}%")
    rul = detail.get("rul_hours")
    c2.metric("Remaining Useful Life", f"{rul:.0f} h" if rul is not None else "—")
    c3.metric("Failure Probability", f"{detail.get('failure_probability', 0) * 100:.0f}%")
    c4.metric("Failures (all time)", detail.get("failure_count", 0))
    c5.metric("Total Repair Cost", f"${detail.get('total_repair_cost', 0):,.2f}")

    with st.expander("📏 Lifecycle details", expanded=True):
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Installed", _fmt_dt(detail.get("installed_at")))
        c2.metric("Age (years)", detail.get("age_years") or "—")
        c3.metric("Expected life", f"{detail.get('expected_life_years', 0):.0f} yrs")
        c4.metric("Replacements", detail.get("replacement_count", 0))
        pct = detail.get("life_used_pct")
        if pct is not None:
            st.progress(min(pct, 100) / 100, text=f"Service life used: {pct:.0f}% of expected")
        st.progress(reliability / 100, text=f"Reliability score: {reliability:.0f}%")

        if detail.get("recommendation"):
            st.info(f"🔮 {detail['recommendation']}")
        st.caption(
            f"Health: **{detail.get('health', 'healthy')}** · Trend: "
            f"{detail.get('trend_c_per_inspection', 0):+.2f} °C/inspection · "
            f"Temp limit (critical): {detail.get('limit_c') or '—'} °C"
        )

    t_temp, t_fail, t_mt, t_actions = st.tabs(
        ["🌡 Temperature History", "⚠ Failure History", "🔧 Maintenance History", "⚙️ Actions"]
    )

    with t_temp:
        series = detail.get("temperature_series", [])
        if series:
            df = pd.DataFrame(series)
            df["time"] = pd.to_datetime(df["time"])
            stats = detail.get("temperature_stats", {})
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Max", f"{stats.get('max')} °C" if stats.get("max") is not None else "—")
            c2.metric("Min", f"{stats.get('min')} °C" if stats.get("min") is not None else "—")
            c3.metric("Avg", f"{stats.get('avg')} °C" if stats.get("avg") is not None else "—")
            c4.metric("Readings", stats.get("readings", 0))
            fig = go.Figure(go.Scatter(x=df["time"], y=df["temp"], mode="lines+markers", name="Temp", line=dict(color="#E8603D", width=2)))
            fig.update_layout(
                title=f"Temperature over time — {detail.get('label')}",
                xaxis_title="Time",
                yaxis_title="°C",
                height=360,
                margin=dict(l=10, r=10, t=40, b=10),
            )
            st.plotly_chart(fig, width="stretch")
        else:
            st.info("No temperature history yet — run panel-linked inspections with thermal enabled.")

    with t_fail:
        failures = detail.get("failures", [])
        if failures:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Inspection": f.get("inspection_code") or f["id"],
                            "Fault Type": f.get("fault_type", "").replace("_", " ").title(),
                            "Severity": f.get("severity", "").upper(),
                            "Confidence": f"{f.get('confidence', 0) * 100:.0f}%",
                            "Temperature °C": f.get("temperature"),
                            "Date": _fmt_dt(f.get("created_at")),
                        }
                        for f in failures
                    ]
                ),
                width="stretch",
                hide_index=True,
            )
        else:
            st.success("No recorded failures for this component.")

    with t_mt:
        records = detail.get("maintenance", [])
        if records:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Work Order": r.get("code"),
                            "Status": r.get("status", "").upper(),
                            "Priority": r.get("priority", "").upper(),
                            "Fault": (r.get("fault_type") or "—").replace("_", " ").title(),
                            "Cost": f"${r.get('cost'):,.2f}" if r.get("cost") is not None else "—",
                            "Completed": _fmt_dt(r.get("completed_at")),
                            "Created": _fmt_dt(r.get("created_at")),
                        }
                        for r in records
                    ]
                ),
                width="stretch",
                hide_index=True,
            )
            total_cost = sum(r.get("cost") or 0 for r in records if r.get("status") == "completed")
            st.caption(f"**Total completed repair cost: ${total_cost:,.2f}**")
        else:
            st.info("No maintenance history linked to this component yet.")

    with t_actions:
        st.markdown("#### 🛠 Lifecycle actions")
        a1, a2, a3 = st.columns([1, 1, 2])
        if a1.button("🔁 Record replacement", type="primary", width="stretch"):
            api.asset_record_replacement(detail["id"])
            st.success(f"Replacement recorded for {detail.get('label')} — counter now {detail.get('replacement_count', 0) + 1}")
            st.rerun()
        if detail.get("retired"):
            if a2.button("♻️ Reactivate", width="stretch"):
                api.asset_activate_component(detail["id"])
                st.rerun()
        elif a2.button("🗄️ Retire", width="stretch"):
            api.asset_retire_component(detail["id"])
            st.rerun()

        with st.form("asset_edit", border=False):
            st.markdown("#### ✏️ Edit lifecycle fields")
            c1, c2, c3 = st.columns(3)
            installed = c1.date_input("Installed date", value=date.fromisoformat(detail["installed_at"]) if detail.get("installed_at") else date.today())
            life = c2.number_input("Expected life (years)", min_value=1.0, max_value=100.0, value=float(detail.get("expected_life_years", 15.0)), step=1.0)
            retired = c3.checkbox("Retired", value=bool(detail.get("retired")))
            if st.form_submit_button("Save lifecycle fields", type="primary"):
                api.asset_update_component(
                    detail["id"],
                    installed_at=installed.isoformat(),
                    expected_life_years=life,
                    retired=retired,
                )
                st.success("Lifecycle fields updated")
                st.rerun()


st.title("🏭 Asset Lifecycle Management")
st.caption(
    "Every panel and component is tracked over months and years — temperature history, "
    "failure history, replacements, remaining useful life and reliability, turning "
    "inspections into an asset management platform."
)

try:
    overview = api.asset_overview()
    totals = overview.get("totals", {})
    panels_data = overview.get("panels", [])

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Panels", totals.get("panels", 0))
    c2.metric("Components", totals.get("components", 0))
    c3.metric("Active", totals.get("active_components", 0))
    c4.metric("At Risk", totals.get("at_risk_components", 0))
    c5.metric("Open Maintenance", totals.get("open_maintenance", 0))
    c6.metric("Total Repair Cost", f"${totals.get('total_repair_cost', 0):,.2f}")

    if not panels_data:
        st.info("No panels registered yet — create one in Admin, then run panel-linked inspections.")
        st.stop()

    with st.sidebar:
        st.subheader("🏭 Panels")
        panel_labels = {f"{p['name']} ({p['code']}) — {p['component_count']} components": p["id"] for p in panels_data}
        panel_choice = st.selectbox("Select panel", list(panel_labels.keys()))
        show_retired = st.checkbox("Show retired components", value=False)

    panel_detail = api.asset_panel(panel_labels[panel_choice])
    components = [c for c in panel_detail.get("components", []) if not c.get("retired") or show_retired]

    st.subheader(f"📋 {panel_detail.get('name', '')} ({panel_detail.get('code', '')})")
    st.caption(
        f"Location: {panel_detail.get('location') or '—'} · Avg reliability: "
        f"**{panel_detail.get('avg_reliability', 0):.0f}%** · At risk: "
        f"**{panel_detail.get('at_risk_components', 0)}** · Open maintenance: "
        f"**{panel_detail.get('open_maintenance', 0)}** · Repair cost: "
        f"**${panel_detail.get('total_repair_cost', 0):,.2f}**"
        + (f" · Last inspection: {_fmt_dt(panel_detail.get('last_inspection_at'))}" if panel_detail.get("last_inspection_at") else "")
    )

    if components:
        rows = []
        for comp in components:
            rows.append(
                {
                    "ID": comp.get("id"),
                    "Code": comp.get("code") or "—",
                    "Component": comp.get("label", "").replace("_", " ").title(),
                    "Type": comp.get("component_type"),
                    "Reliability %": round(comp.get("reliability_score", 0), 0),
                    "Health": comp.get("health", "healthy").upper(),
                    "RUL (h)": comp.get("rul_hours") if comp.get("rul_hours") is not None else "—",
                    "Failures": comp.get("failure_count", 0),
                    "Replacements": comp.get("replacement_count", 0),
                    "Repair Cost": f"${comp.get('total_repair_cost', 0):,.2f}",
                    "Installed": _fmt_dt(comp.get("installed_at")),
                    "Life Used %": comp.get("life_used_pct") if comp.get("life_used_pct") is not None else "—",
                }
            )
        df = pd.DataFrame(rows)
        st.dataframe(
            df,
            width="stretch",
            hide_index=True,
            column_config={
                "Reliability %": st.column_config.ProgressColumn("Reliability %", min_value=0, max_value=100, format="%d%%"),
            },
        )

        # pick a component for the detail view
        options = {f"{r['Code']} — {r['Component']}": r["ID"] for r in rows}
        sel = st.selectbox("🔍 Inspect component lifecycle", list(options.keys()))
        detail = api.asset_component(options[sel])
        render_component_detail(detail)
    else:
        st.info("No components on this panel yet.")

except Exception as exc:  # noqa: BLE001
    st.error(f"Asset lifecycle unavailable: {exc}")
