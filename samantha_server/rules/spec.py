"""Frozen Pydantic models for YAML rule specs.

RuleSpec is the top-level document.  ActionSpec, PanelSpec, and ConditionalMarker
are sub-models.  All models use ConfigDict(frozen=True, extra="forbid").

The ``when`` field on RuleSpec and ConditionalMarker accepts a raw mapping at
validation time (mode="before" validator) and stores the resolved Primitive tree.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from samantha_server.primitives import Primitive

Step = Literal["ACCESSIONING", "SAMPLE_PREP", "HE_QC", "PATHOLOGIST_HE_REVIEW", "IHC", "RESULTING"]
Severity = Literal["REJECT", "HOLD", "REVIEW_HOLD", "PROCEED", "ACCEPT"]


def _build_predicate_from_mapping(v: Any) -> Any:
    """Convert a raw mapping to a Primitive, or pass through if already a Primitive."""
    if isinstance(v, Mapping):
        from samantha_server.rules.loader import build_predicate

        return build_predicate(v)
    return v


class ConditionalMarker(BaseModel):
    """A marker added to a panel only when its predicate holds."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    marker: str
    when: Primitive

    @field_validator("when", mode="before")
    @classmethod
    def _build_when(cls, v: Any) -> Any:
        return _build_predicate_from_mapping(v)


class PanelSpec(BaseModel):
    """IHC panel specification: always-added markers plus conditional additions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    base: tuple[str, ...]
    conditional: tuple[ConditionalMarker, ...]


class ActionSpec(BaseModel):
    """The action block of a rule spec."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    transition: str
    set_flags: tuple[str, ...]
    clear_flags: tuple[str, ...]
    outcome: str = Field(
        description=(
            "Receipt-grade outcome label (snake_case). For ACCESSIONING rules"
            " the prefix mirrors severity (held_*, rejected_*, proceeding_*,"
            " accessioning_* — note ACCEPT is the deliberate exception, named"
            " after the step rather than a verdict verb, and REVIEW_HOLD keeps"
            " its original proceeding_* outcomes for receipt-vocabulary"
            " stability exception). For non-ACCESSIONING"
            " rules the prefix is a past-tense action verb. See"
            " docs/rules/conventions.md for the full table and rationale."
        ),
    )
    panel: PanelSpec | None = None
    branch: str | None = None

    @field_validator("set_flags", "clear_flags", mode="before")
    @classmethod
    def _no_duplicates(cls, v: Any) -> Any:
        items = list(v) if not isinstance(v, list) else v
        seen: set[str] = set()
        for item in items:
            if item in seen:
                raise ValueError(f"Duplicate flag '{item}' in flag list")
            seen.add(item)
        return v

    @model_validator(mode="after")
    def _no_overlap(self) -> ActionSpec:
        overlap = set(self.set_flags) & set(self.clear_flags)
        if overlap:
            raise ValueError(
                f"set_flags and clear_flags must not overlap; found: {sorted(overlap)}"
            )
        return self


class RuleSpec(BaseModel):
    """A single rule spec loaded from a YAML file."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: str
    step: Step

    # Guard rule_id shape so the dispatcher's b"|" HMAC separator
    # (dispatcher.py) cannot be injected via a rule_id that contains "|".
    @field_validator("rule_id")
    @classmethod
    def _validate_rule_id_shape(cls, v: str) -> str:
        if not re.fullmatch(r"[A-Z]+-\d+", v):
            raise ValueError(
                f"rule_id '{v}' must match [A-Z]+-[0-9]+ in full (e.g. 'ACC-001')"
                " ('|' is the dispatch-token HMAC field separator and must"
                " not appear in rule IDs; use uppercase letters, a hyphen, and digits)"
            )
        return v

    applies_at: str | None
    event_type: tuple[str, ...]
    severity: Severity | None
    priority: int | None
    when: Primitive
    action: ActionSpec
    source: str

    @field_validator("event_type", mode="before")
    @classmethod
    def _normalize_event_type(cls, v: Any) -> Any:
        if isinstance(v, str):
            return (v,)
        if isinstance(v, (list, tuple)):
            if len(v) == 0:
                raise ValueError("event_type must not be empty")
            for item in v:
                if not isinstance(item, str):
                    raise ValueError(
                        f"event_type elements must be strings; got {type(item).__name__!r}"
                    )
            return tuple(v)
        raise ValueError(
            f"event_type must be a string or list of strings; got {type(v).__name__!r}"
        )

    @field_validator("when", mode="before")
    @classmethod
    def _build_when(cls, v: Any) -> Any:
        return _build_predicate_from_mapping(v)

    @model_validator(mode="after")
    def _check_step_invariants(self) -> RuleSpec:
        if self.step == "ACCESSIONING":
            if self.severity is None:
                raise ValueError("severity is required for ACCESSIONING rules")
            if self.priority is not None:
                raise ValueError("priority must be None for ACCESSIONING rules")
        else:
            if self.severity is not None:
                raise ValueError(f"severity must be None for non-ACCESSIONING step '{self.step}'")
            if self.priority is None:
                raise ValueError(
                    f"priority (int >= 0) is required for non-ACCESSIONING step '{self.step}'"
                )
            if self.priority < 0:
                raise ValueError(
                    f"priority must be >= 0 for non-ACCESSIONING step"
                    f" '{self.step}', got {self.priority}"
                )

        if self.step == "IHC":
            if not self.applies_at:
                raise ValueError("applies_at is required for IHC rules")
        else:
            if self.applies_at is not None:
                raise ValueError(f"applies_at must be None for non-IHC step '{self.step}'")

        return self


__all__ = [
    "ActionSpec",
    "ConditionalMarker",
    "PanelSpec",
    "RuleSpec",
    "Severity",
    "Step",
]
