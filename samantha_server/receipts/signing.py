"""Ed25519 signing and verification for EngineDecision receipts.

Uses PyNaCl (nacl.signing) with libsodium's randombytes as the entropy source.
No LLM client imports; no PHI in payload (event_input_hash only).

Canonical JSON payload uses _make_serialisable + json.dumps(sort_keys=True)
— the same path Phase 1 proved stable for event_input_hash.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import UTC, datetime
from enum import Enum

import nacl.exceptions
import nacl.signing
from pydantic import BaseModel, field_validator

from samantha_server.engine.decision import EngineDecision, _make_serialisable
from samantha_server.errors import MisconfiguredEnvironmentError

# ---------------------------------------------------------------------------
# H-09: module-level key caches (avoid re-running SHA-512 + scalar multiply on
# every sign/verify call when the seed bytes are the same process-wide constant).
# Keyed by seed bytes so multiple keys (current + previous) can coexist.
# ---------------------------------------------------------------------------

_signing_key_cache: dict[bytes, nacl.signing.SigningKey] = {}
_verify_key_cache: dict[bytes, nacl.signing.VerifyKey] = {}


def _get_signing_key(seed: bytes) -> nacl.signing.SigningKey:
    """Return a cached SigningKey for *seed*, constructing once per unique seed."""
    if seed not in _signing_key_cache:
        _signing_key_cache[seed] = nacl.signing.SigningKey(seed)
    return _signing_key_cache[seed]


def _get_verify_key(seed: bytes) -> nacl.signing.VerifyKey:
    """Return a cached VerifyKey for *seed*, constructing once per unique seed."""
    if seed not in _verify_key_cache:
        _verify_key_cache[seed] = nacl.signing.SigningKey(seed).verify_key
    return _verify_key_cache[seed]


# ---------------------------------------------------------------------------
# ULID — inline implementation (avoids python-ulid dep; 48-bit ms timestamp
# + 80-bit cryptographic random, Crockford base32 encoding).
# ---------------------------------------------------------------------------

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")


def _generate_ulid() -> str:
    """Generate a new ULID string using os.urandom for the random component.

    G19: ULID format — 10 chars timestamp (48-bit ms) + 16 chars random (80-bit).
    Total: 26 Crockford base32 characters. Monotonically sortable by creation time.
    """
    ms = int(time.time() * 1000)
    # Encode 48-bit timestamp into 10 Crockford chars (5 bits each = 50 bits;
    # we use 48 bits of the timestamp, left-justified).
    ts_chars: list[str] = []
    v = ms
    for _ in range(10):
        ts_chars.append(_CROCKFORD[v & 0x1F])
        v >>= 5
    ts_chars.reverse()

    # 80-bit random = 10 bytes
    rand_bytes = os.urandom(10)
    rand_int = int.from_bytes(rand_bytes, "big")  # 80-bit integer

    rand_chars: list[str] = []
    for _ in range(16):
        rand_chars.append(_CROCKFORD[rand_int & 0x1F])
        rand_int >>= 5
    rand_chars.reverse()

    return "".join(ts_chars) + "".join(rand_chars)


# ---------------------------------------------------------------------------
# SignedReceipt
# ---------------------------------------------------------------------------


class SignedReceipt(BaseModel, frozen=True):
    """An EngineDecision bundled with an Ed25519 detached signature.

    receipt_id: Crockford-base32 ULID (26 chars)
    decision: the EngineDecision that was signed
    signer_key_id: identifies which key generation produced this receipt
    signature: 64-byte Ed25519 detached signature over canonical payload
    signed_at_utc: tz-aware UTC timestamp of signing
    """

    receipt_id: str
    decision: EngineDecision
    signer_key_id: str
    signature: bytes
    signed_at_utc: datetime

    @field_validator("receipt_id")
    @classmethod
    def _validate_ulid(cls, v: str) -> str:
        if not _ULID_RE.match(v):
            raise ValueError(f"receipt_id must be a Crockford-base32 ULID; got {v!r}")
        return v

    @field_validator("signature")
    @classmethod
    def _validate_signature_len(cls, v: bytes) -> bytes:
        if len(v) != 64:
            raise ValueError(f"Ed25519 detached signature must be exactly 64 bytes; got {len(v)}")
        return v

    @field_validator("signed_at_utc")
    @classmethod
    def _enforce_utc(cls, v: datetime) -> datetime:
        if not _is_utc(v):
            raise ValueError(f"signed_at_utc must be tz-aware UTC; got {v!r}")
        return v


# ---------------------------------------------------------------------------
# UTC helpers
# ---------------------------------------------------------------------------


def _is_utc(dt: datetime) -> bool:
    """Return True iff *dt* is tz-aware with a zero UTC offset.

    Accepts any zero-offset tzinfo (e.g. timezone.utc, timezone(timedelta(0)),
    or pytz.UTC) rather than requiring identity with datetime.timezone.utc.
    In practice all paths through this codebase use datetime.now(tz=UTC),
    so the non-UTC identity zero-offset case only arises in tests using
    alternative UTC tzinfo instances.
    """
    return dt.tzinfo is not None and dt.utcoffset() == UTC.utcoffset(None)


# ---------------------------------------------------------------------------
# Canonical payload
# ---------------------------------------------------------------------------


def _canonical_signing_payload(decision: EngineDecision) -> bytes:
    """Produce deterministic bytes for Ed25519 signing.

    Path: model_dump(mode="python", exclude_defaults=True) → re-inject
    `kind` for non-empty decision_traces → _make_serialisable →
    json.dumps(sort_keys=True, separators=(",",":")).encode().

    WHY exclude_defaults=True (G20): Fields with default values on
    EngineDecision (e.g. decision_traces=()) must be omitted so that a
    Phase-1-shape receipt (no decision_traces field) produces byte-identical
    canonical JSON to a Phase-2 receipt with an empty decision_traces tuple.
    Without this, pre-Step-6 receipts would fail signature verification after
    the field was added.  Additionally, Pydantic minor-version upgrades can
    alter key ordering in model_dump_json() output — using our own sorted
    json.dumps() avoids that class of serialization instability entirely.

    WHY re-inject `kind`: Pydantic's exclude_defaults=True also strips the
    `kind` discriminator from nested DecisionTrace objects (e.g.,
    QueryTrace.kind="query" is a Literal default). Without `kind` in the
    serialized form, round-trip deserialization via model_validate_json() fails
    because the union discriminator is missing. Re-injecting `kind` from the
    live Python objects restores the discriminator without altering the backward-
    compat guarantee (empty decision_traces is still omitted, so Phase-1-shape
    receipts remain byte-identical to pre-Step-6 payloads).
    """
    raw = decision.model_dump(mode="python", exclude_defaults=True)

    # Re-inject `kind` discriminator for each DecisionTrace in the payload.
    # exclude_defaults=True strips Literal default fields (e.g. kind="query");
    # we restore them so rehydration via model_validate_json() can identify
    # the union variant.
    #
    # H-02: enforce that model_dump produced the same number of trace entries
    # as the live decision_traces tuple. A mismatch means exclude_defaults=True
    # unexpectedly omitted a trace entry; signing with a missing discriminator
    # would produce a VALID-but-unrehydratable receipt (defect surfaces only at
    # audit read time). Raise loudly so the bug is caught at signing time.
    if decision.decision_traces:
        raw_traces = raw.get("decision_traces", [])
        if len(raw_traces) != len(decision.decision_traces):
            raise RuntimeError(
                f"kind-injection: model_dump produced {len(raw_traces)} "
                f"decision_trace entries vs {len(decision.decision_traces)} "
                "live traces. Cannot safely inject kind discriminators; "
                "refusing to sign an incomplete payload."
            )
        for i, trace in enumerate(decision.decision_traces):
            if isinstance(raw_traces[i], dict):
                # All DecisionTrace variants define a `kind` Literal field.
                # Mypy resolves the union and can access .kind directly.
                raw_traces[i]["kind"] = trace.kind
        raw["decision_traces"] = raw_traces

    normalised = _make_serialisable(raw)
    canonical = json.dumps(normalised, sort_keys=True, separators=(",", ":"))
    return canonical.encode()


# ---------------------------------------------------------------------------
# Signing
# ---------------------------------------------------------------------------


def sign_decision(
    decision: EngineDecision,
    *,
    key_id: str,
    signing_key: bytes,
) -> SignedReceipt:
    """Sign *decision* with *signing_key* and return a new SignedReceipt.

    Parameters
    ----------
    decision:
        The EngineDecision to sign.
    key_id:
        The identifier for this key generation (e.g. "v1", "v2"). Stored
        verbatim in the receipt so rotations can identify which key signed.
    signing_key:
        32-byte Ed25519 seed (as returned by nacl.signing.SigningKey.generate()).
    """
    nacl_key = _get_signing_key(signing_key)
    payload = _canonical_signing_payload(decision)
    signed = nacl_key.sign(payload)
    # nacl.signing.SignedMessage = signature (64 bytes) + message
    signature = bytes(signed.signature)

    return SignedReceipt(
        receipt_id=_generate_ulid(),
        decision=decision,
        signer_key_id=key_id,
        signature=signature,
        signed_at_utc=datetime.now(tz=UTC),
    )


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


class VerificationResult(Enum):
    """Result of verifying a SignedReceipt's Ed25519 signature.

    VALID: signature verified against current or previous key.
    INVALID_SIGNATURE: signer_key_id matched a known key but signature failed.
    KEY_EXPIRED: signer_key_id matches neither current nor previous key
        (older than two rotations).
    """

    VALID = "valid"
    INVALID_SIGNATURE = "invalid_signature"
    KEY_EXPIRED = "key_expired"


def verify_signature(
    receipt: SignedReceipt,
    *,
    current_key: bytes,
    current_key_id: str,
    previous_key: bytes | None = None,
    previous_key_id: str | None = None,
) -> VerificationResult:
    """Verify *receipt*'s signature against the supplied key(s).

    Parameters
    ----------
    receipt:
        The SignedReceipt to verify.
    current_key:
        32-byte seed for the current signing key.
    current_key_id:
        Key id string for the current key.
    previous_key:
        32-byte seed for the previous (one rotation back) key, or None.
    previous_key_id:
        Key id string for the previous key, or None.

    Returns
    -------
    VerificationResult.VALID
        signature verified (current or previous key).
    VerificationResult.INVALID_SIGNATURE
        signer_key_id matched a known key, but verification failed.
    VerificationResult.KEY_EXPIRED
        signer_key_id is not in the accepted set (older than two rotations).
    """
    # H-03: Reject the "forgotten id-bump" misconfiguration. When both ids are
    # identical, the previous-key entry would silently overwrite the current-key
    # entry in key_candidates, causing the verifier to accept only previous-key
    # signatures even if the key bytes differ. This is a config-time bug.
    if previous_key is not None and previous_key_id == current_key_id:
        raise MisconfiguredEnvironmentError(
            f"RECEIPT_SIGNING_KEY_ID and RECEIPT_SIGNING_KEY_PREVIOUS_ID are both "
            f"{current_key_id!r}. The key rotation id must be bumped when the key "
            f"material changes. Generate a new key via: "
            f"python -m samantha_server.receipts.signing --gen-key"
        )

    # Build the map of key_id → verify_key from known keys (H-09: cached)
    key_candidates: dict[str, nacl.signing.VerifyKey] = {}
    key_candidates[current_key_id] = _get_verify_key(current_key)
    if previous_key is not None and previous_key_id is not None:
        key_candidates[previous_key_id] = _get_verify_key(previous_key)

    if receipt.signer_key_id not in key_candidates:
        return VerificationResult.KEY_EXPIRED

    verify_key = key_candidates[receipt.signer_key_id]
    payload = _canonical_signing_payload(receipt.decision)
    try:
        verify_key.verify(payload, receipt.signature)
        return VerificationResult.VALID
    except nacl.exceptions.BadSignatureError:
        return VerificationResult.INVALID_SIGNATURE


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Receipt signing utilities")
    parser.add_argument(
        "--gen-key",
        action="store_true",
        help="Generate a new Ed25519 signing key and print the 64-char hex seed to stdout.",
    )
    args = parser.parse_args()

    if args.gen_key:
        seed = bytes(nacl.signing.SigningKey.generate())
        print(seed.hex())


__all__ = [
    "SignedReceipt",
    "VerificationResult",
    "sign_decision",
    "verify_signature",
]
