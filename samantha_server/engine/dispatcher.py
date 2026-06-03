"""Dispatcher — maps a SpecimenContext to the ordered list of applicable RuleSpecs.

list_applicable_rules is the single entry point to evaluation.  It filters by:
  1. applies_at (IHC states) or STATE_TO_STEP (non-IHC states)
  2. event_type match

Returns a DispatchResult with the ordered tuple of rules and a cryptographic token
that the evaluator uses to enforce the dispatch boundary.

Threat model (GH-119 Phase 3 Step 5 — dispatch-token rebinding):
- Token HMAC now binds rules, nonce, session_id, and expires_at.
- Cross-session replay is rejected: token from session A fails session B because
  the session_id is part of the HMAC input.  NOTE: this guard is only live when
  the *caller* passes its own session_id to evaluate().  As of Step 5, production
  callers (POST /events → dispatch_event) do not yet invoke verify_dispatch;
  enforcement lands with Step 5.5+ wiring.
- TTL replay is rejected: verify_dispatch checks expires_at < time.time().
- Token forgery requires knowing _DISPATCH_HMAC_KEY (module-private random).

Deterministic-path purity note:
- `time` is imported for TTL computation (time.time() in list_applicable_rules
  and verify_dispatch). Allowlisted in tests/architectural/test_deterministic_purity.py
  under ("samantha_server.engine.dispatcher", "time") per GH-119 rationale.
- `secrets` is imported for nonce generation (unchanged from Phase 2).
- No LLM imports. Rule selection is deterministic given (ctx, index).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from pydantic import BaseModel

from samantha_server.engine.dispatch_constants import STATE_TO_STEP
from samantha_server.models.context import SpecimenContext
from samantha_server.rules.loader import RuleIndex
from samantha_server.rules.spec import RuleSpec

# Module-private HMAC key — generated once per process lifetime.
# Not exported; lives only in this module's namespace.
_DISPATCH_HMAC_KEY: bytes = secrets.token_bytes(32)


def _serialize_rules(rules: tuple[RuleSpec, ...]) -> bytes:
    """Build a stable byte string from rule ids for HMAC computation."""
    return b"|".join(r.rule_id.encode() for r in rules)


def _make_token(
    rules: tuple[RuleSpec, ...],
    nonce: bytes,
    session_id: str,
    expires_at: int,
) -> bytes:
    """Return HMAC-SHA256(key, rules_bytes || nonce || session_id || expires_at).

    GH-119 Phase 3 Step 5: binds the token to session_id and expires_at.
    The Phase-2 format (rules_bytes + b":" + nonce) is replaced by:
        rules_bytes + b"|" + nonce + b"|" + session_id.encode() + b"|" + str(expires_at).encode()
    """
    msg = (
        _serialize_rules(rules)
        + b"|"
        + nonce
        + b"|"
        + session_id.encode()
        + b"|"
        + str(expires_at).encode()
    )
    return hmac.new(_DISPATCH_HMAC_KEY, msg, hashlib.sha256).digest()


def verify_dispatch(dispatch: DispatchResult, *, session_id: str) -> bool:
    """Return True iff *dispatch* was produced by list_applicable_rules in this process.

    GH-119 Phase 3 Step 5 additions:
    - session_id must match dispatch.session_id (cross-session replay rejected).
    - dispatch.expires_at must be >= time.time() (TTL enforcement).
    - HMAC is recomputed over (rules, nonce, session_id, expires_at).

    Returns False on any mismatch; never raises.
    """
    # Cross-session replay guard.
    if session_id != dispatch.session_id:
        return False

    # TTL guard.
    if dispatch.expires_at < int(time.time()):
        return False

    nonce = dispatch.token[-32:]  # last 32 bytes are the nonce
    mac = dispatch.token[:-32]  # first 32 bytes are the HMAC
    expected = _make_token(dispatch.rules, nonce, dispatch.session_id, dispatch.expires_at)
    return hmac.compare_digest(expected, mac)


class DispatchResult(BaseModel, frozen=True):
    """Return value of list_applicable_rules.

    rules:      ordered tuple of RuleSpecs eligible to fire.
    token:      opaque bytes = HMAC(key, rules || nonce || session_id || expires_at) || nonce.
                Callers must not construct this directly; use list_applicable_rules.
    session_id: session identifier bound into the HMAC (GH-119 Step 5).
    expires_at: unix timestamp; token is invalid after this time (GH-119 Step 5).
    """

    rules: tuple[RuleSpec, ...]
    token: bytes
    session_id: str
    expires_at: int = 0  # default 0; verify_dispatch rejects 0 (always expired)


def list_applicable_rules(
    ctx: SpecimenContext,
    index: RuleIndex,
    *,
    session_id: str,
    ttl_sec: int,
) -> DispatchResult:
    """Return the ordered RuleSpecs applicable to *ctx* and a fresh dispatch token.

    Parameters
    ----------
    ctx:
        The specimen context to evaluate.
    index:
        The loaded RuleIndex.
    session_id:
        Session identifier bound into the dispatch token HMAC. Required.
        Cross-session replay is rejected at verify_dispatch time.
    ttl_sec:
        Token time-to-live in seconds. Callers read this from config and inject;
        dispatcher.py must not import samantha_server.config.

    Dispatch order:
      1. If ctx.current_state is a key in index.rules_by_applies_at (IHC state),
         use that list.
      2. Otherwise map ctx.current_state via STATE_TO_STEP to a step name, then
         look up index.rules_by_step[step].
      3. If neither matches, return an empty tuple (pass-through / terminal state).

    After selecting the candidate pool, filter to rules where ctx.event.event_type
    is in rule.event_type.

    The returned tuple preserves the ordering established by RuleIndex (severity for
    ACCESSIONING, priority for all other steps).
    """
    state = ctx.current_state
    event_type = ctx.event.event_type

    # Step 1: IHC dispatch by applies_at.
    if state in index.rules_by_applies_at:
        candidates = index.rules_by_applies_at[state]
    else:
        # Step 2: non-IHC dispatch by step mapping.
        step = STATE_TO_STEP.get(state)
        candidates = index.rules_by_step.get(step, []) if step is not None else []

    # Filter by event_type.
    filtered = tuple(rule for rule in candidates if event_type in rule.event_type)

    expires_at = int(time.time()) + ttl_sec
    nonce = secrets.token_bytes(32)
    token = _make_token(filtered, nonce, session_id, expires_at) + nonce
    return DispatchResult(
        rules=filtered,
        token=token,
        session_id=session_id,
        expires_at=expires_at,
    )
