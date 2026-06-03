"""Tests for InEnum primitive."""

import logging

import pytest
from pydantic import ValidationError

from samantha_server.models import Event
from samantha_server.primitives.in_enum import InEnum

from .conftest import make_context


def _unhashable_ctx_and_pred() -> tuple[object, InEnum]:
    """Return a (ctx, predicate) pair whose `actual` is an unhashable list."""
    event = Event(event_type="order_received", event_data={"items": [1, 2, 3]}, step_index=0)
    ctx = make_context(event=event)
    return ctx, InEnum(field="event.items", values=frozenset({1, 2, 3}))


class TestInEnumEvaluate:
    def test_returns_true_when_field_in_set(self) -> None:
        ctx = make_context()
        p = InEnum(field="fixative", values=frozenset({"formalin", "ethanol"}))
        assert p.evaluate(ctx) is True

    def test_returns_false_when_field_not_in_set(self) -> None:
        ctx = make_context()
        p = InEnum(field="fixative", values=frozenset({"ethanol", "acetone"}))
        assert p.evaluate(ctx) is False

    def test_returns_false_when_field_is_none(self) -> None:
        ctx = make_context(patient_name=None)
        p = InEnum(field="patient_name", values=frozenset({"Jane Doe"}))
        assert p.evaluate(ctx) is False

    def test_values_is_frozenset(self) -> None:
        p = InEnum(field="fixative", values=frozenset({"formalin"}))
        assert isinstance(p.values, frozenset)

    def test_priority_field(self) -> None:
        ctx = make_context()
        p = InEnum(field="priority", values=frozenset({"routine", "stat", "urgent"}))
        assert p.evaluate(ctx) is True

    def test_is_frozen(self) -> None:
        p = InEnum(field="fixative", values=frozenset({"formalin"}))
        with pytest.raises(ValidationError):
            p.field = "other"  # type: ignore[misc]

    def test_returns_false_on_unhashable_field(self) -> None:
        """Unhashable actual must fail-closed, not raise TypeError: unhashable type."""
        ctx, p = _unhashable_ctx_and_pred()
        assert p.evaluate(ctx) is False

    def test_warns_on_unhashable_field(self, caplog: pytest.LogCaptureFixture) -> None:
        """Unhashable actual must emit a warning so silent fail-open is observable."""
        ctx, p = _unhashable_ctx_and_pred()
        with caplog.at_level(logging.WARNING, logger="samantha_server.primitives.in_enum"):
            assert p.evaluate(ctx) is False
        assert any(
            record.name == "samantha_server.primitives.in_enum"
            and record.levelno == logging.WARNING
            and "event.items" in record.getMessage()
            and "type=list" in record.getMessage()
            for record in caplog.records
        ), f"expected warning, got: {[(r.name, r.getMessage()) for r in caplog.records]}"

    def test_returns_false_on_empty_values(self) -> None:
        """InEnum with empty values frozenset must always return False without raising."""
        ctx = make_context()
        p = InEnum(field="fixative", values=frozenset())
        assert p.evaluate(ctx) is False


class TestInEnumCanonicalization:
    """Canonicalization at comparison time."""

    def test_uppercase_actual_matches_lowercase_values(self) -> None:
        """InEnum(specimen_type, [fna, biopsy]) must match Order(specimen_type='FNA')."""
        ctx = make_context(specimen_type="FNA")
        p = InEnum(field="specimen_type", values=frozenset({"fna", "biopsy"}))
        assert p.evaluate(ctx) is True

    def test_lowercase_actual_still_matches(self) -> None:
        """Existing lowercase match must remain True after canonicalization."""
        ctx = make_context(specimen_type="fna")
        p = InEnum(field="specimen_type", values=frozenset({"fna", "biopsy"}))
        assert p.evaluate(ctx) is True

    def test_different_specimen_type_does_not_match(self) -> None:
        """InEnum must not match a value outside the canonical set."""
        ctx = make_context(specimen_type="resection")
        p = InEnum(field="specimen_type", values=frozenset({"fna", "biopsy"}))
        assert p.evaluate(ctx) is False

    def test_mixed_case_actual_matches_lowercase_value(self) -> None:
        """InEnum(fixative, [formalin]) must match Order(fixative='FORMALIN')."""
        ctx = make_context(fixative="FORMALIN")
        p = InEnum(field="fixative", values=frozenset({"formalin"}))
        assert p.evaluate(ctx) is True

    def test_whitespace_trimmed_before_comparison(self) -> None:
        """Leading/trailing whitespace in actual is trimmed at comparison time."""
        ctx = make_context(specimen_type="  biopsy  ")
        p = InEnum(field="specimen_type", values=frozenset({"biopsy"}))
        assert p.evaluate(ctx) is True

    def test_canonical_values_populated_at_init(self) -> None:
        """_canonical_values must be populated by model_validator before evaluate()."""
        p = InEnum(field="specimen_type", values=frozenset({"FNA", "Biopsy"}))
        # After model_validator, canonical values should be lowercase
        assert p._canonical_values == frozenset({"fna", "biopsy"})

    def test_non_string_values_unaffected(self) -> None:
        """Non-string actual values use existing unhashable/hash behavior."""
        ctx = make_context()
        p = InEnum(field="billing_info_present", values=frozenset({True}))
        assert p.evaluate(ctx) is True

    def test_none_actual_returns_false_unchanged(self) -> None:
        """Null-safe: None actual still returns False."""
        ctx = make_context(patient_name=None)
        p = InEnum(field="patient_name", values=frozenset({"jane doe"}))
        assert p.evaluate(ctx) is False

    def test_mixed_type_values_string_matches_after_canonicalization(self) -> None:
        """InEnum with mixed-type values {fna, 42}: string actual 'FNA' matches."""
        ctx = make_context(specimen_type="FNA")
        p = InEnum(field="specimen_type", values=frozenset({"fna", 42}))
        assert p.evaluate(ctx) is True

    def test_mixed_type_values_non_string_actual_matches(self) -> None:
        """InEnum with mixed-type values: non-string actual (int) preserved and matches."""
        from samantha_server.models import Event

        event = Event(event_type="order_received", event_data={"code": 42}, step_index=0)
        ctx = make_context(event=event)
        p = InEnum(field="event.code", values=frozenset({"fna", 42}))
        assert p.evaluate(ctx) is True

    def test_mixed_type_values_non_matching_returns_false(self) -> None:
        """InEnum with mixed-type values {fna, 42}: unmatched actual returns False."""
        ctx = make_context(specimen_type="biopsy")
        p = InEnum(field="specimen_type", values=frozenset({"fna", 42}))
        assert p.evaluate(ctx) is False

    def test_non_string_preserved_in_canonical_values(self) -> None:
        """Non-string values must be preserved as-is in _canonical_values."""
        p = InEnum(field="specimen_type", values=frozenset({"fna", 42}))
        # The integer 42 must be preserved; "fna" is canonicalized to "fna"
        assert 42 in p._canonical_values
        assert "fna" in p._canonical_values

    def test_string_actual_against_non_string_only_values_returns_false(self) -> None:
        """String actual against a values set containing only non-strings returns False."""
        ctx = make_context(specimen_type="biopsy")
        p = InEnum(field="specimen_type", values=frozenset({1, 2}))
        # _canonical_values contains only ints; casefolded "biopsy" is not in {1, 2}
        assert p.evaluate(ctx) is False


class TestInEnumTrace:
    def test_trace_positive(self) -> None:
        ctx = make_context()
        p = InEnum(field="fixative", values=frozenset({"formalin", "ethanol"}))
        trace = p.trace(ctx)
        assert trace.primitive == "InEnum"
        assert trace.field == "fixative"
        assert trace.expected == frozenset({"formalin", "ethanol"})
        assert trace.actual == "formalin"
        assert trace.result is True
        assert trace.children == ()

    def test_trace_negative(self) -> None:
        ctx = make_context()
        p = InEnum(field="fixative", values=frozenset({"ethanol"}))
        trace = p.trace(ctx)
        assert trace.result is False
        assert trace.actual == "formalin"

    def test_trace_null_field(self) -> None:
        ctx = make_context(patient_name=None)
        p = InEnum(field="patient_name", values=frozenset({"Jane"}))
        trace = p.trace(ctx)
        assert trace.actual is None
        assert trace.result is False

    def test_trace_returns_false_on_unhashable_field(self) -> None:
        ctx, p = _unhashable_ctx_and_pred()
        trace = p.trace(ctx)
        assert trace.result is False

    def test_trace_warns_exactly_once_on_unhashable_field(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """trace() must surface the unhashable warning, and must not double-emit
        it via an internal re-evaluation."""
        ctx, p = _unhashable_ctx_and_pred()
        with caplog.at_level(logging.WARNING, logger="samantha_server.primitives.in_enum"):
            p.trace(ctx)
        warnings = [
            r
            for r in caplog.records
            if r.name == "samantha_server.primitives.in_enum" and r.levelno == logging.WARNING
        ]
        assert len(warnings) == 1, (
            f"expected exactly one WARNING from trace(); got {len(warnings)}: "
            f"{[r.getMessage() for r in warnings]}"
        )

    def test_trace_empty_values_returns_false(self) -> None:
        ctx = make_context()
        p = InEnum(field="fixative", values=frozenset())
        trace = p.trace(ctx)
        assert trace.result is False
