"""PHI strip/hash pipeline.

phi_safe(ctx) -> SafeContext is the only public entry point. Every
LLM-payload-construction path (action handlers in Step 8/9/10/11) MUST
route its context through this transform before any LLMClient.complete()
call.

G3  — allowlist over blocklist (hardcoded frozenset of pass-through Order field names).
G14 — Hypothesis profiles registered in tests/conftest.py.
G17 — HMAC-SHA256 hashed identifiers (NOT sha256(value || salt); length-extension prevention).
G18 — phi_safe raises PHIBoundaryError when ctx.order.age > 89.

No LLM-client imports in this module — only Pydantic + stdlib +
samantha_server.{config,errors,models}.

**event_data is PHI-bearing in production.** ``scenarios/replay.py``
extracts ``patient_name`` from ``event_data`` on the first-step event,
so production ``event_data`` payloads carry PHI. ``event_data_hash``
(HMAC-SHA256 over the canonical JSON encoding) is the only thing
preventing those names from reaching the LLM — the raw payload is
never serialized into ``SafeContext``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from types import MappingProxyType
from typing import Final

from pydantic import BaseModel

from samantha_server import config
from samantha_server.errors import PHIBoundaryError
from samantha_server.models.context import SpecimenContext

_logger = logging.getLogger(__name__)

# G3 — allowlist. Every Order field NOT in this set is PHI and will be
# hashed or stripped. "age" appears here because HIPAA Safe Harbor only
# flags age > 89; the G18 guard enforces that boundary in phi_safe().
#
# GH-367: "order_id" is now in this set (pass-through, not hashed). Rationale:
# order_id is a synthetic LIS identifier — it is NOT a HIPAA Safe Harbor element
# (not patient name, SSN, DOB, geographic subdivision, etc.). The model runs
# locally on oMLX inside the trust boundary (same topology that permits
# gen_ai.prompt stamping per GH-363). The raw order_id is already inside the
# receipt integrity envelope via event_input_hash, so hashing provides no
# additional security benefit within this deployment. The carve-out applies only
# to order_id; patient_name and patient_sex remain STRIPPED (see
# _STRIPPED_PHI_FIELDS), and event_data_hash remains HASHED.
_ORDER_PASS_THROUGH: Final[frozenset[str]] = frozenset(
    {
        "order_id",
        "specimen_type",
        "anatomic_site",
        "fixative",
        "fixation_time_hours",
        "ordered_tests",
        "priority",
        "billing_info_present",
        "age",
    }
)

# Order fields that are hashed (raw value never serialised; HMAC-SHA256
# digest carries forward for audit-trail correlation).
# GH-367: now empty by design — order_id was the only member and is now
# pass-through (see _ORDER_PASS_THROUGH rationale above).
_HASHED_PHI_FIELDS: Final[frozenset[str]] = frozenset()

# Order fields that are stripped entirely from SafeOrder. No representation
# (raw or hashed) reaches the LLM payload.
_STRIPPED_PHI_FIELDS: Final[frozenset[str]] = frozenset({"patient_name", "patient_sex"})


class SafeOrder(BaseModel, frozen=True):
    """Order with PHI fields hashed/stripped; pass-through fields preserved verbatim.

    GH-367: order_id is now a pass-through field (synthetic LIS id, not Safe Harbor,
    local oMLX inside trust boundary). See _ORDER_PASS_THROUGH rationale.
    """

    order_id: str  # GH-367: pass-through (was order_id_hash pre-GH-367)
    specimen_type: str | None
    anatomic_site: str | None
    fixative: str | None
    fixation_time_hours: float | None
    ordered_tests: tuple[str, ...]
    priority: str | None
    billing_info_present: bool
    age: int | None  # matches Order.age; None when unstated; >89 raises (G18)


class SafeContext(BaseModel, frozen=True):
    """SpecimenContext with PHI hashed/stripped for LLM consumption."""

    order: SafeOrder
    current_state: str
    flags: tuple[str, ...]  # sorted tuple — frozenset doesn't JSON-round-trip cleanly (G7)
    event_type: str
    # HMAC-SHA256 hex digest of the canonical JSON encoding of event.event_data
    # (G17). The raw payload is intentionally PHI-bearing in production
    # (patient_name lives in first-step event_data); hashing is the cover.
    event_data_hash: str


def _hmac_hex(value: bytes) -> str:
    """HMAC-SHA256 hex of value with PHI_HASH_SALT (G17 — NOT concat)."""
    return hmac.new(config.PHI_HASH_SALT, value, hashlib.sha256).hexdigest()


def _canonical_event_data(event_data: object) -> bytes:
    """Stable JSON-canonical encoding for hashing.

    Converts MappingProxyType (used by Event.event_data) to plain dict
    before serializing so json.dumps can handle it without a custom encoder.
    """
    if isinstance(event_data, MappingProxyType):
        event_data = dict(event_data)
    return json.dumps(event_data, sort_keys=True, separators=(",", ":")).encode()


def phi_safe(ctx: SpecimenContext) -> SafeContext:
    """Strip/hash PHI from ctx; return an LLM-safe shape.

    G3: allowlist over blocklist.
    G17: HMAC-SHA256 hashed identifiers (not length-extension concat).
    G18: raises PHIBoundaryError if ctx.order.age > 89.
    """
    if ctx.order.age is not None and ctx.order.age > 89:
        raise PHIBoundaryError(age=ctx.order.age)

    order = ctx.order
    safe_order = SafeOrder(
        order_id=order.order_id,
        specimen_type=order.specimen_type,
        anatomic_site=order.anatomic_site,
        fixative=order.fixative,
        fixation_time_hours=order.fixation_time_hours,
        ordered_tests=order.ordered_tests,
        priority=order.priority,
        billing_info_present=order.billing_info_present,
        age=order.age,
    )

    return SafeContext(
        order=safe_order,
        current_state=ctx.current_state,
        flags=tuple(sorted(ctx.flags)),
        event_type=ctx.event.event_type,
        event_data_hash=_hmac_hex(_canonical_event_data(ctx.event.event_data)),
    )


__all__ = [
    "SafeOrder",
    "SafeContext",
    "phi_safe",
    "_ORDER_PASS_THROUGH",
    "_HASHED_PHI_FIELDS",
    "_STRIPPED_PHI_FIELDS",
]
