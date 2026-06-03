"""Chart 10: daily routing-path 100% stacked bar (GH-310).

Renders a 100% normalized stacked bar chart showing the day-over-day mix of
deterministic vs LLM routing. The production target is <=5% LLM; a reference
line marks that threshold.

Note: Langfuse v3.172 cannot render 100%-normalized stacked bars with per-segment
labels in its dashboard (no normalization option, no segment label support in
pie/stacked-bar widgets). This matplotlib chart provides exactly what Langfuse
cannot: 100% normalization + per-bar segment labels + a custom reference line.
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
class DayMix:
    """One day's routing split.

    Parameters
    ----------
    date:
        ISO-8601 date string (YYYY-MM-DD).
    deterministic:
        Count of deterministic-path traces on this day.
    llm:
        Count of LLM-routed traces on this day.
    """

    date: str
    deterministic: int
    llm: int


@dataclass(frozen=True)
class RoutingPathDaily:
    """Daily routing-path distribution with provenance metadata.

    Parameters
    ----------
    days:
        Chronologically ordered list of per-day routing splits. Missing
        calendar days are collapsed (not represented).
    sample_size:
        Total trace count across all days.
    time_range:
        Human-readable date range for the footnote.
    """

    days: list[DayMix]
    sample_size: int
    time_range: str


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

_VALID_ROUTING_PATHS = {"deterministic", "llm"}


def aggregate_routing_path_daily(
    traces: list[dict[str, Any]],
) -> dict[str, dict[str, int]]:
    """Group traces by day and count deterministic vs LLM routing.

    Parameters
    ----------
    traces:
        List of trace dicts. Each is expected to carry
        ``{"timestamp": "YYYY-MM-DDT...", "metadata": {"routing_path": "deterministic"|"llm"}}``.

    Returns
    -------
    dict[str, dict[str, int]]
        Mapping of date string (YYYY-MM-DD) to ``{"deterministic": n, "llm": n}``.
        Only days that have at least one trace are included.

    Raises
    ------
    ValueError
        If any trace contains a routing_path value other than "deterministic"
        or "llm". Fails loud — production data must not have unknown paths.
    """
    result: dict[str, dict[str, int]] = {}

    for i, trace in enumerate(traces):
        metadata = trace.get("metadata", {})
        routing_path = metadata.get("routing_path")
        if routing_path is None:
            continue
        if routing_path not in _VALID_ROUTING_PATHS:
            raise ValueError(
                f"aggregate_routing_path_daily: unexpected routing_path value "
                f"{routing_path!r}. Expected one of {sorted(_VALID_ROUTING_PATHS)}."
            )

        timestamp = trace.get("timestamp")
        if not timestamp:
            raise ValueError(
                f"aggregate_routing_path_daily: trace at index {i} has a valid "
                f"routing_path but is missing a 'timestamp' value."
            )
        day = timestamp[:10]
        if day not in result:
            result[day] = {"deterministic": 0, "llm": 0}
        result[day][routing_path] += 1

    return result


# ---------------------------------------------------------------------------
# Pure normalization helper (directly unit-testable)
# ---------------------------------------------------------------------------


def _normalize(deterministic: int, llm: int) -> tuple[float, float]:
    """Normalize a (deterministic, llm) pair to fractions that sum to 1.0.

    Parameters
    ----------
    deterministic:
        Count of deterministic-path traces.
    llm:
        Count of LLM-routed traces.

    Returns
    -------
    tuple[float, float]
        ``(det_fraction, llm_fraction)`` where both are in [0, 1] and sum to 1.0.
        If the total is 0, returns (0.0, 0.0) rather than dividing by zero.
    """
    total = deterministic + llm
    if total == 0:
        return 0.0, 0.0
    return deterministic / total, llm / total


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_routing_path_daily(path: Path) -> RoutingPathDaily:
    """Read *path* (JSON) and return a RoutingPathDaily.

    Parameters
    ----------
    path:
        Path to a JSON file with a top-level ``"days"`` list and a ``"_meta"``
        object carrying ``sample_size`` and ``time_range``.

    Returns
    -------
    RoutingPathDaily
        Days are loaded in file order (assumed chronological).

    Raises
    ------
    ValueError
        If ``"days"`` is missing or empty, or if ``_meta.sample_size`` /
        ``_meta.time_range`` are absent.
    """
    raw: dict[str, Any] = json.loads(path.read_text())

    days_raw = raw.get("days")
    if not days_raw:
        raise ValueError(f"Expected a non-empty 'days' list in {path}; got: {days_raw!r}")

    meta: dict[str, Any] = raw.get("_meta", {})
    if "sample_size" not in meta:
        raise ValueError(f"Expected '_meta.sample_size' in {path}; key not found.")
    if "time_range" not in meta:
        raise ValueError(f"Expected '_meta.time_range' in {path}; key not found.")

    days = [
        DayMix(
            date=str(d["date"]),
            deterministic=int(d["deterministic"]),
            llm=int(d["llm"]),
        )
        for d in days_raw
    ]

    return RoutingPathDaily(
        days=days,
        sample_size=int(meta["sample_size"]),
        time_range=str(meta["time_range"]),
    )


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------

# Production target: LLM routing must stay at or below this fraction.
_LLM_TARGET_PCT = 5.0


def render_routing_path_daily(data: RoutingPathDaily, output_path: Path) -> None:
    """Render a 100% normalized stacked bar chart to *output_path* (PNG).

    Each bar represents one day, with segments for deterministic (BLUE_PRIMARY)
    and LLM (ORANGE_ACCENT) routing, normalized to 100%. A horizontal reference
    line at 5% marks the production target for LLM routing.

    Parameters
    ----------
    data:
        Daily routing distribution to plot.
    output_path:
        Destination PNG path. Parent directory is created if needed.

    Raises
    ------
    ValueError
        If *data.days* is empty.
    """
    if not data.days:
        raise ValueError("render_routing_path_daily: days list is empty — no data to render.")

    n = len(data.days)
    positions = list(range(n))
    date_labels = [d.date for d in data.days]

    det_fracs: list[float] = []
    llm_fracs: list[float] = []
    for day in data.days:
        df, lf = _normalize(day.deterministic, day.llm)
        det_fracs.append(df * 100.0)
        llm_fracs.append(lf * 100.0)

    fig, ax = plt.subplots(figsize=(12, 6))
    style.apply_style(fig, ax)
    ax.yaxis.grid(True, color=style.GRAY_GRID, linewidth=0.8, zorder=0)
    ax.xaxis.grid(False)

    bar_width = 0.62

    # Draw deterministic segment (bottom).
    ax.bar(
        positions,
        det_fracs,
        width=bar_width,
        label="deterministic",
        color=style.BLUE_PRIMARY,
        zorder=3,
    )
    # Draw LLM segment (stacked on top of deterministic).
    ax.bar(
        positions,
        llm_fracs,
        width=bar_width,
        bottom=det_fracs,
        label="llm",
        color=style.ORANGE_ACCENT,
        zorder=3,
    )

    # Production target reference line at 5% LLM (from top).
    target_y = 100.0 - _LLM_TARGET_PCT
    ax.axhline(
        y=target_y,
        color=style.RED_ACCENT,
        linewidth=1.4,
        linestyle="--",
        zorder=4,
    )
    # Label the target line just above the bars (every bar is 100%-stacked,
    # so the only clear space is above the 100% top — never over the data).
    ax.text(
        n - 0.5,
        101.0,
        f"target: <={_LLM_TARGET_PCT:g}% LLM ({target_y:.0f}% det.)",
        ha="right",
        va="bottom",
        fontsize=9,
        color=style.RED_ACCENT,
        clip_on=False,
    )

    ax.set_xticks(positions)
    ax.set_xticklabels(date_labels, rotation=0, ha="center", fontsize=9)
    ax.set_ylim(0, 100)
    ax.set_ylabel("routing share (%)", color=style.GRAY_SUBTITLE, fontsize=10)

    ax.legend(
        loc="lower right",
        fontsize=9,
        framealpha=0.85,
    )

    subtitle = (
        f"deterministic vs LLM  |  production target: <={_LLM_TARGET_PCT:g}% LLM"
        f"  |  sample size: {data.sample_size}"
    )
    style.set_title(ax, "Daily routing-path mix", subtitle)
    style.add_footnote(
        fig,
        f"Sample size: {data.sample_size}. Time range: {data.time_range}. "
        "Data generated during testing; missing days collapsed. "
        "Source: charts/data/chart10-routing-path-daily.json.",
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=100, bbox_inches="tight", facecolor="white")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Production renderer
# ---------------------------------------------------------------------------


def render_routing_path_daily_chart(output_path: Path) -> None:
    """Production renderer: reads the committed data file and renders to *output_path*.

    Data source: ``charts/data/chart10-routing-path-daily.json`` (committed;
    representative snapshot with footnote annotation "data generated during testing;
    missing days collapsed"). 2026-05-23 and 2026-05-24 are deliberately absent.
    """
    data_path = Path(__file__).resolve().parents[3] / "data" / "chart10-routing-path-daily.json"
    data = load_routing_path_daily(data_path)
    render_routing_path_daily(data, output_path)
