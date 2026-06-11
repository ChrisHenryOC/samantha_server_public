"""Preflight checks for the LLM router-shim.

Detects degenerate inputs (missing required Order fields, or unknown values that
canonicalization passes through with was_unknown=True) before dispatch reaches the
deterministic engine.

No imports from samantha_server.engine.dispatcher or samantha_server.engine.evaluator
— preserves the deterministic-path-purity invariant from the LLM side. Lives under
llm/ because the router-shim consumes it.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

from samantha_server.canonicalization import CANONICAL_FIELDS, canonicalize
from samantha_server.models.context import SpecimenContext

# ---------------------------------------------------------------------------
# Required-order-fields sentinel
# ---------------------------------------------------------------------------

# Empty by design: every Optional Order field that has a deterministic
# null-handling rule (ACC-001 patient_name, ACC-002 patient_sex, ACC-009
# fixation_time_hours) does NOT belong here — the rules engine already
# handles those nulls gracefully. The field 'age' has no rule but is
# HIPAA Safe Harbor-allowlisted and not load-bearing for dispatch.
# Forward-compatible: future Order fields without null-handling rules can
# be added here and preflight will route them to clarification automatically.
REQUIRED_ORDER_FIELDS: frozenset[str] = frozenset()

# ---------------------------------------------------------------------------
# Canonicalized-order-fields sentinel
# ---------------------------------------------------------------------------

# Scalar (str-typed) Order fields whose canonical pick list preflight inspects.
# 'specimen_type' and 'anatomic_site' are intentionally OMITTED: a dedicated
# accessioning rule already routes unknown values for each to
# PENDING_LLM_REVIEW — ACC-010 for specimen_type, ACC-011 for anatomic_site.
# If either were included here, preflight would short-circuit its rule (once
# the router-shim is the production gate), replacing the documented
# PENDING_LLM_REVIEW outcome with `outcome="needs_clarification"`. Leaving both
# to the rule corpus keeps it the authoritative router for those fields (
# SC-115 / LR-006 both expect ACC-011, not preflight clarification, for an
# off-vocab anatomic_site). 'ordered_tests' is tuple-typed and excluded by
# construction (preflight uses isinstance(raw, str)).
CANONICALIZED_ORDER_FIELDS: frozenset[str] = frozenset(
    {
        "fixative",
        "priority",
    }
)

# Materialize the iteration order once so preflight() doesn't allocate a
# fresh sorted list on every call.
_SORTED_CANONICALIZED_FIELDS: tuple[str, ...] = tuple(sorted(CANONICALIZED_ORDER_FIELDS))

# Guard against drift between CANONICALIZED_ORDER_FIELDS and the canonical
# pick-list data. If a field is removed from canonical-fields.json but still
# listed here, preflight would silently pass every value for that field.
# explicit raise so the guard survives Python -O.
# No dedicated unit test for this guard: the guard fires at import time and
# would require mutating the module-level constant to test in isolation;
# the -O subprocess pattern from test_rule_index covers the shape generically.
if not (CANONICALIZED_ORDER_FIELDS <= CANONICAL_FIELDS):
    raise RuntimeError(
        f"CANONICALIZED_ORDER_FIELDS contains fields not in CANONICAL_FIELDS: "
        f"{CANONICALIZED_ORDER_FIELDS - CANONICAL_FIELDS}"
    )


# ---------------------------------------------------------------------------
# PreflightResult — discriminated union
# ---------------------------------------------------------------------------


class PreflightOk(BaseModel, frozen=True):
    """Preflight succeeded — all required fields present, all canonicalized
    fields have known values. Safe to proceed to deterministic dispatch."""

    kind: Literal["ok"] = "ok"


class PreflightMissing(BaseModel, frozen=True):
    """Preflight failed — at least one required field is None or at least one
    canonicalized field has an unknown value. Route to handle_clarification."""

    kind: Literal["missing"] = "missing"
    missing_fields: tuple[str, ...] = ()
    unknown_canonical_fields: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _at_least_one_non_empty(self) -> PreflightMissing:
        if not self.missing_fields and not self.unknown_canonical_fields:
            raise ValueError(
                "PreflightMissing requires at least one non-empty tuple "
                "(missing_fields or unknown_canonical_fields); construct "
                "PreflightOk() when no checks failed."
            )
        return self


PreflightResult = Annotated[
    PreflightOk | PreflightMissing,
    Field(discriminator="kind"),
]


# ---------------------------------------------------------------------------
# Preflight function
# ---------------------------------------------------------------------------


def preflight(ctx: SpecimenContext) -> PreflightOk | PreflightMissing:
    """Detect missing or unknown-canonical fields before dispatch.

    Uses `is None` rather than truthiness — falsy-but-valid values
    (fixation_time_hours == 0.0, billing_info_present is False,
    ordered_tests == ()) must not be treated as missing.

    Unknown canonical values are detected by calling canonicalize() directly
    rather than reading any flag/trace produced by the evaluator. Preflight
    runs *before* evaluate(); no flag bus is involved.

    Returns
    -------
    PreflightOk | PreflightMissing
        PreflightOk() if all required fields present and all canonicalized
        fields have known values; PreflightMissing(...) with the missing/
        unknown field list otherwise.
    """
    # Check for missing required fields (is None, not falsy). Sorted at the
    # source so PreflightMissing.missing_fields is deterministic regardless
    # of frozenset hash-randomization (PYTHONHASHSEED).
    missing = sorted(f for f in REQUIRED_ORDER_FIELDS if getattr(ctx.order, f) is None)

    # Check for unknown canonical values in scalar str-typed fields.
    unknown_canonical: list[str] = []
    for f in _SORTED_CANONICALIZED_FIELDS:
        raw = getattr(ctx.order, f, None)
        if isinstance(raw, str) and canonicalize(f, raw).was_unknown:
            unknown_canonical.append(f)

    if not missing and not unknown_canonical:
        return PreflightOk()

    return PreflightMissing(
        missing_fields=tuple(missing),
        unknown_canonical_fields=tuple(unknown_canonical),
    )


__all__ = [
    "CANONICALIZED_ORDER_FIELDS",
    "PreflightMissing",
    "PreflightOk",
    "PreflightResult",
    "preflight",
]
