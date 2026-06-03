"""BooleanAnd primitive — true iff every child is true; short-circuits on first false.

model_rebuild() is called in samantha_server/primitives/__init__.py after all
primitive classes are imported and the full Primitive union is assembled.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, field_validator

from samantha_server.models import SpecimenContext
from samantha_server.primitives.trace import PrimitiveTrace

if TYPE_CHECKING:
    from samantha_server.primitives import Primitive


class BooleanAnd(BaseModel, frozen=True):
    """Evaluates to true iff every child evaluates to true.

    Short-circuits on the first false child. Requires at least one child.
    """

    children: tuple[Primitive, ...]

    @field_validator("children")
    @classmethod
    def _at_least_one(cls, v: tuple[Any, ...]) -> tuple[Any, ...]:
        if len(v) < 1:
            raise ValueError("BooleanAnd requires at least one child")
        return v

    def evaluate(self, ctx: SpecimenContext) -> bool:
        return all(child.evaluate(ctx) for child in self.children)

    def trace(self, ctx: SpecimenContext) -> PrimitiveTrace:
        evaluated: list[PrimitiveTrace] = []
        result = True
        for child in self.children:
            child_trace = child.trace(ctx)
            evaluated.append(child_trace)
            if not child_trace.result:
                result = False
                break
        return PrimitiveTrace(
            primitive="BooleanAnd",
            field=None,
            expected=None,
            actual=None,
            result=result,
            children=tuple(evaluated),
        )
