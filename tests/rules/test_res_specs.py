"""End-to-end tests that load the 5 RES YAML specs from the specs directory.

Mirrors the structure of tests/rules/test_sp_specs.py.  RES rules differ from
SP/HE in two ways:

  1. They read the order-accumulated `flags` field via the `Contains('flags',
     ...)` primitive (RES-001, RES-003).  Every other step's predicates only
     read order-level scalar fields and event_data.
  2. RES-002 is the canonical receipt-branching example: the predicate fires
     whenever a missing-info event arrives, and the action handler chooses
     between `cleared` and `still_held` at runtime (per
     decision-gate.md § "Action handler boundary").  The spec records the
     branch family in `action.branch` so audits can reconstruct the choice.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from samantha_server.models.context import Event, Order, SpecimenContext
from samantha_server.primitives import (
    BooleanAnd,
    Contains,
    Equals,
    IsNull,
    Not,
)
from samantha_server.rules.loader import RuleIndex, load_rule_specs
from samantha_server.rules.spec import RuleSpec

_SPECS_DIR = Path(__file__).resolve().parents[2] / "samantha_server" / "rules" / "specs"

_EXPECTED_RULE_IDS = {
    "RES-001",
    "RES-002",
    "RES-003",
    "RES-004",
    "RES-005",
}

_PRIORITY_BY_RULE: dict[str, int] = {
    "RES-001": 1,
    "RES-002": 2,
    "RES-003": 3,
    "RES-004": 4,
    "RES-005": 5,
}


@pytest.fixture(scope="module")
def res_specs() -> list[RuleSpec]:
    return [s for s in load_rule_specs(_SPECS_DIR) if s.step == "RESULTING"]


# ---------------------------------------------------------------------------
# A — Loading and inventory invariants
# ---------------------------------------------------------------------------


class TestResSpecsLoad:
    def test_five_specs_loaded(self, res_specs: list[RuleSpec]) -> None:
        assert len(res_specs) == 5

    def test_all_rule_ids_present(self, res_specs: list[RuleSpec]) -> None:
        ids = {s.rule_id for s in res_specs}
        assert ids == _EXPECTED_RULE_IDS

    def test_all_have_step_resulting(self, res_specs: list[RuleSpec]) -> None:
        assert all(s.step == "RESULTING" for s in res_specs)

    def test_priorities_are_sequential(self, res_specs: list[RuleSpec]) -> None:
        priorities = sorted(s.priority for s in res_specs if s.priority is not None)
        assert priorities == [1, 2, 3, 4, 5]

    def test_severity_is_none_for_all(self, res_specs: list[RuleSpec]) -> None:
        assert all(s.severity is None for s in res_specs)

    def test_applies_at_is_none_for_all(self, res_specs: list[RuleSpec]) -> None:
        assert all(s.applies_at is None for s in res_specs)


# ---------------------------------------------------------------------------
# B — Pin event_type, transition, outcome, priority per rule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rule_id, expected_event_types",
    [
        ("RES-001", ("resulting_review",)),
        ("RES-002", ("missing_info_received",)),
        ("RES-003", ("resulting_review",)),
        ("RES-004", ("pathologist_signout",)),
        ("RES-005", ("report_generated",)),
    ],
)
def test_res_event_types_match_canonical_vocabulary(
    res_specs: list[RuleSpec],
    rule_id: str,
    expected_event_types: tuple[str, ...],
) -> None:
    spec = next(s for s in res_specs if s.rule_id == rule_id)
    assert spec.event_type == expected_event_types


@pytest.mark.parametrize(
    "rule_id, expected_transition",
    [
        ("RES-001", "RESULTING_HOLD"),
        ("RES-002", "RESOLVE_MISSING_INFO"),
        ("RES-003", "PATHOLOGIST_SIGNOUT"),
        ("RES-004", "REPORT_GENERATION"),
        ("RES-005", "ORDER_COMPLETE"),
    ],
)
def test_res_transitions_match_canonical_vocabulary(
    res_specs: list[RuleSpec],
    rule_id: str,
    expected_transition: str,
) -> None:
    spec = next(s for s in res_specs if s.rule_id == rule_id)
    assert spec.action.transition == expected_transition


@pytest.mark.parametrize(
    "rule_id, expected_outcome",
    [
        ("RES-001", "held_resulting_pending_missing_info"),
        ("RES-002", "evaluated_missing_info_received"),
        ("RES-003", "advanced_pathologist_signout"),
        ("RES-004", "advanced_report_generation"),
        ("RES-005", "completed_order"),
    ],
)
def test_res_outcomes_match_canonical_vocabulary(
    res_specs: list[RuleSpec],
    rule_id: str,
    expected_outcome: str,
) -> None:
    spec = next(s for s in res_specs if s.rule_id == rule_id)
    assert spec.action.outcome == expected_outcome


@pytest.mark.parametrize("rule_id", sorted(_EXPECTED_RULE_IDS))
def test_res_priorities(res_specs: list[RuleSpec], rule_id: str) -> None:
    spec = next(s for s in res_specs if s.rule_id == rule_id)
    assert spec.priority == _PRIORITY_BY_RULE[rule_id]


def test_res_outcomes_are_unique_across_family(res_specs: list[RuleSpec]) -> None:
    outcomes = [s.action.outcome for s in res_specs]
    assert len(outcomes) == len(set(outcomes))


# ---------------------------------------------------------------------------
# C — RES-002 branch field: the canonical receipt-branching example
# ---------------------------------------------------------------------------


class TestRes002BranchField:
    """RES-002's action.branch records that the action handler chooses between
    `cleared` and `still_held` at runtime.  No other rule in the corpus carries
    a non-null branch field, so these tests pin both the value on RES-002 and
    the absence on every other rule."""

    def test_res002_branch_documents_two_outcomes(self, res_specs: list[RuleSpec]) -> None:
        spec = next(s for s in res_specs if s.rule_id == "RES-002")
        assert spec.action.branch == "cleared|still_held"

    @pytest.mark.parametrize("rule_id", sorted(_EXPECTED_RULE_IDS - {"RES-002"}))
    def test_other_res_rules_have_null_branch(
        self, res_specs: list[RuleSpec], rule_id: str
    ) -> None:
        spec = next(s for s in res_specs if s.rule_id == rule_id)
        assert spec.action.branch is None


# ---------------------------------------------------------------------------
# D — Predicate-shape pins per rule (per categorization.md)
# ---------------------------------------------------------------------------


class TestResPredicateShape:
    def test_res001_is_contains_flags_missing_info_proceed(self, res_specs: list[RuleSpec]) -> None:
        spec = next(s for s in res_specs if s.rule_id == "RES-001")
        assert isinstance(spec.when, Contains)
        assert spec.when.field == "flags"
        assert spec.when.value == "MISSING_INFO_PROCEED"

    def test_res002_is_not_isnull_event_info_type(self, res_specs: list[RuleSpec]) -> None:
        spec = next(s for s in res_specs if s.rule_id == "RES-002")
        assert isinstance(spec.when, Not)
        inner = spec.when.child
        assert isinstance(inner, IsNull)
        assert inner.field == "event.info_type"

    def test_res003_is_boolean_and_advance_and_no_missing_info_flag(
        self, res_specs: list[RuleSpec]
    ) -> None:
        spec = next(s for s in res_specs if s.rule_id == "RES-003")
        assert isinstance(spec.when, BooleanAnd)
        assert len(spec.when.children) == 2
        first, second = spec.when.children
        assert isinstance(first, Equals)
        assert first.field == "event.outcome"
        assert first.value == "advance"
        assert isinstance(second, Not)
        not_inner = second.child
        assert isinstance(not_inner, Contains)
        assert not_inner.field == "flags"
        assert not_inner.value == "MISSING_INFO_PROCEED"

    def test_res004_is_not_isnull_event_reportable_tests(self, res_specs: list[RuleSpec]) -> None:
        spec = next(s for s in res_specs if s.rule_id == "RES-004")
        assert isinstance(spec.when, Not)
        inner = spec.when.child
        assert isinstance(inner, IsNull)
        assert inner.field == "event.reportable_tests"

    def test_res005_is_equals_event_outcome_success(self, res_specs: list[RuleSpec]) -> None:
        spec = next(s for s in res_specs if s.rule_id == "RES-005")
        assert isinstance(spec.when, Equals)
        assert spec.when.field == "event.outcome"
        assert spec.when.value == "success"


# ---------------------------------------------------------------------------
# E — Behavioral round-trip helpers
# ---------------------------------------------------------------------------


def _make_order() -> Order:
    return Order(
        order_id="ORD-RES-TEST",
        patient_name="Jane Doe",
        patient_sex="F",
        age=55,
        specimen_type="biopsy",
        anatomic_site="breast",
        fixative="formalin",
        fixation_time_hours=24.0,
        ordered_tests=("ER", "PR", "HER2", "Ki-67"),
        priority="routine",
        billing_info_present=True,
    )


def _make_ctx(
    *,
    current_state: str,
    event_type: str,
    event_data: dict[str, Any],
    flags: frozenset[str] = frozenset(),
) -> SpecimenContext:
    return SpecimenContext(
        order=_make_order(),
        current_state=current_state,
        flags=flags,
        event=Event(event_type=event_type, event_data=event_data, step_index=0),
    )


# ---------------------------------------------------------------------------
# F — Predicate evaluate() round-trip per rule
# ---------------------------------------------------------------------------


class TestRes001Evaluate:
    def test_fires_when_flag_present(self, res_specs: list[RuleSpec]) -> None:
        rule = next(s for s in res_specs if s.rule_id == "RES-001")
        ctx = _make_ctx(
            current_state="RESULTING",
            event_type="resulting_review",
            event_data={"outcome": "pending"},
            flags=frozenset({"MISSING_INFO_PROCEED"}),
        )
        assert rule.when.evaluate(ctx) is True

    def test_does_not_fire_when_flag_absent(self, res_specs: list[RuleSpec]) -> None:
        rule = next(s for s in res_specs if s.rule_id == "RES-001")
        ctx = _make_ctx(
            current_state="RESULTING",
            event_type="resulting_review",
            event_data={"outcome": "pending"},
            flags=frozenset(),
        )
        assert rule.when.evaluate(ctx) is False


class TestRes002Evaluate:
    def test_fires_when_info_type_present(self, res_specs: list[RuleSpec]) -> None:
        rule = next(s for s in res_specs if s.rule_id == "RES-002")
        ctx = _make_ctx(
            current_state="RESULTING_HOLD",
            event_type="missing_info_received",
            event_data={"info_type": "billing", "value": "BLG-001"},
        )
        assert rule.when.evaluate(ctx) is True

    def test_does_not_fire_when_info_type_null(self, res_specs: list[RuleSpec]) -> None:
        rule = next(s for s in res_specs if s.rule_id == "RES-002")
        ctx = _make_ctx(
            current_state="RESULTING_HOLD",
            event_type="missing_info_received",
            event_data={"info_type": None, "value": None},
        )
        assert rule.when.evaluate(ctx) is False

    def test_does_not_fire_when_info_type_missing(self, res_specs: list[RuleSpec]) -> None:
        rule = next(s for s in res_specs if s.rule_id == "RES-002")
        ctx = _make_ctx(
            current_state="RESULTING_HOLD",
            event_type="missing_info_received",
            event_data={},
        )
        assert rule.when.evaluate(ctx) is False


class TestRes003Evaluate:
    def test_fires_when_advance_and_no_blocking_flag(self, res_specs: list[RuleSpec]) -> None:
        rule = next(s for s in res_specs if s.rule_id == "RES-003")
        ctx = _make_ctx(
            current_state="RESULTING",
            event_type="resulting_review",
            event_data={"outcome": "advance"},
            flags=frozenset(),
        )
        assert rule.when.evaluate(ctx) is True

    def test_does_not_fire_when_blocking_flag_present(self, res_specs: list[RuleSpec]) -> None:
        rule = next(s for s in res_specs if s.rule_id == "RES-003")
        ctx = _make_ctx(
            current_state="RESULTING",
            event_type="resulting_review",
            event_data={"outcome": "advance"},
            flags=frozenset({"MISSING_INFO_PROCEED"}),
        )
        assert rule.when.evaluate(ctx) is False

    def test_does_not_fire_when_outcome_not_advance(self, res_specs: list[RuleSpec]) -> None:
        rule = next(s for s in res_specs if s.rule_id == "RES-003")
        ctx = _make_ctx(
            current_state="RESULTING",
            event_type="resulting_review",
            event_data={"outcome": "hold"},
            flags=frozenset(),
        )
        assert rule.when.evaluate(ctx) is False


class TestRes004Evaluate:
    def test_fires_when_reportable_tests_present(self, res_specs: list[RuleSpec]) -> None:
        rule = next(s for s in res_specs if s.rule_id == "RES-004")
        ctx = _make_ctx(
            current_state="PATHOLOGIST_SIGNOUT",
            event_type="pathologist_signout",
            event_data={"reportable_tests": ["ER", "PR"]},
        )
        assert rule.when.evaluate(ctx) is True

    def test_does_not_fire_when_reportable_tests_null(self, res_specs: list[RuleSpec]) -> None:
        rule = next(s for s in res_specs if s.rule_id == "RES-004")
        ctx = _make_ctx(
            current_state="PATHOLOGIST_SIGNOUT",
            event_type="pathologist_signout",
            event_data={"reportable_tests": None},
        )
        assert rule.when.evaluate(ctx) is False

    def test_does_not_fire_when_reportable_tests_missing(self, res_specs: list[RuleSpec]) -> None:
        rule = next(s for s in res_specs if s.rule_id == "RES-004")
        ctx = _make_ctx(
            current_state="PATHOLOGIST_SIGNOUT",
            event_type="pathologist_signout",
            event_data={},
        )
        assert rule.when.evaluate(ctx) is False


class TestRes005Evaluate:
    def test_fires_when_outcome_is_success(self, res_specs: list[RuleSpec]) -> None:
        rule = next(s for s in res_specs if s.rule_id == "RES-005")
        ctx = _make_ctx(
            current_state="REPORT_GENERATION",
            event_type="report_generated",
            event_data={"outcome": "success"},
        )
        assert rule.when.evaluate(ctx) is True

    def test_does_not_fire_when_outcome_differs(self, res_specs: list[RuleSpec]) -> None:
        rule = next(s for s in res_specs if s.rule_id == "RES-005")
        ctx = _make_ctx(
            current_state="REPORT_GENERATION",
            event_type="report_generated",
            event_data={"outcome": "failed"},
        )
        assert rule.when.evaluate(ctx) is False

    def test_fails_closed_on_empty_event_data(self, res_specs: list[RuleSpec]) -> None:
        """Mirrors IHC § P: an empty event_data carries no `outcome` key,
        so the order stalls at REPORT_GENERATION with applied_rules=[].
        Pinning this documents the current behavior; whether to add a
        catch-all is a kernel-layer decision tracked in GH-15."""
        rule = next(s for s in res_specs if s.rule_id == "RES-005")
        ctx = _make_ctx(
            current_state="REPORT_GENERATION",
            event_type="report_generated",
            event_data={},
        )
        assert rule.when.evaluate(ctx) is False


# ---------------------------------------------------------------------------
# G — Action shape: set_flags / clear_flags
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rule_id", sorted(_EXPECTED_RULE_IDS))
def test_res_rules_set_no_flags(res_specs: list[RuleSpec], rule_id: str) -> None:
    """No RES rule sets or clears a flag at the predicate layer.  Flag
    cleanup on missing-info-received is the kernel's responsibility (the
    action handler decides whether to clear MISSING_INFO_PROCEED based on
    the runtime branch — see decision-gate.md § "Action handler boundary"
    and GH-15).  Pinning empty set/clear here documents the intent so a
    future authoring mistake fails loudly."""
    spec = next(s for s in res_specs if s.rule_id == rule_id)
    assert spec.action.set_flags == ()
    assert spec.action.clear_flags == ()


# ---------------------------------------------------------------------------
# H — RuleIndex dispatch order
# ---------------------------------------------------------------------------


def test_res_dispatch_order(res_specs: list[RuleSpec]) -> None:
    index = RuleIndex(res_specs)
    ids = [r.rule_id for r in index.rules_by_step["RESULTING"]]
    assert ids == ["RES-001", "RES-002", "RES-003", "RES-004", "RES-005"]


# ---------------------------------------------------------------------------
# I — Field-alignment regression guard
#     Mirrors test_he_specs.py § F: a typo in a field name silently
#     fails-closed via SpecimenContext.field()'s getattr fallback.
# ---------------------------------------------------------------------------


def test_res001_flags_lookup_resolves(res_specs: list[RuleSpec]) -> None:
    rule = next(s for s in res_specs if s.rule_id == "RES-001")
    ctx = _make_ctx(
        current_state="RESULTING",
        event_type="resulting_review",
        event_data={"outcome": "pending"},
        flags=frozenset({"MISSING_INFO_PROCEED"}),
    )
    assert ctx.field("flags") == frozenset({"MISSING_INFO_PROCEED"})
    assert rule.when.evaluate(ctx) is True


def test_res002_event_info_type_lookup_resolves(res_specs: list[RuleSpec]) -> None:
    rule = next(s for s in res_specs if s.rule_id == "RES-002")
    ctx = _make_ctx(
        current_state="RESULTING_HOLD",
        event_type="missing_info_received",
        event_data={"info_type": "billing", "value": "BLG-001"},
    )
    assert ctx.field("event.info_type") == "billing"
    assert rule.when.evaluate(ctx) is True


def test_res003_event_outcome_and_flags_lookups_resolve(
    res_specs: list[RuleSpec],
) -> None:
    """Pin the `flags` lookup so a typo (e.g. `flag` or `order.flags`) fails
    loudly instead of silently fail-opening. Without this guard,
    `Contains('flag', 'MISSING_INFO_PROCEED')` would never match, so
    `Not(Contains(...))` would always return True and RES-003 would fire
    even when the blocking flag is set."""
    rule = next(s for s in res_specs if s.rule_id == "RES-003")
    ctx = _make_ctx(
        current_state="RESULTING",
        event_type="resulting_review",
        event_data={"outcome": "advance"},
        flags=frozenset(),
    )
    assert ctx.field("event.outcome") == "advance"
    assert ctx.field("flags") == frozenset()
    assert rule.when.evaluate(ctx) is True


def test_res004_event_reportable_tests_lookup_resolves(
    res_specs: list[RuleSpec],
) -> None:
    rule = next(s for s in res_specs if s.rule_id == "RES-004")
    ctx = _make_ctx(
        current_state="PATHOLOGIST_SIGNOUT",
        event_type="pathologist_signout",
        event_data={"reportable_tests": ["ER", "PR"]},
    )
    assert ctx.field("event.reportable_tests") == ["ER", "PR"]
    assert rule.when.evaluate(ctx) is True


def test_res005_event_outcome_lookup_resolves(res_specs: list[RuleSpec]) -> None:
    rule = next(s for s in res_specs if s.rule_id == "RES-005")
    ctx = _make_ctx(
        current_state="REPORT_GENERATION",
        event_type="report_generated",
        event_data={"outcome": "success"},
    )
    assert ctx.field("event.outcome") == "success"
    assert rule.when.evaluate(ctx) is True
