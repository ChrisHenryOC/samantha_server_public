"""InEnum primitive — true iff field value is in the literal set."""

import logging
from typing import Any

from pydantic import BaseModel, PrivateAttr, model_validator

from samantha_server.canonicalization import canonicalize
from samantha_server.models import SpecimenContext
from samantha_server.primitives._cache import set_cached
from samantha_server.primitives.trace import PrimitiveTrace

_logger = logging.getLogger(__name__)


class InEnum(BaseModel, frozen=True):
    """Evaluates to true iff ctx.field(field) is in values.

    For string-valued fields in the canonical field set, comparison is done
    after trim + casefold on both the actual value and each member of ``values``.
    Non-string values are preserved as-is in ``_canonical_values`` so that a
    mixed-type ``values`` set (e.g., ``{"fna", 42}``) matches both string
    actuals via canonicalization and non-string actuals via hash membership.

    Returns false when the field is None (fail-closed null behavior).
    """

    field: str
    values: frozenset[Any]

    _canonical_values: frozenset[Any] = PrivateAttr(default=frozenset())

    @model_validator(mode="after")
    def _populate_canonical_values(self) -> "InEnum":
        # Strings are canonicalized (trim + casefold); non-strings pass through
        # as-is so that mixed-type values sets work correctly for forward-compat.
        canonical: frozenset[Any] = frozenset(
            canonicalize(self.field, v).canonical if isinstance(v, str) else v for v in self.values
        )
        set_cached(self, "_canonical_values", canonical)
        return self

    def evaluate(self, ctx: SpecimenContext) -> bool:
        return self._matches(ctx.field(self.field))

    def trace(self, ctx: SpecimenContext) -> PrimitiveTrace:
        actual = ctx.field(self.field)
        return PrimitiveTrace(
            primitive="InEnum",
            field=self.field,
            expected=self.values,
            actual=actual,
            result=self._matches(actual),
        )

    def _matches(self, actual: Any) -> bool:
        if actual is None:
            return False
        if isinstance(actual, str):
            return canonicalize(self.field, actual).canonical in self._canonical_values
        try:
            return actual in self.values
        except TypeError:
            _logger.warning(
                "InEnum got unhashable value for field=%r (type=%s); returning False",
                self.field,
                type(actual).__name__,
            )
            return False
