"""Chart 12: receipt vs Langfuse trace, same decision.

Renders a dual-column card showing the same decision from two perspectives:
the signed audit receipt and the Langfuse operator trace. The shared
event_input_hash is displayed prominently as the join key linking the columns.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt

from samantha_charts import style

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReceiptVsTrace:
    """A matched receipt and Langfuse trace for the same decision."""

    receipt: dict[str, Any]
    trace: dict[str, Any]
    join_key: str
    event_input_hash: str


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_receipt_vs_trace(path: Path) -> ReceiptVsTrace:
    """Read *path* (JSON) and return a ReceiptVsTrace.

    Parameters
    ----------
    path:
        Path to a JSON file with top-level ``"receipt"``, ``"trace"``,
        and ``"_meta"`` keys. ``_meta`` must contain ``"join_key"`` and
        ``"event_input_hash"``. The trace's ``"event_input_hash"`` must
        equal ``_meta.event_input_hash`` (this is the chart's whole point).

    Raises
    ------
    ValueError
        If any required key is missing or the join does not hold.
    """
    raw: dict[str, Any] = json.loads(path.read_text())

    if "receipt" not in raw:
        raise ValueError(f"Missing 'receipt' key in {path}")
    if "trace" not in raw:
        raise ValueError(f"Missing 'trace' key in {path}")

    meta = raw.get("_meta", {})
    if "join_key" not in meta:
        raise ValueError(f"Missing '_meta.join_key' in {path}")
    if "event_input_hash" not in meta:
        raise ValueError(f"Missing '_meta.event_input_hash' in {path}")

    meta_hash = meta["event_input_hash"]
    trace_hash = raw["trace"].get("event_input_hash")
    if trace_hash != meta_hash:
        raise ValueError(
            f"event_input_hash join mismatch: trace has {trace_hash!r},"
            f" _meta has {meta_hash!r} in {path}"
        )

    return ReceiptVsTrace(
        receipt=dict(raw["receipt"]),
        trace=dict(raw["trace"]),
        join_key=meta["join_key"],
        event_input_hash=meta_hash,
    )


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------

_COL_LABELS = {
    "receipt": "Receipt (audit surface)",
    "trace": "Langfuse trace (operator surface)",
}

_RECEIPT_ROWS: list[tuple[str, str]] = [
    ("Receipt ID", "receipt_id"),
    ("Rule applied", "applied_rule_id"),
    ("Outcome", "outcome"),
    ("Next state", "next_state"),
    ("Key ID", "signer_key_id"),
    ("Signed at", "signed_at_utc"),
]

_TRACE_ROWS: list[tuple[str, str]] = [
    ("Trace ID", "trace_id"),
    ("Scenario", "name"),
    ("Outcome", "outcome"),
    ("Next state", "next_state"),
    ("Routing path", "routing_path"),
    ("Timestamp", "timestamp"),
]


def render_receipt_vs_trace(data: ReceiptVsTrace, output_path: Path) -> None:
    """Render a dual receipt|trace card to *output_path* (PNG).

    Left column shows the receipt fields; right column shows the Langfuse
    trace fields. The shared event_input_hash is rendered prominently as the
    join key linking both columns.

    Parameters
    ----------
    data:
        A loaded ReceiptVsTrace.
    output_path:
        Destination PNG path. Parent directory is created if needed.
    """
    fig = plt.figure(figsize=(14, 8))
    fig.patch.set_facecolor("white")

    # Two card axes side by side; leave room for title + join key + footnote
    ax_left = fig.add_axes((0.03, 0.12, 0.44, 0.68))
    ax_right = fig.add_axes((0.53, 0.12, 0.44, 0.68))

    for ax, col_key, col_rows in (
        (ax_left, "receipt", _RECEIPT_ROWS),
        (ax_right, "trace", _TRACE_ROWS),
    ):
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.axis("off")

        # Card background
        bg = mpatches.FancyBboxPatch(
            (0.02, 0.02),
            0.96,
            0.96,
            boxstyle="round,pad=0.02",
            facecolor="#F8FAFC",
            edgecolor=style.BLUE_LIGHT,
            linewidth=1.5,
            transform=ax.transAxes,
            zorder=1,
        )
        ax.add_patch(bg)

        # Column header
        ax.text(
            0.5,
            0.93,
            _COL_LABELS[col_key],
            transform=ax.transAxes,
            fontsize=11,
            fontweight="bold",
            color=style.BLUE_PRIMARY,
            ha="center",
            va="center",
            zorder=3,
        )

        source = data.receipt if col_key == "receipt" else data.trace
        label_x = 0.06
        value_x = 0.42
        row_start = 0.82
        row_step = 0.115

        for i, (label, field) in enumerate(col_rows):
            y = row_start - i * row_step
            value = str(source.get(field, ""))
            ax.text(
                label_x,
                y,
                label,
                transform=ax.transAxes,
                fontsize=9,
                color=style.GRAY_SUBTITLE,
                ha="left",
                va="center",
                zorder=3,
            )
            ax.text(
                value_x,
                y,
                value,
                transform=ax.transAxes,
                fontsize=9,
                color="#111827",
                ha="left",
                va="center",
                zorder=3,
            )

    # Join key rendered prominently in the center gap
    # Truncate the hash for readability
    hash_display = data.event_input_hash[:20] + "..." + data.event_input_hash[-8:]
    fig.text(
        0.5,
        0.10,
        f"shared event_input_hash: {hash_display}",
        transform=fig.transFigure,
        fontsize=9,
        color=style.BLUE_PRIMARY,
        fontweight="bold",
        ha="center",
        va="center",
    )

    # Title
    fig.text(
        0.03,
        0.88,
        "Receipt vs. Langfuse trace, same decision",
        transform=fig.transFigure,
        fontsize=16,
        fontweight="bold",
        color="#111827",
        va="bottom",
        ha="left",
    )
    fig.text(
        0.03,
        0.84,
        "receipts = audit surface; traces = operator surface; join key = event_input_hash",
        transform=fig.transFigure,
        fontsize=10,
        color=style.GRAY_SUBTITLE,
        va="bottom",
        ha="left",
    )

    style.add_footnote(
        fig,
        "Real data from the convex-incumbent sweep (2026-05-26)."
        " Both rows describe the same routing decision;"
        " the hash proves it is the same event input."
        " Source: charts/data/chart12-receipt-vs-trace.json.",
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=100, bbox_inches="tight", facecolor="white")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Production renderer
# ---------------------------------------------------------------------------


def render_receipt_vs_trace_chart(output_path: Path) -> None:
    """Production renderer: reads chart12-receipt-vs-trace.json and renders to *output_path*.

    Data source: ``charts/data/chart12-receipt-vs-trace.json`` (committed).
    """
    data_path = Path(__file__).resolve().parents[3] / "data" / "chart12-receipt-vs-trace.json"
    data = load_receipt_vs_trace(data_path)
    render_receipt_vs_trace(data, output_path)
