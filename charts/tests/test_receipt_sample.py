"""Chart 11: single signed-receipt evidence card."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

# ---------------------------------------------------------------------------
# Slice 1: ReceiptSample dataclass + load_receipt_sample
# ---------------------------------------------------------------------------


def test_load_receipt_sample_roundtrip(tmp_path: Path) -> None:
    from samantha_charts.charts.receipt_sample import ReceiptSample, load_receipt_sample

    data: dict[str, Any] = {
        "_meta": {"source": "test", "verification": "valid", "time_range": "2026"},
        "receipt": {
            "receipt_id": "01TEST",
            "scenario_id": "SC-001",
            "applied_rule_id": "ACC-001",
            "outcome": "passed",
            "next_state": "COMPLETE",
            "signer_key_id": "v1",
            "signature_truncated": "abc123...def456",
            "signature_bytes": 64,
            "signed_at_utc": "2026-01-01T00:00:00+00:00",
            "latency_us": 100,
            "primitive_traces": {},
            "verification": "valid",
        },
    }
    p = tmp_path / "sample.json"
    p.write_text(json.dumps(data))

    result = load_receipt_sample(p)

    assert isinstance(result, ReceiptSample)
    assert result.receipt_id == "01TEST"
    assert result.scenario_id == "SC-001"
    assert result.applied_rule_id == "ACC-001"
    assert result.outcome == "passed"
    assert result.next_state == "COMPLETE"
    assert result.signer_key_id == "v1"
    assert result.signature_truncated == "abc123...def456"
    assert result.signature_bytes == 64
    assert result.signed_at_utc == "2026-01-01T00:00:00+00:00"
    assert result.latency_us == 100
    assert result.primitive_traces == {}
    assert result.verification == "valid"


def test_load_receipt_sample_raises_on_missing_receipt_key(tmp_path: Path) -> None:
    from samantha_charts.charts.receipt_sample import load_receipt_sample

    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"_meta": {}}))

    with pytest.raises(ValueError, match="receipt"):
        load_receipt_sample(p)


def test_load_receipt_sample_raises_on_missing_receipt_id(tmp_path: Path) -> None:
    from samantha_charts.charts.receipt_sample import load_receipt_sample

    data: dict[str, Any] = {
        "receipt": {
            "scenario_id": "SC-001",
            "applied_rule_id": "ACC-001",
            "outcome": "passed",
            "next_state": "COMPLETE",
            "signer_key_id": "v1",
            "signature_truncated": "abc...def",
            "signature_bytes": 64,
            "signed_at_utc": "2026-01-01T00:00:00+00:00",
            "latency_us": 100,
            "primitive_traces": {},
            "verification": "valid",
        }
    }
    p = tmp_path / "missing_id.json"
    p.write_text(json.dumps(data))

    with pytest.raises(ValueError, match="receipt_id"):
        load_receipt_sample(p)


# ---------------------------------------------------------------------------
# Slice 2: summarize_primitive_traces helper
# ---------------------------------------------------------------------------


def test_summarize_empty_dict_returns_empty_list() -> None:
    from samantha_charts.charts.receipt_sample import summarize_primitive_traces

    assert summarize_primitive_traces({}) == []


def test_summarize_leaf_only_trace() -> None:
    from samantha_charts.charts.receipt_sample import summarize_primitive_traces

    pt: dict[str, Any] = {
        "ACC-007": {
            "primitive": "Equals",
            "field": "billing_info_present",
            "expected": False,
            "actual": False,
            "result": True,
            "children": [],
        }
    }

    lines = summarize_primitive_traces(pt)

    assert len(lines) >= 1
    assert any("billing_info_present" in line for line in lines)
    assert any("Equals" in line for line in lines)


def test_summarize_nested_trace_produces_lines() -> None:
    from samantha_charts.charts.receipt_sample import summarize_primitive_traces

    pt: dict[str, Any] = {
        "ACC-009": {
            "primitive": "BooleanAnd",
            "field": None,
            "expected": None,
            "actual": None,
            "result": True,
            "children": [
                {
                    "primitive": "Contains",
                    "field": "ordered_tests",
                    "expected": "breast ihc panel",
                    "actual": ["Breast IHC Panel"],
                    "result": True,
                    "children": [],
                },
                {
                    "primitive": "IsNull",
                    "field": "fixation_time_hours",
                    "expected": None,
                    "actual": None,
                    "result": True,
                    "children": [],
                },
            ],
        }
    }

    lines = summarize_primitive_traces(pt)

    assert len(lines) >= 2
    # Should mention the leaf fields
    joined = " ".join(lines)
    assert "ordered_tests" in joined or "breast ihc panel" in joined
    assert "fixation_time_hours" in joined or "IsNull" in joined


# ---------------------------------------------------------------------------
# Slice 3: render_receipt_sample -- produces PNG, rejects missing fields
# ---------------------------------------------------------------------------


def _make_sample() -> Any:
    from samantha_charts.charts.receipt_sample import ReceiptSample

    return ReceiptSample(
        receipt_id="01TEST",
        scenario_id="SC-001",
        applied_rule_id="ACC-001",
        outcome="passed",
        next_state="COMPLETE",
        signer_key_id="v1",
        signature_truncated="abc123...def456",
        signature_bytes=64,
        signed_at_utc="2026-01-01T00:00:00+00:00",
        latency_us=100,
        primitive_traces={},
        verification="valid",
    )


def test_render_receipt_sample_produces_png(tmp_path: Path) -> None:
    from samantha_charts.charts.receipt_sample import render_receipt_sample

    sample = _make_sample()
    out = tmp_path / "chart11.png"
    render_receipt_sample(sample, out)

    raw = out.read_bytes()
    assert raw.startswith(_PNG_MAGIC)
    assert len(raw) > 1000


def test_render_receipt_sample_invalid_verification_still_renders(tmp_path: Path) -> None:
    from samantha_charts.charts.receipt_sample import ReceiptSample, render_receipt_sample

    sample = ReceiptSample(
        receipt_id="01TEST",
        scenario_id="SC-001",
        applied_rule_id="ACC-001",
        outcome="passed",
        next_state="COMPLETE",
        signer_key_id="v1",
        signature_truncated="abc123...def456",
        signature_bytes=64,
        signed_at_utc="2026-01-01T00:00:00+00:00",
        latency_us=100,
        primitive_traces={},
        verification="invalid",
    )
    out = tmp_path / "chart11_invalid.png"
    render_receipt_sample(sample, out)

    raw = out.read_bytes()
    assert raw.startswith(_PNG_MAGIC)
    assert len(raw) > 1000


# ---------------------------------------------------------------------------
# Slice 4: render_receipt_sample_chart -- production renderer
# ---------------------------------------------------------------------------


def test_render_receipt_sample_chart_writes_png(tmp_path: Path) -> None:
    from samantha_charts.charts.receipt_sample import render_receipt_sample_chart

    out = tmp_path / "chart11.png"
    render_receipt_sample_chart(out)

    raw = out.read_bytes()
    assert raw.startswith(_PNG_MAGIC)
    assert len(raw) > 1000
