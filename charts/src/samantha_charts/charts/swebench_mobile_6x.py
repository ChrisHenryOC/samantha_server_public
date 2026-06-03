"""SWE-Bench Mobile "same model, 6x across harnesses" horizontal bar chart.

Serves the CONVEX deck's research-convergence slide ("The researchers found
it too"). Holds the model fixed (Claude Opus 4.5) and varies only the
harness/agent, reproducing the paper's headline finding that agent
scaffolding matters as much as the model: 12% (Cursor) down to 2%
(OpenCode), a 6x gap on the same weights.

Data: charts/data/swebench-mobile-6x.json, transcribed from Tian et al.,
SWE-Bench Mobile (arXiv 2602.09540), Table 6 (Cross-Agent Model Comparison)
and Appendix B. Horizontal bars chosen to fit the slide's wide aspect ratio.
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
class HarnessBar:
    label: str
    success: float
    commercial: bool = True

    def __post_init__(self) -> None:
        if not (0.0 <= self.success <= 100.0):
            raise ValueError(f"success out of range: {self.label} = {self.success}")


def load_swebench_mobile(path: Path) -> tuple[str, list[HarnessBar]]:
    """Read *path* (JSON) and return (fixed model name, list of HarnessBar).

    Raises
    ------
    ValueError
        If the ``"harnesses"`` list is absent or empty.
    """
    raw: dict[str, Any] = json.loads(path.read_text())
    entries = raw.get("harnesses")
    if not entries:
        raise ValueError(f"Expected a non-empty 'harnesses' list in {path}; got: {entries!r}")
    model = str(raw.get("model", "the same model"))
    bars = [
        HarnessBar(
            label=e["label"],
            success=float(e["success"]),
            commercial=bool(e.get("commercial", True)),
        )
        for e in entries
    ]
    return model, bars


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def render_spread(model: str, bars: list[HarnessBar], output_path: Path) -> None:
    """Render the horizontal "same model, Nx across harnesses" chart to PNG.

    All bars share one colour because the model is constant; the only
    variable is the harness, which is the slide's whole point. The best /
    worst spread is annotated as the headline multiple.
    """
    if not bars:
        raise ValueError("render_spread requires at least one HarnessBar")

    # barh draws bottom-up; sort ascending so the best harness sits on top.
    ordered = sorted(bars, key=lambda b: b.success)
    positions = list(range(len(ordered)))

    best = max(b.success for b in ordered)
    worst = min(b.success for b in ordered)
    gap = best / worst if worst else float("inf")

    fig, ax = plt.subplots(figsize=(7.5, 6))
    style.apply_style(fig, ax)
    # Horizontal bars: gridlines belong on the x-axis.
    ax.yaxis.grid(False)
    ax.xaxis.grid(True, color=style.GRAY_GRID, linewidth=0.8, zorder=0)

    ax.barh(
        positions,
        [b.success for b in ordered],
        color=style.BLUE_PRIMARY,
        height=0.6,
        zorder=3,
    )

    ax.set_yticks(positions)
    ax.set_yticklabels([b.label for b in ordered], fontsize=11)
    x_max = best + 4.5
    ax.set_xlim(0.0, x_max)
    ax.set_xticks([0, 5, 10])
    # No x-axis label: the subtitle already states the metric, and the
    # per-bar "%" labels make the units unambiguous. Keeping the bottom clear
    # avoids colliding with the attribution footnote.

    # Value label just past each bar end (bars are short, so labels go outside).
    for pos, b in zip(positions, ordered, strict=True):
        ax.text(
            b.success + 0.2,
            pos,
            f"{b.success:.0f}%",
            va="center",
            ha="left",
            fontsize=11,
            fontweight="bold",
            color="#111827",
            zorder=4,
        )

    # 6x spread bracket on the right, spanning worst bar to best bar.
    bracket_x = best + 2.4
    ax.annotate(
        "",
        xy=(bracket_x, positions[-1]),
        xytext=(bracket_x, positions[0]),
        arrowprops={"arrowstyle": "<->", "color": style.ORANGE_ACCENT, "linewidth": 1.6},
        zorder=5,
    )
    ax.text(
        bracket_x + 0.35,
        (positions[0] + positions[-1]) / 2,
        f"{gap:.0f}x",
        va="center",
        ha="left",
        fontsize=15,
        fontweight="bold",
        color=style.ORANGE_ACCENT,
        zorder=5,
    )

    style.set_title(
        ax,
        f"Same model, {gap:.0f}x across harnesses",
        f"{model} on SWE-Bench Mobile. Only the harness changes.",
    )

    style.add_footnote(
        fig,
        "Source: Tian et al., SWE-Bench Mobile (arXiv 2602.09540), Table 6.",
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=100, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def render_swebench_mobile_6x(output_path: Path) -> None:
    """Production renderer: reads the committed data file and renders to *output_path*."""
    data_path = Path(__file__).resolve().parents[3] / "data" / "swebench-mobile-6x.json"
    model, bars = load_swebench_mobile(data_path)
    render_spread(model, bars, output_path)
