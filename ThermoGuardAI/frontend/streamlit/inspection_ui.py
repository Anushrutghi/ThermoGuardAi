"""Shared post-inspection results view.

Renders a completed inspection's results from the API response only — every
metric, temperature, and count shown comes straight from `GET /inspections/{id}`.
No fabricated numbers: empty data is shown as empty, not invented.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st

SEVERITY_COLORS = {"healthy": "#2E8B57", "warning": "#E8A33D", "high": "#E07B39", "critical": "#D64545"}


def _fmt_dt(value: str | None) -> str:
    if not value:
        return "—"
    try:
        return datetime.fromisoformat(value).strftime("%d %b %Y %H:%M:%S")
    except ValueError:
        return value


def _metrics(detail: dict) -> None:
    ts = detail.get("temperature_stats") or {}
    max_temp = ts.get("max_temp")
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Highest Temperature", f"{max_temp} °C" if max_temp is not None else "—")
    m2.metric("Components", detail.get("component_count", 0))
    m3.metric("Faults", len(detail.get("faults", [])) or detail.get("faults_count", 0))
    m4.metric("Frames", detail.get("frames_processed", 0))
    m5.metric("Risk Score", f"{detail.get('risk_score', 0):.0f}/100")


def _temperature_chart(detail: dict) -> None:
    series = detail.get("temperature_series", [])
    st.markdown("#### 🌡️ Temperature over time")
    if not series:
        st.info("No temperature readings were captured during this inspection.")
        return
    ts = detail.get("temperature_stats") or {}
    c1, c2, c3 = st.columns(3)
    c1.metric("Maximum", f"{ts.get('max_temp')} °C" if ts.get("max_temp") is not None else "—")
    c2.metric("Minimum", f"{ts.get('min_temp')} °C" if ts.get("min_temp") is not None else "—")
    c3.metric("Average", f"{ts.get('avg_temp')} °C" if ts.get("avg_temp") is not None else "—")
    pivot = pd.DataFrame(series)
    pivot["time"] = pd.to_datetime(pivot["time"])
    chart = pivot.pivot_table(index="time", columns="label", values="temp", aggfunc="mean")
    st.line_chart(chart, height=320)


def _severity_chart(detail: dict) -> None:
    counts = detail.get("counts") or {}
    order = ["healthy", "warning", "high", "critical"]
    labels = {"healthy": "Healthy", "warning": "Warning", "high": "High", "critical": "Critical"}
    colors = {"healthy": "#2E8B57", "warning": "#E8A33D", "high": "#E07B39", "critical": "#D64545"}
    st.markdown("#### 🎯 Severity breakdown")
    if not any(counts.values()):
        st.info("No components with faults recorded.")
        return
    df = pd.DataFrame(
        {
            "Severity": [labels[s] for s in order],
            "Count": [counts.get(s, 0) for s in order],
            "color": [colors[s] for s in order],
        }
    )
    st.bar_chart(df.set_index("Severity")["Count"], color=df.set_index("Severity")["color"], height=260)


def _detections(detail: dict) -> None:
    detections = detail.get("detections", [])
    st.markdown("#### 🔍 Detected components")
    if not detections:
        st.info("No components were detected in this inspection.")
        return
    rows = []
    for d in detections:
        rows.append(
            {
                "Code": d.get("component_code") or "—",
                "Component": d.get("label", "").replace("_", " ").title(),
                "Temperature °C": d.get("temperature"),
                "Status": (d.get("health") or "").upper(),
                "Confidence": f"{d.get('confidence', 0) * 100:.0f}%",
            }
        )
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


def _faults(detail: dict) -> None:
    faults = detail.get("faults", [])
    st.markdown("#### ⚠️ Faults found")
    if not faults:
        st.success("No faults were recorded during this inspection.")
        return
    for f in faults:
        sev = f.get("severity", "warning")
        color = SEVERITY_COLORS.get(sev, "#888")
        with st.container(border=True):
            st.markdown(
                f"**{f.get('fault_type', '').replace('_', ' ').title()}** — "
                f"<span style='color:{color};font-weight:600'>{sev.upper()}</span>"
                f" · confidence {f.get('confidence', 0) * 100:.0f}%"
                + (f" · component **{f.get('component_label')}**" if f.get("component_label") else "")
                + (f" · **{f.get('temperature')} °C**" if f.get("temperature") is not None else ""),
                unsafe_allow_html=True,
            )
            st.markdown(f"*{f.get('message')}*")
            if f.get("recommendation"):
                st.markdown(f"**Recommendation:** {f['recommendation']}")


def _incidents(detail: dict) -> None:
    incidents = detail.get("incidents", [])
    st.markdown("#### 📋 Incident reports")
    if not incidents:
        st.info("No incident reports — no critical faults were detected.")
        return
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Event ID": inc.get("code"),
                    "Fault": inc.get("fault_type", "").replace("_", " ").title(),
                    "Severity": inc.get("severity", "").upper(),
                    "Component": inc.get("component_label") or "—",
                    "Temperature °C": inc.get("temperature"),
                    "Suggested Action": inc.get("suggested_action") or "—",
                }
                for inc in incidents
            ]
        ),
        width="stretch",
        hide_index=True,
    )


def _evidence(detail: dict, api) -> None:  # noqa: ANN001
    shots = [
        ("📷 Original", detail.get("original_image_path")),
        ("🖥️ Annotated", detail.get("annotated_image_path")),
        ("🌡️ Thermal", detail.get("thermal_image_path")),
    ]
    present = [(title, path) for title, path in shots if path]
    if not present:
        return
    st.markdown("#### 📸 Capture evidence (embedded in the session report)")
    cols = st.columns(len(present))
    for col, (title, path) in zip(cols, present, strict=False):
        with col:
            st.image(api.media_url(path), caption=title, width="stretch")


def _report(detail: dict, api) -> None:  # noqa: ANN001
    st.markdown("#### 📄 Session report")
    report_id = st.session_state.get("generated_report_id")
    if not report_id:
        try:
            existing = [r for r in api.list_reports() if r.get("inspection_id") == detail.get("id")]
            if existing:
                report_id = existing[-1]["id"]
        except Exception:  # noqa: BLE001
            report_id = None
    if report_id:
        st.success("Session report compiled — every event from this session is in the PDF below.")
        st.link_button("⬇ Download session report (PDF)", api.report_url(report_id), type="primary", width="stretch")
        return
    if st.button("Generate PDF report", type="primary", width="stretch"):
        try:
            with st.spinner("Generating report…"):
                report = api.generate_report(detail["id"])
            st.session_state["generated_report_id"] = report["id"]
            st.rerun()
        except Exception as exc:  # noqa: BLE001
            st.error(f"Report generation failed: {exc}")


def render_inspection_results(detail: dict, api) -> None:  # noqa: ANN001
    """Full results view for a completed inspection (real API data only)."""
    code = detail.get("inspection_code") or f"# {detail['id']}"
    st.subheader(f"📋 {code}")
    st.caption(
        f"Panel: **{detail.get('panel_name') or '—'}** · Location: **{detail.get('panel_location') or '—'}** · "
        f"Inspector: **{detail.get('inspector') or '—'}** · Started: {_fmt_dt(detail.get('started_at'))} · "
        f"Duration: **{detail.get('duration_s', 0):.0f} s**"
    )
    st.caption(f"Mode: **{detail.get('mode')}** · Status: **{detail.get('status', '').upper()}** · AI model: {detail.get('model_version') or '—'}")
    if detail.get("thermal_simulated"):
        st.warning(
            "⚠️ **DEMO / SIMULATED THERMAL** — no real thermal sensor was connected; all temperatures in "
            "this inspection are simulated and are **not** real measurements."
        )

    _metrics(detail)
    left, right = st.columns([3, 2])
    with left:
        _temperature_chart(detail)
    with right:
        _severity_chart(detail)
    st.divider()
    _detections(detail)
    _faults(detail)
    _incidents(detail)
    _evidence(detail, api)
    st.divider()
    _report(detail, api)
