"""Two-card side-by-side quantization comparison chart (Chart 3, GH-308).

Renders a Q4 vs Q8 quantization comparison for Qwen3.5-35B-A3B using
real baseline data from results/baselines/.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch

from samantha_charts import style

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CardSide:
    label: str  # e.g. "Q4 (4-BIT)"
    stable_passed: int
    stable_total: int
    raw_passed: int
    raw_total: int
    flake_count: int
    memory_footprint: str  # e.g. "18–19 GB est."
    latency_p50: str | None = None  # e.g. "48.0s" or None to skip the row


# ---------------------------------------------------------------------------
# Color constants
# ---------------------------------------------------------------------------

_WINNER_BG = "#ECFDF5"
_WINNER_BORDER = style.GREEN_ACCENT
_WINNER_ACCENT = style.GREEN_ACCENT

_LOSER_BG = "#FEF3C7"
_LOSER_BORDER = "#D97706"
_LOSER_ACCENT = "#D97706"

_NEUTRAL_VALUE = "#374151"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _stable_pct(card: CardSide) -> float:
    return card.stable_passed / card.stable_total


def _draw_card(
    ax: plt.Axes,
    card: CardSide,
    is_winner: bool,
) -> None:
    """Draw a single card on *ax* using axes [0,1]x[0,1] coordinates."""
    bg_color = _WINNER_BG if is_winner else _LOSER_BG
    border_color = _WINNER_BORDER if is_winner else _LOSER_BORDER
    accent_color = _WINNER_ACCENT if is_winner else _LOSER_ACCENT

    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_aspect("auto")
    ax.axis("off")

    # Tinted background rectangle
    bg = mpatches.FancyBboxPatch(
        (0.03, 0.03),
        0.94,
        0.94,
        boxstyle="round,pad=0.02",
        facecolor=bg_color,
        edgecolor=border_color,
        linewidth=2,
        transform=ax.transAxes,
        zorder=1,
    )
    ax.add_patch(bg)

    # Top label (e.g. "Q4 (4-BIT)")
    ax.text(
        0.5,
        0.88,
        card.label,
        transform=ax.transAxes,
        fontsize=13,
        fontweight="bold",
        color="#111827",
        ha="center",
        va="center",
        zorder=2,
    )

    # Big stable accuracy number
    stable_pct = _stable_pct(card) * 100
    ax.text(
        0.5,
        0.70,
        f"{stable_pct:.1f}%",
        transform=ax.transAxes,
        fontsize=48,
        fontweight="bold",
        color=accent_color,
        ha="center",
        va="center",
        zorder=2,
    )

    # "state accuracy" caption + direction cue tied directly to the metric
    # (so the reader doesn't have to infer which number "higher is better"
    # refers to — there are many percentages on this chart).
    ax.text(
        0.5,
        0.58,
        "state accuracy",
        transform=ax.transAxes,
        fontsize=10,
        color=style.GRAY_SUBTITLE,
        ha="center",
        va="center",
        zorder=2,
    )
    ax.text(
        0.5,
        0.535,
        "(higher = better)",
        transform=ax.transAxes,
        fontsize=8,
        color=style.GRAY_SUBTITLE,
        fontstyle="italic",
        ha="center",
        va="center",
        zorder=2,
    )

    # Metric rows
    raw_pct = card.raw_passed / card.raw_total * 100
    rows: list[tuple[str, str]] = [
        ("Raw accuracy", f"{card.raw_passed}/{card.raw_total} ({raw_pct:.1f}%)"),
        ("Flake count", str(card.flake_count)),
    ]
    if card.latency_p50 is not None:
        rows.append(("Latency p50", card.latency_p50))
    rows.append(("Memory", card.memory_footprint))

    top_y = 0.46
    row_height = 0.09
    for i, (label_text, value_text) in enumerate(rows):
        y = top_y - i * row_height
        ax.text(
            0.08,
            y,
            label_text,
            transform=ax.transAxes,
            fontsize=11,
            color=style.GRAY_SUBTITLE,
            ha="left",
            va="center",
            zorder=2,
        )
        ax.text(
            0.92,
            y,
            value_text,
            transform=ax.transAxes,
            fontsize=11,
            color=_NEUTRAL_VALUE,
            ha="right",
            va="center",
            zorder=2,
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render_cards(left: CardSide, right: CardSide, output_path: Path) -> None:
    """Render two side-by-side cards to *output_path* (PNG).

    Tints the higher-stable-accuracy card green and the lower one amber,
    draws an arrow pointing from worse to better, and uses the existing
    style helpers for title/subtitle/direction/footnote.
    """
    left_wins = _stable_pct(left) >= _stable_pct(right)

    fig = plt.figure(figsize=(12, 6))
    fig.patch.set_facecolor("white")

    # Two card axes side by side with room for title + footnote
    ax_left = fig.add_axes((0.04, 0.10, 0.42, 0.72))
    ax_right = fig.add_axes((0.54, 0.10, 0.42, 0.72))

    _draw_card(ax_left, left, is_winner=left_wins)
    _draw_card(ax_right, right, is_winner=not left_wins)

    # Arrow between cards pointing toward the winner. Drawn as a
    # FancyArrowPatch (not a Unicode glyph) so it always renders — the
    # default sans-serif cascade is missing the Unicode arrow code points
    # in headless environments.
    if left_wins:
        arrow_start, arrow_end = (0.535, 0.46), (0.475, 0.46)
    else:
        arrow_start, arrow_end = (0.475, 0.46), (0.535, 0.46)
    arrow = FancyArrowPatch(
        arrow_start,
        arrow_end,
        transform=fig.transFigure,
        arrowstyle="->,head_width=10,head_length=12",
        linewidth=2.5,
        color=style.GRAY_SUBTITLE,
        mutation_scale=1,
    )
    fig.add_artist(arrow)

    # Title and subtitle (anchored to left card's position)
    fig.text(
        0.04,
        0.88,
        "Impact of Quantization on Qwen3.5-35B-A3B",
        transform=fig.transFigure,
        fontsize=16,
        fontweight="bold",
        color="#111827",
        va="bottom",
        ha="left",
    )
    fig.text(
        0.04,
        0.84,
        "149 fixtures, N=5 sweeps · post-oMLX-0.3.9 baseline",
        transform=fig.transFigure,
        fontsize=10,
        color=style.GRAY_SUBTITLE,
        va="bottom",
        ha="left",
    )

    # Footnote
    style.add_footnote(
        fig,
        "Counterintuitively, the more aggressive 4-bit quantization outperforms 8-bit on "
        "samantha's routing task — ~15 pt accuracy lift at roughly half the memory footprint.",
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=100, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def render_quantization_card(output_path: Path) -> None:
    """Production renderer — constructs the two sides from the baseline files
    cited in GH-308 and calls render_cards."""
    left = CardSide(
        label="Q4 (4-BIT)",
        stable_passed=137,
        stable_total=149,
        raw_passed=685,
        raw_total=745,
        flake_count=12,
        memory_footprint="18–19 GB est.",
        latency_p50=None,
    )
    right = CardSide(
        label="Q8 (8-BIT)",
        stable_passed=114,
        stable_total=149,
        raw_passed=595,
        raw_total=745,
        flake_count=34,
        memory_footprint="36.89 GB",
        latency_p50=None,
    )
    render_cards(left, right, output_path)
