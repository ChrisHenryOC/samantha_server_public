"""Example chart demonstrating the gtan-samantha aesthetic.

Used by tests/test_style.py and by the render entry point.

The matplotlib backend (``Agg`` for headless rendering) is set in
``samantha_charts.__init__`` at package-import time so the choice is
centralised and the order is deterministic. Chart modules don't manage
backend state themselves.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt

from samantha_charts.style import (
    BLUE_PRIMARY,
    GREEN_ACCENT,
    add_direction_indicator,
    add_footnote,
    apply_style,
    set_title,
)


def render_example(output_path: Path) -> None:
    """Render a stub bar chart to *output_path* (PNG).

    Demonstrates: apply_style, set_title, add_direction_indicator,
    add_footnote.
    """
    models = [
        "Qwen3-80B-A3B 4bit",
        "Gemma 4 26B-A4B 4bit",
        "Qwen3.5-35B-A3B 8bit",
        "Qwen2.5-Coder-32B 4bit",
    ]
    scores = [100.0, 98.68, 98.01, 97.35]
    colors = [
        GREEN_ACCENT,
        BLUE_PRIMARY,
        BLUE_PRIMARY,
        BLUE_PRIMARY,
    ]

    fig, ax = plt.subplots(figsize=(10, 6))
    apply_style(fig, ax)

    bars = ax.barh(models, scores, color=colors, height=0.6, zorder=3)
    ax.bar_label(bars, fmt="%.1f%%", padding=4, fontsize=11, color="#374151")

    ax.set_xlim(0, 105)
    ax.set_xlabel("Stable accuracy (%)", fontsize=10, color="#6B7280")
    ax.invert_yaxis()

    set_title(
        ax,
        "Corpus Accuracy by Model",
        subtitle="151 fixtures, N=5 sweeps · Apple Silicon, 64GB",
    )
    add_direction_indicator(fig, "higher")
    add_footnote(
        fig,
        "Stable accuracy = fixtures where all N sweeps pass. "
        "Results from post-oMLX-0.3.9 upgrade baseline.",
    )

    fig.tight_layout(rect=(0, 0.04, 1, 0.88))
    fig.savefig(output_path, dpi=100, bbox_inches="tight", facecolor="white")
    plt.close(fig)
