"""gtan-samantha aesthetic helpers for matplotlib figures.

Design reference: ~/source/gtan-samantha/drafts/charts/chart*.png

Visual language:
- Bold left-aligned title + small gray subtitle (sample size / hardware)
- Top-right green "higher is better" or red "lower is better" direction indicator
- Blue family primary (#2563EB base), green/red/orange accents
- Methodology footnote in italics at bottom
- White background, sans-serif, no chart-junk

Font configuration
------------------
Sans-serif font family is set ONCE at module-import time on ``plt.rcParams``.
This is a process-wide setting and is intentional — all charts in the
pipeline share the same font cascade. ``apply_style`` does NOT mutate
``rcParams`` (it used to; the mutation was moved here so multi-chart
rendering in a single process doesn't churn the global state on every
call). Tests that want a different font must use ``mpl.rc_context`` or
override the family before invoking style helpers.
"""

from __future__ import annotations

from typing import Literal

import matplotlib.pyplot as plt
from matplotlib.axes import Axes
from matplotlib.figure import Figure

# ---------------------------------------------------------------------------
# Color palette
# ---------------------------------------------------------------------------

BLUE_PRIMARY = "#2563EB"
BLUE_LIGHT = "#93C5FD"
BLUE_MEDIUM = "#3B82F6"
GREEN_ACCENT = "#16A34A"
RED_ACCENT = "#DC2626"
ORANGE_ACCENT = "#EA580C"
GRAY_SUBTITLE = "#6B7280"
GRAY_GRID = "#E5E7EB"

# ---------------------------------------------------------------------------
# Module-level font configuration (set once at import, not per-call)
# ---------------------------------------------------------------------------

plt.rcParams["font.family"] = "sans-serif"
plt.rcParams["font.sans-serif"] = [
    "SF Pro Display",
    "Helvetica Neue",
    "Arial",
    "DejaVu Sans",
]

# ---------------------------------------------------------------------------
# Core style application
# ---------------------------------------------------------------------------


def apply_style(fig: Figure, ax: Axes) -> None:
    """Apply the gtan-samantha look to *fig* and *ax*.

    Caller should invoke this before drawing data so that subsequent
    draw calls inherit the correct defaults. Operates only on the given
    figure/axes objects — does NOT mutate any global matplotlib state.
    """
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    # Remove top and right spines
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(GRAY_GRID)
    ax.spines["bottom"].set_color(GRAY_GRID)

    # Grid — horizontal lines only, subtle
    ax.yaxis.grid(True, color=GRAY_GRID, linewidth=0.8, zorder=0)
    ax.xaxis.grid(False)
    ax.set_axisbelow(True)

    # Tick formatting
    ax.tick_params(colors=GRAY_SUBTITLE, labelsize=10)


# ---------------------------------------------------------------------------
# Title / subtitle
# ---------------------------------------------------------------------------


def set_title(ax: Axes, title: str, subtitle: str | None = None) -> None:
    """Set a bold left-aligned title with an optional smaller gray subtitle.

    Both text elements are anchored to axes coordinates so they scale
    correctly when the figure is resized.

    Raises
    ------
    ValueError
        If *ax* is not attached to a figure (no parent to draw on). Replaces
        a bare ``assert`` so the diagnostic survives ``python -O``.
    """
    fig = ax.get_figure()
    if fig is None:
        raise ValueError(
            "set_title requires an Axes attached to a Figure. "
            "Did you pass a detached axes (ax.figure was cleared)?"
        )

    # Use figure-level text so the title sits above the axes area
    # (axes.set_title clips to the axes bounding box; we want it flush-left
    # at figure level matching the reference charts).
    left = ax.get_position().x0
    top = ax.get_position().y1

    fig.text(
        left,
        top + 0.06,
        title,
        transform=fig.transFigure,
        fontsize=16,
        fontweight="bold",
        color="#111827",
        va="bottom",
        ha="left",
    )

    if subtitle is not None:
        fig.text(
            left,
            top + 0.02,
            subtitle,
            transform=fig.transFigure,
            fontsize=10,
            color=GRAY_SUBTITLE,
            va="bottom",
            ha="left",
        )


# ---------------------------------------------------------------------------
# Direction indicator
# ---------------------------------------------------------------------------


def add_direction_indicator(fig: Figure, direction: Literal["higher", "lower"]) -> None:
    """Add a top-right direction indicator to *fig*.

    Parameters
    ----------
    direction:
        ``"higher"`` renders green "▶ higher is better"; ``"lower"`` renders
        red "▼ lower is better". The ``Literal`` type protects static
        callers; the runtime check below catches stringly-typed callers
        (e.g., values flowing in from a config file).

    Raises
    ------
    ValueError
        On any *direction* value other than "higher" or "lower".
    """
    if direction == "higher":
        symbol = "▶"
        label = "higher is better"
        color = GREEN_ACCENT
    elif direction == "lower":
        symbol = "▼"
        label = "lower is better"
        color = RED_ACCENT
    else:
        raise ValueError(
            f"add_direction_indicator: direction must be 'higher' or 'lower', got {direction!r}"
        )

    fig.text(
        0.98,
        0.96,
        f"{symbol} {label}",
        transform=fig.transFigure,
        fontsize=10,
        color=color,
        va="top",
        ha="right",
        fontstyle="normal",
    )


# ---------------------------------------------------------------------------
# Footnote
# ---------------------------------------------------------------------------


def add_footnote(fig: Figure, text: str) -> None:
    """Add an italicized footnote at the bottom of *fig*."""
    fig.text(
        0.5,
        0.01,
        text,
        transform=fig.transFigure,
        fontsize=8,
        color=GRAY_SUBTITLE,
        va="bottom",
        ha="center",
        fontstyle="italic",
        wrap=True,
    )
