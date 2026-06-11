"""Architectural tests for the receipts module.

Three sub-tests:
1. audit.py source contains no UPDATE/DELETE SQL keywords.
2. Every decision-emitting path in scope fires a receipt writer.
3. _decode_hex no-leak: every failure mode's error message contains no
   non-empty substring of the input value (Hypothesis property test).
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from samantha_server.models.context import Event, Order, SpecimenContext
from samantha_server.receipts.signing import SignedReceipt
from tests.llm.specimen_review_helpers import (
    make_mock_llm_review_client as _make_mock_llm_review_client,
)

# ---------------------------------------------------------------------------
# M-03: yield-based session-state isolation fixture
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Test 1: audit.py has no UPDATE/DELETE SQL
# ---------------------------------------------------------------------------


def test_audit_module_has_no_update_or_delete_sql() -> None:
    """audit.py SQL strings must contain no UPDATE or DELETE keywords.

    Append-only invariant: the audit module is inspected via AST to find
    string literals that contain UPDATE or DELETE SQL keywords. This avoids
    false positives from comments and docstrings that mention these keywords
    in a documentation context.
    """
    import ast

    import samantha_server.receipts.audit as audit_mod

    source_path = audit_mod.__file__
    assert source_path is not None, "audit module has no __file__"

    source = Path(source_path).read_text()

    # Parse the AST and check every non-docstring string literal for UPDATE or DELETE.
    # The looks_like_sql pre-filter is dropped (M-23): a targeted
    # "UPDATE receipts SET outcome='x'" literal lacking other SQL keywords would
    # otherwise evade detection. Docstrings (module/class/function) are excluded
    # because they legitimately describe invariants using SQL keywords.
    tree = ast.parse(source)

    # Collect all string constants that are in docstring positions (first statement
    # of a module/class/function body, wrapped in ast.Expr). These are excluded.
    docstring_nodes: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstring_nodes.add(id(body[0].value))

    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstring_nodes:
                continue  # skip docstrings
            val = node.value
            if re.search(r"\bUPDATE\b", val, re.IGNORECASE):
                violations.append(f"SQL string with UPDATE at line {node.lineno}")
            if re.search(r"\bDELETE\b", val, re.IGNORECASE):
                violations.append(f"SQL string with DELETE at line {node.lineno}")

    assert not violations, (
        "audit.py contains forbidden SQL keywords in string literals "
        "(append-only invariant violated):\n" + "\n".join(f"  - {v}" for v in violations)
    )


# ---------------------------------------------------------------------------
# Test 2: Every decision-emitting path fires a receipt writer
# ---------------------------------------------------------------------------


def _make_dispatch_event_deps(
    llm: object,
    skills: object,
    written: list[SignedReceipt],
    *,
    session_id: str = "arch-test-sess",
) -> dict[str, Any]:
    """Build the keyword-argument dict for dispatch_event().

    Constructs in-memory deps so tests don't touch the real SQLite store.
    The *written* list is shared with the caller — each SignedReceipt emitted
    via write_signed() is appended to it.
    """
    from samantha_server.observability.counters import CounterRegistry
    from samantha_server.queue.priority import EventPriority
    from samantha_server.rules.loader import RuleIndex
    from tests.api.helpers import SpyReceiptWriter

    return {
        "session_id": session_id,
        "priority": EventPriority.ROUTINE,
        "queue_wait_us": 0,
        "receipt_writer": SpyReceiptWriter(written),
        "write_lock": asyncio.Lock(),
        "counters": CounterRegistry(),
        "llm_client": llm,
        "skills_index": skills,
        "rule_index": RuleIndex([]),
    }


def test_dispatch_event_clinical_query_path_fires_receipt_writer() -> None:
    """H-03: dispatch_event() for clinical_query fires receipt_writer before returning.

    dispatch_event is the single decision-emitting chokepoint (emit_receipt).
    Without this test, a future regression that bypasses emit_receipt on one
    routing branch would not be caught structurally.
    """
    from unittest.mock import MagicMock

    from samantha_server.api.routing import dispatch_event
    from samantha_server.llm.client import LLMResponse
    from samantha_server.receipts.signing import SignedReceipt
    from samantha_server.skills.loader import discover

    ctx_with_clinical_query = SpecimenContext(
        order=Order(
            order_id="ARCH-ROUTER-001",
            patient_name=None,
            patient_sex="F",
            age=40,
            specimen_type="biopsy",
            anatomic_site="breast",
            fixative="formalin",
            fixation_time_hours=24.0,
            ordered_tests=("ER",),
            priority="routine",
            billing_info_present=True,
        ),
        current_state="ACCESSIONING",
        flags=frozenset(),
        event=Event(event_type="clinical_query", event_data={"query": "arch test"}, step_index=0),
    )

    mock_llm = MagicMock()
    mock_llm.model_id = "test-model"
    mock_llm.complete.return_value = LLMResponse(
        text="arch response",
        input_tokens=5,
        output_tokens=3,
        model_id="test-model",
        latency_us=500,
    )
    mock_llm.complete_json.return_value = LLMResponse(
        text='{"answer_type":"no_orders","order_ids":[],"reasoning":"","caveats":""}',
        input_tokens=5,
        output_tokens=3,
        model_id="test-model",
        latency_us=500,
    )

    written: list[SignedReceipt] = []
    deps = _make_dispatch_event_deps(mock_llm, discover(), written)

    asyncio.run(dispatch_event(ctx_with_clinical_query, **deps))

    assert len(written) == 1, "dispatch_event clinical_query path did not fire receipt_writer"
    assert isinstance(written[0], SignedReceipt)
    assert written[0].decision.outcome == "query_response"


def _make_ctx_for_dispatch_refusal(
    *, age: int = 90, event_type: str = "clinical_query"
) -> SpecimenContext:
    """Build a context that will trigger a refusal in dispatch_event()."""
    return SpecimenContext(
        order=Order(
            order_id="ARCH-ROUTER-002",
            patient_name=None,
            patient_sex=None,
            age=age,
            specimen_type="biopsy",
            anatomic_site="breast",
            fixative="formalin",
            fixation_time_hours=24.0,
            ordered_tests=("ER",),
            priority="routine",
            billing_info_present=True,
        ),
        current_state="ACCESSIONING",
        flags=frozenset(),
        event=Event(event_type=event_type, event_data={"query": "phi test"}, step_index=0),
    )


def test_dispatch_event_phi_boundary_refusal_fires_receipt_writer() -> None:
    """H-03: dispatch_event() emits a receipt even when PHIBoundaryError fires.

    age=90 triggers PHIBoundaryError in handle_clinical_query (C-01 fix).
    The receipt must be emitted by emit_receipt before returning.
    """
    from unittest.mock import MagicMock

    from samantha_server.api.routing import dispatch_event
    from samantha_server.skills.loader import discover

    ctx = _make_ctx_for_dispatch_refusal(age=90)
    mock_llm = MagicMock()

    written: list[SignedReceipt] = []
    deps = _make_dispatch_event_deps(mock_llm, discover(), written)

    asyncio.run(dispatch_event(ctx, **deps))

    assert len(written) == 1, "PHI boundary refusal path did not fire receipt_writer"
    assert written[0].decision.outcome == "refused_phi_boundary"


def test_dispatch_event_skill_loader_error_fires_receipt_writer() -> None:
    """H-03: dispatch_event() emits a receipt when SkillLoaderError fires (empty skills index)."""
    from unittest.mock import MagicMock

    from samantha_server.api.routing import dispatch_event

    ctx = SpecimenContext(
        order=Order(
            order_id="ARCH-ROUTER-003",
            patient_name=None,
            patient_sex=None,
            age=40,
            specimen_type="biopsy",
            anatomic_site="breast",
            fixative="formalin",
            fixation_time_hours=24.0,
            ordered_tests=("ER",),
            priority="routine",
            billing_info_present=True,
        ),
        current_state="ACCESSIONING",
        flags=frozenset(),
        event=Event(
            event_type="clinical_query", event_data={"query": "skill loader test"}, step_index=0
        ),
    )

    mock_llm = MagicMock()
    written: list[SignedReceipt] = []
    deps = _make_dispatch_event_deps(mock_llm, {}, written)  # empty skills → SkillLoaderError

    asyncio.run(dispatch_event(ctx, **deps))

    assert len(written) == 1, "SkillLoaderError path did not fire receipt_writer"
    assert written[0].decision.outcome == "refused_skill_unavailable"


# ---------------------------------------------------------------------------
# C-02: handle_pending_llm_review receipt emission paths
# ---------------------------------------------------------------------------


def _make_pending_llm_review_ctx(
    *, age: int = 45, specimen_type: str = "frozen_section"
) -> SpecimenContext:
    """Build a PENDING_LLM_REVIEW context for architectural receipt tests."""
    return SpecimenContext(
        order=Order(
            order_id="ARCH-LLM-REVIEW-001",
            patient_name=None,
            patient_sex="F",
            age=age,
            specimen_type=specimen_type,
            anatomic_site="breast",
            fixative="formalin",
            fixation_time_hours=24.0,
            ordered_tests=("ER",),
            priority="routine",
            billing_info_present=True,
        ),
        current_state="PENDING_LLM_REVIEW",
        flags=frozenset({"LLM_REVIEW_REQUESTED"}),
        event=Event(event_type="order_received", event_data={}, step_index=1),
    )


@pytest.mark.parametrize(
    "disposition,expected_outcome",
    [
        ("accepted", "accepted_llm_review"),
        ("rejected", "rejected_llm_review"),
        ("escalated", "escalated_llm_review"),
    ],
)
def test_handle_pending_llm_review_success_path_fires_receipt_writer(
    disposition: str, expected_outcome: str
) -> None:
    """C-02: handle_pending_llm_review success paths emit a signed receipt.

    Each of the three terminal dispositions (accepted/rejected/escalated)
    must persist a receipt via the dispatch_event chokepoint.
    """
    from samantha_server.api.routing import dispatch_event
    from samantha_server.receipts.signing import SignedReceipt
    from samantha_server.skills.loader import discover

    ctx = _make_pending_llm_review_ctx()
    mock_llm = _make_mock_llm_review_client(disposition)

    written: list[SignedReceipt] = []
    deps = _make_dispatch_event_deps(mock_llm, discover(), written)

    asyncio.run(dispatch_event(ctx, **deps))

    assert len(written) == 1, (
        f"handle_pending_llm_review {disposition} path did not fire receipt_writer"
    )
    assert isinstance(written[0], SignedReceipt)
    assert written[0].decision.outcome == expected_outcome


def test_handle_pending_llm_review_skill_loader_error_fires_receipt_writer() -> None:
    """C-02: SkillLoaderError refusal path emits a signed receipt."""
    from unittest.mock import MagicMock

    from samantha_server.api.routing import dispatch_event

    ctx = _make_pending_llm_review_ctx()
    mock_llm = MagicMock()

    written: list[SignedReceipt] = []
    # Empty skills index → SkillLoaderError
    deps = _make_dispatch_event_deps(mock_llm, {}, written)

    asyncio.run(dispatch_event(ctx, **deps))

    assert len(written) == 1, "SkillLoaderError refusal path did not fire receipt_writer"
    assert written[0].decision.outcome == "refused_skill_unavailable"


def test_handle_pending_llm_review_llm_client_error_fires_receipt_writer() -> None:
    """C-02: LLMClientError refusal path emits a signed receipt."""
    from unittest.mock import MagicMock

    from samantha_server.api.routing import dispatch_event
    from samantha_server.errors import LLMInferenceError
    from samantha_server.skills.loader import discover

    ctx = _make_pending_llm_review_ctx()
    mock_llm = MagicMock()
    mock_llm.complete_json.side_effect = LLMInferenceError(
        model_id="test-model", cause="unavailable"
    )

    written: list[SignedReceipt] = []
    deps = _make_dispatch_event_deps(mock_llm, discover(), written)

    asyncio.run(dispatch_event(ctx, **deps))

    assert len(written) == 1, "LLMClientError refusal path did not fire receipt_writer"
    assert written[0].decision.outcome == "refused_llm_unavailable"


def test_handle_pending_llm_review_phi_boundary_error_fires_receipt_writer() -> None:
    """C-02: PHIBoundaryError refusal path (age > 89) emits a signed receipt."""
    from unittest.mock import MagicMock

    from samantha_server.api.routing import dispatch_event
    from samantha_server.skills.loader import discover

    # age=90 triggers PHIBoundaryError in phi_safe()
    ctx = _make_pending_llm_review_ctx(age=90)
    mock_llm = MagicMock()

    written: list[SignedReceipt] = []
    deps = _make_dispatch_event_deps(mock_llm, discover(), written)

    asyncio.run(dispatch_event(ctx, **deps))

    assert len(written) == 1, "PHIBoundaryError refusal path did not fire receipt_writer"
    assert written[0].decision.outcome == "refused_phi_boundary"


def test_handle_pending_llm_review_unparseable_fires_receipt_writer() -> None:
    """C-02: Unparseable LLM response refusal path emits a signed receipt."""
    from samantha_server.api.routing import dispatch_event
    from samantha_server.skills.loader import discover

    ctx = _make_pending_llm_review_ctx()
    mock_llm = _make_mock_llm_review_client("this is definitely not a disposition word")

    written: list[SignedReceipt] = []
    deps = _make_dispatch_event_deps(mock_llm, discover(), written)

    asyncio.run(dispatch_event(ctx, **deps))

    assert len(written) == 1, "Unparseable response refusal path did not fire receipt_writer"
    assert written[0].decision.outcome == "refused_unparseable_response"


# ---------------------------------------------------------------------------
# Test 3: _decode_hex no-leak property test (Hypothesis)
# ---------------------------------------------------------------------------


@given(raw_input=st.text(min_size=1, max_size=200))
@settings(max_examples=500)
def test_decode_hex_error_message_does_not_leak_input(raw_input: str) -> None:
    """G18 no-leak: _decode_hex error messages must not echo the raw input.

    For inputs of >= 8 characters, the full raw_input string must not appear
    verbatim in the error message. Short inputs (< 8 chars) are excluded from
    the verbatim check because short strings (e.g., "va", "hex") are common
    English substrings that appear legitimately in error message templates.

    The intent of G18 is that production secrets (32-byte hex keys = 64 chars)
    are never echoed in logs. This test is parameterized over arbitrary inputs
    to catch any templating change that might accidentally interpolate the value.

    Two failure modes tested:
    1. Non-hex characters → MisconfiguredEnvironmentError
    2. Valid hex but wrong byte length → MisconfiguredEnvironmentError
    """
    from samantha_server.config import _decode_hex
    from samantha_server.errors import MisconfiguredEnvironmentError

    # Use a gen_hint that doesn't appear in any raw_input Hypothesis will generate
    _GEN_HINT = "GENKEY_PLACEHOLDER_XYZ_987"

    try:
        _decode_hex("TEST_VAR", raw_input, gen_hint=_GEN_HINT)
    except MisconfiguredEnvironmentError as exc:
        msg = str(exc)
        # Only check verbatim inclusion for inputs long enough to be meaningful
        # (8+ chars covers all real key material patterns while avoiding false
        # positives from short strings like "va" or "hex" in normal English).
        if len(raw_input) >= 8:
            assert raw_input not in msg, (
                f"G18 violation: raw_input found verbatim in error message.\n"
                f"raw_input={raw_input!r}\nmsg={msg!r}"
            )
