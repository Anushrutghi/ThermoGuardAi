"""Plotly figure builders for the dashboard."""
from __future__ import annotations

import plotly.graph_objects as go


def inspections_bar(daily: list[dict]) -> go.Figure:
    fig = go.Figure(
        go.Bar(
            x=[d["date"] for d in daily],
            y=[d["count"] for d in daily],
            marker_color="#0E4DA4",
            name="Inspections",
        )
    )
    fig.update_layout(title="Inspections per Day", xaxis_title="Date", yaxis_title="Count", height=320, margin=dict(l=10, r=10, t=40, b=10))
    return fig


def severity_pie(breakdown: dict[str, int]) -> go.Figure:
    labels = list(breakdown.keys())
    values = [breakdown[k] for k in labels]
    colors = {"healthy": "#2E8B57", "warning": "#E8A33D", "high": "#E8603D", "critical": "#D64545"}
    fig = go.Figure(go.Pie(labels=labels, values=values, hole=0.4, marker=dict(colors=[colors.get(k, "#888") for k in labels])))
    fig.update_layout(title="Fault Severity Breakdown", height=320, margin=dict(l=10, r=10, t=40, b=10))
    return fig


def temperature_trend(series: list[dict]) -> go.Figure:
    """Line chart of temperature readings over time, one trace per label."""
    fig = go.Figure()
    by_label: dict[str, list[tuple[str, float]]] = {}
    for point in series:
        by_label.setdefault(point["label"], []).append((point["time"], point["temp"]))
    for label, points in by_label.items():
        fig.add_trace(
            go.Scatter(
                x=[p[0] for p in points],
                y=[p[1] for p in points],
                mode="lines+markers",
                name=label,
            )
        )
    fig.update_layout(title="Component Temperature Trends", xaxis_title="Time", yaxis_title="°C", height=360, margin=dict(l=10, r=10, t=40, b=10))
    return fig


def risk_gauge(risk: float) -> go.Figure:
    fig = go.Figure(
        go.Indicator(
            mode="gauge+number",
            value=risk,
            title={"text": "Overall Risk"},
            gauge={
                "axis": {"range": [0, 100]},
                "bar": {"color": "#0E4DA4"},
                "steps": [
                    {"range": [0, 25], "color": "#2E8B57"},
                    {"range": [25, 50], "color": "#E8A33D"},
                    {"range": [50, 75], "color": "#E8603D"},
                    {"range": [75, 100], "color": "#D64545"},
                ],
            },
        )
    )
    fig.update_layout(height=280, margin=dict(l=30, r=30, t=40, b=10))
    return fig
