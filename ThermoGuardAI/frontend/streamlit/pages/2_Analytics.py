"""Analytics page — trends, faults, temperatures, predictions."""
from __future__ import annotations

import os
import sys

import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from analytics.charts import inspections_bar, severity_pie, temperature_trend  # noqa: E402
from frontend.streamlit.api_client import ApiClient  # noqa: E402

st.set_page_config(page_title="Analytics", page_icon="📊", layout="wide")

if not st.session_state.get("token"):
    st.warning("Please sign in first.")
    st.stop()

api: ApiClient = st.session_state.api

st.title("📊 Analytics")
days = st.select_slider("Period (days)", options=[7, 14, 30, 60, 90], value=30)

try:
    summary = api.analytics_summary(days)
    daily = api.analytics_inspections(days)["items"]
    temps = api.analytics_temps(days)["items"]
    health = api.analytics_health()["items"]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Inspections", summary["total_inspections"])
    c2.metric("Faults", summary["total_faults"])
    c3.metric("Critical", summary["critical_faults"])
    c4.metric("Avg risk", f"{summary['avg_risk']:.0f}%")

    col_a, col_b = st.columns(2)
    with col_a:
        st.plotly_chart(inspections_bar(daily), width="stretch")
    with col_b:
        st.plotly_chart(severity_pie(summary["severity_breakdown"]), width="stretch")

    st.subheader("Component temperature trends")
    if temps:
        st.plotly_chart(temperature_trend(temps), width="stretch")
    else:
        st.info("No temperature history yet — run an inspection with thermal enabled.")

    st.subheader("Component health summary")
    if health:
        st.dataframe(health, width="stretch", hide_index=True)
    else:
        st.info("No component data yet.")

    st.subheader("Top fault types")
    if summary["top_fault_types"]:
        fig = go.Figure(go.Bar(x=[t["fault_type"] for t in summary["top_fault_types"]], y=[t["count"] for t in summary["top_fault_types"]], marker_color="#E8603D"))
        fig.update_layout(height=300, margin=dict(l=10, r=10, t=20, b=10))
        st.plotly_chart(fig, width="stretch")

    # Predictive maintenance
    st.subheader("🔮 Predictive maintenance")
    pred = api.predictive()
    if pred["items"]:
        rows = []
        for p in pred["items"]:
            rows.append(
                {
                    "Component": p["label"],
                    "Type": p["component_type"],
                    "Trend (°C/insp)": p["trend_c_per_inspection"],
                    "RUL (hours)": p["rul_hours"] if p["rul_hours"] is not None else "—",
                    "Failure prob": f"{p['failure_probability'] * 100:.0f}%",
                    "Health": p["health"],
                }
            )
        st.dataframe(rows, width="stretch", hide_index=True)
        risky = [p for p in pred["items"] if p["failure_probability"] >= 0.5]
        if risky:
            st.warning("**At-risk components:** " + ", ".join(r["label"] for r in risky))
            for r in risky:
                st.markdown(f"- **{r['label']}** — {r['recommendation']}")
    else:
        st.info("Run a few inspections to build temperature history for predictions.")
except Exception as exc:  # noqa: BLE001
    st.error(f"Analytics unavailable: {exc}")
