"""Tests for dispatch-token rebinding (GH-119 feature b).

Covers:
- Cross-session replay rejected (token from session A fails in session B).
- Expired token rejected.
- Same-session within TTL passes.
- Tampered session_id, expires_at, rules each fail.
- Phase-2 token shape (without session_id binding) is rejected (regression guard).
- DispatchResult has session_id and expires_at fields.
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
import secrets
import time
from unittest.mock import patch

import pytest

from samantha_server.engine.dispatcher import (
    DispatchResult,
    list_applicable_rules,
    verify_dispatch,
)
from samantha_server.models.context import Event, Order, SpecimenContext
from samantha_server.rules.loader import RuleIndex


def _make_order() -> Order:
    return Order(
        order_id="RB-001",
        patient_name="Jane Doe",
        patient_sex="F",
        age=45,
        specimen_type="biopsy",
        anatomic_site="breast",
        fixative="formalin",
        fixation_time_hours=8.0,
        ordered_tests=("HER2",),
        priority="ROUTINE",
        billing_info_present=True,
    )


def _make_ctx(state: str = "ACCESSIONING", event_type: str = "order_received") -> SpecimenContext:
    return SpecimenContext(
        order=_make_order(),
        current_state=state,
        flags=frozenset(),
        event=Event(event_type=event_type, event_data={}, step_index=0),
    )


@pytest.fixture
def empty_rule_index() -> RuleIndex:
    return RuleIndex([])


# ---------------------------------------------------------------------------
# DispatchResult schema — new fields
# ---------------------------------------------------------------------------


def test_dispatch_result_has_session_id_field() -> None:
    """DispatchResult must have a session_id field (str)."""
    ctx = _make_ctx()
    idx = RuleIndex([])
    result = list_applicable_rules(ctx, idx, session_id="test-session", ttl_sec=60)
    assert hasattr(result, "session_id")
    assert result.session_id == "test-session"


def test_dispatch_result_has_expires_at_field() -> None:
    """DispatchResult must have an expires_at field (int, unix timestamp)."""
    ctx = _make_ctx()
    idx = RuleIndex([])
    now = int(time.time())
    result = list_applicable_rules(ctx, idx, session_id="test-session", ttl_sec=60)
    assert hasattr(result, "expires_at")
    # expires_at should be close to now + 60
    assert now <= result.expires_at <= now + 70


def test_dispatch_result_token_length() -> None:
    """Token is still 64 bytes (32-byte HMAC + 32-byte nonce)."""
    ctx = _make_ctx()
    idx = RuleIndex([])
    result = list_applicable_rules(ctx, idx, session_id="test-session", ttl_sec=60)
    assert len(result.token) == 64


# ---------------------------------------------------------------------------
# Same-session, within TTL — passes
# ---------------------------------------------------------------------------


def test_same_session_within_ttl_passes() -> None:
    """Token issued and verified within TTL for the same session passes."""
    ctx = _make_ctx()
    idx = RuleIndex([])
    dispatch = list_applicable_rules(ctx, idx, session_id="session-A", ttl_sec=60)
    assert verify_dispatch(dispatch, session_id="session-A") is True


def test_same_session_multiple_tokens_each_verify() -> None:
    """Each list_applicable_rules call issues an independent token; all verify."""
    ctx = _make_ctx()
    idx = RuleIndex([])
    d1 = list_applicable_rules(ctx, idx, session_id="session-X", ttl_sec=60)
    d2 = list_applicable_rules(ctx, idx, session_id="session-X", ttl_sec=60)
    assert d1.token != d2.token  # distinct nonces
    assert verify_dispatch(d1, session_id="session-X") is True
    assert verify_dispatch(d2, session_id="session-X") is True


# ---------------------------------------------------------------------------
# Cross-session replay — rejected
# ---------------------------------------------------------------------------


def test_cross_session_replay_rejected() -> None:
    """Token from session A fails verification when presented in session B."""
    ctx = _make_ctx()
    idx = RuleIndex([])
    dispatch_a = list_applicable_rules(ctx, idx, session_id="session-A", ttl_sec=60)
    # Verify with session B's id — must fail.
    assert verify_dispatch(dispatch_a, session_id="session-B") is False


def test_cross_session_replay_rejected_empty_rules() -> None:
    """Cross-session replay is rejected even for empty-rules dispatch."""
    ctx = _make_ctx("ORDER_COMPLETE", "order_closed")
    idx = RuleIndex([])
    dispatch = list_applicable_rules(ctx, idx, session_id="session-A", ttl_sec=60)
    assert dispatch.rules == ()
    assert verify_dispatch(dispatch, session_id="session-B") is False


# ---------------------------------------------------------------------------
# Expired token — rejected
# ---------------------------------------------------------------------------


def test_expired_token_rejected() -> None:
    """Token with expires_at < now fails verify_dispatch."""
    ctx = _make_ctx()
    idx = RuleIndex([])

    # Issue a token with a past expires_at by mocking time.time() during issuance.
    past = int(time.time()) - 120  # 2 minutes ago
    with patch("samantha_server.engine.dispatcher.time") as mock_time:
        mock_time.time.return_value = float(past)
        # ttl=1 means expires_at = past + 1 = past + 1 second (still in the past)
        dispatch = list_applicable_rules(ctx, idx, session_id="session-A", ttl_sec=1)

    assert dispatch.expires_at == past + 1
    assert dispatch.expires_at < int(time.time())
    assert verify_dispatch(dispatch, session_id="session-A") is False


def test_same_session_expired_boundary() -> None:
    """Token expires exactly at the boundary — test TTL boundary semantics."""
    ctx = _make_ctx()
    idx = RuleIndex([])

    fixed_now = int(time.time())

    with patch("samantha_server.engine.dispatcher.time") as mock_time:
        mock_time.time.return_value = float(fixed_now)
        dispatch = list_applicable_rules(ctx, idx, session_id="session-A", ttl_sec=60)

    # Token should still be valid when time hasn't advanced
    with patch("samantha_server.engine.dispatcher.time") as mock_time:
        mock_time.time.return_value = float(fixed_now + 59)
        assert verify_dispatch(dispatch, session_id="session-A") is True

    # Token should be expired when time advances past expires_at
    with patch("samantha_server.engine.dispatcher.time") as mock_time:
        mock_time.time.return_value = float(fixed_now + 61)
        assert verify_dispatch(dispatch, session_id="session-A") is False


def test_dispatch_token_valid_at_exact_expires_at() -> None:
    """Token with expires_at == fixed_now is still valid (strict < semantics).

    verify_dispatch uses `dispatch.expires_at < int(time.time())` — so a token
    where expires_at exactly equals now is NOT expired.  Pin this invariant.
    GH-119 fix #16.
    """
    ctx = _make_ctx()
    idx = RuleIndex([])

    fixed_now = int(time.time())

    with patch("samantha_server.engine.dispatcher.time") as mock_time:
        mock_time.time.return_value = float(fixed_now)
        dispatch = list_applicable_rules(ctx, idx, session_id="session-A", ttl_sec=60)

    # expires_at = fixed_now + 60; verify at exactly expires_at → still valid
    with patch("samantha_server.engine.dispatcher.time") as mock_time:
        mock_time.time.return_value = float(fixed_now + 60)
        assert verify_dispatch(dispatch, session_id="session-A") is True


def test_dispatch_token_expired_one_second_after_expires_at() -> None:
    """Token with expires_at == now - 1 is expired (strict < semantics).

    GH-119 fix #16.
    """
    ctx = _make_ctx()
    idx = RuleIndex([])

    fixed_now = int(time.time())

    with patch("samantha_server.engine.dispatcher.time") as mock_time:
        mock_time.time.return_value = float(fixed_now)
        dispatch = list_applicable_rules(ctx, idx, session_id="session-A", ttl_sec=60)

    # expires_at = fixed_now + 60; verify at fixed_now + 61 → expired
    with patch("samantha_server.engine.dispatcher.time") as mock_time:
        mock_time.time.return_value = float(fixed_now + 61)
        assert verify_dispatch(dispatch, session_id="session-A") is False


# ---------------------------------------------------------------------------
# Tampered token — each mutation fails
# ---------------------------------------------------------------------------


def test_tampered_session_id_in_dispatch_result_rejected() -> None:
    """Changing session_id on a DispatchResult (without recomputing HMAC) fails."""
    ctx = _make_ctx()
    idx = RuleIndex([])
    dispatch = list_applicable_rules(ctx, idx, session_id="real-session", ttl_sec=60)

    # Construct a tampered result with a different session_id but the same token.
    tampered = DispatchResult(
        rules=dispatch.rules,
        token=dispatch.token,
        session_id="forged-session",  # tampered
        expires_at=dispatch.expires_at,
    )
    # Verification with "forged-session" must fail (HMAC won't match).
    assert verify_dispatch(tampered, session_id="forged-session") is False


def test_tampered_expires_at_rejected() -> None:
    """Extending expires_at without recomputing HMAC fails verification."""
    ctx = _make_ctx()
    idx = RuleIndex([])
    dispatch = list_applicable_rules(ctx, idx, session_id="session-A", ttl_sec=60)

    # Extend expires_at by 10000 seconds without recomputing HMAC.
    tampered = DispatchResult(
        rules=dispatch.rules,
        token=dispatch.token,
        session_id=dispatch.session_id,
        expires_at=dispatch.expires_at + 10000,  # tampered
    )
    assert verify_dispatch(tampered, session_id="session-A") is False


def test_tampered_rules_rejected() -> None:
    """Modifying rules without recomputing HMAC fails verification.

    GH-119 fix #17: synthesize a DispatchResult with two known rules so the
    test never passes vacuously when the corpus returns only 1 rule.  We build
    a second dispatch from a different ctx and reuse its rule tuple — both
    rule tuples are valid RuleSpec objects; we just mix them to get >=2 entries.
    """
    import pathlib

    from samantha_server.rules.loader import load_rule_specs

    specs_dir = pathlib.Path(__file__).parent.parent.parent / "samantha_server" / "rules" / "specs"
    idx = RuleIndex(load_rule_specs(specs_dir))

    # Build a dispatch for an ACCESSIONING context to get >=2 ACC rules.
    # (The ACCESSIONING corpus always dispatches multiple ACC rules for a biopsy order.)
    ctx = _make_ctx("ACCESSIONING", "order_received")
    dispatch = list_applicable_rules(ctx, idx, session_id="session-A", ttl_sec=60)

    if len(dispatch.rules) < 2:
        # Corpus has only 1 ACCESSIONING rule — explicitly skip rather than pass vacuously.
        pytest.skip(
            "ACCESSIONING corpus returned only 1 rule; cannot test tampered-rules rejection"
        )

    tampered = DispatchResult(
        rules=dispatch.rules[:-1],  # drop last rule
        token=dispatch.token,
        session_id=dispatch.session_id,
        expires_at=dispatch.expires_at,
    )
    assert verify_dispatch(tampered, session_id="session-A") is False


def test_zeroed_token_rejected() -> None:
    """All-zero token fails verification."""
    ctx = _make_ctx()
    idx = RuleIndex([])
    dispatch = list_applicable_rules(ctx, idx, session_id="session-A", ttl_sec=60)

    forged = DispatchResult(
        rules=dispatch.rules,
        token=b"\x00" * 64,
        session_id=dispatch.session_id,
        expires_at=dispatch.expires_at,
    )
    assert verify_dispatch(forged, session_id="session-A") is False


# ---------------------------------------------------------------------------
# Phase-2 token shape regression guard
# ---------------------------------------------------------------------------


def test_phase2_token_shape_rejected() -> None:
    """A Phase-2-style token (HMAC over rules||nonce only, no session_id) is rejected.

    Constructs an old-format token inline and asserts verify_dispatch rejects it.
    This guards against any accidental reversion to the old HMAC construction.
    """
    import samantha_server.engine.dispatcher as disp_mod

    # Replicate the Phase-2 _make_token logic (without session_id / expires_at).
    rules_bytes = b""  # empty rules for simplicity
    nonce = secrets.token_bytes(32)

    # Old HMAC: key over rules_bytes + b":" + nonce (Phase-2 format)
    old_mac = _hmac.new(
        disp_mod._DISPATCH_HMAC_KEY,
        rules_bytes + b":" + nonce,
        hashlib.sha256,
    ).digest()
    old_token = old_mac + nonce  # 64 bytes total

    # Wrap in a DispatchResult with session_id and expires_at to satisfy the schema.
    old_dispatch = DispatchResult(
        rules=(),
        token=old_token,
        session_id="session-A",
        expires_at=int(time.time()) + 3600,
    )

    # Must be rejected — the new verifier includes session_id and expires_at in the HMAC.
    assert verify_dispatch(old_dispatch, session_id="session-A") is False
