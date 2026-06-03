"""BooleanOr primitive — true iff at least one child is true; short-circuits on first true.

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


class BooleanOr(BaseModel, frozen=True):
    """Evaluates to true iff at least one child evaluates to true.

    Short-circuits on the first true child. Requires at least one child.
    """

    children: tuple[Primitive, ...]

    @field_validator("children")
    @classmethod
    def _at_least_one(cls, v: tuple[Any, ...]) -> tuple[Any, ...]:
        if len(v) < 1:
            raise ValueError("BooleanOr requires at least one child")
        return v

    def evaluate(self, ctx: SpecimenContext) -> bool:
        return any(child.evaluate(ctx) for child in self.children)

    def trace(self, ctx: SpecimenContext) -> PrimitiveTrace:
        evaluated: list[PrimitiveTrace] = []
        result = False
        for child in self.children:
            child_trace = child.trace(ctx)
            evaluated.append(child_trace)
            if child_trace.result:
                result = True
                break
        return PrimitiveTrace(
            primitive="BooleanOr",
            field=None,
            expected=None,
            actual=None,
            result=result,
            children=tuple(evaluated),
        )
