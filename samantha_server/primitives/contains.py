"""Contains primitive — true iff value is an element of the collection field."""

import logging
from typing import Any

from pydantic import BaseModel, PrivateAttr, model_validator

from samantha_server.canonicalization import canonicalize
from samantha_server.models import SpecimenContext
from samantha_server.primitives.trace import PrimitiveTrace

logger = logging.getLogger(__name__)


class Contains(BaseModel, frozen=True):
    """Evaluates to true iff value is contained in the collection at field.

    For string-valued rule literals and string-element collections (e.g.,
    ordered_tests), comparison is done after trim + casefold on both the
    rule literal and each collection element.

    Returns false when the collection is None (treated as empty — fail-closed).
    """

    field: str
    value: Any

    _canonical_value: str = PrivateAttr(default="")

    @model_validator(mode="after")
    def _populate_canonical_value(self) -> "Contains":
        if isinstance(self.value, str):
            object.__setattr__(
                self, "_canonical_value", canonicalize(self.field, self.value).canonical
            )
        return self

    def evaluate(self, ctx: SpecimenContext) -> bool:
        collection = ctx.field(self.field)
        if collection is None or isinstance(collection, (str, bytes)):
            return False
        if isinstance(self.value, str):
            try:
                if logger.isEnabledFor(logging.DEBUG):
                    non_string_count = sum(1 for e in collection if not isinstance(e, str))
                    if non_string_count:
                        logger.debug(
                            "Contains: field=%r collection contains %d non-string element(s) "
                            "that will be skipped during canonicalized comparison",
                            self.field,
                            non_string_count,
                        )
                canonical_elements = {
                    canonicalize(self.field, e).canonical for e in collection if isinstance(e, str)
                }
                return self._canonical_value in canonical_elements
            except TypeError:
                return False
        try:
            return self.value in collection
        except TypeError:
            return False

    def trace(self, ctx: SpecimenContext) -> PrimitiveTrace:
        actual = ctx.field(self.field)
        return PrimitiveTrace(
            primitive="Contains",
            field=self.field,
            expected=self.value,
            actual=actual,
            result=self.evaluate(ctx),
        )
