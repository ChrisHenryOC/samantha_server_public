"""Slice 9: Key-rotation matrix test.

Spec: receipt signed under K1 still verifies after one rotation (K2 is
current, K1 is previous). After a second rotation (K3 is current, K2 is
previous, K1 is gone), the K1-signed receipt returns KEY_EXPIRED.
"""

from __future__ import annotations

import nacl.signing


def _make_key() -> bytes:
    return bytes(nacl.signing.SigningKey.generate())


def _make_decision() -> object:
    from samantha_server.engine.decision import EngineDecision

    return EngineDecision(
        applied_rule_id="ACC-001",
        next_state="HOLD",
        flags_added=(),
        flags_cleared=(),
        outcome="hold_missing_name",
        also_matched=(),
        dispatched_rule_ids=("ACC-001",),
        event_input_hash="d" * 64,
        primitive_traces={},
        latency_us=5,
    )


def test_rotation_k1_receipt_verifies_after_one_rotation() -> None:
    """After rotating K1 → K2 (K1 in previous), K1-signed receipt is VALID."""
    from samantha_server.receipts.signing import VerificationResult, sign_decision, verify_signature

    k1 = _make_key()
    k2 = _make_key()

    decision = _make_decision()
    receipt_k1 = sign_decision(decision, key_id="v1", signing_key=k1)  # type: ignore[arg-type]

    # After rotation: current=K2 (v2), previous=K1 (v1)
    result = verify_signature(
        receipt_k1,
        current_key=k2,
        current_key_id="v2",
        previous_key=k1,
        previous_key_id="v1",
    )
    assert result == VerificationResult.VALID


def test_rotation_k1_receipt_is_key_expired_after_two_rotations() -> None:
    """After K1→K2→K3, K1-signed receipt returns KEY_EXPIRED (not INVALID_SIGNATURE)."""
    from samantha_server.receipts.signing import VerificationResult, sign_decision, verify_signature

    k1 = _make_key()
    k2 = _make_key()
    k3 = _make_key()

    decision = _make_decision()
    receipt_k1 = sign_decision(decision, key_id="v1", signing_key=k1)  # type: ignore[arg-type]

    # After two rotations: current=K3 (v3), previous=K2 (v2), K1 gone
    result = verify_signature(
        receipt_k1,
        current_key=k3,
        current_key_id="v3",
        previous_key=k2,
        previous_key_id="v2",
    )
    assert result == VerificationResult.KEY_EXPIRED


def test_rotation_k2_receipt_verifies_after_two_rotations() -> None:
    """K2-signed receipt is VALID after second rotation (K2 in previous slot)."""
    from samantha_server.receipts.signing import VerificationResult, sign_decision, verify_signature

    k2 = _make_key()
    k3 = _make_key()

    decision = _make_decision()
    receipt_k2 = sign_decision(decision, key_id="v2", signing_key=k2)  # type: ignore[arg-type]

    # After second rotation: current=K3 (v3), previous=K2 (v2)
    result = verify_signature(
        receipt_k2,
        current_key=k3,
        current_key_id="v3",
        previous_key=k2,
        previous_key_id="v2",
    )
    assert result == VerificationResult.VALID


def test_rotation_current_key_always_valid() -> None:
    """A receipt signed under the current key is always VALID."""
    from samantha_server.receipts.signing import VerificationResult, sign_decision, verify_signature

    k = _make_key()
    decision = _make_decision()
    receipt = sign_decision(decision, key_id="v1", signing_key=k)  # type: ignore[arg-type]

    result = verify_signature(receipt, current_key=k, current_key_id="v1")
    assert result == VerificationResult.VALID
