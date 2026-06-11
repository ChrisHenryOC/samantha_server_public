"""Equals primitive — true iff field == value."""

from typing import Any

from pydantic import BaseModel, PrivateAttr, model_validator

from samantha_server.canonicalization import canonicalize
from samantha_server.models import SpecimenContext
from samantha_server.primitives._cache import set_cached
from samantha_server.primitives.trace import PrimitiveTrace


class Equals(BaseModel, frozen=True):
    """Evaluates to true iff ctx.field(field) == value.

    For string-valued fields in the canonical field set, comparison is done
    after trim + casefold. Non-string fields use plain equality.

    On a null field, returns false unless value is also None (null-safe equality,
    not fail-closed — Equals(field, None) against a null field returns True).
    """

    field: str
    value: Any

    _canonical_value: str = PrivateAttr(default="")

    @model_validator(mode="after")
    def _populate_canonical(self) -> "Equals":
        if isinstance(self.value, str):
            set_cached(self, "_canonical_value", canonicalize(self.field, self.value).canonical)
        return self

    def evaluate(self, ctx: SpecimenContext) -> bool:
        actual = ctx.field(self.field)
        if actual is None:
            return self.value is None
        if isinstance(actual, str) and isinstance(self.value, str):
            return canonicalize(self.field, actual).canonical == self._canonical_value
        return bool(actual == self.value)

    def trace(self, ctx: SpecimenContext) -> PrimitiveTrace:
        actual = ctx.field(self.field)
        return PrimitiveTrace(
            primitive="Equals",
            field=self.field,
            expected=self.value,
            actual=actual,
            result=self.evaluate(ctx),
        )
