"""Chart 11: single signed-receipt evidence card.

Renders a single signed receipt as a labeled card, showing every field plus
a cryptographic-signature badge and a human-readable summary of the primitive
trace that produced the decision.
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

_REQUIRED_RECEIPT_KEYS = (
    "receipt_id",
    "scenario_id",
    "applied_rule_id",
    "outcome",
    "next_state",
    "signer_key_id",
    "signature_truncated",
    "signature_bytes",
    "signed_at_utc",
    "latency_us",
    "primitive_traces",
    "verification",
)


@dataclass(frozen=True)
class ReceiptSample:
    """One signed receipt extracted from a real sweep run."""

    receipt_id: str
    scenario_id: str
    applied_rule_id: str
    outcome: str
    next_state: str
    signer_key_id: str
    signature_truncated: str
    signature_bytes: int
    signed_at_utc: str
    latency_us: int
    primitive_traces: dict[str, Any]
    verification: str


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_receipt_sample(path: Path) -> ReceiptSample:
    """Read *path* (JSON) and return a ReceiptSample.

    Parameters
    ----------
    path:
        Path to a JSON file with a top-level ``"receipt"`` key containing
        the receipt fields.

    Raises
    ------
    ValueError
        If ``"receipt"`` key is absent or any required sub-key is missing.
    """
    raw: dict[str, Any] = json.loads(path.read_text())
    receipt = raw.get("receipt")
    if receipt is None:
        raise ValueError(f"Expected a 'receipt' key in {path}; got keys: {list(raw.keys())!r}")

    for key in _REQUIRED_RECEIPT_KEYS:
        if key not in receipt:
            raise ValueError(f"Missing required receipt field {key!r} in {path}")

    return ReceiptSample(
        receipt_id=receipt["receipt_id"],
        scenario_id=receipt["scenario_id"],
        applied_rule_id=receipt["applied_rule_id"],
        outcome=receipt["outcome"],
        next_state=receipt["next_state"],
        signer_key_id=receipt["signer_key_id"],
        signature_truncated=receipt["signature_truncated"],
        signature_bytes=int(receipt["signature_bytes"]),
        signed_at_utc=receipt["signed_at_utc"],
        latency_us=int(receipt["latency_us"]),
        primitive_traces=dict(receipt["primitive_traces"]),
        verification=receipt["verification"],
    )


# ---------------------------------------------------------------------------
# Primitive-trace summarizer
# ---------------------------------------------------------------------------


def summarize_primitive_traces(pt: dict[str, Any]) -> list[str]:
    """Flatten nested primitive_traces into short human-readable lines.

    Walks the trace tree depth-first, collecting leaf nodes (nodes with no
    children or only empty-children lists) into one line each. Returns at
    most 12 lines to keep the card readable.

    Parameters
    ----------
    pt:
        The primitive_traces dict from a ReceiptSample. May be empty.

    Returns
    -------
    list[str]
        Human-readable summary lines, one per leaf evaluation.
    """
    if not pt:
        return []

    lines: list[str] = []
    _MAX_LINES = 12

    def _walk(node: dict[str, Any], rule_id: str) -> None:
        children = node.get("children") or []
        primitive = node.get("primitive", "?")
        field = node.get("field")
        result = node.get("result")
        expected = node.get("expected")

        result_str = "true" if result else "false"

        if not children:
            # Leaf node — emit one summary line.
            if field is not None:
                if expected is not None:
                    lines.append(f"{field} {primitive} {expected!r} -> {result_str}")
                else:
                    lines.append(f"{field} {primitive} -> {result_str}")
            else:
                lines.append(f"[{rule_id}] {primitive} -> {result_str}")
        else:
            for child in children:
                if len(lines) >= _MAX_LINES:
                    break
                _walk(child, rule_id)

    for rule_id, node in pt.items():
        if len(lines) >= _MAX_LINES:
            break
        _walk(node, rule_id)

    return lines


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


def render_receipt_sample(sample: ReceiptSample, output_path: Path) -> None:
    """Render a single signed-receipt evidence card to *output_path* (PNG).

    Lays out the receipt as labeled rows in an axis-less card style, with a
    green signature-valid badge when verification == "valid" and a section
    showing the primitive-trace summary.

    Parameters
    ----------
    sample:
        A loaded ReceiptSample.
    output_path:
        Destination PNG path. Parent directory is created if needed.
    """
    fig, ax = plt.subplots(figsize=(11, 8))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)

    # Card background
    import matplotlib.patches as mpatches

    bg = mpatches.FancyBboxPatch(
        (0.02, 0.02),
        0.96,
        0.90,
        boxstyle="round,pad=0.02",
        facecolor="#F8FAFC",
        edgecolor=style.GRAY_GRID,
        linewidth=1.5,
        transform=ax.transAxes,
        zorder=1,
    )
    ax.add_patch(bg)

    # Signature badge
    if sample.verification == "valid":
        badge_text = f"signature valid (Ed25519, key {sample.signer_key_id})"
        badge_color = style.GREEN_ACCENT
    else:
        badge_text = f"signature {sample.verification}"
        badge_color = style.RED_ACCENT

    ax.text(
        0.5,
        0.90,
        badge_text,
        transform=ax.transAxes,
        fontsize=10,
        fontweight="bold",
        color=badge_color,
        ha="center",
        va="center",
        zorder=3,
    )

    # Labeled receipt rows
    label_x = 0.06
    value_x = 0.38
    row_start = 0.82
    row_step = 0.075

    rows: list[tuple[str, str]] = [
        ("Receipt ID", sample.receipt_id),
        ("Scenario", sample.scenario_id),
        ("Rule applied", sample.applied_rule_id),
        ("Outcome", sample.outcome),
        ("Next state", sample.next_state),
        ("Signed at (UTC)", sample.signed_at_utc),
        ("Key ID", sample.signer_key_id),
        ("Latency", f"{sample.latency_us} us"),
    ]

    for i, (label, value) in enumerate(rows):
        y = row_start - i * row_step
        ax.text(
            label_x,
            y,
            label,
            transform=ax.transAxes,
            fontsize=10,
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
            fontsize=10,
            color="#111827",
            ha="left",
            va="center",
            zorder=3,
            family="monospace" if label in ("Receipt ID", "Key ID") else "sans-serif",
        )

    # Signature truncated
    sig_y = row_start - len(rows) * row_step
    ax.text(
        label_x,
        sig_y,
        "Signature",
        transform=ax.transAxes,
        fontsize=10,
        color=style.GRAY_SUBTITLE,
        ha="left",
        va="center",
        zorder=3,
    )
    ax.text(
        value_x,
        sig_y,
        sample.signature_truncated,
        transform=ax.transAxes,
        fontsize=9,
        color="#374151",
        ha="left",
        va="center",
        zorder=3,
        family="monospace",
    )

    # Primitive trace summary section
    trace_lines = summarize_primitive_traces(sample.primitive_traces)
    if trace_lines:
        divider_y = sig_y - 0.06
        ax.plot(
            [0.04, 0.96],
            [divider_y, divider_y],
            color=style.GRAY_GRID,
            linewidth=0.8,
            transform=ax.transAxes,
            zorder=2,
        )
        ax.text(
            label_x,
            divider_y - 0.03,
            "What was decided:",
            transform=ax.transAxes,
            fontsize=9,
            color=style.GRAY_SUBTITLE,
            fontweight="bold",
            ha="left",
            va="center",
            zorder=3,
        )
        for j, line in enumerate(trace_lines[:6]):
            ax.text(
                label_x + 0.02,
                divider_y - 0.07 - j * 0.045,
                line,
                transform=ax.transAxes,
                fontsize=8,
                color="#374151",
                ha="left",
                va="center",
                zorder=3,
            )

    style.set_title(
        ax,
        "Signed audit receipt",
        f"rule {sample.applied_rule_id} applied to scenario {sample.scenario_id}",
    )
    style.add_footnote(
        fig,
        "Real receipt from the convex-incumbent sweep (2026-05-26)."
        " Every routing decision produces a cryptographically signed record."
        " Source: charts/data/chart11-receipt-sample.json.",
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=100, bbox_inches="tight", facecolor="white")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Production renderer
# ---------------------------------------------------------------------------


def render_receipt_sample_chart(output_path: Path) -> None:
    """Production renderer: reads chart11-receipt-sample.json and renders to *output_path*.

    Data source: ``charts/data/chart11-receipt-sample.json`` (committed).
    """
    data_path = Path(__file__).resolve().parents[3] / "data" / "chart11-receipt-sample.json"
    sample = load_receipt_sample(data_path)
    render_receipt_sample(sample, output_path)
