"""Chart N2: benchmarks clustered bar chart (GH-309, CONVEX Slide 24b).

Renders a clustered bar chart grouped by benchmark category (x-axis), with one
bar per model in each group. Three x-groups:
  - Published (MMLU-Pro): vendor/HF headline figures
  - Local (composite): our quants under one oMLX harness
  - samantha (N=5 stable): task accuracy on the 149-fixture corpus

Models are ordered by samantha stable % descending within each group, so the
leader flips between groups -- that contrast is the point.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np

from samantha_charts import style
from samantha_charts.models import MODEL_DISPLAY_NAMES

# Per-model contrasting colors (Tableau-10-ish, projector-friendly).
# Key is the full model name; incumbent gets red, others get distinct hues.
_MODEL_COLORS: dict[str, str] = {
    "Qwen3-Next-80B-A3B-Instruct-4bit": "#E15759",  # red (incumbent)
    "Qwen3.5-35B-A3B-8bit": "#4E79A7",  # blue
    "gemma-4-26B-A4B-it-MLX-4bit": "#59A14F",  # green
    "Qwen2.5-Coder-32B-Instruct-MLX-4bit": "#F28E2B",  # orange
}

# Fallback color for any model not in _MODEL_COLORS.
_FALLBACK_COLOR = style.BLUE_PRIMARY

_BAR_WIDTH = 0.2

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_benchmarks(path: Path) -> dict[str, dict[str, Any]]:
    """Read *path* (JSON) and return the ``models`` mapping.

    Parameters
    ----------
    path:
        Path to a JSON file with a top-level ``"models"`` key mapping model
        names to dicts with ``local``, ``published``, and
        ``samantha_stable_pct`` fields.

    Returns
    -------
    dict[str, dict]
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


def render_bars(
    models: dict[str, dict[str, Any]],
    output_path: Path,
    *,
    incumbent: str | None = None,
) -> None:
    """Render a grouped-by-benchmark clustered bar chart to *output_path* (PNG).

    X-axis has 3 groups (Published/Local/samantha). Within each group, one bar
    per model, ordered by samantha stable % descending. Per-model contrasting
    colors are used; no incumbent asterisk or bold label.

    Parameters
    ----------
    models:
        Mapping of model name to data dict with ``local.composite``,
        ``published.mmlu_pro``, and ``samantha_stable_pct`` fields.
        Must be non-empty.
    output_path:
        Destination PNG path. Parent directory is created if needed.
    incumbent:
        Accepted for API compatibility; not used in the grouped-by-benchmark
        layout (no asterisk or bold label is applied).

    Raises
    ------
    ValueError
        If *models* is empty.
    """
    if not models:
        raise ValueError("render_bars requires at least one model in models")

    # incumbent unused: per-model color already encodes identity (kept for API symmetry).

    # Sort models by samantha stable % descending (incumbent first).
    ordered_names = sorted(
        models.keys(),
        key=lambda n: float(models[n]["samantha_stable_pct"]),
        reverse=True,
    )

    # Three benchmark groups on x-axis.
    groups = ["Published\n(MMLU-Pro)", "Local\n(composite)", "samantha\n(N=5 stable)"]
    gx = np.arange(len(groups))
    n_models = len(ordered_names)
    bw = _BAR_WIDTH

    fig, ax = plt.subplots(figsize=(12, 7))
    style.apply_style(fig, ax)

    for i, name in enumerate(ordered_names):
        entry = models[name]
        if "mmlu_pro" not in entry.get("published", {}):
            raise ValueError(f"Model {name!r} is missing 'published.mmlu_pro' in models data")
        if "composite" not in entry.get("local", {}):
            raise ValueError(f"Model {name!r} is missing 'local.composite' in models data")
        vals = [
            float(entry["published"]["mmlu_pro"]),
            float(entry["local"]["composite"]),
            float(entry["samantha_stable_pct"]),
        ]
        color = _MODEL_COLORS.get(name, _FALLBACK_COLOR)
        short = MODEL_DISPLAY_NAMES.get(name, name)
        off = (i - (n_models - 1) / 2.0) * bw
        bars = ax.bar(gx + off, vals, width=bw, color=color, zorder=3, label=short)
        for b, v in zip(bars, vals, strict=True):
            ax.text(
                b.get_x() + b.get_width() / 2,
                b.get_height() + 0.8,
                f"{v:.1f}",
                ha="center",
                va="bottom",
                fontsize=12,
                color=style.subtitle_color(),
            )

    ax.set_xticks(gx)
    ax.set_xticklabels(groups, fontsize=12)
    ax.set_ylim(0, 124)
    ax.set_yticks([0, 20, 40, 60, 80, 100])
    ax.set_ylabel("score %  (higher is better)", color=style.subtitle_color(), fontsize=11)

    # Legend placed horizontally below the axes (in the spot the footnote
    # used to occupy); title and footnote intentionally removed.
    ax.legend(
        loc="upper center",
        bbox_to_anchor=(0.5, -0.10),
        ncol=4,
        fontsize=11,
        frameon=False,
        columnspacing=1.6,
        handletextpad=0.5,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=100, bbox_inches="tight", facecolor=style.bg_color())
    plt.close(fig)


def render_benchmark_bars(output_path: Path) -> None:
    """Production renderer: reads the committed data file and renders to *output_path*.

    Data source: ``charts/data/chart-n2-benchmarks.json`` (committed, following
    the Charts 1/5/7/8 precedent of repo-committed data + committed PNG).
    """
    data_path = Path(__file__).resolve().parents[3] / "data" / "chart-n2-benchmarks.json"
    raw: dict[str, Any] = json.loads(data_path.read_text())
    models_raw = raw.get("models")
    if not models_raw:
        raise ValueError(
            f"Expected a non-empty 'models' mapping in {data_path}; got: {models_raw!r}"
        )
    models = dict(models_raw)
    incumbent: str | None = raw.get("_meta", {}).get("incumbent")
    render_bars(models, output_path, incumbent=incumbent)
