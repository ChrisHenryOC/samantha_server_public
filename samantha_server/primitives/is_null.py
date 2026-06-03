"""IsNull primitive — true iff the named field is absent or null."""

from pydantic import BaseModel

from samantha_server.models import SpecimenContext
from samantha_server.primitives.trace import PrimitiveTrace


class IsNull(BaseModel, frozen=True):
    """Evaluates to true iff ctx.field(field) is None."""

    field: str

    def evaluate(self, ctx: SpecimenContext) -> bool:
        return ctx.field(self.field) is None

    def trace(self, ctx: SpecimenContext) -> PrimitiveTrace:
        actual = ctx.field(self.field)
        return PrimitiveTrace(
            primitive="IsNull",
            field=self.field,
            expected=None,
            actual=actual,
            result=self.evaluate(ctx),
        )
