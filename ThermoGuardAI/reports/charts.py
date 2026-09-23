"""Matplotlib chart helpers used inside PDF reports."""
from __future__ import annotations

import logging
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

logger = logging.getLogger(__name__)


def temperature_trend_chart(points: list[tuple[str, float]], output_path: str | Path, title: str = "Temperature Trend") -> Path:
    """Render a temperature trend line chart (inspection date → °C)."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    labels = [p[0] for p in points]
    values = [p[1] for p in points]
    fig, ax = plt.subplots(figsize=(7, 2.6))
    ax.plot(range(len(values)), values, marker="o", color="#0E4DA4", linewidth=2)
    ax.fill_between(range(len(values)), values, color="#0E4DA4", alpha=0.12)
    ax.set_title(title, fontsize=10, color="#0E4DA4", fontweight="bold")
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=7)
    ax.set_ylabel("°C", fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out, dpi=140)
    plt.close(fig)
    return out


def severity_pie_chart(severity_counts: dict[str, int], output_path: str | Path) -> Path:
    """Render a severity distribution pie chart."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    labels = list(severity_counts.keys())
    values = list(severity_counts.values())
    colors_map = {"healthy": "#2E8B57", "warning": "#E8A33D", "high": "#E8603D", "critical": "#D64545"}
    fig, ax = plt.subplots(figsize=(3.2, 2.6))
    if values and sum(values) > 0:
        ax.pie(values, labels=labels, autopct="%1.0f%%", colors=[colors_map.get(label, "#888") for label in labels], startangle=90)
        ax.set_title("Fault Severity", fontsize=9, color="#0E4DA4", fontweight="bold")
    else:
        ax.text(0.5, 0.5, "No faults", ha="center", va="center")
        ax.axis("off")
    fig.tight_layout()
    fig.savefig(out, dpi=140)
    plt.close(fig)
    return out
