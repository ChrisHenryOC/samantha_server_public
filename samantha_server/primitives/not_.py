"""Not primitive — Boolean inversion of exactly one child primitive.

model_rebuild() is called in samantha_server/primitives/__init__.py after all
primitive classes are imported and the full Primitive union is assembled.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from samantha_server.models import SpecimenContext
from samantha_server.primitives.trace import PrimitiveTrace

if TYPE_CHECKING:
    from samantha_server.primitives import Primitive


class Not(BaseModel, frozen=True):
    """Evaluates to the Boolean inverse of its single child primitive."""

    child: Primitive

    def evaluate(self, ctx: SpecimenContext) -> bool:
        return not self.child.evaluate(ctx)

    def trace(self, ctx: SpecimenContext) -> PrimitiveTrace:
        child_trace = self.child.trace(ctx)
        return PrimitiveTrace(
            primitive="Not",
            field=None,
            expected=None,
            actual=None,
            result=not child_trace.result,
            children=(child_trace,),
        )
