"""Tests for ThresholdGTE primitive."""

import pytest
from pydantic import ValidationError

from samantha_server.primitives.threshold_gte import ThresholdGTE

from .conftest import make_context


class TestThresholdGTEEvaluate:
    def test_returns_true_when_field_greater_than_value(self) -> None:
        ctx = make_context(fixation_time_hours=48.0)
        assert ThresholdGTE(field="fixation_time_hours", value=6.0).evaluate(ctx) is True

    def test_returns_true_when_field_equals_value(self) -> None:
        ctx = make_context(fixation_time_hours=6.0)
        assert ThresholdGTE(field="fixation_time_hours", value=6.0).evaluate(ctx) is True

    def test_returns_false_when_field_less_than_value(self) -> None:
        ctx = make_context(fixation_time_hours=3.0)
        assert ThresholdGTE(field="fixation_time_hours", value=6.0).evaluate(ctx) is False

    def test_returns_false_when_field_is_none(self) -> None:
        ctx = make_context(fixation_time_hours=None)
        assert ThresholdGTE(field="fixation_time_hours", value=6.0).evaluate(ctx) is False

    def test_integer_value_accepted(self) -> None:
        ctx = make_context(age=45)
        assert ThresholdGTE(field="age", value=18).evaluate(ctx) is True

    def test_is_frozen(self) -> None:
        p = ThresholdGTE(field="fixation_time_hours", value=6.0)
        with pytest.raises(ValidationError):
            p.field = "other"  # type: ignore[misc]


class TestThresholdGTETrace:
    def test_trace_positive(self) -> None:
        ctx = make_context(fixation_time_hours=24.0)
        trace = ThresholdGTE(field="fixation_time_hours", value=6.0).trace(ctx)
        assert trace.primitive == "ThresholdGTE"
        assert trace.field == "fixation_time_hours"
        assert trace.expected == 6.0
        assert trace.actual == 24.0
        assert trace.result is True
        assert trace.children == ()

    def test_trace_false_on_null_field(self) -> None:
        ctx = make_context(fixation_time_hours=None)
        trace = ThresholdGTE(field="fixation_time_hours", value=6.0).trace(ctx)
        assert trace.actual is None
        assert trace.result is False

    def test_trace_negative(self) -> None:
        ctx = make_context(fixation_time_hours=2.0)
        trace = ThresholdGTE(field="fixation_time_hours", value=6.0).trace(ctx)
        assert trace.result is False
        assert trace.actual == 2.0

    def test_evaluate_returns_false_on_wrong_typed_field(self) -> None:
        """Wrong-typed non-None field must fail-closed, not raise TypeError."""
        ctx = make_context()
        assert ThresholdGTE(field="fixative", value=6.0).evaluate(ctx) is False

    def test_trace_returns_false_on_wrong_typed_field(self) -> None:
        ctx = make_context()
        trace = ThresholdGTE(field="fixative", value=6.0).trace(ctx)
        assert trace.result is False
