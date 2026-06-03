"""Primitive library — 9 frozen Pydantic predicates for the deterministic rule engine.

Exports all primitive classes, PrimitiveTrace, and PRIMITIVE_REGISTRY.

model_rebuild() is called here for all three combinator classes (Not, BooleanAnd,
BooleanOr) after the full Primitive union is assembled, resolving the 'Primitive'
forward reference used in their children/child fields.
"""

from samantha_server.primitives.boolean_and import BooleanAnd
from samantha_server.primitives.boolean_or import BooleanOr
from samantha_server.primitives.contains import Contains
from samantha_server.primitives.equals import Equals
from samantha_server.primitives.in_enum import InEnum
from samantha_server.primitives.is_null import IsNull
from samantha_server.primitives.not_ import Not
from samantha_server.primitives.threshold_gte import ThresholdGTE
from samantha_server.primitives.threshold_lte import ThresholdLTE
from samantha_server.primitives.trace import PrimitiveTrace

# ---------------------------------------------------------------------------
# Resolve recursive / forward-referenced type annotations in combinators.
# The 'Primitive' annotation in Not.child, BooleanAnd.children, and
# BooleanOr.children is a string (from __future__ import annotations) that
# Pydantic cannot resolve at class-definition time. Providing the full union
# here allows Pydantic to complete the models.
# ---------------------------------------------------------------------------

Primitive = (
    IsNull | Equals | ThresholdGTE | ThresholdLTE | InEnum | Contains | Not | BooleanAnd | BooleanOr
)

_ns: dict[str, object] = {"Primitive": Primitive}

Not.model_rebuild(_types_namespace=_ns)
BooleanAnd.model_rebuild(_types_namespace=_ns)
BooleanOr.model_rebuild(_types_namespace=_ns)

# ---------------------------------------------------------------------------
# Registry: snake_case name → primitive class
# ---------------------------------------------------------------------------

PRIMITIVE_REGISTRY: dict[str, type[Primitive]] = {
    "is_null": IsNull,
    "equals": Equals,
    "threshold_gte": ThresholdGTE,
    "threshold_lte": ThresholdLTE,
    "in_enum": InEnum,
    "contains": Contains,
    "boolean_and": BooleanAnd,
    "boolean_or": BooleanOr,
    "not": Not,
}

__all__ = [
    "BooleanAnd",
    "BooleanOr",
    "Contains",
    "Equals",
    "InEnum",
    "IsNull",
    "Not",
    "PRIMITIVE_REGISTRY",
    "Primitive",
    "PrimitiveTrace",
    "ThresholdGTE",
    "ThresholdLTE",
]
