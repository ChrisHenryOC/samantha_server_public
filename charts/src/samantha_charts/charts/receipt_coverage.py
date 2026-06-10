"""Chart 13: receipt-coverage hero Big Number.

Renders a hero card showing the total decision count and 100% receipt coverage.
Every routing decision in the sweep has a corresponding signed receipt and a
Langfuse trace, proving cryptographic auditability at full coverage.
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
class ReceiptCoverage:
    """Aggregated receipt coverage statistics for a time window."""

    decisions: int
    receipts: int
    traces: int
    coverage_pct: float
    time_range: str


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

_REQUIRED_KEYS = ("decisions", "receipts", "traces", "coverage_pct")


def load_receipt_coverage(path: Path) -> ReceiptCoverage:
    """Read *path* (JSON) and return a ReceiptCoverage.

    Parameters
    ----------
    path:
        Path to a JSON file with top-level ``decisions``, ``receipts``,
        ``traces``, ``coverage_pct``, and ``_meta.time_range`` keys.

    Raises
    ------
    ValueError
        If any required key is missing.
    """
    raw: dict[str, Any] = json.loads(path.read_text())

    for key in _REQUIRED_KEYS:
        if key not in raw:
            raise ValueError(f"Missing required key {key!r} in {path}")

    meta = raw.get("_meta", {})
    time_range = meta.get("time_range", "")

    return ReceiptCoverage(
        decisions=int(raw["decisions"]),
        receipts=int(raw["receipts"]),
        traces=int(raw["traces"]),
        coverage_pct=float(raw["coverage_pct"]),
        time_range=time_range,
    )


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


def render_receipt_coverage(data: ReceiptCoverage, output_path: Path) -> None:
    """Render a receipt-coverage hero Big Number card to *output_path* (PNG).

    Shows the total decision count as a huge hero number, the coverage
    percentage, and a subtitle confirming every routing decision is
    cryptographically auditable.

    Parameters
    ----------
    data:
        A loaded ReceiptCoverage.
    output_path:
        Destination PNG path. Parent directory is created if needed.

    Raises
    ------
    ValueError
        If ``data.receipts != data.traces`` -- any coverage mismatch is a bug,
        not something to render.
    """
    if data.receipts != data.traces:
        raise ValueError(
            f"Coverage mismatch: receipts={data.receipts} != traces={data.traces}."
            " Every receipt must have a matching trace."
        )
    if data.receipts != data.decisions:
        raise ValueError(
            f"Coverage mismatch: receipts={data.receipts} != decisions={data.decisions}."
            " Every decision must have a matching receipt."
        )

    fig, ax = plt.subplots(figsize=(10, 6))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)

    import matplotlib.patches as mpatches

    # Card background
    bg = mpatches.FancyBboxPatch(
        (0.05, 0.05),
        0.90,
        0.82,
        boxstyle="round,pad=0.02",
        facecolor="#F0FDF4",
        edgecolor=style.GREEN_ACCENT,
        linewidth=2.0,
        transform=ax.transAxes,
        zorder=1,
    )
    ax.add_patch(bg)

    # Hero number
    ax.text(
        0.5,
        0.68,
        f"{data.decisions:,}",
        transform=ax.transAxes,
        fontsize=72,
        fontweight="bold",
        color=style.BLUE_PRIMARY,
        ha="center",
        va="center",
        zorder=3,
    )

    # Decision + coverage line
    ax.text(
        0.5,
        0.46,
        f"decisions, {data.coverage_pct:g}% with signed receipts",
        transform=ax.transAxes,
        fontsize=14,
        color="#111827",
        ha="center",
        va="center",
        zorder=3,
    )

    # Subtitle
    ax.text(
        0.5,
        0.32,
        "every routing decision is cryptographically auditable",
        transform=ax.transAxes,
        fontsize=11,
        color=style.GRAY_SUBTITLE,
        fontstyle="italic",
        ha="center",
        va="center",
        zorder=3,
    )

    # Receipt = trace = decisions confirmation line
    ax.text(
        0.5,
        0.18,
        f"{data.receipts:,} receipts = {data.traces:,} traces = {data.decisions:,} decisions",
        transform=ax.transAxes,
        fontsize=10,
        color=style.GREEN_ACCENT,
        fontweight="bold",
        ha="center",
        va="center",
        zorder=3,
    )

    style.set_title(
        ax,
        "Receipt coverage",
        f"{data.time_range}",
    )
    style.add_footnote(
        fig,
        "Coverage = receipts / decisions. 100% means every routing decision has a signed,"
        " verifiable audit record. Source: charts/data/chart13-receipt-coverage.json.",
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=100, bbox_inches="tight", facecolor="white")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Production renderer
# ---------------------------------------------------------------------------


def render_receipt_coverage_chart(output_path: Path) -> None:
    """Production renderer: reads chart13-receipt-coverage.json and renders to *output_path*.

    Data source: ``charts/data/chart13-receipt-coverage.json`` (committed).
    """
    data_path = Path(__file__).resolve().parents[3] / "data" / "chart13-receipt-coverage.json"
    data = load_receipt_coverage(data_path)
    render_receipt_coverage(data, output_path)
