"""Chart N3: accuracy-evolution line chart (GH-307, CONVEX Slide 17).

Reproduces the original gtan-samantha "Accuracy Evolution" connected-scatter
aesthetic: a dashed connector through each optimization stage, a bold
percentage plus model label under every point, a horizontal "cloud baseline"
reference line, and a top-right "higher is better" indicator.

The series is a set of project-intervention deltas, NOT a single-model
temporal run. Stages 1-6 are the original POC eval accuracy (issue #317);
the final point is samantha_server stable accuracy on the 149-fixture
corpus (docs/presentation/current-truth.md, "Phase progression").
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

from samantha_charts import style

# Violet dashed reference line for the cloud baseline (matches the original).
_CLOUD_COLOR = "#7C3AED"

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvolutionPoint:
    """One optimization stage on the accuracy-evolution arc.

    Parameters
    ----------
    stage:
        X-axis label for the stage (may contain newlines for wrapping).
    pct:
        Accuracy percentage, must be in [0, 100].
    model_label:
        Annotation under the point naming the model / technique (may
        contain newlines).
    is_endpoint:
        When True the marker and percentage are tinted green (the
        samantha_server endpoint); otherwise blue.
    """

    stage: str
    pct: float
    model_label: str
    is_endpoint: bool = False

    def __post_init__(self) -> None:
        if not (0.0 <= self.pct <= 100.0):
            raise ValueError(f"pct must be in [0, 100], got {self.pct!r}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render_evolution(
    points: list[EvolutionPoint],
    output_path: Path,
    *,
    cloud_baseline: float | None = 95.0,
    cloud_label: str = "Cloud baseline (95%, unoptimized)",
) -> None:
    """Render a connected accuracy-evolution chart to *output_path* (PNG).

    Points are plotted left to right in the order given, joined by a dashed
    connector. Each point gets a bold percentage and a model label beneath
    it. The endpoint marker is tinted green.

    Parameters
    ----------
    points:
        Ordered list of EvolutionPoint. Must be non-empty.
    output_path:
        Destination PNG path. Parent directory is created if needed.
    cloud_baseline:
        Y-value for the horizontal reference line, or None to omit it.
    cloud_label:
        Label drawn at the left end of the reference line.

    Raises
    ------
    ValueError
        If *points* is empty.
    """
    if not points:
        raise ValueError("points must be non-empty")

    xs = list(range(len(points)))
    ys = [p.pct for p in points]

    fig, ax = plt.subplots(figsize=(11, 7.4))
    style.apply_style(fig, ax)

    # Dashed connector behind the markers.
    ax.plot(xs, ys, linestyle="--", color=style.BLUE_LIGHT, linewidth=2.0, zorder=2)

    # Optional cloud-baseline reference line (violet dashed, label at left).
    if cloud_baseline is not None:
        ax.axhline(
            cloud_baseline,
            linestyle="--",
            color=_CLOUD_COLOR,
            linewidth=1.0,
            alpha=0.55,
            zorder=1,
        )
        ax.text(
            0.012,
            cloud_baseline + 0.5,
            cloud_label,
            transform=ax.get_yaxis_transform(),
            fontsize=9,
            fontstyle="italic",
            color=_CLOUD_COLOR,
            va="bottom",
            ha="left",
            zorder=4,
        )

    # Markers + per-point annotations.
    for x, p in zip(xs, points, strict=True):
        marker_color = style.GREEN_ACCENT if p.is_endpoint else style.BLUE_MEDIUM
        ax.scatter(
            [x],
            [p.pct],
            s=140,
            color=marker_color,
            edgecolor="white",
            linewidth=1.5,
            zorder=3,
        )
        ax.annotate(
            f"{p.pct:.1f}%",
            (x, p.pct),
            textcoords="offset points",
            xytext=(0, -11),
            ha="center",
            va="top",
            fontsize=12,
            fontweight="bold",
            color=marker_color,
            zorder=4,
        )
        ax.annotate(
            p.model_label,
            (x, p.pct),
            textcoords="offset points",
            xytext=(0, -34),
            ha="center",
            va="top",
            fontsize=8,
            color=style.GRAY_SUBTITLE,
            zorder=4,
        )

    ax.set_xlim(-0.5, len(points) - 0.5)
    ax.set_xticks(xs)
    ax.set_xticklabels([p.stage for p in points], fontsize=9)
    ax.set_ylim(50, 102)
    ax.set_yticks([50, 60, 70, 80, 90, 100])
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}%"))
    ax.tick_params(axis="x", length=0)

    # Top-right green direction indicator. Mathtext triangle renders via the
    # self-contained mathtext font, sidestepping the missing-glyph warning the
    # plain Unicode triangle triggers under the headless Agg backend.
    fig.text(
        0.98,
        0.96,
        r"$\blacktriangle$ higher is better",
        transform=fig.transFigure,
        fontsize=10,
        color=style.GREEN_ACCENT,
        va="top",
        ha="right",
    )

    style.set_title(
        ax,
        "Accuracy Evolution: From Baseline to 100%",
        subtitle="Best local-model accuracy at each optimization stage, no fine-tuning",
    )
    style.add_footnote(
        fig,
        "Stages 1-6 are POC eval accuracy; the final point is samantha_server stable "
        "accuracy on the 149-fixture corpus. Every gain came from structuring "
        "information and tools, not model fine-tuning.",
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=100, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def render_scaffolding_progression(output_path: Path) -> None:
    """Production renderer: the locked accuracy-evolution arc.

    Stages 1-6 reproduce the original gtan-samantha "Accuracy Evolution"
    chart (issue #317); the final point is the samantha_server endpoint from
    current-truth.md's "Phase progression" section.
    """
    points = [
        EvolutionPoint("No\noptimization", 62.0, "Llama 70B\nbaseline"),
        EvolutionPoint("Prompt\ntuning", 86.0, "Llama 70B\n+ few-shot"),
        EvolutionPoint("Skills-based\nrouting", 97.6, "Llama 70B\n+ skills"),
        EvolutionPoint("Better model\nselection", 99.1, "Qwen3 32B\n+ skills"),
        EvolutionPoint("Best model\nselection", 99.7, "Qwen 2.5\nCoder 32B"),
        EvolutionPoint("Tool-assisted\nrouting", 99.8, "Gemma 4 26B\n+ tool"),
        EvolutionPoint(
            "Deterministic\n+ LLM",
            100.0,
            "Qwen3-Next-80B\n(149-corpus)",
            is_endpoint=True,
        ),
    ]
    render_evolution(points, output_path)
