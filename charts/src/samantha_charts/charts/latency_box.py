"""Chart 5: per-model LLM-call latency box plot (CONVEX Slide 25/26).

Renders one vertical boxplot per model showing median, quartiles, and whiskers
for query + llm_review fixture latencies. Incumbent model (Qwen3-Next-80B) is
tinted green; all others blue. Default x-axis order is ascending median
latency (fastest left).
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any, cast

import matplotlib.pyplot as plt

from samantha_charts import style
from samantha_charts.models import MODEL_DISPLAY_NAMES

_INCUMBENT_SUBSTRING = "Qwen3-Next-80B"

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_latencies(path: Path) -> dict[str, list[float]]:
    """Read *path* (JSON) and return the ``latencies`` mapping.

    Parameters
    ----------
    path:
        Path to a JSON file with a top-level ``"latencies"`` key mapping
        model names to lists of float seconds.

    Returns
    -------
    dict[str, list[float]]
        The raw latencies mapping from the file.

    Raises
    ------
    ValueError
        If ``"latencies"`` key is absent or the mapping is empty.
    """
    raw: dict[str, Any] = json.loads(path.read_text())
    latencies = raw.get("latencies")
    if not latencies:
        raise ValueError(f"Expected a non-empty 'latencies' mapping in {path}; got: {latencies!r}")
    return cast(dict[str, list[float]], latencies)


def render_latency_box(
    data: dict[str, list[float]],
    output_path: Path,
    *,
    order: list[str] | None = None,
) -> None:
    """Render one vertical boxplot per model to *output_path* (PNG).

    Parameters
    ----------
    data:
        Mapping of model name to list of latency samples (seconds). Must be
        non-empty.
    output_path:
        Destination PNG path. Parent directory is created if needed.
    order:
        If given, only the models listed here are plotted (left to right).
        Models absent from *data* are silently skipped. If None, all models
        in *data* are plotted ordered by ascending median latency.

    Raises
    ------
    ValueError
        If *data* is empty.
    """
    if not data:
        raise ValueError("render_latency_box requires at least one model in data")

    # Determine plotting order.
    if order is not None:
        ordered_names = [n for n in order if n in data]
        if not ordered_names:
            raise ValueError("no models from `order` are present in data")
    else:
        ordered_names = sorted(data, key=lambda n: statistics.median(data[n]))

    # Build per-box lists and metadata.
    box_data: list[list[float]] = [data[n] for n in ordered_names]
    labels: list[str] = [MODEL_DISPLAY_NAMES.get(n, n) for n in ordered_names]
    colors: list[str] = [
        style.GREEN_ACCENT if _INCUMBENT_SUBSTRING in n else style.BLUE_PRIMARY
        for n in ordered_names
    ]

    fig, ax = plt.subplots(figsize=(11, 6))
    style.apply_style(fig, ax)

    # On the dark theme the default black box lines/fliers vanish against the
    # navy background; tint them light. Light theme keeps matplotlib defaults
    # (empty kwargs) so existing renders are byte-stable.
    _dark = style.active_theme() == "dark"
    _line = style.subtitle_color()
    _line_kw = {"color": _line} if _dark else {}
    _box_kw = {"edgecolor": _line} if _dark else {}
    _flier_kw = {"markerfacecolor": _line, "markeredgecolor": _line} if _dark else {}

    bp = ax.boxplot(
        box_data,
        positions=list(range(len(ordered_names))),
        widths=0.5,
        patch_artist=True,
        medianprops={"linewidth": 2.0, "color": "white"},
        whiskerprops={"linewidth": 1.2, **_line_kw},
        capprops={"linewidth": 1.5, **_line_kw},
        boxprops=_box_kw,
        flierprops={"marker": "o", "markersize": 4, "alpha": 0.5, **_flier_kw},
    )

    # Tint each box with the appropriate color.
    for patch, color in zip(bp["boxes"], colors, strict=True):
        patch.set_facecolor(color)
        patch.set_alpha(0.85)

    ax.set_xticks(list(range(len(ordered_names))))
    ax.set_xticklabels(labels, fontsize=10)
    ax.set_xlim(-0.7, len(ordered_names) - 1 + 0.7)

    # Headroom above the tallest box for the "slowest sample" labels.
    overall_max = max(max(samples) for samples in box_data)
    ax.set_ylim(0, overall_max * 1.12)

    # Reserve room beneath the axis for the stacked median labels that sit
    # under each model name (drawn below in axis-fraction y).
    fig.subplots_adjust(bottom=0.24)

    y_range = ax.get_ylim()[1] - ax.get_ylim()[0]
    xtrans = ax.get_xaxis_transform()  # x in data coords, y in axis fraction
    for i, name in enumerate(ordered_names):
        samples = data[name]
        med = statistics.median(samples)
        y_max = max(samples)
        # Slowest sample, just above the top whisker / fliers (uncaptioned).
        ax.text(
            i,
            y_max + 0.015 * y_range,
            f"{y_max:.1f}s",
            ha="center",
            va="bottom",
            fontsize=8,
            color=style.subtitle_color(),
        )
        # Median stacked under the model name: a small "median" caption, then
        # the value in bold at the model-name font size.
        ax.text(
            i,
            -0.072,
            "median",
            transform=xtrans,
            ha="center",
            va="top",
            fontsize=7.5,
            color=style.subtitle_color(),
        )
        ax.text(
            i,
            -0.112,
            f"{med:.1f}s",
            transform=xtrans,
            ha="center",
            va="top",
            fontsize=10,
            fontweight="bold",
            color=style.ink_color(),
        )
    ax.set_ylabel(
        "latency (seconds)  (lower is better)",
        color=style.GRAY_SUBTITLE,
        fontsize=10,
    )

    style.set_title(
        ax,
        "Per-model LLM-call latency",
        "query + llm_review fixtures, N=5 isolated single-model runs"
        " (oMLX restarted between models)",
    )
    style.add_footnote(
        fig,
        "Latency is per-fixture wall time on LLM-routed scenarios"
        " (deterministic categories excluded)."
        " Source: charts/data/chart5-latencies.json.",
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=100, bbox_inches="tight", facecolor=style.bg_color())
    plt.close(fig)


def render_latency_box_chart(output_path: Path) -> None:
    """Production renderer: reads the committed data file and renders to *output_path*.

    Data source: ``charts/data/chart5-latencies.json`` (committed, following
    Charts 1/5/7/8/N2 precedent of repo-committed data + committed PNG).
    """
    data_path = Path(__file__).resolve().parents[3] / "data" / "chart5-latencies.json"
    latencies = load_latencies(data_path)
    render_latency_box(latencies, output_path)
