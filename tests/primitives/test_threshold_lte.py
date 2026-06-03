"""Tests for ThresholdLTE primitive."""

import pytest
from pydantic import ValidationError

from samantha_server.primitives.threshold_lte import ThresholdLTE

from .conftest import make_context


class TestThresholdLTEEvaluate:
    def test_returns_true_when_field_less_than_value(self) -> None:
        ctx = make_context(fixation_time_hours=48.0)
        assert ThresholdLTE(field="fixation_time_hours", value=72.0).evaluate(ctx) is True

    def test_returns_true_when_field_equals_value(self) -> None:
        ctx = make_context(fixation_time_hours=72.0)
        assert ThresholdLTE(field="fixation_time_hours", value=72.0).evaluate(ctx) is True

    def test_returns_false_when_field_greater_than_value(self) -> None:
        ctx = make_context(fixation_time_hours=96.0)
        assert ThresholdLTE(field="fixation_time_hours", value=72.0).evaluate(ctx) is False

    def test_returns_false_when_field_is_none(self) -> None:
        ctx = make_context(fixation_time_hours=None)
        assert ThresholdLTE(field="fixation_time_hours", value=72.0).evaluate(ctx) is False

    def test_integer_value_accepted(self) -> None:
        ctx = make_context(age=17)
        assert ThresholdLTE(field="age", value=18).evaluate(ctx) is True

    def test_is_frozen(self) -> None:
        p = ThresholdLTE(field="fixation_time_hours", value=72.0)
        with pytest.raises(ValidationError):
            p.field = "other"  # type: ignore[misc]


class TestThresholdLTETrace:
    def test_trace_positive(self) -> None:
        ctx = make_context(fixation_time_hours=48.0)
        trace = ThresholdLTE(field="fixation_time_hours", value=72.0).trace(ctx)
        assert trace.primitive == "ThresholdLTE"
        assert trace.field == "fixation_time_hours"
        assert trace.expected == 72.0
        assert trace.actual == 48.0
        assert trace.result is True
        assert trace.children == ()

    def test_trace_false_on_null_field(self) -> None:
        ctx = make_context(fixation_time_hours=None)
        trace = ThresholdLTE(field="fixation_time_hours", value=72.0).trace(ctx)
        assert trace.actual is None
        assert trace.result is False

    def test_trace_negative(self) -> None:
        ctx = make_context(fixation_time_hours=100.0)
        trace = ThresholdLTE(field="fixation_time_hours", value=72.0).trace(ctx)
        assert trace.result is False
        assert trace.actual == 100.0

    def test_evaluate_returns_false_on_wrong_typed_field(self) -> None:
        """Wrong-typed non-None field must fail-closed, not raise TypeError."""
        ctx = make_context()
        assert ThresholdLTE(field="fixative", value=72.0).evaluate(ctx) is False

    def test_trace_returns_false_on_wrong_typed_field(self) -> None:
        ctx = make_context()
        trace = ThresholdLTE(field="fixative", value=72.0).trace(ctx)
        assert trace.result is False
