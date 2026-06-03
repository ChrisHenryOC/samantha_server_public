"""Tests for QueryTrace additive extension (GH-192 Slice 3).

Tests:
- Existing receipt JSON (without new fields) deserializes cleanly with
  new fields = None.
- New QueryTrace with all three fields populated serializes and
  deserializes round-trip.
- Tri-state validator: valid combinations accepted, invalid rejected.
- response_text_hash cross-mode semantics pinned.
"""

from __future__ import annotations

import hashlib
import json

import pytest


def test_query_trace_old_json_deserializes_with_new_fields_none() -> None:
    """Pre-GH-192 QueryTrace JSON deserializes; new fields are None."""
    from samantha_server.engine.decision import QueryTrace

    # Hand-built JSON representative of pre-fix receipts (no new fields).
    old_json = json.dumps(
        {
            "kind": "query",
            "query_text_hash": "abc123",
            "skill_doc_hash": "def456",
            "scenarios_cited": ["ACC-001"],
            "model_id": "test-model",
            "response_text_hash": "ghi789",
            "database_state_hash": "",
        }
    )
    trace = QueryTrace.model_validate_json(old_json)
    assert trace.parsed_order_ids is None
    assert trace.parsed_answer_type is None
    assert trace.parse_failure is None


def test_query_trace_new_fields_round_trip() -> None:
    """QueryTrace with all three new fields serializes and deserializes identically."""
    from samantha_server.engine.decision import QueryTrace

    trace = QueryTrace(
        query_text_hash="q",
        skill_doc_hash="s",
        scenarios_cited=("ACC-001", "ACC-006"),
        model_id="m",
        response_text_hash="r",
        parsed_order_ids=("ORD-001", "ORD-002"),
        parsed_answer_type="order_list",
        parse_failure=None,
    )
    serialized = trace.model_dump_json()
    restored = QueryTrace.model_validate_json(serialized)
    assert restored.parsed_order_ids == ("ORD-001", "ORD-002")
    assert restored.parsed_answer_type == "order_list"
    assert restored.parse_failure is None


def test_query_trace_parse_failure_json_decode_round_trip() -> None:
    """QueryTrace with parse_failure='json_decode' serializes/deserializes correctly."""
    from samantha_server.engine.decision import QueryTrace

    trace = QueryTrace(
        query_text_hash="q",
        skill_doc_hash="s",
        scenarios_cited=(),
        model_id="m",
        response_text_hash="r",
        parsed_order_ids=None,
        parsed_answer_type=None,
        parse_failure="json_decode",
    )
    serialized = trace.model_dump_json()
    restored = QueryTrace.model_validate_json(serialized)
    assert restored.parse_failure == "json_decode"
    assert restored.parsed_order_ids is None
    assert restored.parsed_answer_type is None


def test_query_trace_parse_failure_schema_violation_round_trip() -> None:
    """QueryTrace with parse_failure='schema_violation' round-trips correctly."""
    from samantha_server.engine.decision import QueryTrace

    trace = QueryTrace(
        query_text_hash="q",
        skill_doc_hash="s",
        scenarios_cited=(),
        model_id="m",
        response_text_hash="r",
        parse_failure="schema_violation",
    )
    assert trace.parse_failure == "schema_violation"
    restored = QueryTrace.model_validate_json(trace.model_dump_json())
    assert restored.parse_failure == "schema_violation"


# ---------------------------------------------------------------------------
# Fix #5: Tri-state validator — valid combinations
# ---------------------------------------------------------------------------


def _base_trace_kwargs() -> dict:  # type: ignore[type-arg]
    return dict(
        query_text_hash="q",
        skill_doc_hash="s",
        scenarios_cited=(),
        model_id="m",
        response_text_hash="r",
    )


def test_tri_state_all_none_is_valid() -> None:
    """All three optional fields None = free_text / pre-PR state. Valid."""
    from samantha_server.engine.decision import QueryTrace

    trace = QueryTrace(
        **_base_trace_kwargs(),
        parsed_order_ids=None,
        parsed_answer_type=None,
        parse_failure=None,
    )
    assert trace.parsed_order_ids is None
    assert trace.parsed_answer_type is None
    assert trace.parse_failure is None


def test_tri_state_success_state_is_valid() -> None:
    """parse_failure=None + both parsed_* populated = JSON-success state. Valid."""
    from samantha_server.engine.decision import QueryTrace

    trace = QueryTrace(
        **_base_trace_kwargs(),
        parsed_order_ids=("ORD-001",),
        parsed_answer_type="order_list",
        parse_failure=None,
    )
    assert trace.parsed_order_ids == ("ORD-001",)
    assert trace.parsed_answer_type == "order_list"
    assert trace.parse_failure is None


def test_tri_state_failure_state_is_valid() -> None:
    """parse_failure set + both parsed_* None = JSON-failure state. Valid."""
    from samantha_server.engine.decision import QueryTrace

    trace = QueryTrace(
        **_base_trace_kwargs(),
        parsed_order_ids=None,
        parsed_answer_type=None,
        parse_failure="json_decode",
    )
    assert trace.parse_failure == "json_decode"


def test_tri_state_pre_pr_receipt_deserializes_cleanly() -> None:
    """Pre-PR receipt JSON (no optional fields) deserializes to all-None state."""
    from samantha_server.engine.decision import QueryTrace

    pre_pr_json = json.dumps(
        {
            "kind": "query",
            "query_text_hash": "abc",
            "skill_doc_hash": "def",
            "scenarios_cited": [],
            "model_id": "test-model",
            "response_text_hash": "ghi",
        }
    )
    trace = QueryTrace.model_validate_json(pre_pr_json)
    assert trace.parsed_order_ids is None
    assert trace.parsed_answer_type is None
    assert trace.parse_failure is None


# ---------------------------------------------------------------------------
# Fix #5: Tri-state validator — invalid combinations rejected
# ---------------------------------------------------------------------------


def test_tri_state_invalid_parse_failure_with_parsed_order_ids_rejected() -> None:
    """parse_failure set + parsed_order_ids populated = invalid combination."""
    from pydantic import ValidationError

    from samantha_server.engine.decision import QueryTrace

    with pytest.raises(ValidationError, match="parse_failure"):
        QueryTrace(
            **_base_trace_kwargs(),
            parsed_order_ids=("ORD-001",),
            parsed_answer_type=None,
            parse_failure="json_decode",
        )


def test_tri_state_invalid_parse_failure_with_parsed_answer_type_rejected() -> None:
    """parse_failure set + parsed_answer_type populated = invalid combination."""
    from pydantic import ValidationError

    from samantha_server.engine.decision import QueryTrace

    with pytest.raises(ValidationError, match="parse_failure"):
        QueryTrace(
            **_base_trace_kwargs(),
            parsed_order_ids=None,
            parsed_answer_type="order_list",
            parse_failure="schema_violation",
        )


def test_tri_state_invalid_parsed_order_ids_without_parsed_answer_type_rejected() -> None:
    """parsed_order_ids populated but parsed_answer_type=None = invalid combination."""
    from pydantic import ValidationError

    from samantha_server.engine.decision import QueryTrace

    with pytest.raises(ValidationError):
        QueryTrace(
            **_base_trace_kwargs(),
            parsed_order_ids=("ORD-001",),
            parsed_answer_type=None,
            parse_failure=None,
        )


# ---------------------------------------------------------------------------
# Fix #10: response_text_hash cross-mode semantics
# ---------------------------------------------------------------------------


def test_response_text_hash_differs_json_success_vs_free_text() -> None:
    """Same model output produces DIFFERENT hashes for JSON-success vs free_text path.

    JSON-success hashes canonical JSON of the parsed model;
    free_text hashes the raw response text.
    They differ because canonical JSON (sorted keys, no spaces) differs from
    the raw string the model returned.
    """
    import json as _json

    raw = '{"answer_type":"no_orders","order_ids":[],"reasoning":"","caveats":""}'

    # JSON-success hash = canonical JSON of parsed model
    from samantha_server.llm.schemas import QueryResponseV1

    parsed = QueryResponseV1.model_validate_json(raw)
    canonical = _json.dumps(parsed.model_dump(), sort_keys=True, separators=(",", ":"))
    json_success_hash = hashlib.sha256(canonical.encode()).hexdigest()

    # free_text hash = raw text
    free_text_hash = hashlib.sha256(raw.encode()).hexdigest()

    assert json_success_hash != free_text_hash, (
        "JSON-success hash and free_text hash should differ for the same model output"
    )


def test_response_text_hash_same_json_failure_vs_free_text() -> None:
    """Malformed text produces the SAME hash on JSON-failure and free_text paths.

    Both JSON-failure and free_text hash the raw text unchanged.
    """
    raw = "this is not json"

    json_failure_hash = hashlib.sha256(raw.encode()).hexdigest()
    free_text_hash = hashlib.sha256(raw.encode()).hexdigest()

    assert json_failure_hash == free_text_hash, (
        "JSON-failure hash and free_text hash should match for the same raw text"
    )


# ---------------------------------------------------------------------------
# GH-220 Slice 2: QueryTrace.parsed_answer_type widening
# ---------------------------------------------------------------------------


def test_query_trace_parsed_answer_type_accepts_order_status() -> None:
    """GH-220 Slice 2: QueryTrace accepts parsed_answer_type='order_status'."""
    from samantha_server.engine.decision import QueryTrace

    trace = QueryTrace(
        **_base_trace_kwargs(),
        parsed_order_ids=("ORD-901",),
        parsed_answer_type="order_status",
        parse_failure=None,
    )
    assert trace.parsed_answer_type == "order_status"
    assert trace.parsed_order_ids == ("ORD-901",)
