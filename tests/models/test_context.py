"""Tests for samantha_server.models.context — SpecimenContext and related models."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

import pytest
from pydantic import ValidationError

from samantha_server.models import (
    FIELD_MAX_LENGTHS,
    VALID_FLAGS,
    VALID_STATES,
    Event,
    Order,
    SpecimenContext,
)

# ---------------------------------------------------------------------------
# Slice 1 — VALID_STATES, VALID_FLAGS, FIELD_MAX_LENGTHS
# ---------------------------------------------------------------------------


class TestConstants:
    def test_valid_states_is_frozenset(self) -> None:
        assert isinstance(VALID_STATES, frozenset)

    def test_valid_states_contains_expected_values(self) -> None:
        assert "ACCESSIONING" in VALID_STATES
        assert "ORDER_COMPLETE" in VALID_STATES
        assert "ORDER_TERMINATED_QNS" in VALID_STATES

    def test_valid_flags_is_frozenset(self) -> None:
        assert isinstance(VALID_FLAGS, frozenset)

    def test_valid_flags_contains_expected_values(self) -> None:
        """The full VALID_FLAGS set, including GH-169's FIXATION_WARNING
        re-introduction (vocabulary-only — emitter rule tracked as GH-175;
        until that lands, the flag is valid input but no engine path
        produces it)."""
        assert (
            frozenset(
                {
                    "MISSING_INFO_PROCEED",
                    "RECUT_REQUESTED",
                    "HER2_FIXATION_REJECT",
                    "FISH_SUGGESTED",
                    "LLM_REVIEW_REQUESTED",
                    "FIXATION_WARNING",
                }
            )
            == VALID_FLAGS
        )

    def test_pending_llm_review_in_valid_states(self) -> None:
        """GH-34 Slice 1: PENDING_LLM_REVIEW must be a valid workflow state."""
        assert "PENDING_LLM_REVIEW" in VALID_STATES

    def test_pending_human_review_in_valid_states(self) -> None:
        """GH-34 Slice 1: PENDING_HUMAN_REVIEW must be a valid workflow state (terminal v1)."""
        assert "PENDING_HUMAN_REVIEW" in VALID_STATES

    def test_llm_review_requested_in_valid_flags(self) -> None:
        """GH-34 Slice 1: LLM_REVIEW_REQUESTED flag must be in VALID_FLAGS."""
        assert "LLM_REVIEW_REQUESTED" in VALID_FLAGS

    def test_field_max_lengths_is_mapping(self) -> None:
        assert isinstance(FIELD_MAX_LENGTHS, Mapping)

    def test_field_max_lengths_is_immutable(self) -> None:
        with pytest.raises(TypeError):
            FIELD_MAX_LENGTHS["order_id"] = 999  # type: ignore[index]

    def test_field_max_lengths_has_expected_keys(self) -> None:
        assert FIELD_MAX_LENGTHS["order_id"] == 100
        assert FIELD_MAX_LENGTHS["patient_name"] == 200
        assert FIELD_MAX_LENGTHS["event_type"] == 100
        assert FIELD_MAX_LENGTHS["fixative"] == 50
        assert FIELD_MAX_LENGTHS["priority"] == 20


# ---------------------------------------------------------------------------
# Slice 2 — Order model
# ---------------------------------------------------------------------------


def _make_order(**kwargs: Any) -> Order:
    defaults: dict[str, Any] = {
        "order_id": "ORD-001",
        "patient_name": "Jane Doe",
        "patient_sex": "F",
        "age": 45,
        "specimen_type": "core_needle_biopsy",
        "anatomic_site": "breast",
        "fixative": "formalin",
        "fixation_time_hours": 24.0,
        "ordered_tests": ("ER", "PR", "HER2"),
        "priority": "routine",
        "billing_info_present": True,
    }
    defaults.update(kwargs)
    return Order(**defaults)


class TestOrder:
    def test_order_creates_with_valid_fields(self) -> None:
        order = _make_order()
        assert order.order_id == "ORD-001"
        assert order.patient_name == "Jane Doe"

    def test_order_optional_fields_accept_none(self) -> None:
        order = _make_order(patient_name=None, patient_sex=None, age=None, fixation_time_hours=None)
        assert order.patient_name is None
        assert order.patient_sex is None
        assert order.age is None
        assert order.fixation_time_hours is None

    def test_order_ordered_tests_is_tuple(self) -> None:
        order = _make_order(ordered_tests=("ER", "PR"))
        assert isinstance(order.ordered_tests, tuple)
        assert order.ordered_tests == ("ER", "PR")

    def test_order_is_frozen(self) -> None:
        order = _make_order()
        with pytest.raises(ValidationError):
            order.order_id = "mutated"  # type: ignore[misc]

    def test_order_has_no_current_state_field(self) -> None:
        order = _make_order()
        assert not hasattr(order, "current_state")

    def test_order_has_no_flags_field(self) -> None:
        order = _make_order()
        assert not hasattr(order, "flags")

    def test_order_billing_info_present_is_bool(self) -> None:
        order = _make_order(billing_info_present=False)
        assert order.billing_info_present is False

    def test_order_age_field_name_is_age_not_patient_age(self) -> None:
        order = _make_order(age=30)
        assert order.age == 30

    def test_order_fixation_time_hours_is_float(self) -> None:
        order = _make_order(fixation_time_hours=72.5)
        assert isinstance(order.fixation_time_hours, float)
        assert order.fixation_time_hours == 72.5

    def test_order_age_is_int(self) -> None:
        order = _make_order(age=30)
        assert isinstance(order.age, int)

    def test_order_nullable_str_fields_accept_none(self) -> None:
        """GH-105 Slice 2: specimen_type, anatomic_site, fixative, priority must accept None.

        SC-104 has all four as null (completely empty order). These fields must mirror
        the precedent set by patient_name/patient_sex (already str | None).
        """
        order = _make_order(
            specimen_type=None,
            anatomic_site=None,
            fixative=None,
            priority=None,
        )
        assert order.specimen_type is None
        assert order.anatomic_site is None
        assert order.fixative is None
        assert order.priority is None


# ---------------------------------------------------------------------------
# Slice 3 — FIELD_MAX_LENGTHS validators on Order
# ---------------------------------------------------------------------------

# (field, max_length) pairs for parametrized boundary tests.
_ORDER_LENGTH_FIELDS: list[tuple[str, int]] = [
    ("order_id", 100),
    ("patient_name", 200),
    ("patient_sex", 10),
    ("specimen_type", 100),
    ("anatomic_site", 100),
    ("fixative", 50),
    ("priority", 20),
]


class TestOrderFieldMaxLengths:
    @pytest.mark.parametrize("field_name,max_len", _ORDER_LENGTH_FIELDS)
    def test_at_max_length_passes(self, field_name: str, max_len: int) -> None:
        order = _make_order(**{field_name: "x" * max_len})
        assert len(getattr(order, field_name)) == max_len

    @pytest.mark.parametrize("field_name,max_len", _ORDER_LENGTH_FIELDS)
    def test_one_over_max_length_raises(self, field_name: str, max_len: int) -> None:
        with pytest.raises(ValidationError):
            _make_order(**{field_name: "x" * (max_len + 1)})

    def test_none_optional_string_is_accepted(self) -> None:
        order = _make_order(patient_name=None)
        assert order.patient_name is None


# ---------------------------------------------------------------------------
# Slice 4 — Event model
# ---------------------------------------------------------------------------


def _make_event(**kwargs: Any) -> Event:
    defaults: dict[str, Any] = {
        "event_type": "order_received",
        "event_data": {},
        "step_index": 0,
    }
    defaults.update(kwargs)
    return Event(**defaults)


class TestEvent:
    def test_event_creates_with_valid_fields(self) -> None:
        ev = _make_event()
        assert ev.event_type == "order_received"
        assert ev.step_index == 0

    def test_event_data_accepts_empty_mapping(self) -> None:
        ev = _make_event(event_data={})
        assert dict(ev.event_data) == {}

    def test_event_data_accepts_nonempty_mapping(self) -> None:
        ev = _make_event(event_data={"outcome": "success"})
        assert ev.event_data["outcome"] == "success"

    def test_event_is_frozen(self) -> None:
        ev = _make_event()
        with pytest.raises(ValidationError):
            ev.event_type = "mutated"  # type: ignore[misc]

    def test_event_data_top_level_is_immutable(self) -> None:
        ev = _make_event(event_data={"outcome": "success"})
        with pytest.raises(TypeError):
            ev.event_data["outcome"] = "fail"  # type: ignore[index]

    def test_event_type_too_long_raises(self) -> None:
        with pytest.raises(ValidationError):
            _make_event(event_type="x" * 101)

    def test_event_type_at_max_length_is_accepted(self) -> None:
        ev = _make_event(event_type="x" * 100)
        assert len(ev.event_type) == 100

    def test_event_data_field_is_mapping(self) -> None:
        ev = _make_event(event_data={"k": "v"})
        assert isinstance(ev.event_data, Mapping)


# ---------------------------------------------------------------------------
# Slice 5 — SpecimenContext model
# ---------------------------------------------------------------------------


def _make_context(**kwargs: Any) -> SpecimenContext:
    order = _make_order()
    event = _make_event()
    defaults: dict[str, Any] = {
        "order": order,
        "current_state": "ACCESSIONING",
        "flags": frozenset(),
        "event": event,
    }
    defaults.update(kwargs)
    return SpecimenContext(**defaults)


class TestSpecimenContext:
    def test_context_creates_with_valid_fields(self) -> None:
        ctx = _make_context()
        assert ctx.current_state == "ACCESSIONING"

    def test_context_is_frozen(self) -> None:
        ctx = _make_context()
        with pytest.raises(ValidationError):
            ctx.current_state = "mutated"  # type: ignore[misc]

    def test_invalid_state_raises(self) -> None:
        with pytest.raises(ValidationError):
            _make_context(current_state="NOT_A_REAL_STATE")

    def test_non_string_state_raises(self) -> None:
        with pytest.raises(ValidationError):
            _make_context(current_state=42)

    def test_state_with_control_chars_is_sanitized_in_our_message(self) -> None:
        """Our injected message strips control chars; Pydantic's own input_value echo
        is out of our control but renders via repr() which escapes them."""
        with pytest.raises(ValidationError) as excinfo:
            _make_context(current_state="EVIL\r\nLOG_INJECTION")
        # The "Invalid state ..." message we emit should contain the sanitized form,
        # not the raw control chars.
        assert "Invalid state 'EVILLOG_INJECTION'" in str(excinfo.value)

    @pytest.mark.parametrize("state", sorted(VALID_STATES))
    def test_every_valid_state_accepted(self, state: str) -> None:
        ctx = _make_context(current_state=state)
        assert ctx.current_state == state

    @pytest.mark.parametrize("flag", sorted(VALID_FLAGS))
    def test_every_valid_flag_accepted(self, flag: str) -> None:
        ctx = _make_context(flags=frozenset({flag}))
        assert flag in ctx.flags

    def test_invalid_flag_in_frozenset_raises(self) -> None:
        with pytest.raises(ValidationError):
            _make_context(flags=frozenset({"NOT_A_VALID_FLAG"}))

    def test_invalid_flag_in_set_raises(self) -> None:
        with pytest.raises(ValidationError):
            _make_context(flags={"NOT_A_VALID_FLAG"})

    def test_valid_flag_in_set_accepted(self) -> None:
        ctx = _make_context(flags={"FISH_SUGGESTED"})
        assert "FISH_SUGGESTED" in ctx.flags

    def test_invalid_flag_in_list_raises(self) -> None:
        """Pydantic coerces list -> frozenset; validator must still catch invalid flags."""
        with pytest.raises(ValidationError):
            _make_context(flags=["MISSING_INFO_PROCEED", "NOT_A_VALID_FLAG"])

    def test_flags_is_frozenset(self) -> None:
        ctx = _make_context(flags=frozenset({"FISH_SUGGESTED"}))
        assert isinstance(ctx.flags, frozenset)

    def test_multiple_valid_flags_accepted(self) -> None:
        ctx = _make_context(flags=frozenset({"MISSING_INFO_PROCEED", "RECUT_REQUESTED"}))
        assert len(ctx.flags) == 2

    def test_empty_flags_accepted(self) -> None:
        ctx = _make_context(flags=frozenset())
        assert ctx.flags == frozenset()

    def test_fixation_warning_flag_accepted(self) -> None:
        """GH-169 re-introduces FIXATION_WARNING after its PR #61 retirement.

        Round-trip: PR #61 dropped the flag because samantha_server had no
        emit site for it. GH-169 (parity replay against the samantha POC's
        accumulated_state corpus) showed SC-092 expects it as an
        informational signal that persists from accessioning through IHC.
        Vocabulary widened here; emitter rule design is tracked separately
        — until that lands, the flag is valid input but no engine path
        produces it.
        """
        ctx = _make_context(flags=frozenset({"FIXATION_WARNING"}))
        assert "FIXATION_WARNING" in ctx.flags

    @pytest.mark.parametrize(
        "typo",
        ["FIXATION_WARN", "FIXATION_WARNINGS", "fixation_warning", "FIXATION-WARNING"],
    )
    def test_fixation_warning_misspellings_rejected(self, typo: str) -> None:
        """PR #176 M2: the inverted accept-test loses the spelling-typo guard
        the original reject-test carried. Pin the validator boundary on
        near-spellings so a future GH-175 emit site can't silently introduce
        a typo'd flag name (which would silently mismatch_flags against the
        POC's corpus rather than surfacing as a vocab error).
        """
        with pytest.raises(ValidationError):
            _make_context(flags=frozenset({typo}))


# ---------------------------------------------------------------------------
# Slice 6 — SpecimenContext.field() namespace accessor
# ---------------------------------------------------------------------------


class TestSpecimenContextField:
    def test_bare_name_returns_order_attribute(self) -> None:
        ctx = _make_context()
        assert ctx.field("order_id") == "ORD-001"

    def test_bare_name_patient_name(self) -> None:
        ctx = _make_context()
        assert ctx.field("patient_name") == "Jane Doe"

    def test_bare_name_missing_returns_none(self) -> None:
        ctx = _make_context()
        assert ctx.field("nonexistent_field") is None

    def test_event_prefix_returns_event_data_value(self) -> None:
        ev = _make_event(event_data={"outcome": "success"})
        ctx = _make_context(event=ev)
        assert ctx.field("event.outcome") == "success"

    def test_event_prefix_missing_key_returns_none(self) -> None:
        ctx = _make_context()
        assert ctx.field("event.nonexistent") is None

    def test_event_prefix_explicit_none_value_returns_none(self) -> None:
        """A literal None in event_data and a missing key both return None — by design."""
        ev = _make_event(event_data={"explicit_null": None})
        ctx = _make_context(event=ev)
        assert ctx.field("event.explicit_null") is None
        assert ctx.field("event.absent") is None

    def test_event_dot_only_returns_event_data_empty_string_lookup(self) -> None:
        """field('event.') strips the prefix to '' and looks up '' in event_data."""
        ctx = _make_context()
        assert ctx.field("event.") is None

    def test_event_dot_only_with_empty_string_key_present(self) -> None:
        ev = _make_event(event_data={"": "sentinel"})
        ctx = _make_context(event=ev)
        assert ctx.field("event.") == "sentinel"

    def test_flags_name_returns_frozenset(self) -> None:
        ctx = _make_context(flags=frozenset({"FISH_SUGGESTED"}))
        result = ctx.field("flags")
        assert isinstance(result, frozenset)
        assert "FISH_SUGGESTED" in result

    def test_flags_empty_frozenset_returned(self) -> None:
        ctx = _make_context(flags=frozenset())
        assert ctx.field("flags") == frozenset()

    def test_event_prefix_nested_value_types(self) -> None:
        ev = _make_event(event_data={"all_slides_complete": True, "count": 3})
        ctx = _make_context(event=ev)
        assert ctx.field("event.all_slides_complete") is True
        assert ctx.field("event.count") == 3

    def test_current_state_returns_specimen_context_value(self) -> None:
        """current_state lives on SpecimenContext; field() must surface it."""
        ctx = _make_context(current_state="ACCEPTED")
        assert ctx.field("current_state") == "ACCEPTED"


# ---------------------------------------------------------------------------
# Slice 1 (continued) — public re-exports from samantha_server.models
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["Order", "Event", "SpecimenContext", "VALID_STATES", "VALID_FLAGS", "FIELD_MAX_LENGTHS"],
)
def test_public_reexport(name: str) -> None:
    import samantha_server.models as models

    assert hasattr(models, name)


def test_field_max_lengths_is_mapping_proxy_type() -> None:
    """Sanity-check on the immutability vehicle so future refactors don't silently regress."""
    assert isinstance(FIELD_MAX_LENGTHS, MappingProxyType)


# ---------------------------------------------------------------------------
# M-13: Event.event_data byte-size validator
# ---------------------------------------------------------------------------


def test_event_data_oversized_raises_validation_error() -> None:
    """M-13: event_data exceeding MAX_EVENT_DATA_BYTES must raise ValidationError.

    Oversized payloads reach compute_event_input_hash, the prompt, and the
    receipt without a guard — potentially causing prompt injection via large
    payloads. The validator caps at 64 KB.
    """
    # Build a dict whose JSON representation is > 64 KB
    oversized_value = "x" * 65_000  # 65 KB string
    oversized_data = {"payload": oversized_value}

    with pytest.raises(ValidationError, match="event_data"):
        Event(event_type="clinical_query", event_data=oversized_data, step_index=0)


def test_event_data_at_size_limit_passes() -> None:
    """M-13: event_data well within the limit must be accepted."""
    # A small but non-trivial dict that fits comfortably within 64 KB
    data = {"query": "Is this specimen ready for grossing?"}
    event = Event(event_type="clinical_query", event_data=data, step_index=0)
    assert event.event_data["query"] == "Is this specimen ready for grossing?"
