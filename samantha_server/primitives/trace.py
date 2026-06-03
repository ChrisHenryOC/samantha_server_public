"""PrimitiveTrace — fixed-schema trace record emitted by every primitive's trace() method."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

PrimitiveName = Literal[
    "IsNull",
    "Equals",
    "ThresholdGTE",
    "ThresholdLTE",
    "InEnum",
    "Contains",
    "BooleanAnd",
    "BooleanOr",
    "Not",
]


class PrimitiveTrace(BaseModel, frozen=True):
    """Immutable trace record capturing what a primitive evaluated and what it decided."""

    primitive: PrimitiveName
    field: str | None
    expected: Any  # populated for atomics; None for combinators
    actual: Any  # populated for atomics; None for combinators
    result: bool
    children: tuple[PrimitiveTrace, ...] = ()


PrimitiveTrace.model_rebuild()
