"""Tests for samantha_server.receipts.signing (Phase 2 Step 6)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_signing_key() -> bytes:
    """Return a random 32-byte Ed25519 seed for use in tests."""
    import nacl.signing

    return bytes(nacl.signing.SigningKey.generate())


def _make_minimal_decision() -> object:
    """Return a minimal EngineDecision for signing tests."""
    from samantha_server.engine.decision import EngineDecision

    return EngineDecision(
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
    )


# ---------------------------------------------------------------------------
# SignedReceipt validators
# ---------------------------------------------------------------------------


def test_signed_receipt_rejects_non_ulid_receipt_id() -> None:
    from pydantic import ValidationError

    from samantha_server.engine.decision import EngineDecision
    from samantha_server.receipts.signing import SignedReceipt

    decision = _make_minimal_decision()
    assert isinstance(decision, EngineDecision)

    with pytest.raises(ValidationError):
        SignedReceipt(
            receipt_id="NOT_A_ULID",  # invalid — not Crockford base32
            decision=decision,
            signer_key_id="v1",
            signature=b"\x00" * 64,
            signed_at_utc=datetime.now(tz=UTC),
        )


def test_signed_receipt_rejects_non_64_byte_signature() -> None:
    from pydantic import ValidationError

    from samantha_server.receipts.signing import SignedReceipt

    decision = _make_minimal_decision()
    with pytest.raises(ValidationError):
        SignedReceipt(
            receipt_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
            decision=decision,  # type: ignore[arg-type]
            signer_key_id="v1",
            signature=b"\x00" * 32,  # wrong length (32 instead of 64)
            signed_at_utc=datetime.now(tz=UTC),
        )


def test_signed_receipt_rejects_naive_datetime() -> None:
    from pydantic import ValidationError

    from samantha_server.receipts.signing import SignedReceipt

    decision = _make_minimal_decision()
    with pytest.raises(ValidationError):
        SignedReceipt(
            receipt_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
            decision=decision,  # type: ignore[arg-type]
            signer_key_id="v1",
            signature=b"\x00" * 64,
            signed_at_utc=datetime(2025, 1, 1, 0, 0, 0),  # naive — no tzinfo
        )


def test_signed_receipt_rejects_non_utc_datetime() -> None:
    """Non-UTC aware datetime (e.g. EST) should be rejected."""
    import zoneinfo

    from pydantic import ValidationError

    from samantha_server.receipts.signing import SignedReceipt

    decision = _make_minimal_decision()
    est = zoneinfo.ZoneInfo("America/New_York")
    with pytest.raises(ValidationError):
        SignedReceipt(
            receipt_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
            decision=decision,  # type: ignore[arg-type]
            signer_key_id="v1",
            signature=b"\x00" * 64,
            signed_at_utc=datetime(2025, 1, 1, 0, 0, 0, tzinfo=est),
        )


# ---------------------------------------------------------------------------
# sign_decision
# ---------------------------------------------------------------------------


def test_sign_decision_returns_signed_receipt() -> None:
    from samantha_server.engine.decision import EngineDecision
    from samantha_server.receipts.signing import SignedReceipt, sign_decision

    key = _make_signing_key()
    decision = _make_minimal_decision()
    assert isinstance(decision, EngineDecision)

    receipt = sign_decision(decision, key_id="v1", signing_key=key)
    assert isinstance(receipt, SignedReceipt)


def test_sign_decision_receipt_id_is_ulid() -> None:
    """receipt_id must be a 26-char Crockford base32 ULID."""
    from samantha_server.receipts.signing import _ULID_RE, sign_decision

    key = _make_signing_key()
    decision = _make_minimal_decision()
    receipt = sign_decision(decision, key_id="v1", signing_key=key)  # type: ignore[arg-type]
    assert _ULID_RE.match(receipt.receipt_id), f"Not a ULID: {receipt.receipt_id!r}"


def test_sign_decision_signed_at_utc_is_tz_aware() -> None:
    from samantha_server.receipts.signing import sign_decision

    key = _make_signing_key()
    decision = _make_minimal_decision()
    receipt = sign_decision(decision, key_id="v1", signing_key=key)  # type: ignore[arg-type]
    assert receipt.signed_at_utc.tzinfo is not None
    assert receipt.signed_at_utc.utcoffset() == UTC.utcoffset(None)


def test_sign_decision_signature_is_64_bytes() -> None:
    from samantha_server.receipts.signing import sign_decision

    key = _make_signing_key()
    decision = _make_minimal_decision()
    receipt = sign_decision(decision, key_id="v1", signing_key=key)  # type: ignore[arg-type]
    assert len(receipt.signature) == 64


def test_sign_decision_signer_key_id_matches() -> None:
    from samantha_server.receipts.signing import sign_decision

    key = _make_signing_key()
    decision = _make_minimal_decision()
    receipt = sign_decision(decision, key_id="v2", signing_key=key)  # type: ignore[arg-type]
    assert receipt.signer_key_id == "v2"


# ---------------------------------------------------------------------------
# _canonical_signing_payload
# ---------------------------------------------------------------------------


def test_canonical_signing_payload_is_bytes() -> None:
    from samantha_server.receipts.signing import _canonical_signing_payload

    decision = _make_minimal_decision()
    payload = _canonical_signing_payload(decision)  # type: ignore[arg-type]
    assert isinstance(payload, bytes)


def test_canonical_signing_payload_is_deterministic() -> None:
    """Same decision produces the same bytes across two calls."""
    from samantha_server.receipts.signing import _canonical_signing_payload

    decision = _make_minimal_decision()
    p1 = _canonical_signing_payload(decision)  # type: ignore[arg-type]
    p2 = _canonical_signing_payload(decision)  # type: ignore[arg-type]
    assert p1 == p2


def test_canonical_signing_payload_uses_sorted_keys() -> None:
    """Payload bytes decode as valid JSON with sorted keys (no whitespace)."""
    import json

    from samantha_server.receipts.signing import _canonical_signing_payload

    decision = _make_minimal_decision()
    payload = _canonical_signing_payload(decision)  # type: ignore[arg-type]
    decoded = payload.decode()
    parsed = json.loads(decoded)
    # Re-encode with sort_keys to confirm the payload is already sorted
    resorted = json.dumps(parsed, sort_keys=True, separators=(",", ":"))
    assert decoded == resorted


# ---------------------------------------------------------------------------
# verify_signature
# ---------------------------------------------------------------------------


def test_verify_signature_valid_current_key() -> None:
    from samantha_server.receipts.signing import VerificationResult, sign_decision, verify_signature

    key = _make_signing_key()
    decision = _make_minimal_decision()
    receipt = sign_decision(decision, key_id="v1", signing_key=key)  # type: ignore[arg-type]

    result = verify_signature(
        receipt,
        current_key=key,
        current_key_id="v1",
    )
    assert result == VerificationResult.VALID


def test_verify_signature_valid_previous_key() -> None:
    """A receipt signed under the previous key still verifies as VALID."""
    from samantha_server.receipts.signing import VerificationResult, sign_decision, verify_signature

    old_key = _make_signing_key()
    new_key = _make_signing_key()
    decision = _make_minimal_decision()
    receipt = sign_decision(decision, key_id="v1", signing_key=old_key)  # type: ignore[arg-type]

    result = verify_signature(
        receipt,
        current_key=new_key,
        current_key_id="v2",
        previous_key=old_key,
        previous_key_id="v1",
    )
    assert result == VerificationResult.VALID


def test_verify_signature_key_expired() -> None:
    """Receipt with signer_key_id that matches neither current nor previous → KEY_EXPIRED."""
    from samantha_server.receipts.signing import VerificationResult, sign_decision, verify_signature

    old_key = _make_signing_key()
    new_key = _make_signing_key()
    decision = _make_minimal_decision()
    receipt = sign_decision(decision, key_id="v1", signing_key=old_key)  # type: ignore[arg-type]

    result = verify_signature(
        receipt,
        current_key=new_key,
        current_key_id="v3",  # v1 gone; no previous supplied
    )
    assert result == VerificationResult.KEY_EXPIRED


def test_verify_signature_invalid_signature_when_payload_tampered() -> None:
    """signer_key_id matches current key but signature fails → INVALID_SIGNATURE."""
    from samantha_server.receipts.signing import (
        SignedReceipt,
        VerificationResult,
        sign_decision,
        verify_signature,
    )

    key = _make_signing_key()
    decision = _make_minimal_decision()
    receipt = sign_decision(decision, key_id="v1", signing_key=key)  # type: ignore[arg-type]

    # Build a tampered receipt: same signer_key_id but corrupt the signature
    tampered = SignedReceipt(
        receipt_id=receipt.receipt_id,
        decision=receipt.decision,
        signer_key_id=receipt.signer_key_id,
        signature=bytes(b ^ 0xFF for b in receipt.signature),  # flip all bits
        signed_at_utc=receipt.signed_at_utc,
    )

    result = verify_signature(
        tampered,
        current_key=key,
        current_key_id="v1",
    )
    assert result == VerificationResult.INVALID_SIGNATURE


def test_verify_signature_invalid_via_previous_key_slot() -> None:
    """H-11: INVALID_SIGNATURE when receipt was signed with previous key but signature is tampered.

    signer_key_id matches previous_key_id, but BadSignatureError fires.
    The branch in verify_signature that verifies against the previous key must
    return INVALID_SIGNATURE (not KEY_EXPIRED) when the id matches but verification fails.
    """
    from samantha_server.receipts.signing import (
        SignedReceipt,
        VerificationResult,
        sign_decision,
        verify_signature,
    )

    k1 = _make_signing_key()  # previous key
    k2 = _make_signing_key()  # current key
    decision = _make_minimal_decision()
    # Signed with k1 (previous key, id="v1")
    receipt = sign_decision(decision, key_id="v1", signing_key=k1)  # type: ignore[arg-type]

    # Tamper the signature so it fails verification
    tampered = SignedReceipt(
        receipt_id=receipt.receipt_id,
        decision=receipt.decision,
        signer_key_id="v1",  # matches previous_key_id
        signature=bytes(b ^ 0xFF for b in receipt.signature),  # corrupt
        signed_at_utc=receipt.signed_at_utc,
    )

    result = verify_signature(
        tampered,
        current_key=k2,
        current_key_id="v2",
        previous_key=k1,
        previous_key_id="v1",
    )
    assert result == VerificationResult.INVALID_SIGNATURE


def test_verify_signature_previous_key_without_id_raises() -> None:
    """previous_key set without previous_key_id.

    Mirror of the Item 1 guard: supply both or neither. When previous_key
    is set but previous_key_id is None, verify_signature must raise
    MisconfiguredEnvironmentError rather than silently dropping the key and
    returning KEY_EXPIRED with no diagnostic. The "both or neither" contract
    is now symmetric for both asymmetric cases.
    """
    from samantha_server.errors import MisconfiguredEnvironmentError
    from samantha_server.receipts.signing import sign_decision, verify_signature

    k1 = _make_signing_key()
    k2 = _make_signing_key()
    decision = _make_minimal_decision()
    receipt = sign_decision(decision, key_id="v1", signing_key=k1)  # type: ignore[arg-type]

    # previous_key provided but no previous_key_id — mirror asymmetric misconfiguration.
    with pytest.raises(MisconfiguredEnvironmentError, match="previous_key_id"):
        verify_signature(
            receipt,
            current_key=k2,
            current_key_id="v2",
            previous_key=k1,
            # previous_key_id intentionally absent
        )


def test_canonical_signing_payload_raises_on_length_mismatch() -> None:
    """H-02: _canonical_signing_payload must raise RuntimeError when model_dump
    produces a different number of trace entries than live decision_traces.

    This invariant is enforced by construction (exclude_defaults=True omits the
    empty tuple but never a populated one), but the explicit check makes the
    failure loud rather than silently producing an unsigned partial payload.
    We test it by monkeypatching model_dump to return a mismatched list.
    """
    from unittest.mock import patch

    from samantha_server.engine.decision import EngineDecision, QueryTrace
    from samantha_server.receipts.signing import _canonical_signing_payload

    trace = QueryTrace(
        query_text_hash="a" * 64,
        skill_doc_hash="b" * 64,
        scenarios_cited=(),
        model_id="test-model",
        response_text_hash="c" * 64,
    )
    decision = EngineDecision(
        applied_rule_id=None,
        next_state="ACCESSIONING",
        flags_added=(),
        flags_cleared=(),
        outcome="query_response",
        also_matched=(),
        dispatched_rule_ids=(),
        event_input_hash="a" * 64,
        primitive_traces={},
        latency_us=0,
        decision_traces=(trace,),
    )

    # Simulate model_dump returning a different number of trace dicts than live traces
    original_dump = decision.model_dump(mode="python", exclude_defaults=True)
    # Remove the decision_traces entry to simulate mismatch (0 raw vs 1 live)
    mismatch_dump = {**original_dump, "decision_traces": []}

    with (
        patch.object(type(decision), "model_dump", return_value=mismatch_dump),
        pytest.raises(RuntimeError, match="kind-injection"),
    ):
        _canonical_signing_payload(decision)


def test_pre_pr_receipt_canonical_payload_byte_identity() -> None:
    """Fix #12: pre-PR QueryTrace JSON round-trips through _canonical_signing_payload
    with byte-identical output.

    A pre-PR receipt (no parsed_*, no parse_failure) must produce the same
    canonical payload bytes before and after deserialization. If
    exclude_defaults=True were ever changed, or a new non-None-default field
    were added to QueryTrace, this test would catch the regression.
    """

    from samantha_server.engine.decision import EngineDecision, QueryTrace
    from samantha_server.receipts.signing import _canonical_signing_payload

    # Hand-craft a pre-PR QueryTrace with no new fields
    trace = QueryTrace(
        query_text_hash="a" * 64,
        skill_doc_hash="b" * 64,
        scenarios_cited=("ACC-001",),
        model_id="test-model",
        response_text_hash="c" * 64,
        # parsed_order_ids, parsed_answer_type, parse_failure all default to None
    )
    decision = EngineDecision(
        applied_rule_id=None,
        next_state="ACCESSIONING",
        flags_added=(),
        flags_cleared=(),
        outcome="query_response",
        also_matched=(),
        dispatched_rule_ids=(),
        event_input_hash="d" * 64,
        primitive_traces={},
        latency_us=0,
        decision_traces=(trace,),
    )

    # First canonical payload
    payload_bytes_1 = _canonical_signing_payload(decision)

    # Deserialize the QueryTrace via model_validate_json and re-serialize
    trace_json = trace.model_dump_json()
    restored_trace = QueryTrace.model_validate_json(trace_json)
    restored_decision = EngineDecision(
        applied_rule_id=None,
        next_state="ACCESSIONING",
        flags_added=(),
        flags_cleared=(),
        outcome="query_response",
        also_matched=(),
        dispatched_rule_ids=(),
        event_input_hash="d" * 64,
        primitive_traces={},
        latency_us=0,
        decision_traces=(restored_trace,),
    )

    # Second canonical payload — must be byte-identical
    payload_bytes_2 = _canonical_signing_payload(restored_decision)

    assert payload_bytes_1 == payload_bytes_2, (
        "Pre-PR QueryTrace canonical payload changed after round-trip "
        "through model_validate_json.\n"
        f"Before: {payload_bytes_1!r}\nAfter:  {payload_bytes_2!r}"
    )


def test_verify_signature_raises_on_previous_key_id_without_previous_key() -> None:
    """previous_key_id set while previous_key is None.

    Raises MisconfiguredEnvironmentError.

    The mirror case of H-03: previous_key_id without previous_key is an asymmetric
    misconfiguration. The documented rotation playbook never passes through this state
    (previous_key_id is only meaningful alongside previous_key), so it must be rejected
    loudly rather than silently discarding the id and leaving v1 receipts as KEY_EXPIRED
    with no diagnostic.
    """
    from samantha_server.errors import MisconfiguredEnvironmentError
    from samantha_server.receipts.signing import sign_decision, verify_signature

    k1 = _make_signing_key()
    k2 = _make_signing_key()
    decision = _make_minimal_decision()
    receipt = sign_decision(decision, key_id="v1", signing_key=k1)  # type: ignore[arg-type]

    # previous_key_id is set but previous_key is None — asymmetric misconfiguration.
    with pytest.raises(MisconfiguredEnvironmentError, match="previous_key_id"):
        verify_signature(
            receipt,
            current_key=k2,
            current_key_id="v2",
            previous_key=None,  # unset
            previous_key_id="v1",  # set — asymmetric!
        )


def test_verify_signature_previous_key_and_id_both_set_still_verifies_valid() -> None:
    """Symmetric previous_key + previous_key_id config still verifies VALID.

    Regression guard: the new asymmetric-misconfig check must not break the
    normal rotation case where both previous_key and previous_key_id are set.
    """
    from samantha_server.receipts.signing import VerificationResult, sign_decision, verify_signature

    k1 = _make_signing_key()  # previous key
    k2 = _make_signing_key()  # current key
    decision = _make_minimal_decision()
    receipt = sign_decision(decision, key_id="v1", signing_key=k1)  # type: ignore[arg-type]

    result = verify_signature(
        receipt,
        current_key=k2,
        current_key_id="v2",
        previous_key=k1,  # both set — normal rotation
        previous_key_id="v1",
    )
    assert result == VerificationResult.VALID


def test_verify_signature_raises_on_duplicate_key_ids() -> None:
    """H-03: verify_signature must raise MisconfiguredEnvironmentError when
    current_key_id == previous_key_id AND previous_key is not None.

    The previous-key entry would silently overwrite the current-key entry in
    key_candidates, causing the verifier to accept only previous-key signatures
    even if current-key bytes differ.  This is a config-time bug and must fail fast.
    """
    from samantha_server.errors import MisconfiguredEnvironmentError
    from samantha_server.receipts.signing import sign_decision, verify_signature

    k1 = _make_signing_key()
    k2 = _make_signing_key()  # different bytes, same id
    decision = _make_minimal_decision()
    receipt = sign_decision(decision, key_id="v1", signing_key=k1)  # type: ignore[arg-type]

    with pytest.raises(MisconfiguredEnvironmentError, match="v1"):
        verify_signature(
            receipt,
            current_key=k1,
            current_key_id="v1",
            previous_key=k2,
            previous_key_id="v1",  # same id as current!
        )


# ---------------------------------------------------------------------------
# ULID same-millisecond encoding
# ---------------------------------------------------------------------------


def test_ulid_same_millisecond_shares_timestamp_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two ULIDs generated at the same frozen millisecond share
    their first 10 chars (the timestamp prefix) and differ in the suffix.

    Monkeypatches time.time on the signing module so both calls see the same ms.
    """
    import samantha_server.receipts.signing as _signing

    _FROZEN_SECONDS = 1_000_000.0  # 1_000_000_000 ms — known fixed timestamp

    monkeypatch.setattr(_signing.time, "time", lambda: _FROZEN_SECONDS)

    ulid1 = _signing._generate_ulid()
    ulid2 = _signing._generate_ulid()

    assert len(ulid1) == 26
    assert len(ulid2) == 26

    # Same frozen ms → identical 10-char timestamp prefix.
    assert ulid1[:10] == ulid2[:10], (
        f"Both ULIDs must share the same 10-char timestamp prefix at the same ms; "
        f"got {ulid1[:10]!r} vs {ulid2[:10]!r}"
    )

    # Different random suffixes (80-bit random → collision probability negligible).
    assert ulid1[10:] != ulid2[10:], (
        "Two ULIDs generated in the same call must have different random suffixes"
    )


def test_ulid_timestamp_prefix_known_value() -> None:
    """Mutation-sensitivity check — the Crockford encode loop
    must produce the hand-computed prefix for ms=1_000_000_000.

    The expected value is derived INDEPENDENTLY below via divmod against the
    Crockford alphabet (no reuse of the production encode loop), so the pin
    cannot be circular. If the encode loop (e.g. the & 0x1F
    mask or the >> 5 shift) is broken, this test catches it.
    """
    import samantha_server.receipts.signing as _signing

    # ms = 1_000_000_000 (exactly 1 billion milliseconds)
    _FROZEN_MS_SECONDS = 1_000_000.0  # time.time() * 1000 == 1_000_000_000 ms

    # Independent reference derivation (divmod, not the production bit loop).
    _crockford = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    _v, _digits = 1_000_000_000, []
    for _ in range(10):
        _v, _rem = divmod(_v, 32)
        _digits.append(_crockford[_rem])
    _EXPECTED_PREFIX = "".join(reversed(_digits))
    assert _EXPECTED_PREFIX == "0000XSNJG0"  # sanity: matches the hand computation

    import pytest as _pytest

    with _pytest.MonkeyPatch.context() as mp:
        mp.setattr(_signing.time, "time", lambda: _FROZEN_MS_SECONDS)
        ulid = _signing._generate_ulid()

    assert ulid[:10] == _EXPECTED_PREFIX, (
        f"ULID timestamp prefix mismatch for ms=1_000_000_000: "
        f"expected {_EXPECTED_PREFIX!r}, got {ulid[:10]!r}. "
        "The Crockford encode loop may be broken."
    )
