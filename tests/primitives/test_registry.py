"""Tests for PRIMITIVE_REGISTRY and __init__.py re-exports."""

import pytest

from samantha_server.primitives import (
    PRIMITIVE_REGISTRY,
    BooleanAnd,
    BooleanOr,
    Contains,
    Equals,
    InEnum,
    IsNull,
    Not,
    PrimitiveTrace,
    ThresholdGTE,
    ThresholdLTE,
)

from .conftest import make_context

_EXPECTED_REGISTRY: dict[str, type[object]] = {
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


class TestPrimitiveRegistry:
    @pytest.mark.parametrize("key,cls", list(_EXPECTED_REGISTRY.items()))
    def test_snake_case_name_maps_to_correct_class(self, key: str, cls: type) -> None:
        assert PRIMITIVE_REGISTRY[key] is cls

    def test_registry_has_exactly_nine_entries(self) -> None:
        assert len(PRIMITIVE_REGISTRY) == 9

    def test_registry_keys_match_expected_set(self) -> None:
        assert set(PRIMITIVE_REGISTRY.keys()) == set(_EXPECTED_REGISTRY.keys())


class TestReExports:
    @pytest.mark.parametrize(
        "name",
        [
            "IsNull",
            "Equals",
            "ThresholdGTE",
            "ThresholdLTE",
            "InEnum",
            "Contains",
            "Not",
            "BooleanAnd",
            "BooleanOr",
            "PrimitiveTrace",
            "PRIMITIVE_REGISTRY",
        ],
    )
    def test_name_is_in_all(self, name: str) -> None:
        import samantha_server.primitives as pkg

        assert name in pkg.__all__


class TestModelRebuildRoundTrip:
    def test_boolean_and_with_nested_children_constructs_and_evaluates(self) -> None:
        """Round-trip that proves model_rebuild() resolved recursive types."""
        ctx = make_context()
        p = BooleanAnd(
            children=(
                IsNull(field="nonexistent"),
                Equals(field="fixative", value="formalin"),
            )
        )
        # IsNull(nonexistent) is True (field is None), Equals(fixative, formalin) is True
        assert p.evaluate(ctx) is True

    def test_nested_combinator_not_wrapping_equals(self) -> None:
        ctx = make_context()
        p = BooleanAnd(
            children=(
                IsNull(field="nonexistent"),
                Not(child=Equals(field="fixative", value="ethanol")),
            )
        )
        assert p.evaluate(ctx) is True

    def test_deeply_nested_boolean_and_or_not(self) -> None:
        """Three-level nesting: BooleanAnd(IsNull, BooleanOr(Not(IsNull), Equals))."""
        ctx = make_context()
        inner_or = BooleanOr(
            children=(
                Not(child=IsNull(field="patient_name")),  # Not(False) = True
                Equals(field="fixative", value="ethanol"),  # False
            )
        )
        outer_and = BooleanAnd(
            children=(
                IsNull(field="nonexistent"),  # True
                inner_or,
            )
        )
        assert outer_and.evaluate(ctx) is True

    def test_primitive_trace_is_re_exported(self) -> None:
        trace = PrimitiveTrace(
            primitive="IsNull",
            field="patient_name",
            expected=None,
            actual=None,
            result=True,
        )
        assert trace.primitive == "IsNull"
