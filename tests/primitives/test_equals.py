"""Tests for Equals primitive."""

import pytest

from samantha_server.models import Event
from samantha_server.primitives.equals import Equals

from .conftest import make_context


class TestEqualsEvaluate:
    def test_returns_true_when_field_equals_value(self) -> None:
        ctx = make_context()
        assert Equals(field="fixative", value="formalin").evaluate(ctx) is True

    def test_returns_false_when_field_does_not_equal_value(self) -> None:
        ctx = make_context()
        assert Equals(field="fixative", value="ethanol").evaluate(ctx) is False

    def test_none_field_with_non_none_value_is_false(self) -> None:
        ctx = make_context(patient_name=None)
        assert Equals(field="patient_name", value="Jane").evaluate(ctx) is False

    def test_none_field_with_none_value_is_true(self) -> None:
        ctx = make_context(patient_name=None)
        assert Equals(field="patient_name", value=None).evaluate(ctx) is True

    def test_event_namespace(self) -> None:
        event = Event(event_type="order_received", event_data={"outcome": "success"}, step_index=0)
        ctx = make_context(event=event)
        assert Equals(field="event.outcome", value="success").evaluate(ctx) is True
        assert Equals(field="event.outcome", value="fail").evaluate(ctx) is False

    def test_current_state_namespace(self) -> None:
        ctx = make_context(current_state="ACCESSIONING")
        assert Equals(field="current_state", value="ACCESSIONING").evaluate(ctx) is True
        assert Equals(field="current_state", value="ACCEPTED").evaluate(ctx) is False

    def test_integer_value(self) -> None:
        ctx = make_context(age=45)
        assert Equals(field="age", value=45).evaluate(ctx) is True
        assert Equals(field="age", value=46).evaluate(ctx) is False

    def test_boolean_value(self) -> None:
        ctx = make_context()
        assert Equals(field="billing_info_present", value=True).evaluate(ctx) is True

    def test_is_frozen(self) -> None:
        from pydantic import ValidationError

        p = Equals(field="fixative", value="formalin")
        with pytest.raises(ValidationError):
            p.field = "other"  # type: ignore[misc]

    def test_int_float_equivalence(self) -> None:
        """Equals uses Python ==, which treats int and float as equal when numerically equal.

        Rule authors writing value: 45.0 against an age: int field should not get silent
        failures — this behavior is intentional and locked by this test.
        """
        ctx = make_context(age=45)
        assert Equals(field="age", value=45.0).evaluate(ctx) is True
        assert Equals(field="age", value=45).evaluate(ctx) is True


class TestEqualsCanonicalization:
    """Canonicalization at comparison time."""

    def test_uppercase_fixative_matches_lowercase_rule_literal(self) -> None:
        """Equals(fixative, formalin) must match Order(fixative='FORMALIN')."""
        ctx = make_context(fixative="FORMALIN")
        assert Equals(field="fixative", value="formalin").evaluate(ctx) is True

    def test_lowercase_fixative_still_matches(self) -> None:
        """Existing lowercase match must remain True after canonicalization."""
        ctx = make_context(fixative="formalin")
        assert Equals(field="fixative", value="formalin").evaluate(ctx) is True

    def test_different_fixative_does_not_match(self) -> None:
        """Equals(fixative, formalin) must not match Order(fixative='alcohol')."""
        ctx = make_context(fixative="alcohol")
        assert Equals(field="fixative", value="formalin").evaluate(ctx) is False

    def test_mixed_case_specimen_type_matches(self) -> None:
        """Equals(specimen_type, biopsy) must match Order(specimen_type='Biopsy')."""
        ctx = make_context(specimen_type="Biopsy")
        assert Equals(field="specimen_type", value="biopsy").evaluate(ctx) is True

    def test_numeric_field_unaffected_by_canonicalization(self) -> None:
        """Non-string fields must use plain equality (no canonicalization)."""
        ctx = make_context(age=45)
        assert Equals(field="age", value=45).evaluate(ctx) is True
        assert Equals(field="age", value=46).evaluate(ctx) is False

    def test_none_actual_returns_false_unchanged(self) -> None:
        """Null-safe: None actual with string value still returns False."""
        ctx = make_context(patient_name=None)
        assert Equals(field="patient_name", value="formalin").evaluate(ctx) is False

    def test_whitespace_trimmed_before_comparison(self) -> None:
        """Leading/trailing whitespace on actual is trimmed at comparison time."""
        ctx = make_context(fixative="  formalin  ")
        assert Equals(field="fixative", value="formalin").evaluate(ctx) is True

    def test_none_value_against_non_none_string_actual_returns_false(self) -> None:
        """Equals(field, None) against a non-None string actual returns False."""
        ctx = make_context(fixative="formalin")
        assert Equals(field="fixative", value=None).evaluate(ctx) is False


class TestEqualsTrace:
    def test_trace_positive(self) -> None:
        ctx = make_context()
        trace = Equals(field="fixative", value="formalin").trace(ctx)
        assert trace.primitive == "Equals"
        assert trace.field == "fixative"
        assert trace.expected == "formalin"
        assert trace.actual == "formalin"
        assert trace.result is True
        assert trace.children == ()

    def test_trace_negative(self) -> None:
        ctx = make_context()
        trace = Equals(field="fixative", value="ethanol").trace(ctx)
        assert trace.actual == "formalin"
        assert trace.expected == "ethanol"
        assert trace.result is False

    def test_trace_none_field_non_none_value(self) -> None:
        ctx = make_context(patient_name=None)
        trace = Equals(field="patient_name", value="Jane").trace(ctx)
        assert trace.actual is None
        assert trace.result is False
