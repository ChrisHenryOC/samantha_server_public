"""Model accuracy ranking horizontal bar chart (Chart 1).

Ranks the samantha_server candidate models by stable accuracy (a fixture
passing all 5 sweeps) over the full 149-fixture corpus. Serves Slide 24a of
the CONVEX deck. Each stable pass already requires content correctness (the
 per-step gate applies regardless of category), so this single number
is content-checked, not weaker. Values are the locked isolated single-model
N=5 baseline; see charts/data/chart1-accuracy-ranking.json.
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
class ModelBar:
    label: str
    passed: int
    total: int
    is_incumbent: bool = False

    def __post_init__(self) -> None:
        if self.total <= 0:
            raise ValueError(f"total must be positive: {self.label}")
        if not (0 <= self.passed <= self.total):
            raise ValueError(f"passed out of range: {self.label}")

    @property
    def pct(self) -> float:
        return self.passed / self.total * 100.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_accuracy_ranking(path: Path) -> list[ModelBar]:
    """Read *path* (JSON) and return a list of ModelBar objects.

    Parameters
    ----------
    path:
        Path to a JSON file with a top-level ``"models"`` key containing a
        list of objects with ``label``, ``passed``, ``total``, and
        ``incumbent`` fields.

    Returns
    -------
    list[ModelBar]
        One ModelBar per entry in the JSON list, in file order.

    Raises
    ------
    ValueError
        If ``"models"`` key is absent or the list is empty.
    """
    raw: dict[str, Any] = json.loads(path.read_text())
    entries = raw.get("models")
    if not entries:
        raise ValueError(f"Expected a non-empty 'models' list in {path}; got: {entries!r}")
    return [
        ModelBar(
            label=e["label"],
            passed=int(e["passed"]),
            total=int(e["total"]),
            is_incumbent=bool(e.get("incumbent", False)),
        )
        for e in entries
    ]


# X-axis floor. A modest floor (not 0) gives the bars visible length while a
# higher floor (e.g. 96) would visually overstate the spread, making 96.6%
# look far worse than 100% when all four candidates are in fact strong. 60
# keeps the bars distinguishable without that distortion; the subtitle states
# the floor so the truncation is explicit.
_X_FLOOR = 60.0


def render_ranking(bars: list[ModelBar], output_path: Path) -> None:
    """Render a horizontal accuracy-ranking bar chart to *output_path* (PNG).

    The incumbent bar is tinted green; the rest are the blue primary. Bars
    are sorted so the highest accuracy sits at the top.
    """
    if not bars:
        raise ValueError("render_ranking requires at least one ModelBar")

    # barh draws bottom-up, so sort ascending to put the best at the top.
    ordered = sorted(bars, key=lambda b: b.pct)
    positions = list(range(len(ordered)))

    fig, ax = plt.subplots(figsize=(11, 6))
    style.apply_style(fig, ax)
    # Horizontal bars: move the subtle gridlines to the x-axis.
    ax.yaxis.grid(False)
    ax.xaxis.grid(True, color=style.GRAY_GRID, linewidth=0.8, zorder=0)

    colors = [style.GREEN_ACCENT if b.is_incumbent else style.BLUE_PRIMARY for b in ordered]
    ax.barh(positions, [b.pct for b in ordered], color=colors, height=0.62, zorder=3)

    ax.set_yticks(positions)
    ax.set_yticklabels([b.label for b in ordered], fontsize=10)
    ax.set_xlim(_X_FLOOR, 101.0)
    ax.set_xticks([60, 70, 80, 90, 100])
    # Direction cue inline on the metric label (glyph-free: the shared
    # add_direction_indicator uses a triangle glyph missing from the headless
    # font, and this is a single-metric chart so an inline cue reads cleaner).
    ax.set_xlabel("stable accuracy %  (higher is better)", color=style.GRAY_SUBTITLE, fontsize=10)

    # Percentage label inside the right end of each bar.
    for pos, b in zip(positions, ordered, strict=True):
        ax.text(
            b.pct - 0.06,
            pos,
            f"{b.pct:.2f}%",
            va="center",
            ha="right",
            fontsize=10,
            fontweight="bold",
            color="white",
            zorder=4,
        )

    style.set_title(
        ax,
        "Model accuracy ranking on samantha_server",
        "149-fixture corpus, N=5 stable · incumbent in green · x-axis starts at 60%",
    )

    counts = " · ".join(f"{b.passed}/{b.total}" for b in reversed(ordered))
    style.add_footnote(
        fig,
        "Stable accuracy = a fixture passing all 5 sweeps over the full 149-fixture "
        "corpus; each pass already requires content correctness (the per-step "
        f"gate). Counts, best to worst: {counts}. "
        "Source: charts/data/chart1-accuracy-ranking.json.",
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=100, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def render_accuracy_ranking(output_path: Path) -> None:
    """Production renderer: reads the committed data file and renders to *output_path*.

    Data source: ``charts/data/chart1-accuracy-ranking.json`` (committed,
    following the Charts 5/7/8/N2 precedent of repo-committed data + PNG).
    """
    data_path = Path(__file__).resolve().parents[3] / "data" / "chart1-accuracy-ranking.json"
    bars = load_accuracy_ranking(data_path)
    render_ranking(bars, output_path)
