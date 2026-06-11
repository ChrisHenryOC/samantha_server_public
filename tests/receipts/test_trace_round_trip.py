"""C-02: Round-trip sign→store→fetch→verify for all 5 DecisionTrace subtypes.

Ensures that the kind-discriminator re-injection in _canonical_signing_payload
works correctly for every union variant, not just QueryTrace. A re-injection
bug for any of the four non-QueryTrace subtypes would silently produce
verifiable-but-unrehydratable receipts (defect only surfaces at audit read time).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from samantha_server.engine.decision import (
    CanonicalizationTrace,
    ClarificationTrace,
    EngineDecision,
    LLMReviewTrace,
    QueryTrace,
    RefusalTrace,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_signing_key() -> bytes:
    """Return a random 32-byte Ed25519 seed."""
    import nacl.signing

    return bytes(nacl.signing.SigningKey.generate())


def _base_decision(**overrides: Any) -> EngineDecision:
    """Return a minimal EngineDecision; caller supplies decision_traces via overrides."""
    defaults: dict[str, Any] = dict(
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
        decision_traces=(),
    )
    defaults.update(overrides)
    return EngineDecision(**defaults)


# ---------------------------------------------------------------------------
# Parametrized cases: one per DecisionTrace subtype
# ---------------------------------------------------------------------------


def _make_query_trace() -> QueryTrace:
    return QueryTrace(
        query_text_hash="a" * 64,
        skill_doc_hash="b" * 64,
        scenarios_cited=("QR-001",),
        model_id="test-model",
        response_text_hash="c" * 64,
    )


def _make_refusal_trace() -> RefusalTrace:
    return RefusalTrace(
        refusal_reason="STAGE_PRE_LLM_UNAVAILABLE",
        refusal_stage="PRE",
        judge_verdict=None,
        alternatives=(),
    )


def _make_clarification_trace() -> ClarificationTrace:
    return ClarificationTrace(
        missing_fields=("patient_name",),
        suggested_values={"patient_name": "Unknown"},
    )


def _make_clarification_trace_llm_failed() -> ClarificationTrace:
    # llm_failed=True is the only case that puts the
    # new field on the signed payload (default-False is canonically excluded),
    # so it needs its own round-trip case. The invariant requires empty
    # suggested_values when llm_failed is set.
    return ClarificationTrace(
        missing_fields=("patient_name",),
        suggested_values={},
        llm_failed=True,
    )


def _make_llm_review_trace() -> LLMReviewTrace:
    return LLMReviewTrace(
        skill_doc_hash="d" * 64,
        model_id="reviewer-model",
        response_text_hash="e" * 64,
        disposition="accept",
    )


def _make_canonicalization_trace() -> CanonicalizationTrace:
    return CanonicalizationTrace(
        field="priority",
        raw_value="ROUTINE",
        canonical_value_attempted="routine",
    )


_TRACE_CASES = [
    ("query", _make_query_trace()),
    ("refusal", _make_refusal_trace()),
    ("clarification", _make_clarification_trace()),
    # llm_failed=True case — same kind discriminator; pytest suffixes
    # the duplicate id (clarification0/clarification1).
    ("clarification", _make_clarification_trace_llm_failed()),
    ("llm_review", _make_llm_review_trace()),
    ("canonicalization_warning", _make_canonicalization_trace()),
]


@pytest.fixture
def round_trip_db(tmp_path: Path) -> Path:
    """Return a path to an initialized receipt store."""
    from samantha_server.receipts.store import init_store

    db_path = tmp_path / "round_trip.db"
    init_store(db_path)
    return db_path


@pytest.mark.parametrize(
    "expected_kind,trace",
    _TRACE_CASES,
    ids=[kind for kind, _ in _TRACE_CASES],
)
def test_trace_round_trip_sign_store_fetch_verify(
    round_trip_db: Path,
    expected_kind: str,
    trace: Any,
) -> None:
    """Full round-trip: sign → store insert → fetch_by_event_hash → verify + rehydrate.

    C-02: asserts that the rehydrated receipt's decision_traces[0].kind matches
    the expected discriminator and that verify_signature returns VALID.
    Without the kind re-injection fix in _canonical_signing_payload, the round-
    trip would succeed for signing (HMAC is over the full canonical payload
    including the re-injected kind) but fail at rehydration because the stored
    payload_json would lack the discriminator, causing model_validate_json to
    raise a Pydantic discriminator error.
    """
    from samantha_server.receipts.audit import fetch_by_event_hash
    from samantha_server.receipts.signing import VerificationResult, sign_decision, verify_signature
    from samantha_server.receipts.store import _open_write_conn, insert

    key = _make_signing_key()
    decision = _base_decision(decision_traces=(trace,))

    # --- Sign ---
    receipt = sign_decision(decision, key_id="v1", signing_key=key)

    # --- Store ---
    conn = _open_write_conn(round_trip_db)
    insert(conn, receipt)
    conn.commit()
    conn.close()

    # --- Fetch ---
    read_conn = _open_write_conn(round_trip_db)
    fetched_receipts = fetch_by_event_hash(read_conn, "a" * 64)
    read_conn.close()

    assert len(fetched_receipts) == 1, f"Expected 1 receipt, got {len(fetched_receipts)}"
    rehydrated = fetched_receipts[0]

    # --- Assert kind discriminator survived round-trip ---
    assert len(rehydrated.decision.decision_traces) == 1
    rehydrated_trace = rehydrated.decision.decision_traces[0]
    assert rehydrated_trace.kind == expected_kind, (
        f"kind mismatch after round-trip: expected {expected_kind!r}, got {rehydrated_trace.kind!r}"
    )

    # --- Verify signature ---
    result = verify_signature(
        rehydrated,
        current_key=key,
        current_key_id="v1",
    )
    assert result == VerificationResult.VALID, (
        f"Signature verification failed after round-trip for kind={expected_kind!r}: {result}"
    )
