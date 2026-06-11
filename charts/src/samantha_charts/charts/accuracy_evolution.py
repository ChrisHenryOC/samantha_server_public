"""Chart 14: accuracy evolution across milestones.

Renders a line chart showing how stable accuracy (N=5, vendored corpus)
changed across real sweep milestones. Points are plotted left-to-right in
file order (chronological). 100% points are tinted green.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt

from samantha_charts import style

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvolutionPoint:
    """One accuracy milestone in the evolution chart."""

    date: str
    label: str
    accuracy_pct: float
    passed: int
    total: int
    sweep_file: str


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_accuracy_evolution(path: Path) -> list[EvolutionPoint]:
    """Read *path* (JSON) and return a list of EvolutionPoints in file order.

    Parameters
    ----------
    path:
        Path to a JSON file with a top-level ``"points"`` key containing a
        non-empty list of objects with ``date``, ``label``, ``accuracy_pct``,
        ``passed``, ``total``, and ``sweep_file`` fields.

    Returns
    -------
    list[EvolutionPoint]
        One EvolutionPoint per entry in file order (chronological).

    Raises
    ------
    ValueError
        If ``"points"`` key is absent or the list is empty.
    """
    raw: dict[str, Any] = json.loads(path.read_text())
    points = raw.get("points")
    if not points:
        raise ValueError(f"Expected a non-empty 'points' list in {path}; got: {points!r}")
    return [
        EvolutionPoint(
            date=p["date"],
            label=p["label"],
            accuracy_pct=float(p["accuracy_pct"]),
            passed=int(p["passed"]),
            total=int(p["total"]),
            sweep_file=p["sweep_file"],
        )
        for p in points
    ]


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


def render_accuracy_evolution(points: list[EvolutionPoint], output_path: Path) -> None:
    """Render an accuracy-evolution line chart to *output_path* (PNG).

    X-axis is categorical in file order (chronological left to right). Y-axis
    is accuracy percent. Each point is annotated with its label and percent.
    100% points are tinted GREEN_ACCENT.

    Parameters
    ----------
    points:
        List of EvolutionPoints. Must be non-empty.
    output_path:
        Destination PNG path. Parent directory is created if needed.

    Raises
    ------
    ValueError
        If *points* is empty.
    """
    if not points:
        raise ValueError("render_accuracy_evolution requires at least one point")

    x_labels = [pt.date for pt in points]
    y_values = [pt.accuracy_pct for pt in points]
    x_positions = list(range(len(points)))

    fig, ax = plt.subplots(figsize=(11, 6))
    style.apply_style(fig, ax)

    # Line
    ax.plot(
        x_positions,
        y_values,
        color=style.BLUE_PRIMARY,
        linewidth=2.0,
        zorder=3,
    )

    # Per-point markers and labels
    for i, pt in enumerate(points):
        color = style.GREEN_ACCENT if pt.accuracy_pct == 100.0 else style.BLUE_PRIMARY
        ax.scatter(
            i,
            pt.accuracy_pct,
            color=color,
            s=80,
            zorder=4,
        )
        # Label above the point: label text + pct
        pct_str = f"{pt.accuracy_pct:g}%"
        annotation = f"{pt.label}\n{pct_str}"
        ax.text(
            i,
            pt.accuracy_pct + 0.15,
            annotation,
            ha="center",
            va="bottom",
            fontsize=8,
            color=color,
            zorder=5,
        )

    ax.set_xticks(x_positions)
    ax.set_xticklabels(x_labels, fontsize=9)

    # Y-range: give headroom above 100 and a sensible floor
    min_y = min(y_values)
    y_floor = max(min_y - 2.0, 0.0)
    ax.set_ylim(y_floor, 101.5)
    ax.set_ylabel("accuracy %  (higher is better)", color=style.GRAY_SUBTITLE, fontsize=10)

    style.set_title(
        ax,
        "Accuracy evolution across milestones",
        "stable N=5, vendored corpus (149 -> 151 fixtures)",
    )

    meta_source = "results/baselines/*.txt sweep summaries"
    style.add_footnote(
        fig,
        f"Metric: stable accuracy (N=5) on the vendored corpus (149 -> 151 fixtures)."
        f" Source: {meta_source}. Source file: charts/data/chart14-evolution.json.",
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=100, bbox_inches="tight", facecolor="white")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Production renderer
# ---------------------------------------------------------------------------


def render_accuracy_evolution_chart(output_path: Path) -> None:
    """Production renderer: reads chart14-evolution.json and renders to *output_path*.

    Data source: ``charts/data/chart14-evolution.json`` (committed).
    """
    data_path = Path(__file__).resolve().parents[3] / "data" / "chart14-evolution.json"
    points = load_accuracy_evolution(data_path)
    render_accuracy_evolution(points, output_path)
