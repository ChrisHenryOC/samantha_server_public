"""End-to-end tests that load the 11 IHC YAML specs from the specs directory.

Mirrors the structure of tests/rules/test_he_specs.py.  IHC rules differ from
the other steps in two ways:

  1. They share `step: IHC` but partition by `applies_at:` into five buckets:
     IHC_STAINING, IHC_QC, IHC_SCORING, SUGGEST_FISH_REFLEX, FISH_SEND_OUT.
  2. IHC-001 carries the deepest-nested predicate in the corpus and is the
     canonical fail-closed null-semantics example for `fixation_time_hours`:
     when the field is None, `Not(ThresholdGTE(...))` returns True, so the
     out-of-tolerance branch fires and HER2 is rejected.

Event/data shapes are pinned to the values that appear in the imported
scenarios (sc_048, sc_050, sc_052, sc_054, sc_056, sc_058, sc_060, sc_062,
sc_064, sc_066, sc_068).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from samantha_server.models.context import Event, Order, SpecimenContext
from samantha_server.primitives import (
    BooleanAnd,
    BooleanOr,
    Equals,
    Not,
    ThresholdGTE,
    ThresholdLTE,
)
from samantha_server.rules.loader import RuleIndex, load_rule_specs
from samantha_server.rules.spec import RuleSpec

_SPECS_DIR = Path(__file__).resolve().parents[2] / "samantha_server" / "rules" / "specs"

_EXPECTED_RULE_IDS = {
    "IHC-001",
    "IHC-002",
    "IHC-003",
    "IHC-004",
    "IHC-005",
    "IHC-006",
    "IHC-007",
    "IHC-008",
    "IHC-009",
    "IHC-010",
    "IHC-011",
}

_APPLIES_AT_BY_RULE: dict[str, str] = {
    "IHC-001": "IHC_STAINING",
    "IHC-002": "IHC_QC",
    "IHC-003": "IHC_QC",
    "IHC-004": "IHC_QC",
    "IHC-005": "IHC_QC",
    "IHC-006": "IHC_SCORING",
    "IHC-007": "IHC_SCORING",
    "IHC-008": "SUGGEST_FISH_REFLEX",
    "IHC-009": "SUGGEST_FISH_REFLEX",
    "IHC-010": "FISH_SEND_OUT",
    "IHC-011": "FISH_SEND_OUT",
}

_PRIORITY_BY_RULE: dict[str, int] = {
    "IHC-001": 1,
    "IHC-002": 2,
    "IHC-003": 3,
    "IHC-004": 4,
    "IHC-005": 5,
    "IHC-006": 6,
    "IHC-007": 7,
    "IHC-008": 8,
    "IHC-009": 9,
    "IHC-010": 10,
    "IHC-011": 11,
}


@pytest.fixture(scope="module")
def ihc_specs() -> list[RuleSpec]:
    return [s for s in load_rule_specs(_SPECS_DIR) if s.step == "IHC"]


# ---------------------------------------------------------------------------
# A — Loading and inventory invariants
# ---------------------------------------------------------------------------


class TestIhcSpecsLoad:
    def test_eleven_specs_loaded(self, ihc_specs: list[RuleSpec]) -> None:
        assert len(ihc_specs) == 11

    def test_all_rule_ids_present(self, ihc_specs: list[RuleSpec]) -> None:
        ids = {s.rule_id for s in ihc_specs}
        assert ids == _EXPECTED_RULE_IDS

    def test_all_have_step_ihc(self, ihc_specs: list[RuleSpec]) -> None:
        assert all(s.step == "IHC" for s in ihc_specs)

    def test_priorities_are_sequential(self, ihc_specs: list[RuleSpec]) -> None:
        priorities = sorted(s.priority for s in ihc_specs if s.priority is not None)
        assert priorities == list(range(1, 12))


# ---------------------------------------------------------------------------
# B — applies_at scoping (the differentiator for the IHC step)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rule_id, expected_applies_at", sorted(_APPLIES_AT_BY_RULE.items()))
def test_applies_at_per_rule(
    ihc_specs: list[RuleSpec], rule_id: str, expected_applies_at: str
) -> None:
    spec = next(s for s in ihc_specs if s.rule_id == rule_id)
    assert spec.applies_at == expected_applies_at


# ---------------------------------------------------------------------------
# C1 — Pin event_type per rule (canonical vocabulary)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rule_id, expected_event_types",
    [
        ("IHC-001", ("ihc_staining_complete",)),
        ("IHC-002", ("ihc_qc",)),
        ("IHC-003", ("ihc_qc",)),
        ("IHC-004", ("ihc_qc",)),
        ("IHC-005", ("ihc_qc",)),
        ("IHC-006", ("ihc_scoring",)),
        ("IHC-007", ("ihc_scoring",)),
        ("IHC-008", ("fish_decision",)),
        ("IHC-009", ("fish_decision",)),
        ("IHC-010", ("fish_result",)),
        ("IHC-011", ("fish_result",)),
    ],
)
def test_ihc_event_types_match_canonical_vocabulary(
    ihc_specs: list[RuleSpec],
    rule_id: str,
    expected_event_types: tuple[str, ...],
) -> None:
    spec = next(s for s in ihc_specs if s.rule_id == rule_id)
    assert spec.event_type == expected_event_types


# ---------------------------------------------------------------------------
# C2 — Pin outcome per rule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rule_id, expected_outcome",
    [
        ("IHC-001", "rejected_her2_late_fixation"),
        ("IHC-002", "advanced_ihc_qc_passed"),
        ("IHC-003", "held_ihc_slides_pending"),
        ("IHC-004", "retried_ihc_staining_failed"),
        ("IHC-005", "aborted_ihc_qns"),
        ("IHC-006", "advanced_ihc_scoring_complete"),
        ("IHC-007", "suggested_fish_reflex"),
        ("IHC-008", "approved_fish_send_out"),
        ("IHC-009", "declined_fish_proceeded_resulting"),
        ("IHC-010", "advanced_resulting_fish_received"),
        ("IHC-011", "aborted_fish_qns"),
    ],
)
def test_ihc_outcomes_match_canonical_vocabulary(
    ihc_specs: list[RuleSpec],
    rule_id: str,
    expected_outcome: str,
) -> None:
    spec = next(s for s in ihc_specs if s.rule_id == rule_id)
    assert spec.action.outcome == expected_outcome


# ---------------------------------------------------------------------------
# C3 — Pin transition per rule (from samantha-public workflow_states.yaml)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rule_id, expected_transition",
    [
        ("IHC-001", "IHC_STAINING"),
        ("IHC-002", "IHC_SCORING"),
        ("IHC-003", "IHC_QC"),
        ("IHC-004", "IHC_STAINING"),
        ("IHC-005", "ORDER_TERMINATED_QNS"),
        ("IHC-006", "RESULTING"),
        ("IHC-007", "SUGGEST_FISH_REFLEX"),
        ("IHC-008", "FISH_SEND_OUT"),
        ("IHC-009", "RESULTING"),
        ("IHC-010", "RESULTING"),
        ("IHC-011", "ORDER_TERMINATED_QNS"),
    ],
)
def test_ihc_transitions_match_canonical_vocabulary(
    ihc_specs: list[RuleSpec],
    rule_id: str,
    expected_transition: str,
) -> None:
    spec = next(s for s in ihc_specs if s.rule_id == rule_id)
    assert spec.action.transition == expected_transition


# ---------------------------------------------------------------------------
# C4 — Pin schema invariants: severity None, applies_at present, priority sequential
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rule_id", sorted(_EXPECTED_RULE_IDS))
def test_ihc_schema_invariants(ihc_specs: list[RuleSpec], rule_id: str) -> None:
    spec = next(s for s in ihc_specs if s.rule_id == rule_id)
    assert spec.severity is None
    assert spec.applies_at is not None
    assert spec.priority == _PRIORITY_BY_RULE[rule_id]


# ---------------------------------------------------------------------------
# D — Behavioral round-trip helpers
# ---------------------------------------------------------------------------


def _make_order(
    *,
    fixative: str = "formalin",
    fixation_time_hours: float | None = 24.0,
    ordered_tests: tuple[str, ...] = ("ER", "PR", "Ki-67"),
) -> Order:
    return Order(
        order_id="ORD-IHC-TEST",
        patient_name="Jane Doe",
        patient_sex="F",
        age=55,
        specimen_type="biopsy",
        anatomic_site="breast",
        fixative=fixative,
        fixation_time_hours=fixation_time_hours,
        ordered_tests=ordered_tests,
        priority="routine",
        billing_info_present=True,
    )


def _make_ctx(
    *,
    current_state: str,
    event_type: str,
    event_data: dict[str, Any],
    order: Order | None = None,
    flags: frozenset[str] = frozenset(),
) -> SpecimenContext:
    return SpecimenContext(
        order=order if order is not None else _make_order(),
        current_state=current_state,
        flags=flags,
        event=Event(event_type=event_type, event_data=event_data, step_index=0),
    )


# ---------------------------------------------------------------------------
# E — IHC-001: deepest-nested predicate, fail-closed null semantics
# ---------------------------------------------------------------------------


class TestIhc001PredicateShape:
    """IHC-001's predicate is `BooleanAnd(her2_added_at_review, BooleanOr(<3 fixation checks>))`.

    Pinned because IHC-001 is the deepest-nested rule in the corpus and the
    canonical example of fail-closed null-semantics on fixation_time_hours.
    Any reshape (e.g. collapsing the BooleanOr or removing a Not wrapper)
    silently changes which inputs trigger HER2 rejection.
    """

    def test_outer_is_boolean_and(self, ihc_specs: list[RuleSpec]) -> None:
        spec = next(s for s in ihc_specs if s.rule_id == "IHC-001")
        assert isinstance(spec.when, BooleanAnd)
        assert len(spec.when.children) == 2

    def test_first_child_is_her2_added_at_review_equals_true(
        self, ihc_specs: list[RuleSpec]
    ) -> None:
        spec = next(s for s in ihc_specs if s.rule_id == "IHC-001")
        assert isinstance(spec.when, BooleanAnd)
        first = spec.when.children[0]
        assert isinstance(first, Equals)
        assert first.field == "event.her2_added_at_review"
        assert first.value is True

    def test_second_child_is_boolean_or_with_three_fixation_checks(
        self, ihc_specs: list[RuleSpec]
    ) -> None:
        spec = next(s for s in ihc_specs if s.rule_id == "IHC-001")
        assert isinstance(spec.when, BooleanAnd)
        second = spec.when.children[1]
        assert isinstance(second, BooleanOr)
        assert len(second.children) == 3


def _ihc001_ctx(
    *,
    fixative: str = "formalin",
    fixation_time_hours: float | None = 24.0,
    her2_added_at_review: bool = True,
) -> SpecimenContext:
    return _make_ctx(
        current_state="IHC_STAINING",
        event_type="ihc_staining_complete",
        event_data={
            "outcome": "partial",
            "her2_added_at_review": her2_added_at_review,
            "fixation_out_of_tolerance": True,
            "her2_rejected": True,
        },
        order=_make_order(fixative=fixative, fixation_time_hours=fixation_time_hours),
    )


class TestIhc001EvaluateBehavior:
    def test_fires_when_her2_added_and_fixative_not_formalin(
        self, ihc_specs: list[RuleSpec]
    ) -> None:
        rule = next(s for s in ihc_specs if s.rule_id == "IHC-001")
        assert rule.when.evaluate(_ihc001_ctx(fixative="bouin")) is True

    def test_fires_when_her2_added_and_fixation_too_short(self, ihc_specs: list[RuleSpec]) -> None:
        rule = next(s for s in ihc_specs if s.rule_id == "IHC-001")
        assert rule.when.evaluate(_ihc001_ctx(fixation_time_hours=5.0)) is True

    def test_fires_when_her2_added_and_fixation_too_long(self, ihc_specs: list[RuleSpec]) -> None:
        rule = next(s for s in ihc_specs if s.rule_id == "IHC-001")
        assert rule.when.evaluate(_ihc001_ctx(fixation_time_hours=80.0)) is True

    def test_does_not_fire_when_in_tolerance(self, ihc_specs: list[RuleSpec]) -> None:
        rule = next(s for s in ihc_specs if s.rule_id == "IHC-001")
        assert rule.when.evaluate(_ihc001_ctx(fixation_time_hours=24.0)) is False

    def test_does_not_fire_when_her2_not_added(self, ihc_specs: list[RuleSpec]) -> None:
        rule = next(s for s in ihc_specs if s.rule_id == "IHC-001")
        assert (
            rule.when.evaluate(_ihc001_ctx(fixation_time_hours=5.0, her2_added_at_review=False))
            is False
        )

    def test_fail_closed_on_null_fixation_time(self, ihc_specs: list[RuleSpec]) -> None:
        """Canonical fail-closed null semantics: when fixation_time_hours is None,
        ThresholdGTE returns False, so Not(ThresholdGTE) returns True, so the
        BooleanOr fires and HER2 is rejected. Pin this — it's the documented
        intent of IHC-001 in fixation_requirements.md.
        """
        rule = next(s for s in ihc_specs if s.rule_id == "IHC-001")
        assert rule.when.evaluate(_ihc001_ctx(fixation_time_hours=None)) is True


# ---------------------------------------------------------------------------
# F — IHC-002..IHC-011 evaluate() round-trip per scenario corpus
# ---------------------------------------------------------------------------


class TestIhcQcEvaluateBehavior:
    """IHC-002..IHC-005 fire on `ihc_qc` events with applies_at=IHC_QC."""

    def test_ihc002_fires_when_all_slides_complete(self, ihc_specs: list[RuleSpec]) -> None:
        rule = next(s for s in ihc_specs if s.rule_id == "IHC-002")
        ctx = _make_ctx(
            current_state="IHC_QC",
            event_type="ihc_qc",
            event_data={"all_slides_complete": True, "slides": []},
        )
        assert rule.when.evaluate(ctx) is True

    def test_ihc002_does_not_fire_when_pending(self, ihc_specs: list[RuleSpec]) -> None:
        rule = next(s for s in ihc_specs if s.rule_id == "IHC-002")
        ctx = _make_ctx(
            current_state="IHC_QC",
            event_type="ihc_qc",
            event_data={"all_slides_complete": False, "slides": []},
        )
        assert rule.when.evaluate(ctx) is False

    def test_ihc003_fires_when_some_slides_pending(self, ihc_specs: list[RuleSpec]) -> None:
        rule = next(s for s in ihc_specs if s.rule_id == "IHC-003")
        ctx = _make_ctx(
            current_state="IHC_QC",
            event_type="ihc_qc",
            event_data={"all_slides_complete": False, "slides": []},
        )
        assert rule.when.evaluate(ctx) is True

    def test_ihc003_does_not_fire_when_complete(self, ihc_specs: list[RuleSpec]) -> None:
        rule = next(s for s in ihc_specs if s.rule_id == "IHC-003")
        ctx = _make_ctx(
            current_state="IHC_QC",
            event_type="ihc_qc",
            event_data={"all_slides_complete": True, "slides": []},
        )
        assert rule.when.evaluate(ctx) is False

    def test_ihc004_fires_on_staining_failure_with_tissue(self, ihc_specs: list[RuleSpec]) -> None:
        rule = next(s for s in ihc_specs if s.rule_id == "IHC-004")
        ctx = _make_ctx(
            current_state="IHC_QC",
            event_type="ihc_qc",
            event_data={"staining_failure": True, "tissue_available": True},
        )
        assert rule.when.evaluate(ctx) is True

    def test_ihc004_does_not_fire_without_tissue(self, ihc_specs: list[RuleSpec]) -> None:
        rule = next(s for s in ihc_specs if s.rule_id == "IHC-004")
        ctx = _make_ctx(
            current_state="IHC_QC",
            event_type="ihc_qc",
            event_data={"staining_failure": True, "tissue_available": False},
        )
        assert rule.when.evaluate(ctx) is False

    def test_ihc005_fires_on_staining_failure_without_tissue(
        self, ihc_specs: list[RuleSpec]
    ) -> None:
        rule = next(s for s in ihc_specs if s.rule_id == "IHC-005")
        ctx = _make_ctx(
            current_state="IHC_QC",
            event_type="ihc_qc",
            event_data={"staining_failure": True, "tissue_available": False},
        )
        assert rule.when.evaluate(ctx) is True

    def test_ihc005_does_not_fire_with_tissue(self, ihc_specs: list[RuleSpec]) -> None:
        rule = next(s for s in ihc_specs if s.rule_id == "IHC-005")
        ctx = _make_ctx(
            current_state="IHC_QC",
            event_type="ihc_qc",
            event_data={"staining_failure": True, "tissue_available": True},
        )
        assert rule.when.evaluate(ctx) is False


class TestIhcScoringEvaluateBehavior:
    """IHC-006/IHC-007 fire on `ihc_scoring` events with applies_at=IHC_SCORING."""

    def test_ihc006_fires_when_complete_and_no_equivocal(self, ihc_specs: list[RuleSpec]) -> None:
        rule = next(s for s in ihc_specs if s.rule_id == "IHC-006")
        ctx = _make_ctx(
            current_state="IHC_SCORING",
            event_type="ihc_scoring",
            event_data={"all_scores_complete": True, "any_equivocal": False, "scores": []},
        )
        assert rule.when.evaluate(ctx) is True

    def test_ihc006_does_not_fire_when_equivocal(self, ihc_specs: list[RuleSpec]) -> None:
        rule = next(s for s in ihc_specs if s.rule_id == "IHC-006")
        ctx = _make_ctx(
            current_state="IHC_SCORING",
            event_type="ihc_scoring",
            event_data={"all_scores_complete": True, "any_equivocal": True, "scores": []},
        )
        assert rule.when.evaluate(ctx) is False

    def test_ihc007_fires_when_complete_and_equivocal(self, ihc_specs: list[RuleSpec]) -> None:
        rule = next(s for s in ihc_specs if s.rule_id == "IHC-007")
        ctx = _make_ctx(
            current_state="IHC_SCORING",
            event_type="ihc_scoring",
            event_data={"all_scores_complete": True, "any_equivocal": True, "scores": []},
        )
        assert rule.when.evaluate(ctx) is True

    def test_ihc007_does_not_fire_when_not_equivocal(self, ihc_specs: list[RuleSpec]) -> None:
        rule = next(s for s in ihc_specs if s.rule_id == "IHC-007")
        ctx = _make_ctx(
            current_state="IHC_SCORING",
            event_type="ihc_scoring",
            event_data={"all_scores_complete": True, "any_equivocal": False, "scores": []},
        )
        assert rule.when.evaluate(ctx) is False


class TestFishDecisionEvaluateBehavior:
    """IHC-008/IHC-009 fire on `fish_decision` events with applies_at=SUGGEST_FISH_REFLEX."""

    def test_ihc008_fires_when_approved(self, ihc_specs: list[RuleSpec]) -> None:
        rule = next(s for s in ihc_specs if s.rule_id == "IHC-008")
        ctx = _make_ctx(
            current_state="SUGGEST_FISH_REFLEX",
            event_type="fish_decision",
            event_data={"approved": True},
        )
        assert rule.when.evaluate(ctx) is True

    def test_ihc008_does_not_fire_when_declined(self, ihc_specs: list[RuleSpec]) -> None:
        rule = next(s for s in ihc_specs if s.rule_id == "IHC-008")
        ctx = _make_ctx(
            current_state="SUGGEST_FISH_REFLEX",
            event_type="fish_decision",
            event_data={"approved": False},
        )
        assert rule.when.evaluate(ctx) is False

    def test_ihc009_fires_when_declined(self, ihc_specs: list[RuleSpec]) -> None:
        rule = next(s for s in ihc_specs if s.rule_id == "IHC-009")
        ctx = _make_ctx(
            current_state="SUGGEST_FISH_REFLEX",
            event_type="fish_decision",
            event_data={"approved": False},
        )
        assert rule.when.evaluate(ctx) is True

    def test_ihc009_does_not_fire_when_approved(self, ihc_specs: list[RuleSpec]) -> None:
        rule = next(s for s in ihc_specs if s.rule_id == "IHC-009")
        ctx = _make_ctx(
            current_state="SUGGEST_FISH_REFLEX",
            event_type="fish_decision",
            event_data={"approved": True},
        )
        assert rule.when.evaluate(ctx) is False


class TestFishResultEvaluateBehavior:
    """IHC-010/IHC-011 fire on `fish_result` events with applies_at=FISH_SEND_OUT."""

    def test_ihc010_fires_on_status_success(self, ihc_specs: list[RuleSpec]) -> None:
        rule = next(s for s in ihc_specs if s.rule_id == "IHC-010")
        ctx = _make_ctx(
            current_state="FISH_SEND_OUT",
            event_type="fish_result",
            event_data={"result": "negative", "status": "success"},
        )
        assert rule.when.evaluate(ctx) is True

    def test_ihc010_does_not_fire_on_qns(self, ihc_specs: list[RuleSpec]) -> None:
        rule = next(s for s in ihc_specs if s.rule_id == "IHC-010")
        ctx = _make_ctx(
            current_state="FISH_SEND_OUT",
            event_type="fish_result",
            event_data={"result": "qns", "status": "qns"},
        )
        assert rule.when.evaluate(ctx) is False

    def test_ihc011_fires_on_qns(self, ihc_specs: list[RuleSpec]) -> None:
        rule = next(s for s in ihc_specs if s.rule_id == "IHC-011")
        ctx = _make_ctx(
            current_state="FISH_SEND_OUT",
            event_type="fish_result",
            event_data={"result": "qns", "status": "qns"},
        )
        assert rule.when.evaluate(ctx) is True

    def test_ihc011_does_not_fire_on_success(self, ihc_specs: list[RuleSpec]) -> None:
        rule = next(s for s in ihc_specs if s.rule_id == "IHC-011")
        ctx = _make_ctx(
            current_state="FISH_SEND_OUT",
            event_type="fish_result",
            event_data={"result": "negative", "status": "success"},
        )
        assert rule.when.evaluate(ctx) is False


# ---------------------------------------------------------------------------
# G — Action shape: flags set by IHC-001 and IHC-007
# ---------------------------------------------------------------------------


class TestIhc001SetsHer2FixationRejectFlag:
    """IHC-001 is the only IHC rule that sets HER2_FIXATION_REJECT (per SC-048)."""

    def test_set_flags_contains_her2_fixation_reject(self, ihc_specs: list[RuleSpec]) -> None:
        spec = next(s for s in ihc_specs if s.rule_id == "IHC-001")
        assert spec.action.set_flags == ("HER2_FIXATION_REJECT",)

    def test_clear_flags_is_empty(self, ihc_specs: list[RuleSpec]) -> None:
        spec = next(s for s in ihc_specs if s.rule_id == "IHC-001")
        assert spec.action.clear_flags == ()


class TestIhc007SetsFishSuggestedFlag:
    """IHC-007 is the only IHC rule that sets FISH_SUGGESTED (per SC-060)."""

    def test_set_flags_contains_fish_suggested(self, ihc_specs: list[RuleSpec]) -> None:
        spec = next(s for s in ihc_specs if s.rule_id == "IHC-007")
        assert spec.action.set_flags == ("FISH_SUGGESTED",)

    def test_clear_flags_is_empty(self, ihc_specs: list[RuleSpec]) -> None:
        spec = next(s for s in ihc_specs if s.rule_id == "IHC-007")
        assert spec.action.clear_flags == ()


@pytest.mark.parametrize(
    "rule_id",
    # IHC-001/007 set flags; IHC-008/009 clear FISH_SUGGESTED on fish decision.
    sorted(_EXPECTED_RULE_IDS - {"IHC-001", "IHC-007", "IHC-008", "IHC-009"}),
)
def test_other_ihc_rules_set_no_flags(ihc_specs: list[RuleSpec], rule_id: str) -> None:
    spec = next(s for s in ihc_specs if s.rule_id == rule_id)
    assert spec.action.set_flags == ()
    assert spec.action.clear_flags == ()


@pytest.mark.parametrize("rule_id", ["IHC-008", "IHC-009"])
def test_fish_decision_rules_clear_fish_suggested(ihc_specs: list[RuleSpec], rule_id: str) -> None:
    """IHC-008 and IHC-009 clear FISH_SUGGESTED when the fish decision is made."""
    spec = next(s for s in ihc_specs if s.rule_id == rule_id)
    assert spec.action.set_flags == ()
    assert spec.action.clear_flags == ("FISH_SUGGESTED",)


# ---------------------------------------------------------------------------
# H — Outcome uniqueness across the IHC family
# ---------------------------------------------------------------------------


def test_ihc_outcomes_are_unique_across_family(ihc_specs: list[RuleSpec]) -> None:
    outcomes = [s.action.outcome for s in ihc_specs]
    assert len(outcomes) == len(set(outcomes))


# ---------------------------------------------------------------------------
# I — RuleIndex dispatch order within each applies_at bucket
# ---------------------------------------------------------------------------


def test_ihc_qc_dispatch_order(ihc_specs: list[RuleSpec]) -> None:
    index = RuleIndex(ihc_specs)
    ids = [r.rule_id for r in index.rules_by_applies_at["IHC_QC"]]
    assert ids == ["IHC-002", "IHC-003", "IHC-004", "IHC-005"]


def test_ihc_scoring_dispatch_order(ihc_specs: list[RuleSpec]) -> None:
    index = RuleIndex(ihc_specs)
    ids = [r.rule_id for r in index.rules_by_applies_at["IHC_SCORING"]]
    assert ids == ["IHC-006", "IHC-007"]


def test_suggest_fish_reflex_dispatch_order(ihc_specs: list[RuleSpec]) -> None:
    index = RuleIndex(ihc_specs)
    ids = [r.rule_id for r in index.rules_by_applies_at["SUGGEST_FISH_REFLEX"]]
    assert ids == ["IHC-008", "IHC-009"]


def test_fish_send_out_dispatch_order(ihc_specs: list[RuleSpec]) -> None:
    index = RuleIndex(ihc_specs)
    ids = [r.rule_id for r in index.rules_by_applies_at["FISH_SEND_OUT"]]
    assert ids == ["IHC-010", "IHC-011"]


def test_ihc_staining_dispatch_order(ihc_specs: list[RuleSpec]) -> None:
    index = RuleIndex(ihc_specs)
    ids = [r.rule_id for r in index.rules_by_applies_at["IHC_STAINING"]]
    assert ids == ["IHC-001"]


# ---------------------------------------------------------------------------
# J — Field-alignment regression guard
#     Mirrors test_he_specs.py § F: a typo in the `field:` name silently
#     fails-closed via SpecimenContext.field()'s getattr fallback.  Pin the
#     event lookups exercised by the most-touched event_data keys.
# ---------------------------------------------------------------------------


def test_ihc002_event_lookup_resolves(ihc_specs: list[RuleSpec]) -> None:
    rule = next(s for s in ihc_specs if s.rule_id == "IHC-002")
    ctx = _make_ctx(
        current_state="IHC_QC",
        event_type="ihc_qc",
        event_data={"all_slides_complete": True, "slides": []},
    )
    assert ctx.field("event.all_slides_complete") is True
    assert rule.when.evaluate(ctx) is True


def test_ihc007_event_lookup_resolves(ihc_specs: list[RuleSpec]) -> None:
    rule = next(s for s in ihc_specs if s.rule_id == "IHC-007")
    ctx = _make_ctx(
        current_state="IHC_SCORING",
        event_type="ihc_scoring",
        event_data={"all_scores_complete": True, "any_equivocal": True, "scores": []},
    )
    assert ctx.field("event.any_equivocal") is True
    assert rule.when.evaluate(ctx) is True


def test_ihc001_fixation_lookup_resolves(ihc_specs: list[RuleSpec]) -> None:
    """Pin both the order-level fixative/fixation_time_hours lookups and the
    event.her2_added_at_review lookup so a misspelled field name (e.g.
    `fixation_hours` or `event.her2_added`) fails the test instead of
    silently fail-closing."""
    rule = next(s for s in ihc_specs if s.rule_id == "IHC-001")
    ctx = _ihc001_ctx(fixation_time_hours=5.0)
    assert ctx.field("fixative") == "formalin"
    assert ctx.field("fixation_time_hours") == 5.0
    assert ctx.field("event.her2_added_at_review") is True
    assert rule.when.evaluate(ctx) is True


# ---------------------------------------------------------------------------
# K — IHC-001 fixation predicate inner shape
#     Pin the BooleanOr children to the three Not-wrapped checks so a future
#     refactor (e.g. dropping the upper bound) fails loudly.
# ---------------------------------------------------------------------------


def test_ihc001_fixation_or_contains_three_not_wrapped_checks(
    ihc_specs: list[RuleSpec],
) -> None:
    spec = next(s for s in ihc_specs if s.rule_id == "IHC-001")
    assert isinstance(spec.when, BooleanAnd)
    fixation_or = spec.when.children[1]
    assert isinstance(fixation_or, BooleanOr)
    fields_seen: set[str] = set()
    for child in fixation_or.children:
        assert isinstance(child, Not)
        inner = child.child
        if isinstance(inner, Equals):
            assert inner.field == "fixative"
            assert inner.value == "formalin"
            fields_seen.add("fixative")
        elif isinstance(inner, ThresholdGTE):
            assert inner.field == "fixation_time_hours"
            assert inner.value == 6.0
            fields_seen.add("fixation_time_gte")
        else:
            assert isinstance(inner, ThresholdLTE)
            assert inner.field == "fixation_time_hours"
            assert inner.value == 72.0
            fields_seen.add("fixation_time_lte")
    assert fields_seen == {"fixative", "fixation_time_gte", "fixation_time_lte"}


# ---------------------------------------------------------------------------
# L — Predicate-shape pins for IHC-002..IHC-011
#     Mirrors test_he_specs.py § B. A field-name typo in any of these YAMLs
#     would otherwise be caught only behaviorally; pin shape, field, and
#     value so the corpus fails loudly at load time.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rule_id, expected_field, expected_value",
    [
        ("IHC-002", "event.all_slides_complete", True),
        ("IHC-003", "event.all_slides_complete", False),
        ("IHC-008", "event.approved", True),
        ("IHC-009", "event.approved", False),
        ("IHC-010", "event.status", "success"),
        ("IHC-011", "event.status", "qns"),
    ],
)
def test_simple_equals_predicate_shape(
    ihc_specs: list[RuleSpec],
    rule_id: str,
    expected_field: str,
    expected_value: object,
) -> None:
    spec = next(s for s in ihc_specs if s.rule_id == rule_id)
    assert isinstance(spec.when, Equals)
    assert spec.when.field == expected_field
    assert spec.when.value == expected_value


@pytest.mark.parametrize(
    "rule_id, expected_pairs",
    [
        (
            "IHC-004",
            (("event.staining_failure", True), ("event.tissue_available", True)),
        ),
        (
            "IHC-005",
            (("event.staining_failure", True), ("event.tissue_available", False)),
        ),
        (
            "IHC-006",
            (("event.all_scores_complete", True), ("event.any_equivocal", False)),
        ),
        (
            "IHC-007",
            (("event.all_scores_complete", True), ("event.any_equivocal", True)),
        ),
    ],
)
def test_two_clause_boolean_and_predicate_shape(
    ihc_specs: list[RuleSpec],
    rule_id: str,
    expected_pairs: tuple[tuple[str, object], ...],
) -> None:
    spec = next(s for s in ihc_specs if s.rule_id == rule_id)
    assert isinstance(spec.when, BooleanAnd)
    assert len(spec.when.children) == len(expected_pairs)
    for child, (expected_field, expected_value) in zip(
        spec.when.children, expected_pairs, strict=True
    ):
        assert isinstance(child, Equals)
        assert child.field == expected_field
        assert child.value == expected_value


# ---------------------------------------------------------------------------
# M — Field-alignment guards for the event-data keys not covered by § J
#     A typo in any of these field names would silently fail-close because
#     SpecimenContext.field() returns None via getattr fallback.
# ---------------------------------------------------------------------------


def test_ihc004_event_lookups_resolve(ihc_specs: list[RuleSpec]) -> None:
    rule = next(s for s in ihc_specs if s.rule_id == "IHC-004")
    ctx = _make_ctx(
        current_state="IHC_QC",
        event_type="ihc_qc",
        event_data={"staining_failure": True, "tissue_available": True},
    )
    assert ctx.field("event.staining_failure") is True
    assert ctx.field("event.tissue_available") is True
    assert rule.when.evaluate(ctx) is True


def test_ihc006_event_lookups_resolve(ihc_specs: list[RuleSpec]) -> None:
    rule = next(s for s in ihc_specs if s.rule_id == "IHC-006")
    ctx = _make_ctx(
        current_state="IHC_SCORING",
        event_type="ihc_scoring",
        event_data={"all_scores_complete": True, "any_equivocal": False, "scores": []},
    )
    assert ctx.field("event.all_scores_complete") is True
    assert ctx.field("event.any_equivocal") is False
    assert rule.when.evaluate(ctx) is True


def test_ihc008_event_lookup_resolves(ihc_specs: list[RuleSpec]) -> None:
    rule = next(s for s in ihc_specs if s.rule_id == "IHC-008")
    ctx = _make_ctx(
        current_state="SUGGEST_FISH_REFLEX",
        event_type="fish_decision",
        event_data={"approved": True},
    )
    assert ctx.field("event.approved") is True
    assert rule.when.evaluate(ctx) is True


def test_ihc010_event_lookup_resolves(ihc_specs: list[RuleSpec]) -> None:
    rule = next(s for s in ihc_specs if s.rule_id == "IHC-010")
    ctx = _make_ctx(
        current_state="FISH_SEND_OUT",
        event_type="fish_result",
        event_data={"result": "negative", "status": "success"},
    )
    assert ctx.field("event.status") == "success"
    assert rule.when.evaluate(ctx) is True


# ---------------------------------------------------------------------------
# N — Inclusive-boundary contract for IHC-001's fixation thresholds
#     ThresholdGTE/ThresholdLTE are inclusive (>=, <=). A future swap to
#     strict comparators would silently change which boundary inputs reject
#     HER2; pin the inclusive behavior here.
# ---------------------------------------------------------------------------


def test_ihc001_does_not_fire_at_lower_boundary(ihc_specs: list[RuleSpec]) -> None:
    rule = next(s for s in ihc_specs if s.rule_id == "IHC-001")
    assert rule.when.evaluate(_ihc001_ctx(fixation_time_hours=6.0)) is False


def test_ihc001_does_not_fire_at_upper_boundary(ihc_specs: list[RuleSpec]) -> None:
    rule = next(s for s in ihc_specs if s.rule_id == "IHC-001")
    assert rule.when.evaluate(_ihc001_ctx(fixation_time_hours=72.0)) is False


# ---------------------------------------------------------------------------
# O — Shared-guard predicates fire only when the guard clause is True
#     Without these tests, dropping the `staining_failure` clause from
#     IHC-004/IHC-005's BooleanAnd or the `all_scores_complete` clause from
#     IHC-006/IHC-007's BooleanAnd would not fail any other test.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rule_id", ["IHC-004", "IHC-005"])
def test_ihc_qc_failure_rules_do_not_fire_when_no_staining_failure(
    ihc_specs: list[RuleSpec], rule_id: str
) -> None:
    rule = next(s for s in ihc_specs if s.rule_id == rule_id)
    ctx = _make_ctx(
        current_state="IHC_QC",
        event_type="ihc_qc",
        event_data={"staining_failure": False, "tissue_available": True},
    )
    assert rule.when.evaluate(ctx) is False


@pytest.mark.parametrize("rule_id", ["IHC-006", "IHC-007"])
def test_ihc_scoring_rules_do_not_fire_when_scores_incomplete(
    ihc_specs: list[RuleSpec], rule_id: str
) -> None:
    rule = next(s for s in ihc_specs if s.rule_id == rule_id)
    ctx = _make_ctx(
        current_state="IHC_SCORING",
        event_type="ihc_scoring",
        event_data={"all_scores_complete": False, "any_equivocal": False, "scores": []},
    )
    assert rule.when.evaluate(ctx) is False


# ---------------------------------------------------------------------------
# P — Malformed events fail-close across the bucket
#     An empty event_data carries no recognized keys, so every rule in the
#     bucket evaluates False. Today the order stalls with applied_rules=[].
#     Pinning this documents the current behavior and forces an explicit
# decision when (kernel) lands a fallback.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rule_id", ["IHC-002", "IHC-003", "IHC-004", "IHC-005"])
def test_ihc_qc_rules_fail_closed_on_empty_event_data(
    ihc_specs: list[RuleSpec], rule_id: str
) -> None:
    rule = next(s for s in ihc_specs if s.rule_id == rule_id)
    ctx = _make_ctx(current_state="IHC_QC", event_type="ihc_qc", event_data={})
    assert rule.when.evaluate(ctx) is False


@pytest.mark.parametrize(
    "rule_id, status",
    [
        ("IHC-010", "error"),
        ("IHC-010", "indeterminate"),
        ("IHC-011", "error"),
        ("IHC-011", "indeterminate"),
    ],
)
def test_fish_result_rules_fail_closed_on_unknown_status(
    ihc_specs: list[RuleSpec], rule_id: str, status: str
) -> None:
    """Unknown fish_result.status values match neither IHC-010 nor IHC-011 — the
    order stalls at FISH_SEND_OUT with applied_rules=[]. Pinning this documents
    the current behavior; whether to add a catch-all rule is a kernel-layer
    decision tracked in the open kernel issue."""
    rule = next(s for s in ihc_specs if s.rule_id == rule_id)
    ctx = _make_ctx(
        current_state="FISH_SEND_OUT",
        event_type="fish_result",
        event_data={"result": "unknown", "status": status},
    )
    assert rule.when.evaluate(ctx) is False


@pytest.mark.parametrize("rule_id", ["IHC-010", "IHC-011"])
def test_fish_result_rules_fail_closed_on_missing_status_key(
    ihc_specs: list[RuleSpec], rule_id: str
) -> None:
    rule = next(s for s in ihc_specs if s.rule_id == rule_id)
    ctx = _make_ctx(
        current_state="FISH_SEND_OUT",
        event_type="fish_result",
        event_data={"result": "negative"},
    )
    assert rule.when.evaluate(ctx) is False
