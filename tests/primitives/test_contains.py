"""Tests for Contains primitive."""

import pytest
from pydantic import ValidationError

from samantha_server.primitives.contains import Contains

from .conftest import make_context


class TestContainsEvaluate:
    def test_returns_true_when_value_in_tuple(self) -> None:
        ctx = make_context(ordered_tests=("ER", "PR", "HER2"))
        assert Contains(field="ordered_tests", value="ER").evaluate(ctx) is True

    def test_returns_false_when_value_not_in_tuple(self) -> None:
        ctx = make_context(ordered_tests=("ER", "PR"))
        assert Contains(field="ordered_tests", value="HER2").evaluate(ctx) is False

    def test_returns_true_when_value_in_frozenset_flags(self) -> None:
        ctx = make_context(flags=frozenset({"FISH_SUGGESTED", "RECUT_REQUESTED"}))
        assert Contains(field="flags", value="FISH_SUGGESTED").evaluate(ctx) is True

    def test_returns_false_when_value_not_in_frozenset_flags(self) -> None:
        ctx = make_context(flags=frozenset({"RECUT_REQUESTED"}))
        assert Contains(field="flags", value="FISH_SUGGESTED").evaluate(ctx) is False

    def test_returns_false_when_collection_is_none(self) -> None:
        ctx = make_context()
        assert Contains(field="nonexistent_collection", value="x").evaluate(ctx) is False

    def test_is_frozen(self) -> None:
        p = Contains(field="ordered_tests", value="ER")
        with pytest.raises(ValidationError):
            p.field = "other"  # type: ignore[misc]


class TestContainsCanonicalization:
    """Canonicalization at comparison time."""

    def test_uppercase_actual_element_matches_lowercase_rule_value(self) -> None:
        """Contains(ordered_tests, her2) must match Order(ordered_tests=('HER2',))."""
        ctx = make_context(ordered_tests=("HER2",))
        assert Contains(field="ordered_tests", value="her2").evaluate(ctx) is True

    def test_uppercase_rule_value_matches_lowercase_actual_element(self) -> None:
        """Contains(ordered_tests, HER2) must match Order(ordered_tests=('her2',))."""
        ctx = make_context(ordered_tests=("her2",))
        assert Contains(field="ordered_tests", value="HER2").evaluate(ctx) is True

    def test_breast_ihc_panel_case_insensitive(self) -> None:
        """Contains(ordered_tests, Breast IHC Panel) matches lowercase actual."""
        ctx = make_context(ordered_tests=("breast ihc panel",))
        assert Contains(field="ordered_tests", value="Breast IHC Panel").evaluate(ctx) is True

    def test_breast_ihc_panel_uppercase_actual(self) -> None:
        """Contains(ordered_tests, breast ihc panel) matches uppercase actual."""
        ctx = make_context(ordered_tests=("Breast IHC Panel",))
        assert Contains(field="ordered_tests", value="breast ihc panel").evaluate(ctx) is True

    def test_non_matching_value_returns_false(self) -> None:
        """Contains must not match a value not in the collection."""
        ctx = make_context(ordered_tests=("her2",))
        assert Contains(field="ordered_tests", value="er").evaluate(ctx) is False

    def test_canonical_value_populated_at_init(self) -> None:
        """_canonical_value PrivateAttr must be populated at model init."""
        p = Contains(field="ordered_tests", value="HER2")
        assert p._canonical_value == "her2"

    def test_string_field_still_rejected_as_collection(self) -> None:
        """String fields must still be treated as non-collections (substring footgun)."""
        ctx = make_context(fixative="formalin")
        assert Contains(field="fixative", value="form").evaluate(ctx) is False

    def test_whitespace_trimmed_in_collection_elements(self) -> None:
        """Whitespace in actual collection elements is trimmed at comparison time."""
        ctx = make_context(ordered_tests=("  HER2  ",))
        assert Contains(field="ordered_tests", value="her2").evaluate(ctx) is True

    def test_non_string_value_uses_direct_membership(self) -> None:
        """When self.value is not a string, use plain membership check (existing behavior)."""
        from samantha_server.models import Event

        event = Event(event_type="order_received", event_data={"items": [1, 2, 3]}, step_index=0)
        ctx = make_context(event=event)
        # Non-string value: integer 2 in a list [1, 2, 3]
        assert Contains(field="event.items", value=2).evaluate(ctx) is True

    def test_non_string_value_not_in_collection_returns_false(self) -> None:
        """Non-string value not in collection returns False without raising."""
        from samantha_server.models import Event

        event = Event(event_type="order_received", event_data={"items": [1, 2, 3]}, step_index=0)
        ctx = make_context(event=event)
        assert Contains(field="event.items", value=99).evaluate(ctx) is False

    def test_non_string_unhashable_value_in_frozenset_collection_returns_false(self) -> None:
        """Non-string unhashable value (list) in a frozenset collection must
        fail-closed, not raise TypeError."""
        # flags is a frozenset; attempting to check if a list is in a frozenset
        # raises TypeError — the except branch must catch it.
        ctx = make_context(flags=frozenset({"FISH_SUGGESTED"}))
        p = Contains(field="flags", value=[1, 2, 3])  # unhashable value
        assert p.evaluate(ctx) is False

    def test_mixed_string_non_string_collection_string_element_matches(self) -> None:
        """Mixed string/non-string collection: non-strings skipped, string still matches."""
        from samantha_server.models import Event

        event = Event(event_type="order_received", event_data={"items": ["HER2", 42]}, step_index=0)
        ctx = make_context(event=event)
        p = Contains(field="event.items", value="her2")
        assert p.evaluate(ctx) is True

    def test_mixed_string_non_string_collection_emits_debug_log(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Non-string elements in collection cause a debug-level log entry."""
        import logging

        from samantha_server.models import Event

        event = Event(event_type="order_received", event_data={"items": ["HER2", 42]}, step_index=0)
        ctx = make_context(event=event)
        p = Contains(field="event.items", value="her2")
        with caplog.at_level(logging.DEBUG, logger="samantha_server.primitives.contains"):
            p.evaluate(ctx)
        assert any(
            "non-string" in record.getMessage().lower()
            for record in caplog.records
            if record.name == "samantha_server.primitives.contains"
        )


class TestContainsTrace:
    def test_trace_positive(self) -> None:
        ctx = make_context(ordered_tests=("ER", "PR", "HER2"))
        trace = Contains(field="ordered_tests", value="ER").trace(ctx)
        assert trace.primitive == "Contains"
        assert trace.field == "ordered_tests"
        assert trace.expected == "ER"
        assert trace.actual == ("ER", "PR", "HER2")
        assert trace.result is True
        assert trace.children == ()

    def test_trace_negative(self) -> None:
        ctx = make_context(ordered_tests=("ER", "PR"))
        trace = Contains(field="ordered_tests", value="HER2").trace(ctx)
        assert trace.result is False
        assert trace.actual == ("ER", "PR")

    def test_trace_null_collection(self) -> None:
        ctx = make_context()
        trace = Contains(field="nonexistent_collection", value="x").trace(ctx)
        assert trace.actual is None
        assert trace.result is False

    def test_evaluate_returns_false_on_non_iterable_field(self) -> None:
        """Non-iterable non-None collection must fail-closed, not raise TypeError."""
        from samantha_server.models import Event

        event = Event(event_type="order_received", event_data={"count": 5}, step_index=0)
        ctx = make_context(event=event)
        assert Contains(field="event.count", value="HER2").evaluate(ctx) is False

    def test_trace_returns_false_on_non_iterable_field(self) -> None:
        from samantha_server.models import Event

        event = Event(event_type="order_received", event_data={"count": 5}, step_index=0)
        ctx = make_context(event=event)
        trace = Contains(field="event.count", value="HER2").trace(ctx)
        assert trace.result is False

    def test_evaluate_returns_false_when_collection_is_str(self) -> None:
        """String fields must not be treated as collections — substring match is a footgun."""
        ctx = make_context(fixative="formalin")
        assert Contains(field="fixative", value="form").evaluate(ctx) is False

    def test_trace_returns_false_when_collection_is_str(self) -> None:
        ctx = make_context(fixative="formalin")
        trace = Contains(field="fixative", value="form").trace(ctx)
        assert trace.result is False
