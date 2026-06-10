"""EngineDecision schema and event_input_hash helper.

No LLM imports. Deterministic path only.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, Field, model_validator

from samantha_server.llm.schemas import AnswerType
from samantha_server.models.context import SpecimenContext
from samantha_server.models.roles import UserRole
from samantha_server.primitives.trace import PrimitiveTrace

# ---------------------------------------------------------------------------
# Decision-level trace types (G11)
# ---------------------------------------------------------------------------


class CanonicalizationTrace(BaseModel, frozen=True):
    """Records a canonicalization warning for an unknown field value.

    Clinical traceability rationale: when ``canonicalize`` returns
    ``was_unknown=True``, this trace is appended to
    ``EngineDecision.decision_traces``.  Auditors and clinical engineers can
    see the raw upstream value (``raw_value``) alongside the normalized form
    (``canonical_value_attempted``) without re-fetching the source message.
    This surfaces new or misspelled specimen types / fixatives / etc. in
    receipts so they can be triaged and the pick list updated if needed.
    """

    kind: Literal["canonicalization_warning"] = "canonicalization_warning"
    field: str
    raw_value: str
    canonical_value_attempted: str


class QueryTrace(BaseModel, frozen=True):
    """Records metadata for an LLM query operation.

    database_state_hash: HMAC-SHA256 hex digest of the canonical JSON encoding
    of any `orders` list injected into the prompt. Empty when the
    query carried no orders payload, so existing receipts replay unchanged.

    response_text_hash: sha256 hex of canonical JSON of the parsed model when
    parse succeeded (JSON mode), or sha256 hex of raw response text when
    parse failed or in free_text mode.

    parsed_order_ids: order IDs extracted from the structured JSON response.
    None when SAMANTHA_LLM_OUTPUT_MODE=free_text or when parse_failure is set.

    parsed_answer_type: the answer_type field from the structured JSON response.
    None under the same conditions as parsed_order_ids.

    parse_failure: short error class name when structured JSON parsing failed.
    "json_decode" when json.JSONDecodeError, "schema_violation" when
    pydantic.ValidationError. None on success or in free_text mode.
    """

    kind: Literal["query"] = "query"
    query_text_hash: str
    skill_doc_hash: str
    scenarios_cited: tuple[str, ...]
    model_id: str
    response_text_hash: str
    """sha256 hex of canonical JSON of the parsed model on JSON-success;
    sha256 hex of raw response text on JSON-failure or in free_text mode.

    Three semantic cases:
    - JSON-success: hash of json.dumps(model.model_dump(), sort_keys=True, separators=(",",":"))
    - JSON-failure: hash of raw response text (same as free_text)
    - free_text: hash of raw response text
    """
    database_state_hash: str = ""
    # HMAC-SHA256 hex digest of the ISO-8601 prompt_timestamp string
    # when a temporal anchor was injected into the prompt. Empty when no
    # prompt_timestamp was provided. Additive default preserves backward
    # compat with legacy receipts.
    prompt_timestamp_hash: str = ""
    # Additive optional fields — None defaults preserve backward compat
    # with pre-structured-output receipts.
    parsed_order_ids: tuple[str, ...] | None = None
    parsed_answer_type: AnswerType | None = None
    parse_failure: Literal["json_decode", "schema_violation"] | None = None
    # User role of the requesting user. Not PHI; passed through
    # verbatim. None when absent. Additive default preserves backward compat
    # with legacy receipts.
    user_role: UserRole | None = None
    # PR243 review S4: distinguishes "no role provided" from "role was
    # dropped during coercion" for post-hoc accuracy analysis.
    user_role_coercion_failure: bool = False

    @model_validator(mode="after")
    def _tri_state_invariant(self) -> QueryTrace:
        """Enforce tri-state XOR invariant on the three optional JSON-mode fields.

        Valid states:
        - Failure: parse_failure is set → parsed_order_ids is None AND parsed_answer_type is None.
        - Success: parse_failure is None AND parsed_order_ids is not None
          → parsed_answer_type is not None.
        - Pre-PR / free_text: all three are None.
        """
        if self.parse_failure is not None:
            if self.parsed_order_ids is not None or self.parsed_answer_type is not None:
                raise ValueError(
                    f"parse_failure={self.parse_failure!r} requires both parsed_order_ids "
                    "and parsed_answer_type to be None (failure state invariant)"
                )
        elif self.parsed_order_ids is not None and self.parsed_answer_type is None:
            raise ValueError(
                "parsed_order_ids is set but parsed_answer_type is None — "
                "both must be populated together in JSON-success state"
            )
        return self


class ClarificationTrace(BaseModel, frozen=True):
    """Records clarification metadata (missing/unknown fields, suggested values).

    suggested_values is LLM-supplied. Each value is canonicalized + length-capped
    before storage (see handlers.handle_clarification). Downstream consumers MUST
    still treat the values as advisory — a future Phase 4 orchestrator that auto-
    applies them needs to re-validate against the live pick list.
    """

    kind: Literal["clarification"] = "clarification"
    missing_fields: tuple[str, ...]
    unknown_canonical_fields: tuple[str, ...] = ()
    suggested_values: dict[str, str]
    model_id: str = ""


_REFUSAL_REASONS = Literal[
    "STAGE_PRE_SKILL_UNAVAILABLE",
    "STAGE_PRE_LLM_UNAVAILABLE",
    "STAGE_PRE_UNPARSEABLE",
    "STAGE_PRE_PHI_BOUNDARY",
]


class RefusalTrace(BaseModel, frozen=True):
    """Records a refusal decision with controlled-vocabulary reason."""

    kind: Literal["refusal"] = "refusal"
    refusal_reason: _REFUSAL_REASONS
    refusal_stage: Literal["A", "B", "PRE"]
    judge_verdict: Literal["grounded", "ungrounded", "uncertain"] | None
    alternatives: tuple[str, ...] = ()
    # Type name of the underlying LLMClientError subclass, when the
    # refusal was triggered by an LLM failure. Type name only — never str(exc)
    # (exception messages can echo prompt content, violating the PHI boundary).
    # Constrained to the three known LLMClientError subclasses so a future
    # subclass that wasn't intended for the operator surface fails Pydantic
    # validation loudly rather than leaking through.
    underlying_error_type: (
        Literal["LLMTimeoutError", "LLMInferenceError", "LLMModelLoadError"] | None
    ) = None

    @model_validator(mode="after")
    def _stage_invariants(self) -> RefusalTrace:
        """Enforce cross-field invariants on refusal_stage and refusal_reason.

        1. judge_verdict and alternatives are valid only on Stage B.
        2. Reason prefix must match stage: STAGE_PRE_* requires 'PRE'.
        """
        if self.refusal_stage != "B":
            if self.judge_verdict is not None:
                raise ValueError(
                    f"judge_verdict set on stage={self.refusal_stage!r}; "
                    "only Stage B carries a judge verdict"
                )
            if self.alternatives:
                raise ValueError(
                    f"alternatives set on stage={self.refusal_stage!r}; "
                    "only Stage B carries alternatives"
                )
        # Reason-to-stage prefix enforcement (M-11): every reason code is now
        # STAGE_PRE_*, which must carry refusal_stage="PRE".
        if self.refusal_reason.startswith("STAGE_PRE_") and self.refusal_stage != "PRE":
            raise ValueError(
                f"refusal_reason={self.refusal_reason!r} requires "
                f"refusal_stage='PRE'; got refusal_stage={self.refusal_stage!r}"
            )
        return self


class LLMReviewTrace(BaseModel, frozen=True):
    """Records metadata for an LLM review operation.

    parsed_disposition: disposition extracted from the structured JSON response.
    None on parse failure or when pre-JSON receipts are deserialized (backward compat).

    parse_failure: short error class name when structured JSON parsing failed.
    "json_decode" when json.JSONDecodeError, "schema_violation" when
    pydantic.ValidationError. None on success or for pre-JSON receipts.

    response_text_hash: sha256 hex of canonical JSON of the parsed model on
    JSON-success; sha256 hex of raw response text on JSON-failure.

    Tri-state XOR invariant (matches QueryTrace._tri_state_invariant):
    - Failure: parse_failure set → parsed_disposition is None.
    - Success: parse_failure is None AND parsed_disposition is not None.
    - Pre-PR / legacy: both are None.

    Production reachability of parse_failure (asymmetry vs QueryTrace): in the
    current handle_pending_llm_review handler, a JSON parse failure routes the
    decision to a RefusalTrace (STAGE_PRE_UNPARSEABLE), not an LLMReviewTrace —
    so an LLMReviewTrace with parse_failure set is not produced by the handler
    today. The field is retained for forward compatibility:
    a future revision that records the failure on LLMReviewTrace (e.g., to
    surface attempt metadata before retry) can populate it without a schema
    migration. The failure branch of the invariant therefore exists as an
    enforced contract for future writers, not as a path exercised by today's
    production code. (PR204 review #5.)
    """

    kind: Literal["llm_review"] = "llm_review"
    skill_doc_hash: str
    model_id: str
    response_text_hash: str
    disposition: Literal["accept", "reject", "escalate"]
    # Additive optional fields — None defaults preserve backward compat
    # with pre-structured-output receipts.
    parsed_disposition: Literal["accept", "reject", "escalate"] | None = None
    parse_failure: Literal["json_decode", "schema_violation"] | None = None

    @model_validator(mode="after")
    def _tri_state_invariant(self) -> LLMReviewTrace:
        """Enforce tri-state XOR invariant on the two optional JSON-mode fields.

        Valid states:
        - Failure: parse_failure is set → parsed_disposition is None.
        - Success: parse_failure is None AND parsed_disposition is not None.
        - Pre-PR / legacy: both are None.
        """
        if self.parse_failure is not None and self.parsed_disposition is not None:
            raise ValueError(
                f"parse_failure={self.parse_failure!r} requires parsed_disposition "
                "to be None (failure state invariant)"
            )
        return self


# Maps past-tense disposition strings (SpecimenReviewResponseV1.disposition) to the
# LLMReviewTrace.disposition verb form used in EngineDecision records.
# Canonical source: samantha_server.llm.handlers imports from here to prevent
# silent drift between the handler and the trace record.
_DISPOSITION_TO_TRACE_VERB: Final[Mapping[str, Literal["accept", "reject", "escalate"]]] = {
    "accepted": "accept",
    "rejected": "reject",
    "escalated": "escalate",
}


DecisionTrace = Annotated[
    CanonicalizationTrace | QueryTrace | ClarificationTrace | RefusalTrace | LLMReviewTrace,
    Field(discriminator="kind"),
]


class EngineDecision(BaseModel, frozen=True):
    """The output of one evaluation cycle of the rules engine.

    Per G5 (receipt-emission on every decision path).

    session_id: optional identifier linking decisions in a multi-step chain.
    When set, all decisions in the chain carry the same session_id, enabling
    audit-chain reconstruction by direct join rather than timeline-from-store.
    The router populates session_id on every EngineDecision it returns;
    the deterministic engine path sets it when a session_id was provided to
    evaluate(). None when no session linkage is needed (e.g., in unit
    tests and replay fixtures that use the engine directly).
    """

    applied_rule_id: str | None  # None when no rule matches (dispatch_empty)
    next_state: str  # workflow state to transition to
    flags_added: tuple[str, ...]
    flags_cleared: tuple[str, ...]
    outcome: str  # from action.outcome
    also_matched: tuple[str, ...]  # other rule_ids that matched (ACCESSIONING all_match)
    dispatched_rule_ids: tuple[str, ...]  # rules eligible per (step, applies_at, event_type)
    event_input_hash: str  # sha256 hex of canonical-JSON serialisation
    primitive_traces: dict[str, PrimitiveTrace]  # keyed by rule_id; matched/also-matched only
    latency_us: int  # perf_counter_ns delta, integer microseconds
    decision_traces: tuple[DecisionTrace, ...] = ()  # G11: decision-level metadata
    session_id: str | None = None  # links decisions in a multi-step chain (H-04)
    # Populated on deterministic single-order paths (no_match, accessioning,
    # first_match). None for multi-order query events (which keep parsed_order_ids)
    # and for LLM paths.
    order_id: str | None = None


def _make_serialisable(obj: Any) -> Any:
    """Recursively convert non-JSON-native types to JSON-native equivalents.

    - frozenset / set → sorted list (deterministic across processes)
    - MappingProxyType / Mapping → dict (keys and values recursively normalised)
    - list / tuple → list (elements recursively normalised)
    - scalars (None / bool / int / float / str) pass through unchanged
    - all other types raise TypeError — there is no silent fallback for
      datetime / UUID / Decimal / Enum / Path / Pydantic models. Construction-
      time validation on Event.event_data forbids those at the boundary.
    """
    if isinstance(obj, (frozenset, set)):
        return sorted(_make_serialisable(v) for v in obj)
    if isinstance(obj, Mapping):
        return {k: _make_serialisable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_make_serialisable(v) for v in obj]
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    raise TypeError(f"_make_serialisable: unsupported type {type(obj).__name__!r}")


def compute_event_input_hash(ctx: SpecimenContext) -> str:
    """Return the lowercase hex SHA-256 of the canonical JSON serialisation of *ctx*.

    Uses ``ctx.model_dump(mode="python")`` so frozenset-typed fields (e.g.
    ``flags``) are preserved as ``frozenset`` and can be normalised to a
    *sorted* list by ``_make_serialisable`` before JSON encoding.  Using
    ``mode="json"`` would have Pydantic flatten the frozenset to a plain list
    *before* this code can sort it — and frozenset iteration order depends on
    the runtime ``PYTHONHASHSEED`` for string elements, so the resulting list
    is non-deterministic across processes.

    ``Event.event_data`` is a ``MappingProxyType`` per the Event field
    serialiser; ``_make_serialisable`` handles it via the ``Mapping`` branch.
    Construction-time validation on ``Event.event_data`` (see
    ``models.context.Event._validate_event_data_values``) restricts nested
    values to JSON-native types, so ``_make_serialisable`` cannot encounter
    datetime / UUID / Decimal / Enum / Path inside ``event_data`` here.

    Same input always produces the same hash, regardless of process
    ``PYTHONHASHSEED``; audit can re-derive.
    """
    raw = ctx.model_dump(mode="python")
    normalised = _make_serialisable(raw)
    canonical = json.dumps(normalised, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


__all__ = [
    "CanonicalizationTrace",
    "ClarificationTrace",
    "DecisionTrace",
    "EngineDecision",
    "LLMReviewTrace",
    "QueryTrace",
    "RefusalTrace",
    "compute_event_input_hash",
]
