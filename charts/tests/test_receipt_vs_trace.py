"""Chart 12 (GH-311): receipt vs Langfuse trace dual-column card."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

_HASH = "2cb5603bc342ff93ff5c3f0336ba546c88ddf3a178a68dc5d855edcc6d02b4ee"

# ---------------------------------------------------------------------------
# Slice 5: ReceiptVsTrace dataclass + load_receipt_vs_trace
# ---------------------------------------------------------------------------


def _make_valid_json(event_input_hash: str = _HASH) -> dict[str, Any]:
    return {
        "_meta": {
            "join_key": "event_input_hash",
            "event_input_hash": event_input_hash,
            "source": "test",
        },
        "receipt": {
            "receipt_id": "01TEST",
            "applied_rule_id": "ACC-001",
            "outcome": "passed",
            "next_state": "COMPLETE",
            "signer_key_id": "v1",
            "signed_at_utc": "2026-01-01T00:00:00+00:00",
        },
        "trace": {
            "trace_id": "abc123",
            "name": "SC-001",
            "timestamp": "2026-01-01T00:00:00Z",
            "outcome": "passed",
            "routing_path": "deterministic",
            "next_state": "COMPLETE",
            "event_input_hash": event_input_hash,
        },
    }


def test_load_receipt_vs_trace_roundtrip(tmp_path: Path) -> None:
    from samantha_charts.charts.receipt_vs_trace import ReceiptVsTrace, load_receipt_vs_trace

    data = _make_valid_json()
    p = tmp_path / "chart12.json"
    p.write_text(json.dumps(data))

    result = load_receipt_vs_trace(p)

    assert isinstance(result, ReceiptVsTrace)
    assert result.receipt["receipt_id"] == "01TEST"
    assert result.trace["trace_id"] == "abc123"
    assert result.join_key == "event_input_hash"
    assert result.event_input_hash == _HASH


def test_load_receipt_vs_trace_raises_on_missing_receipt(tmp_path: Path) -> None:
    from samantha_charts.charts.receipt_vs_trace import load_receipt_vs_trace

    data = _make_valid_json()
    del data["receipt"]
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(data))

    with pytest.raises(ValueError, match="receipt"):
        load_receipt_vs_trace(p)


def test_load_receipt_vs_trace_raises_on_missing_trace(tmp_path: Path) -> None:
    from samantha_charts.charts.receipt_vs_trace import load_receipt_vs_trace

    data = _make_valid_json()
    del data["trace"]
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(data))

    with pytest.raises(ValueError, match="trace"):
        load_receipt_vs_trace(p)


def test_load_receipt_vs_trace_raises_on_missing_join_key(tmp_path: Path) -> None:
    from samantha_charts.charts.receipt_vs_trace import load_receipt_vs_trace

    data = _make_valid_json()
    del data["_meta"]["join_key"]
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(data))

    with pytest.raises(ValueError, match="join_key"):
        load_receipt_vs_trace(p)


def test_load_receipt_vs_trace_raises_on_missing_event_input_hash_meta(tmp_path: Path) -> None:
    from samantha_charts.charts.receipt_vs_trace import load_receipt_vs_trace

    data = _make_valid_json()
    del data["_meta"]["event_input_hash"]
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(data))

    with pytest.raises(ValueError, match="event_input_hash"):
        load_receipt_vs_trace(p)


def test_load_receipt_vs_trace_raises_on_join_mismatch(tmp_path: Path) -> None:
    from samantha_charts.charts.receipt_vs_trace import load_receipt_vs_trace

    data = _make_valid_json()
    # Force trace hash to be different from _meta hash
    data["trace"]["event_input_hash"] = "deadbeef"
    p = tmp_path / "mismatch.json"
    p.write_text(json.dumps(data))

    with pytest.raises(ValueError, match="event_input_hash"):
        load_receipt_vs_trace(p)


# ---------------------------------------------------------------------------
# Slice 6: render_receipt_vs_trace -- produces PNG
# ---------------------------------------------------------------------------


def _make_data() -> Any:
    from samantha_charts.charts.receipt_vs_trace import ReceiptVsTrace

    return ReceiptVsTrace(
        receipt={
            "receipt_id": "01TEST",
            "applied_rule_id": "ACC-001",
            "outcome": "passed",
            "next_state": "COMPLETE",
            "signer_key_id": "v1",
            "signed_at_utc": "2026-01-01T00:00:00+00:00",
        },
        trace={
            "trace_id": "abc123",
            "name": "SC-001",
            "timestamp": "2026-01-01T00:00:00Z",
            "outcome": "passed",
            "routing_path": "deterministic",
            "next_state": "COMPLETE",
            "event_input_hash": _HASH,
        },
        join_key="event_input_hash",
        event_input_hash=_HASH,
    )


def test_render_receipt_vs_trace_produces_png(tmp_path: Path) -> None:
    from samantha_charts.charts.receipt_vs_trace import render_receipt_vs_trace

    data = _make_data()
    out = tmp_path / "chart12.png"
    render_receipt_vs_trace(data, out)

    raw = out.read_bytes()
    assert raw.startswith(_PNG_MAGIC)
    assert len(raw) > 1000


# ---------------------------------------------------------------------------
# Slice 7: render_receipt_vs_trace_chart -- production renderer
# ---------------------------------------------------------------------------


def test_render_receipt_vs_trace_chart_writes_png(tmp_path: Path) -> None:
    from samantha_charts.charts.receipt_vs_trace import render_receipt_vs_trace_chart

    out = tmp_path / "chart12.png"
    render_receipt_vs_trace_chart(out)

    raw = out.read_bytes()
    assert raw.startswith(_PNG_MAGIC)
    assert len(raw) > 1000
