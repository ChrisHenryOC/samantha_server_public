"""Slice 11: G20 backward-compat golden test.

Construct a Phase-1-shape EngineDecision (no decision_traces; default ())
and compute _canonical_signing_payload(decision). Compare bytes-for-bytes to
a checked-in golden file (tests/regression/golden_phase1_canonical.bin).

On first run (golden file absent), write it and assert. On every subsequent
run, assert exact bytes match.

Regen procedure (docstring, not CLAUDE.md):
  rm tests/regression/golden_phase1_canonical.bin
  uv run pytest tests/regression/test_signed_receipt_compat.py -x
The test will recreate the golden file from the current canonical path.
Only regen after a deliberate serialization change that has been reviewed.
Pydantic minor upgrades that perturb serialization must fail this test
loudly — that's the design.
"""

from __future__ import annotations

from pathlib import Path

_GOLDEN_PATH = Path(__file__).parent / "golden_phase1_canonical.bin"

# Phase-1-shape EngineDecision: all fields from Phase 1, decision_traces absent
# (defaults to ()). This is the exact shape that existed before Step 6.
_PHASE1_KWARGS = dict(
    applied_rule_id="ACC-001",
    next_state="HOLD",
    flags_added=(),
    flags_cleared=(),
    outcome="hold_missing_name",
    also_matched=(),
    dispatched_rule_ids=("ACC-001",),
    event_input_hash="a" * 64,
    primitive_traces={},
    latency_us=42,
    # decision_traces is intentionally absent — it defaults to ()
)


def test_phase1_canonical_payload_matches_golden() -> None:
    """Phase-1-shape EngineDecision canonical payload matches the golden bytes.

    On first run (golden absent): writes the file and passes.
    On subsequent runs: asserts exact match (pins serialization stability).

    G20 invariant: if Pydantic's model_dump or json.dumps changes its output
    for an empty-tuple field, this test fails loudly so the change is reviewed
    before it silently breaks receipt verification for historical receipts.
    """
    from samantha_server.engine.decision import EngineDecision
    from samantha_server.receipts.signing import _canonical_signing_payload

    decision = EngineDecision(**_PHASE1_KWARGS)  # type: ignore[arg-type]
    payload: bytes = _canonical_signing_payload(decision)

    if not _GOLDEN_PATH.exists():
        import pytest

        pytest.fail(
            "Golden file tests/regression/golden_phase1_canonical.bin is missing. "
            "Regenerate it by running:\n"
            "  rm tests/regression/golden_phase1_canonical.bin\n"
            "  uv run pytest tests/regression/test_signed_receipt_compat.py -x\n"
            "Only regenerate after a deliberate serialization change that has been reviewed."
        )

    golden = _GOLDEN_PATH.read_bytes()
    assert payload == golden, (
        "Canonical signing payload for a Phase-1-shape EngineDecision has changed.\n"
        "This means historical receipts signed before this change will fail "
        "signature verification (the payload they were signed over is different "
        "from what the verifier now recomputes).\n\n"
        "If this change was intentional (e.g., Pydantic upgrade, field addition),\n"
        "1. Understand the impact: all pre-change receipts will have INVALID_SIGNATURE.\n"
        "2. If acceptable, regen the golden file:\n"
        "   rm tests/regression/golden_phase1_canonical.bin\n"
        "   uv run pytest tests/regression/test_signed_receipt_compat.py -x\n"
        "3. Commit the new golden file with a clear commit message explaining the break."
    )


def test_decision_traces_empty_tuple_is_stable() -> None:
    """decision_traces=() does not change the canonical payload.

    Explicit () and absent (default) must produce identical bytes.
    This pins the backward-compat for pre-Step-6 receipts.
    """
    from samantha_server.engine.decision import EngineDecision
    from samantha_server.receipts.signing import _canonical_signing_payload

    decision_no_traces = EngineDecision(**_PHASE1_KWARGS)  # type: ignore[arg-type]
    decision_explicit_empty = EngineDecision(
        **_PHASE1_KWARGS,
        decision_traces=(),  # type: ignore[arg-type]
    )

    assert _canonical_signing_payload(decision_no_traces) == _canonical_signing_payload(
        decision_explicit_empty
    ), "Empty decision_traces changes the canonical payload — backward-compat break"
