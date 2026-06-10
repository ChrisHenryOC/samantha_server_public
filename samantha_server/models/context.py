"""SpecimenContext and related Pydantic models for the deterministic rule engine."""

import json
import re
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel, Field, ValidationInfo, field_serializer, field_validator

# M-13: maximum byte size for event_data (as JSON). Oversized payloads reach
# compute_event_input_hash, the LLM prompt, and the receipt without guards;
# 64 KB is well above any legitimate clinical-query payload and well below
# SQLite row limits.
MAX_EVENT_DATA_BYTES: int = 64_000

# ---------------------------------------------------------------------------
# Verbatim port from samantha-public/src/workflow/models.py.
# These fields flow into LLM prompts — enforce limits to prevent
# prompt injection via oversized payloads.
# ---------------------------------------------------------------------------

# Some keys (slide_id, decision_id, run_id, model_id, predicted_/expected_next_state,
# prompt_template_version, scenario_set_version, status, qc_result, test_assignment,
# event_id) are forward-reserved for samantha-public models not yet ported.
FIELD_MAX_LENGTHS: Mapping[str, int] = MappingProxyType(
    {
        "order_id": 100,
        "scenario_id": 100,
        "patient_name": 200,
        "patient_sex": 10,
        "specimen_type": 100,
        "anatomic_site": 100,
        "fixative": 50,
        "priority": 20,
        "current_state": 50,
        "slide_id": 100,
        "test_assignment": 50,
        "status": 50,
        "qc_result": 100,
        "event_id": 100,
        "event_type": 100,
        "decision_id": 100,
        "run_id": 100,
        "model_id": 100,
        "predicted_next_state": 50,
        "expected_next_state": 50,
        "prompt_template_version": 100,
        "scenario_set_version": 100,
    }
)

# Valid workflow states (see docs/rule-breakdown/inventory.md for state-by-rule mapping)
VALID_STATES: frozenset[str] = frozenset(
    {
        "ACCESSIONING",
        "ACCEPTED",
        "MISSING_INFO_HOLD",
        "MISSING_INFO_PROCEED",
        "DO_NOT_PROCESS",
        "SAMPLE_PREP_PROCESSING",
        "SAMPLE_PREP_EMBEDDING",
        "SAMPLE_PREP_SECTIONING",
        "SAMPLE_PREP_QC",
        "HE_STAINING",
        "HE_QC",
        "PATHOLOGIST_HE_REVIEW",
        "IHC_STAINING",
        "IHC_QC",
        "IHC_SCORING",
        "SUGGEST_FISH_REFLEX",
        "FISH_SEND_OUT",
        "RESULTING_HOLD",
        "RESULTING",
        "PATHOLOGIST_SIGNOUT",
        "REPORT_GENERATION",
        "ORDER_COMPLETE",
        "ORDER_TERMINATED",
        "ORDER_TERMINATED_QNS",
        # LLM-path routing states:
        # PENDING_LLM_REVIEW: transient state set by ACC-010 when specimen_type is
        # unrecognized (not in whitelist or blacklist). The router-shim detects this
        # state and dispatches to handle_pending_llm_review on the next event.
        "PENDING_LLM_REVIEW",
        # PENDING_HUMAN_REVIEW: terminal state for v1. Set by the LLM review handler
        # when the specimen type is novel or genuinely ambiguous. A human picks it up
        # out-of-band. Phase 4 (FastAPI orchestrator) promotes this to a queue worker.
        "PENDING_HUMAN_REVIEW",
    }
)

# Valid flags (see docs/rule-breakdown/inventory.md — flag emit sites are listed per rule row)
VALID_FLAGS: frozenset[str] = frozenset(
    {
        "MISSING_INFO_PROCEED",
        "RECUT_REQUESTED",
        "HER2_FIXATION_REJECT",
        "FISH_SUGGESTED",
        # Set by ACC-010 when routing to LLM review
        "LLM_REVIEW_REQUESTED",
        # Informational flag emitted by the samantha
        # POC's accessioning skill when fixation_time_hours falls in the
        # borderline band ([6.0, 8.0] or [68.0, 72.0]) for HER2-bearing
        # orders that otherwise pass ACC-008. samantha_server does not yet
        # have an emitter rule — this entry only widens the vocabulary so
        # the parity-replay corpus can construct SpecimenContext without
        # Pydantic rejecting the flag. Emitter design is tracked separately.
        "FIXATION_WARNING",
    }
)


# ANSI escapes (multi-char) and single control chars — strip before logging untrusted values.
_CONTROL_CHAR_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|[\x00-\x1f\x7f]")


def _sanitize_for_log(value: str, max_length: int = 100) -> str:
    """Strip control characters and truncate. Use before interpolating untrusted input."""
    return _CONTROL_CHAR_RE.sub("", value)[:max_length]


def _assert_max_length(field_name: str, value: str) -> None:
    """Raise ValueError if a string field exceeds its FIELD_MAX_LENGTHS limit."""
    max_len = FIELD_MAX_LENGTHS.get(field_name)
    if max_len is not None and len(value) > max_len:
        raise ValueError(f"Field '{field_name}' exceeds maximum length ({len(value)} > {max_len})")


class Order(BaseModel, frozen=True):
    """Order-level fields for a laboratory specimen routing context.

    Does not carry current_state or flags — those live on SpecimenContext.
    Uses 'age' (not samantha-public's 'patient_age') per the new design;
    no rule in the inventory references 'age', so no compatibility shim is needed.
    """

    # min_length=1 prevents empty-string order_ids: HMAC-SHA256 of b"" is a
    # deterministic constant, so all empty-id orders would collide on the
    # same order_id_hash silently. Loud ValidationError at the boundary is
    # the right failure mode (review M-02).
    order_id: str = Field(min_length=1)
    patient_name: str | None
    patient_sex: str | None
    age: int | None
    specimen_type: str | None
    anatomic_site: str | None
    fixative: str | None
    fixation_time_hours: float | None
    ordered_tests: tuple[str, ...]
    priority: str | None
    billing_info_present: bool

    @field_validator(
        "order_id",
        "patient_name",
        "patient_sex",
        "specimen_type",
        "anatomic_site",
        "fixative",
        "priority",
        mode="after",
    )
    @classmethod
    def _enforce_max_length(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is not None and info.field_name is not None:
            _assert_max_length(info.field_name, value)
        return value


class Event(BaseModel, frozen=True):
    """A single event in the order lifecycle, passed to the rule engine."""

    event_type: str
    event_data: Mapping[str, Any]
    step_index: int

    @field_validator("event_type", mode="after")
    @classmethod
    def _enforce_max_length(cls, value: str) -> str:
        _assert_max_length("event_type", value)
        return value

    @field_validator("event_data", mode="before")
    @classmethod
    def _validate_event_data_size(cls, value: Any) -> Any:
        """M-13: reject event_data whose canonical JSON representation exceeds MAX_EVENT_DATA_BYTES.

        Oversized payloads reach compute_event_input_hash, the LLM prompt, and
        receipts without a guard. The 64 KB limit is documented in MAX_EVENT_DATA_BYTES.
        This validator runs before _freeze_event_data so it operates on the raw dict.
        """
        data = dict(value) if isinstance(value, Mapping) else value
        try:
            size = len(json.dumps(data))
        except (TypeError, ValueError):
            return value  # let later validators handle non-serializable inputs
        if size > MAX_EVENT_DATA_BYTES:
            raise ValueError(
                f"event_data JSON representation ({size} bytes) exceeds the "
                f"{MAX_EVENT_DATA_BYTES}-byte limit. Reduce event_data size."
            )
        return value

    @field_validator("event_data", mode="after")
    @classmethod
    def _freeze_event_data(cls, value: Mapping[str, Any]) -> Mapping[str, Any]:
        # Top-level immutability. Nested dict values remain mutable; deeper freezing
        # would require recursive copy and is deferred until rule primitives need it.
        if isinstance(value, MappingProxyType):
            return value
        return MappingProxyType(dict(value))

    @field_validator("event_data", mode="after")
    @classmethod
    def _validate_event_data_values(cls, value: Mapping[str, Any]) -> Mapping[str, Any]:
        """Restrict event_data values to JSON-native types.

        Rejects datetime / UUID / Decimal / Enum / Path / Pydantic models /
        arbitrary objects at construction time — these would otherwise reach
        ``_make_serialisable`` in the receipt-hash path and raise an uncaught
        TypeError, losing the decision rather than scoring it incorrectly.

        Allowed value types: None, bool, int, float, str, and arbitrarily
        nested list / tuple / set / frozenset / Mapping of the same.
        """

        def _walk(v: Any, path: str) -> None:
            if v is None or isinstance(v, (bool, int, float, str)):
                return
            if isinstance(v, Mapping):
                for k, sub in v.items():
                    _walk(sub, f"{path}[{k!r}]")
                return
            if isinstance(v, (list, tuple, set, frozenset)):
                for i, sub in enumerate(v):
                    _walk(sub, f"{path}[{i}]")
                return
            raise ValueError(
                f"event_data value at {path} has unsupported type "
                f"{type(v).__name__!r}; supported: None, bool, int, float, str, "
                "and nested list/tuple/set/dict of those"
            )

        for key, sub_value in value.items():
            _walk(sub_value, f"event_data[{key!r}]")
        return value

    @field_serializer("event_data")
    def _serialise_event_data(self, value: Mapping[str, Any]) -> dict[str, Any]:
        # MappingProxyType is not JSON-serialisable by Pydantic's default serializer.
        # Returning a plain dict lets model_dump(mode="json") succeed without warning.
        return dict(value)


class SpecimenContext(BaseModel, frozen=True):
    """Complete evaluation context passed to every primitive's evaluate() call."""

    order: Order
    current_state: str
    flags: frozenset[str]
    event: Event

    @field_validator("current_state", mode="after")
    @classmethod
    def _validate_state(cls, value: str) -> str:
        _assert_max_length("current_state", value)
        if value not in VALID_STATES:
            raise ValueError(
                f"Invalid state {_sanitize_for_log(value)!r}. "
                f"Must be one of the known workflow states."
            )
        return value

    @field_validator("flags", mode="after")
    @classmethod
    def _validate_flags(cls, value: frozenset[str]) -> frozenset[str]:
        invalid = {flag for flag in value if not isinstance(flag, str) or flag not in VALID_FLAGS}
        if invalid:
            sanitized = sorted(_sanitize_for_log(str(f)) for f in invalid)
            valid_list = ", ".join(sorted(VALID_FLAGS))
            raise ValueError(f"Invalid flag(s) {sanitized!r}. Must be one of: {valid_list}")
        return value

    def field(self, name: str) -> Any:
        """Namespace accessor used by every primitive.

        - 'event.<key>'   → event_data.get(key); None for missing keys *and* explicit None
                             values (callers needing to distinguish must read event.event_data
                             directly).
        - 'flags'         → self.flags (frozenset; the Contains primitive checks membership).
        - 'current_state' → self.current_state (lives on SpecimenContext, not Order).
        - otherwise       → getattr(self.order, name, None).
        """
        if name.startswith("event."):
            return self.event.event_data.get(name[len("event.") :])
        if name == "flags":
            return self.flags
        if name == "current_state":
            return self.current_state
        return getattr(self.order, name, None)
