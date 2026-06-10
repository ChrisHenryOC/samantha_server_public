"""Chart 13: receipt-coverage hero Big Number."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

# ---------------------------------------------------------------------------
# Slice 8: ReceiptCoverage dataclass + load_receipt_coverage
# ---------------------------------------------------------------------------


def _make_valid_json(
    decisions: int = 4545,
    receipts: int = 4545,
    traces: int = 4545,
    coverage_pct: float = 100.0,
) -> dict[str, Any]:
    return {
        "_meta": {
            "time_range": "2026-05-26T23:15:00Z to 2026-05-26T23:26:00Z",
            "source": "receipts sqlite + Langfuse",
        },
        "decisions": decisions,
        "receipts": receipts,
        "traces": traces,
        "coverage_pct": coverage_pct,
    }


def test_load_receipt_coverage_roundtrip(tmp_path: Path) -> None:
    from samantha_charts.charts.receipt_coverage import ReceiptCoverage, load_receipt_coverage

    data = _make_valid_json()
    p = tmp_path / "chart13.json"
    p.write_text(json.dumps(data))

    result = load_receipt_coverage(p)

    assert isinstance(result, ReceiptCoverage)
    assert result.decisions == 4545
    assert result.receipts == 4545
    assert result.traces == 4545
    assert result.coverage_pct == pytest.approx(100.0)
    assert "2026-05-26" in result.time_range


def test_load_receipt_coverage_raises_on_missing_decisions(tmp_path: Path) -> None:
    from samantha_charts.charts.receipt_coverage import load_receipt_coverage

    data = _make_valid_json()
    del data["decisions"]
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(data))

    with pytest.raises(ValueError, match="decisions"):
        load_receipt_coverage(p)


def test_load_receipt_coverage_raises_on_missing_receipts(tmp_path: Path) -> None:
    from samantha_charts.charts.receipt_coverage import load_receipt_coverage

    data = _make_valid_json()
    del data["receipts"]
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(data))

    with pytest.raises(ValueError, match="receipts"):
        load_receipt_coverage(p)


def test_load_receipt_coverage_raises_on_missing_coverage_pct(tmp_path: Path) -> None:
    from samantha_charts.charts.receipt_coverage import load_receipt_coverage

    data = _make_valid_json()
    del data["coverage_pct"]
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(data))

    with pytest.raises(ValueError, match="coverage_pct"):
        load_receipt_coverage(p)


# ---------------------------------------------------------------------------
# Slice 9: render_receipt_coverage -- produces PNG, rejects coverage mismatch
# ---------------------------------------------------------------------------


def _make_coverage(
    decisions: int = 100,
    receipts: int = 100,
    traces: int = 100,
    coverage_pct: float = 100.0,
) -> Any:
    from samantha_charts.charts.receipt_coverage import ReceiptCoverage

    return ReceiptCoverage(
        decisions=decisions,
        receipts=receipts,
        traces=traces,
        coverage_pct=coverage_pct,
        time_range="2026-01-01 to 2026-01-02",
    )


def test_render_receipt_coverage_produces_png(tmp_path: Path) -> None:
    from samantha_charts.charts.receipt_coverage import render_receipt_coverage

    data = _make_coverage()
    out = tmp_path / "chart13.png"
    render_receipt_coverage(data, out)

    raw = out.read_bytes()
    assert raw.startswith(_PNG_MAGIC)
    assert len(raw) > 1000


def test_render_receipt_coverage_raises_on_mismatch(tmp_path: Path) -> None:
    from samantha_charts.charts.receipt_coverage import render_receipt_coverage

    data = _make_coverage(decisions=100, receipts=99, traces=99, coverage_pct=99.0)
    with pytest.raises(ValueError, match="receipts"):
        render_receipt_coverage(data, tmp_path / "x.png")


def test_render_receipt_coverage_raises_when_traces_differ_from_receipts(tmp_path: Path) -> None:
    from samantha_charts.charts.receipt_coverage import render_receipt_coverage

    data = _make_coverage(decisions=100, receipts=100, traces=95, coverage_pct=100.0)
    with pytest.raises(ValueError):
        render_receipt_coverage(data, tmp_path / "x.png")


# ---------------------------------------------------------------------------
# Slice 10: render_receipt_coverage_chart -- production renderer
# ---------------------------------------------------------------------------


def test_render_receipt_coverage_chart_writes_png(tmp_path: Path) -> None:
    from samantha_charts.charts.receipt_coverage import render_receipt_coverage_chart

    out = tmp_path / "chart13.png"
    render_receipt_coverage_chart(out)

    raw = out.read_bytes()
    assert raw.startswith(_PNG_MAGIC)
    assert len(raw) > 1000
