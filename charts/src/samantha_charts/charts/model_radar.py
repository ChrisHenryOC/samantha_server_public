"""Chart 7: model-selection radar (Option B: 3 axes) (GH-309, CONVEX Slide 24b).

Renders a 3-axis polar/radar chart comparing 4 candidate models on:
  - Accuracy: overall stable accuracy % (higher is better)
  - Robustness: worst-category stable accuracy % (higher is better)
  - Speed: median LLM-call latency, inverted so faster = farther from center

Each axis is normalized to [0.1, 1.0] across the 4 models so the weakest
model on an axis still shows a small but visible polygon (center = weakest
candidate, not zero). The incumbent model (Qwen3-Next-80B) is drawn in green;
the others in distinct blue/orange/red.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt

from samantha_charts import style
from samantha_charts.models import MODEL_DISPLAY_NAMES

# ---------------------------------------------------------------------------
# Display-name mapping for the four candidate models
# model_radar applies the "(incumbent)" suffix to the incumbent entry on top
# of the shared base names.
# ---------------------------------------------------------------------------

_INCUMBENT_KEY = "Qwen3-Next-80B-A3B-Instruct-4bit"
_DISPLAY_NAMES: dict[str, str] = {
    **MODEL_DISPLAY_NAMES,
    _INCUMBENT_KEY: MODEL_DISPLAY_NAMES[_INCUMBENT_KEY] + " (incumbent)",
}

# Colors for non-incumbent models (cycled if more than 3 non-incumbents).
# BLUE_MEDIUM is excluded: it is visually indistinguishable from BLUE_PRIMARY.
_NON_INCUMBENT_COLORS = [
    style.BLUE_PRIMARY,
    style.ORANGE_ACCENT,
    style.RED_ACCENT,
]

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_radar_data(path: Path) -> dict[str, dict[str, float]]:
    """Read *path* (JSON) and return the ``models`` mapping.

    Parameters
    ----------
    path:
        Path to a JSON file with a top-level ``"models"`` key mapping model
        names to dicts with at least ``accuracy``, ``robustness``, and
        ``latency_p50_s`` fields.

    Returns
    -------
    dict[str, dict[str, float]]
        The models mapping from the file (all fields preserved).

    Raises
    ------
    ValueError
        If ``"models"`` key is absent or the mapping is empty.
    """
    raw: dict[str, Any] = json.loads(path.read_text())
    models = raw.get("models")
    if not models:
        raise ValueError(f"Expected a non-empty 'models' mapping in {path}; got: {models!r}")
    return dict(models)


def _normalize_axes(
    models: dict[str, dict[str, float]],
) -> dict[str, dict[str, float]]:
    """Return per-model normalized values in [0.1, 1.0] for the 3 radar axes.

    Normalization rules
    -------------------
    - ``accuracy``: higher is better -> ``0.1 + 0.9 * (x - min) / (max - min)``
    - ``robustness``: higher is better -> ``0.1 + 0.9 * (x - min) / (max - min)``
    - ``speed``: derived from ``latency_p50_s``, lower latency is better
      -> ``0.1 + 0.9 * (max - x) / (max - min)`` so fastest model gets 1.0.
    - Degenerate axis (max == min): every model gets 0.5.

    The [0.1, 1.0] floor ensures the weakest model on any axis still renders
    as a small polygon rather than collapsing to a degenerate line. The center
    of the chart represents the weakest candidate, not zero.

    Parameters
    ----------
    models:
        Mapping of model name to raw values; each entry must have
        ``accuracy``, ``robustness``, and ``latency_p50_s`` keys.

    Returns
    -------
    dict[str, dict[str, float]]
        Mapping of model name to normalized values with keys ``accuracy``,
        ``robustness``, and ``speed``.
    """
    names = list(models.keys())

    for n in names:
        for key in ("accuracy", "robustness", "latency_p50_s"):
            if key not in models[n]:
                raise ValueError(f"Model {n!r} is missing required key {key!r} in radar data")

    acc_vals = [models[n]["accuracy"] for n in names]
    rob_vals = [models[n]["robustness"] for n in names]
    lat_vals = [models[n]["latency_p50_s"] for n in names]

    def _higher_better(values: list[float], x: float) -> float:
        lo, hi = min(values), max(values)
        if hi == lo:
            return 0.5
        return 0.1 + 0.9 * (x - lo) / (hi - lo)

    def _lower_better(values: list[float], x: float) -> float:
        lo, hi = min(values), max(values)
        if hi == lo:
            return 0.5
        return 0.1 + 0.9 * (hi - x) / (hi - lo)

    result: dict[str, dict[str, float]] = {}
    for name in names:
        result[name] = {
            "accuracy": _higher_better(acc_vals, models[name]["accuracy"]),
            "robustness": _higher_better(rob_vals, models[name]["robustness"]),
            "speed": _lower_better(lat_vals, models[name]["latency_p50_s"]),
        }
    return result


def render_radar(
    models: dict[str, dict[str, float]],
    output_path: Path,
    *,
    incumbent: str | None = None,
) -> None:
    """Render a 3-axis radar/spider chart to *output_path* (PNG).

    Parameters
    ----------
    models:
        Mapping of model name to raw values with ``accuracy``, ``robustness``,
        and ``latency_p50_s`` keys. Must be non-empty.
    output_path:
        Destination PNG path. Parent directory is created if needed.
    incumbent:
        Model name to highlight in green. If None or not found in *models*,
        all models are drawn in the non-incumbent palette.

    Raises
    ------
    ValueError
        If *models* is empty.
    """
    if not models:
        raise ValueError("render_radar requires at least one model in models")

    normalized = _normalize_axes(models)

    axes_labels = ["Accuracy", "Robustness", "Speed"]
    n_axes = len(axes_labels)

    # Compute spoke angles: evenly spaced, starting from top (pi/2 offset so
    # "Accuracy" points straight up for a natural reading of the chart).
    angles = [math.pi / 2 + 2 * math.pi * i / n_axes for i in range(n_axes)]
    # Close the polygon by appending the first angle.
    angles_closed = angles + [angles[0]]

    fig = plt.figure(figsize=(9, 8))
    ax = fig.add_subplot(111, polar=True)

    # White background on both figure and polar axes.
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    # Radial axis: 0 to 1 with faint gridlines.
    ax.set_ylim(0, 1)
    ax.set_yticks([0.25, 0.5, 0.75, 1.0])
    ax.set_yticklabels(["0.25", "0.50", "0.75", "1.00"], fontsize=7, color=style.GRAY_SUBTITLE)
    ax.yaxis.grid(True, color=style.GRAY_GRID, linewidth=0.8)
    ax.xaxis.grid(True, color=style.GRAY_GRID, linewidth=0.8)

    # Spoke positions and labels.
    ax.set_xticks(angles)
    ax.set_xticklabels(axes_labels, fontsize=12, color="#111827")

    # Remove polar frame spine so the chart looks clean.
    ax.spines["polar"].set_color(style.GRAY_GRID)

    # Draw each model's polygon.
    non_incumbent_color_iter = iter(_NON_INCUMBENT_COLORS)
    for name in models:
        norm = normalized[name]
        values = [norm["accuracy"], norm["robustness"], norm["speed"]]
        values_closed = values + [values[0]]

        is_incumbent = name == incumbent
        if is_incumbent:
            color = style.GREEN_ACCENT
            alpha_fill = 0.20
            alpha_edge = 0.90
            zorder = 5
            linewidth = 2.5
        else:
            color = next(non_incumbent_color_iter, style.BLUE_PRIMARY)
            alpha_fill = 0.10
            alpha_edge = 0.75
            zorder = 3
            linewidth = 1.8

        label = _DISPLAY_NAMES.get(name, name)
        ax.fill(angles_closed, values_closed, color=color, alpha=alpha_fill, zorder=zorder)
        ax.plot(
            angles_closed,
            values_closed,
            color=color,
            linewidth=linewidth,
            alpha=alpha_edge,
            label=label,
            zorder=zorder,
        )

    # Legend outside the polar plot area.
    ax.legend(
        loc="upper left",
        bbox_to_anchor=(-0.15, -0.08),
        fontsize=9,
        frameon=True,
        framealpha=0.9,
        ncol=2,
    )

    # Title and subtitle via figure-level text (matches style.set_title layout).
    fig.text(
        0.5,
        0.97,
        "Model selection: no single model wins every axis",
        transform=fig.transFigure,
        fontsize=15,
        fontweight="bold",
        color="#111827",
        va="top",
        ha="center",
    )
    fig.text(
        0.5,
        0.93,
        "Normalized per axis; center is the weakest candidate, not zero (outer = best)",
        transform=fig.transFigure,
        fontsize=10,
        color=style.GRAY_SUBTITLE,
        va="top",
        ha="center",
    )

    # Footnote.
    footnote = (
        "Accuracy = overall stable accuracy; Robustness = worst-category stable accuracy; "
        "Speed = median LLM-call latency (inverted). "
        "Each axis normalized to [0.1, 1.0] across the 4 models; center = weakest, not zero. "
        "Source: charts/data/chart7-radar.json."
    )
    fig.text(
        0.5,
        0.01,
        footnote,
        transform=fig.transFigure,
        fontsize=8,
        color=style.GRAY_SUBTITLE,
        va="bottom",
        ha="center",
        fontstyle="italic",
        wrap=True,
    )

    # Tight layout to avoid clipping, then save.
    fig.tight_layout(rect=(0.0, 0.06, 1.0, 0.91))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=100, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def render_model_radar(output_path: Path) -> None:
    """Production renderer: reads the committed data file and renders to *output_path*.

    Data source: ``charts/data/chart7-radar.json`` (committed, following the
    Charts 1/5/8 precedent of repo-committed data + committed PNG).
    """
    data_path = Path(__file__).resolve().parents[3] / "data" / "chart7-radar.json"
    parsed: dict[str, Any] = json.loads(data_path.read_text())
    models_raw = parsed.get("models")
    if not models_raw:
        raise ValueError(
            f"Expected a non-empty 'models' mapping in {data_path}; got: {models_raw!r}"
        )
    models = dict(models_raw)
    incumbent: str | None = parsed.get("_meta", {}).get("incumbent")
    render_radar(models, output_path, incumbent=incumbent)
