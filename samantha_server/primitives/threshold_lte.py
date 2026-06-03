"""ThresholdLTE primitive — true iff field <= value; false on null field (fail-closed)."""

from pydantic import BaseModel

from samantha_server.models import SpecimenContext
from samantha_server.primitives.trace import PrimitiveTrace


class ThresholdLTE(BaseModel, frozen=True):
    """Evaluates to true iff ctx.field(field) <= value.

    Returns false when the field is None (fail-closed null behavior).
    """

    field: str
    value: int | float

    def evaluate(self, ctx: SpecimenContext) -> bool:
        actual = ctx.field(self.field)
        if actual is None:
            return False
        try:
            return bool(actual <= self.value)
        except TypeError:
            return False

    def trace(self, ctx: SpecimenContext) -> PrimitiveTrace:
        actual = ctx.field(self.field)
        return PrimitiveTrace(
            primitive="ThresholdLTE",
            field=self.field,
            expected=self.value,
            actual=actual,
            result=self.evaluate(ctx),
        )
