"""gtan-samantha aesthetic helpers for matplotlib figures.

Design reference: the gtan-samantha draft chart PNGs

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

import os
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
# Theme (light default; opt-in dark via SAMANTHA_CHART_THEME=dark)
# ---------------------------------------------------------------------------
#
# Non-destructive: the accessors below return the existing light values
# unless SAMANTHA_CHART_THEME=dark, so default (light) renders are byte-stable.
# Dark targets the deck's slide-master navy (#0A1628). Render dark variants to
# a separate path (e.g. charts/output/dark/) so light PNGs are never clobbered.

_DARK_BG = "#0A1628"  # deck slide-master background
_DARK_INK = "#E5E7EB"  # primary text on dark
_DARK_SUBTITLE = "#9AA7B2"  # muted text on dark (matches deck model-cell label)
_DARK_GRID = "#22324A"  # faint gridline on dark
_DARK_SPINE = "#3A4A63"  # axis spine on dark


def active_theme() -> str:
    """Current chart theme: "dark" iff SAMANTHA_CHART_THEME=dark, else "light"."""
    return "dark" if os.environ.get("SAMANTHA_CHART_THEME", "").lower() == "dark" else "light"


def bg_color() -> str:
    """Figure/axes background: deck navy on dark, white on light."""
    return _DARK_BG if active_theme() == "dark" else "white"


def ink_color() -> str:
    """Primary text/title color."""
    return _DARK_INK if active_theme() == "dark" else "#111827"


def subtitle_color() -> str:
    """Secondary/label/footnote text color."""
    return _DARK_SUBTITLE if active_theme() == "dark" else GRAY_SUBTITLE


def grid_color() -> str:
    """Gridline color."""
    return _DARK_GRID if active_theme() == "dark" else GRAY_GRID


def spine_color() -> str:
    """Axis spine color."""
    return _DARK_SPINE if active_theme() == "dark" else GRAY_GRID

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

# Dark-theme text/tick/spine defaults, set once at import and gated on the env
# theme so light renders are untouched. This makes legends, axis labels, tick
# labels, and any default-color text render light on the navy background
# without a per-chart edit (only explicitly-colored dark elements still need
# the theme accessors).
if active_theme() == "dark":
    plt.rcParams.update(
        {
            "text.color": _DARK_INK,
            "axes.labelcolor": _DARK_SUBTITLE,
            "xtick.color": _DARK_SUBTITLE,
            "ytick.color": _DARK_SUBTITLE,
            "axes.edgecolor": _DARK_SPINE,
        }
    )

# ---------------------------------------------------------------------------
# Core style application
# ---------------------------------------------------------------------------


def apply_style(fig: Figure, ax: Axes) -> None:
    """Apply the gtan-samantha look to *fig* and *ax*.

    Caller should invoke this before drawing data so that subsequent
    draw calls inherit the correct defaults. Operates only on the given
    figure/axes objects — does NOT mutate any global matplotlib state.
    """
    fig.patch.set_facecolor(bg_color())
    ax.set_facecolor(bg_color())

    # Remove top and right spines
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(spine_color())
    ax.spines["bottom"].set_color(spine_color())

    # Grid — horizontal lines only, subtle
    ax.yaxis.grid(True, color=grid_color(), linewidth=0.8, zorder=0)
    ax.xaxis.grid(False)
    ax.set_axisbelow(True)

    # Tick formatting
    ax.tick_params(colors=subtitle_color(), labelsize=10)


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
        color=ink_color(),
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
            color=subtitle_color(),
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
        color=subtitle_color(),
        va="bottom",
        ha="center",
        fontstyle="italic",
        wrap=True,
    )
