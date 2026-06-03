"""Tests for Not primitive."""

import pytest
from pydantic import ValidationError

from samantha_server.primitives import BooleanAnd, Equals, IsNull, Not

from .conftest import make_context


class TestNotEvaluate:
    def test_inverts_true_to_false(self) -> None:
        ctx = make_context(patient_name=None)
        child = IsNull(field="patient_name")
        assert child.evaluate(ctx) is True
        assert Not(child=child).evaluate(ctx) is False

    def test_inverts_false_to_true(self) -> None:
        ctx = make_context()
        child = IsNull(field="patient_name")
        assert child.evaluate(ctx) is False
        assert Not(child=child).evaluate(ctx) is True

    def test_is_frozen(self) -> None:
        child = IsNull(field="patient_name")
        p = Not(child=child)
        with pytest.raises(ValidationError):
            p.child = IsNull(field="fixative")  # type: ignore[misc]


class TestNotTrace:
    def test_trace_inverts_result(self) -> None:
        ctx = make_context()
        child = IsNull(field="patient_name")
        trace = Not(child=child).trace(ctx)
        assert trace.primitive == "Not"
        assert trace.field is None
        assert trace.expected is None
        assert trace.actual is None
        assert trace.result is True  # Not(IsNull(non-null field)) == True
        assert len(trace.children) == 1

    def test_trace_child_is_the_evaluated_child_trace(self) -> None:
        ctx = make_context()
        child = IsNull(field="patient_name")
        parent_trace = Not(child=child).trace(ctx)
        child_trace = parent_trace.children[0]
        assert child_trace.primitive == "IsNull"
        assert child_trace.result is False

    def test_trace_always_has_exactly_one_child(self) -> None:
        ctx = make_context(patient_name=None)
        child = IsNull(field="patient_name")
        trace = Not(child=child).trace(ctx)
        assert len(trace.children) == 1
        assert trace.children[0].result is True
        assert trace.result is False


class TestNotWithCombinatorChild:
    def test_not_with_boolean_and_child_evaluates(self) -> None:
        """Not(BooleanAnd(...)) verifies model_rebuild() resolved forward refs at this level."""
        ctx = make_context()
        inner = BooleanAnd(
            children=(
                IsNull(field="nonexistent"),  # True
                Equals(field="current_state", value="ACCESSIONING"),  # True
            )
        )
        outer = Not(child=inner)
        # BooleanAnd(True, True) = True; Not(True) = False
        assert outer.evaluate(ctx) is False

    def test_not_with_boolean_and_child_trace(self) -> None:
        ctx = make_context()
        inner = BooleanAnd(
            children=(
                IsNull(field="nonexistent"),
                Equals(field="current_state", value="ACCESSIONING"),
            )
        )
        outer = Not(child=inner)
        trace = outer.trace(ctx)
        assert trace.primitive == "Not"
        assert trace.result is False
        assert len(trace.children) == 1
        assert trace.children[0].primitive == "BooleanAnd"
        assert trace.children[0].result is True
