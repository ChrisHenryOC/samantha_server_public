"""Tests for PrimitiveTrace — the fixed-schema trace record emitted by every primitive."""

import pytest
from pydantic import ValidationError

from samantha_server.primitives.trace import PrimitiveTrace

# ---------------------------------------------------------------------------
# Slice 1 — PrimitiveTrace model
# ---------------------------------------------------------------------------

VALID_PRIMITIVES = [
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


class TestPrimitiveTraceInstantiation:
    def test_atomic_trace_constructs_with_all_fields(self) -> None:
        trace = PrimitiveTrace(
            primitive="IsNull",
            field="patient_name",
            expected=None,
            actual=None,
            result=True,
        )
        assert trace.primitive == "IsNull"
        assert trace.field == "patient_name"
        assert trace.expected is None
        assert trace.actual is None
        assert trace.result is True

    def test_children_defaults_to_empty_tuple(self) -> None:
        trace = PrimitiveTrace(
            primitive="Equals",
            field="fixative",
            expected="formalin",
            actual="formalin",
            result=True,
        )
        assert trace.children == ()

    def test_trace_is_frozen(self) -> None:
        trace = PrimitiveTrace(
            primitive="IsNull",
            field="patient_name",
            expected=None,
            actual=None,
            result=False,
        )
        with pytest.raises(ValidationError):
            trace.result = True  # type: ignore[misc]

    @pytest.mark.parametrize(
        "field_name,mutated_value",
        [
            ("primitive", "Equals"),
            ("field", "other_field"),
            ("expected", "new_expected"),
            ("actual", "new_actual"),
            ("children", ()),
        ],
    )
    def test_all_fields_are_frozen(self, field_name: str, mutated_value: object) -> None:
        trace = PrimitiveTrace(
            primitive="IsNull",
            field="patient_name",
            expected=None,
            actual=None,
            result=False,
        )
        with pytest.raises(ValidationError):
            setattr(trace, field_name, mutated_value)  # type: ignore[call-overload]

    @pytest.mark.parametrize("name", VALID_PRIMITIVES)
    def test_every_valid_primitive_name_accepted(self, name: str) -> None:
        trace = PrimitiveTrace(
            primitive=name,  # type: ignore[arg-type]
            field=None,
            expected=None,
            actual=None,
            result=False,
        )
        assert trace.primitive == name

    def test_bogus_primitive_name_raises(self) -> None:
        with pytest.raises(ValidationError):
            PrimitiveTrace(
                primitive="NotAPrimitive",  # type: ignore[arg-type]
                field=None,
                expected=None,
                actual=None,
                result=False,
            )

    def test_combinator_trace_with_children(self) -> None:
        child = PrimitiveTrace(
            primitive="IsNull",
            field="patient_name",
            expected=None,
            actual=None,
            result=True,
        )
        parent = PrimitiveTrace(
            primitive="Not",
            field=None,
            expected=None,
            actual=None,
            result=False,
            children=(child,),
        )
        assert len(parent.children) == 1
        assert parent.children[0] == child

    def test_field_and_actual_can_be_none_for_combinators(self) -> None:
        trace = PrimitiveTrace(
            primitive="BooleanAnd",
            field=None,
            expected=None,
            actual=None,
            result=True,
        )
        assert trace.field is None
        assert trace.expected is None
        assert trace.actual is None

    def test_actual_can_hold_non_string_value(self) -> None:
        trace = PrimitiveTrace(
            primitive="ThresholdGTE",
            field="fixation_time_hours",
            expected=6.0,
            actual=24.0,
            result=True,
        )
        assert trace.actual == 24.0
        assert trace.expected == 6.0
