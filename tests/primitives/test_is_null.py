"""Tests for IsNull primitive."""

import pytest

from samantha_server.models import Event
from samantha_server.primitives.is_null import IsNull

from .conftest import make_context


class TestIsNullEvaluate:
    def test_returns_true_when_field_is_none(self) -> None:
        ctx = make_context(patient_name=None)
        assert IsNull(field="patient_name").evaluate(ctx) is True

    def test_returns_false_when_field_is_not_none(self) -> None:
        ctx = make_context()
        assert IsNull(field="patient_name").evaluate(ctx) is False

    def test_nonexistent_order_field_is_none(self) -> None:
        ctx = make_context()
        assert IsNull(field="nonexistent_field").evaluate(ctx) is True

    def test_event_key_missing_is_none(self) -> None:
        ctx = make_context()
        assert IsNull(field="event.missing_key").evaluate(ctx) is True

    def test_event_key_present_returns_false(self) -> None:
        event = Event(event_type="order_received", event_data={"outcome": "success"}, step_index=0)
        ctx = make_context(event=event)
        assert IsNull(field="event.outcome").evaluate(ctx) is False

    def test_event_key_explicitly_none_returns_true(self) -> None:
        event = Event(event_type="order_received", event_data={"outcome": None}, step_index=0)
        ctx = make_context(event=event)
        assert IsNull(field="event.outcome").evaluate(ctx) is True

    def test_is_frozen(self) -> None:
        from pydantic import ValidationError

        p = IsNull(field="patient_name")
        with pytest.raises(ValidationError):
            p.field = "other"  # type: ignore[misc]


class TestIsNullTrace:
    def test_trace_positive(self) -> None:
        ctx = make_context(patient_name=None)
        trace = IsNull(field="patient_name").trace(ctx)
        assert trace.primitive == "IsNull"
        assert trace.field == "patient_name"
        assert trace.expected is None
        assert trace.actual is None
        assert trace.result is True
        assert trace.children == ()

    def test_trace_negative(self) -> None:
        ctx = make_context()
        trace = IsNull(field="patient_name").trace(ctx)
        assert trace.primitive == "IsNull"
        assert trace.actual == "Jane Doe"
        assert trace.result is False

    def test_trace_nonexistent_field(self) -> None:
        ctx = make_context()
        trace = IsNull(field="nonexistent_field").trace(ctx)
        assert trace.result is True
        assert trace.actual is None
