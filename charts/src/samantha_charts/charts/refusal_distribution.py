"""Chart 9: refusal-type distribution.

Renders a horizontal bar chart showing how often each refusal type fires
when samantha_server cannot proceed. Known refusal types are displayed in
blue; any novel refused_* outcome is bucketed under "other" in orange.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt

from samantha_charts import style

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

KNOWN_REFUSAL_TYPES: tuple[str, ...] = (
    "refused_llm_unavailable",
    "refused_phi_boundary",
    "refused_no_skill_for_state",
    "refused_skill_unavailable",
    "refused_ungrounded_stage_a",
    "refused_ungrounded_stage_b",
    "refused_uncertain",
    "refused_unparseable",
    "refused_unparseable_response",
    "refused_rule_not_in_dispatch",
    "refused_undispatched_rule",
    "refused_internal_error",
)

# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def aggregate_refusal_types(traces: list[dict[str, Any]]) -> dict[str, int]:
    """Count refusal-type outcomes from a list of Langfuse traces.

    Parameters
    ----------
    traces:
        List of trace dicts, each optionally carrying
        ``{"metadata": {"outcome": "<str>"}}``. Traces lacking metadata,
        lacking an outcome key, or with a None outcome are silently ignored
        (treated as non-refused).

    Returns
    -------
    dict[str, int]
        All 8 known refusal-type keys (0 if absent) plus an ``"other"`` key
        for any unrecognized ``refused_*`` outcome. Non-refused outcomes are
        not counted.
    """
    counts: dict[str, int] = {k: 0 for k in KNOWN_REFUSAL_TYPES}
    counts["other"] = 0

    for trace in traces:
        metadata = trace.get("metadata")
        if not isinstance(metadata, dict):
            continue
        outcome = metadata.get("outcome")
        if outcome is None:
            continue
        if not isinstance(outcome, str):
            continue
        if not outcome.startswith("refused_"):
            continue
        if outcome in counts:
            counts[outcome] += 1
        else:
            counts["other"] += 1

    return counts


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RefusalDistribution:
    """Aggregated refusal counts with provenance metadata.

    Parameters
    ----------
    counts:
        Mapping of refusal type to count. Must contain exactly all 12 known
        types plus ``"other"``; raises ValueError if keys are missing or extra.
    sample_size:
        Total number of traces the distribution was drawn from.
    time_range:
        Human-readable date range string for the footnote.
    """

    counts: dict[str, int]
    sample_size: int
    time_range: str

    def __post_init__(self) -> None:
        expected = set(KNOWN_REFUSAL_TYPES) | {"other"}
        actual = set(self.counts.keys())
        missing = expected - actual
        extra = actual - expected
        if missing or extra:
            raise ValueError(
                f"RefusalDistribution.counts keys do not match KNOWN_REFUSAL_TYPES | {{'other'}}. "
                f"Missing: {sorted(missing)}. Extra: {sorted(extra)}."
            )


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_refusal_distribution(path: Path) -> RefusalDistribution:
    """Read *path* (JSON) and return a RefusalDistribution.

    Parameters
    ----------
    path:
        Path to a JSON file with a top-level ``"refusal_counts"`` key and
        a ``"_meta"`` object carrying ``sample_size`` and ``time_range``.

    Returns
    -------
    RefusalDistribution

    Raises
    ------
    ValueError
        If ``"refusal_counts"`` is missing, or if ``_meta.sample_size`` or
        ``_meta.time_range`` are absent.
    """
    raw: dict[str, Any] = json.loads(path.read_text())

    refusal_counts = raw.get("refusal_counts")
    if refusal_counts is None:
        raise ValueError(f"Expected a 'refusal_counts' mapping in {path}; key not found.")
    if not isinstance(refusal_counts, dict):
        raise ValueError(
            f"Expected 'refusal_counts' to be a dict in {path}; "
            f"got {type(refusal_counts).__name__!r}."
        )

    meta: dict[str, Any] = raw.get("_meta", {})
    if "sample_size" not in meta:
        raise ValueError(f"Expected '_meta.sample_size' in {path}; key not found.")
    if "time_range" not in meta:
        raise ValueError(f"Expected '_meta.time_range' in {path}; key not found.")

    return RefusalDistribution(
        counts=dict(refusal_counts),
        sample_size=int(meta["sample_size"]),
        time_range=str(meta["time_range"]),
    )


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


def render_refusal_distribution(dist: RefusalDistribution, output_path: Path) -> None:
    """Render a horizontal bar chart of refusal-type counts to *output_path* (PNG).

    Bars are sorted descending by count (highest count at top, as barh draws
    bottom-up). Known refusal types are colored BLUE_PRIMARY; the "other"
    bucket is colored ORANGE_ACCENT to distinguish it visually.

    Parameters
    ----------
    dist:
        Aggregated refusal distribution to plot.
    output_path:
        Destination PNG path. Parent directory is created if needed.

    Raises
    ------
    ValueError
        If all counts are zero (nothing to render).
    """
    if sum(dist.counts.values()) == 0:
        raise ValueError("render_refusal_distribution: all counts are zero — no data to render.")

    # Sort ascending so barh puts the highest count at top.
    ordered = sorted(dist.counts.items(), key=lambda kv: kv[1])
    labels = [kv[0] for kv in ordered]
    values = [kv[1] for kv in ordered]
    colors = [style.ORANGE_ACCENT if label == "other" else style.BLUE_PRIMARY for label in labels]

    fig, ax = plt.subplots(figsize=(11, 7))
    style.apply_style(fig, ax)
    # Horizontal bars: gridlines on x-axis.
    ax.yaxis.grid(False)
    ax.xaxis.grid(True, color=style.GRAY_GRID, linewidth=0.8, zorder=0)

    positions = list(range(len(labels)))
    ax.barh(positions, values, color=colors, height=0.62, zorder=3)

    ax.set_yticks(positions)
    ax.set_yticklabels(labels, fontsize=10)

    # Count label at the right end of each bar.
    x_max = max(values)
    for pos, val in zip(positions, values, strict=True):
        ax.text(
            val + x_max * 0.01,
            pos,
            str(val),
            va="center",
            ha="left",
            fontsize=10,
            fontweight="bold",
            color=style.GRAY_SUBTITLE,
            zorder=4,
        )

    ax.set_xlabel("count", color=style.GRAY_SUBTITLE, fontsize=10)
    # Give count labels a little breathing room on the right.
    ax.set_xlim(0, x_max * 1.12)

    style.set_title(
        ax,
        "Refusal-type distribution",
        f"samantha_server refusal outcomes  |  sample size: {dist.sample_size}",
    )
    style.add_footnote(
        fig,
        f"Sample size: {dist.sample_size}. Time range: {dist.time_range}. "
        "Data generated during testing. "
        "Source: charts/data/chart9-refusal-types.json.",
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=100, bbox_inches="tight", facecolor="white")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Production renderer
# ---------------------------------------------------------------------------


def render_refusal_distribution_chart(output_path: Path) -> None:
    """Production renderer: reads the committed data file and renders to *output_path*.

    Data source: ``charts/data/chart9-refusal-types.json`` (committed;
    representative snapshot with footnote annotation "data generated during testing").
    """
    data_path = Path(__file__).resolve().parents[3] / "data" / "chart9-refusal-types.json"
    dist = load_refusal_distribution(data_path)
    render_refusal_distribution(dist, output_path)
