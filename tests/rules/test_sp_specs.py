"""End-to-end tests that load the 7 SP YAML specs from the specs directory.

Mirrors the structure of tests/rules/test_acc_specs.py.  Tests are driven
TDD-style: the test file was written before the YAML specs existed and each
rule's tests turned green one at a time as the corresponding YAML was authored.

State convention used in _make_context helpers:
  - SP-001..003 (processing_complete / embedding_complete / sectioning_complete):
      current_state SAMPLE_PREP_PROCESSING; behavioral tests use processing_complete.
  - SP-004..006 (sample_prep_qc): current_state SAMPLE_PREP_QC.
  - SP-007 (sectioning_complete + RECUT_REQUESTED): current_state SAMPLE_PREP_SECTIONING.
Both are valid VALID_STATES per samantha_server/models/context.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from samantha_server.models.context import Event, Order, SpecimenContext
from samantha_server.rules.loader import load_rule_specs
from samantha_server.rules.spec import RuleSpec

_SPECS_DIR = Path(__file__).resolve().parents[2] / "samantha_server" / "rules" / "specs"

_EXPECTED_RULE_IDS = {
    "SP-001",
    "SP-002",
    "SP-003",
    "SP-004",
    "SP-005",
    "SP-006",
    "SP-007",
}


@pytest.fixture(scope="module")
def sp_specs() -> list[RuleSpec]:
    return [s for s in load_rule_specs(_SPECS_DIR) if s.step == "SAMPLE_PREP"]


class TestSpSpecsLoad:
    def test_seven_specs_loaded(self, sp_specs: list[RuleSpec]) -> None:
        assert len(sp_specs) == 7

    def test_all_rule_ids_present(self, sp_specs: list[RuleSpec]) -> None:
        ids = {s.rule_id for s in sp_specs}
        assert ids == _EXPECTED_RULE_IDS

    def test_priorities_are_sequential(self, sp_specs: list[RuleSpec]) -> None:
        priorities = {s.priority for s in sp_specs}
        assert priorities == {0, 1, 2, 3, 4, 5, 6}


@pytest.mark.parametrize(
    "rule_id, expected_predicate_type, expected_field, expected_value_or_values",
    [
        ("SP-001", "equals", "event.outcome", "success"),
        ("SP-002", "equals", "event.outcome", "failure"),
        ("SP-003", "equals", "event.outcome", "fail_qns"),
        ("SP-004", "equals", "event.outcome", "pass"),
        ("SP-005", "in_enum", "event.outcome", frozenset({"fail_tissue_available"})),
        ("SP-006", "equals", "event.outcome", "fail_qns"),
    ],
)
def test_each_predicate_loads_with_expected_shape(
    sp_specs: list[RuleSpec],
    rule_id: str,
    expected_predicate_type: str,
    expected_field: str,
    expected_value_or_values: object,
) -> None:
    from samantha_server.primitives import Equals, InEnum

    spec = next(s for s in sp_specs if s.rule_id == rule_id)
    if expected_predicate_type == "equals":
        assert isinstance(spec.when, Equals)
        assert spec.when.field == expected_field
        assert spec.when.value == expected_value_or_values
    elif expected_predicate_type == "in_enum":
        assert isinstance(spec.when, InEnum)
        assert spec.when.field == expected_field
        assert spec.when.values == expected_value_or_values


# ---------------------------------------------------------------------------
# Helpers shared by the behavioral round-trip class
# ---------------------------------------------------------------------------


def _make_order() -> Order:
    return Order(
        order_id="ORD-TEST",
        patient_name="Jane Doe",
        patient_sex="F",
        age=None,
        specimen_type="biopsy",
        anatomic_site="breast",
        fixative="formalin",
        fixation_time_hours=24.0,
        ordered_tests=("HE",),
        priority="routine",
        billing_info_present=True,
    )


def _make_context(
    current_state: str,
    event_type: str,
    outcome: str,
    flags: frozenset[str] = frozenset(),
) -> SpecimenContext:
    return SpecimenContext(
        order=_make_order(),
        current_state=current_state,
        flags=flags,
        event=Event(
            event_type=event_type,
            event_data={"outcome": outcome},
            step_index=0,
        ),
    )


class TestSpEvaluateBehavior:
    """Behavioral round-trip: evaluate() against a SpecimenContext.

    SP-001..003 fire on processing_complete / embedding_complete / sectioning_complete;
    behavioral tests use processing_complete with current_state SAMPLE_PREP_PROCESSING.
    SP-004..006 fire on qc_complete with current_state SAMPLE_PREP_QC.
    """

    # --- SP-001 ---

    def test_sp001_fires_on_success(self, sp_specs: list[RuleSpec]) -> None:
        sp001 = next(s for s in sp_specs if s.rule_id == "SP-001")
        ctx = _make_context("SAMPLE_PREP_PROCESSING", "processing_complete", "success")
        assert sp001.when.evaluate(ctx) is True

    def test_sp001_does_not_fire_on_failure(self, sp_specs: list[RuleSpec]) -> None:
        sp001 = next(s for s in sp_specs if s.rule_id == "SP-001")
        ctx = _make_context("SAMPLE_PREP_PROCESSING", "processing_complete", "failure")
        assert sp001.when.evaluate(ctx) is False

    # --- SP-002 ---

    def test_sp002_fires_on_failure(self, sp_specs: list[RuleSpec]) -> None:
        sp002 = next(s for s in sp_specs if s.rule_id == "SP-002")
        ctx = _make_context("SAMPLE_PREP_PROCESSING", "processing_complete", "failure")
        assert sp002.when.evaluate(ctx) is True

    def test_sp002_does_not_fire_on_success(self, sp_specs: list[RuleSpec]) -> None:
        sp002 = next(s for s in sp_specs if s.rule_id == "SP-002")
        ctx = _make_context("SAMPLE_PREP_PROCESSING", "processing_complete", "success")
        assert sp002.when.evaluate(ctx) is False

    # --- SP-003 ---

    def test_sp003_fires_on_fail_qns(self, sp_specs: list[RuleSpec]) -> None:
        sp003 = next(s for s in sp_specs if s.rule_id == "SP-003")
        ctx = _make_context("SAMPLE_PREP_PROCESSING", "processing_complete", "fail_qns")
        assert sp003.when.evaluate(ctx) is True

    def test_sp003_does_not_fire_on_success(self, sp_specs: list[RuleSpec]) -> None:
        sp003 = next(s for s in sp_specs if s.rule_id == "SP-003")
        ctx = _make_context("SAMPLE_PREP_PROCESSING", "processing_complete", "success")
        assert sp003.when.evaluate(ctx) is False

    # --- SP-004 ---

    def test_sp004_fires_on_pass(self, sp_specs: list[RuleSpec]) -> None:
        """SP-004 fires when sample_prep_qc outcome is 'pass'.

        Distinguished from SP-001 by event_type (sample_prep_qc vs
        processing_complete/embedding_complete/sectioning_complete) and at the
        kernel level by current_state.  Both predicates must load and evaluate
        cleanly.
        """
        sp004 = next(s for s in sp_specs if s.rule_id == "SP-004")
        ctx = _make_context("SAMPLE_PREP_QC", "sample_prep_qc", "pass")
        assert sp004.when.evaluate(ctx) is True

    def test_sp004_does_not_fire_on_fail_tissue_available(self, sp_specs: list[RuleSpec]) -> None:
        sp004 = next(s for s in sp_specs if s.rule_id == "SP-004")
        ctx = _make_context("SAMPLE_PREP_QC", "sample_prep_qc", "fail_tissue_available")
        assert sp004.when.evaluate(ctx) is False

    # --- SP-005 ---

    def test_sp005_fires_on_fail_tissue_available(self, sp_specs: list[RuleSpec]) -> None:
        sp005 = next(s for s in sp_specs if s.rule_id == "SP-005")
        ctx = _make_context("SAMPLE_PREP_QC", "sample_prep_qc", "fail_tissue_available")
        assert sp005.when.evaluate(ctx) is True

    def test_sp005_does_not_fire_on_fail_qns(self, sp_specs: list[RuleSpec]) -> None:
        sp005 = next(s for s in sp_specs if s.rule_id == "SP-005")
        ctx = _make_context("SAMPLE_PREP_QC", "sample_prep_qc", "fail_qns")
        assert sp005.when.evaluate(ctx) is False

    # --- SP-006 ---

    def test_sp006_fires_on_fail_qns(self, sp_specs: list[RuleSpec]) -> None:
        """SP-006 shares fail_qns predicate with SP-003.

        Distinct outcomes (aborted_sample_prep_qc_qns vs
        aborted_sample_prep_processing_qns) ensure receipt clarity.
        """
        sp006 = next(s for s in sp_specs if s.rule_id == "SP-006")
        ctx = _make_context("SAMPLE_PREP_QC", "qc_complete", "fail_qns")
        assert sp006.when.evaluate(ctx) is True

    def test_sp006_does_not_fire_on_success(self, sp_specs: list[RuleSpec]) -> None:
        sp006 = next(s for s in sp_specs if s.rule_id == "SP-006")
        ctx = _make_context("SAMPLE_PREP_QC", "qc_complete", "success")
        assert sp006.when.evaluate(ctx) is False

    # --- SP-007 ---

    def test_sp007_fires_on_sectioning_complete_with_recut_flag(
        self, sp_specs: list[RuleSpec]
    ) -> None:
        """SP-007 fires when sectioning_complete succeeds and RECUT_REQUESTED is present."""
        sp007 = next(s for s in sp_specs if s.rule_id == "SP-007")
        ctx = _make_context(
            "SAMPLE_PREP_SECTIONING",
            "sectioning_complete",
            "success",
            flags=frozenset({"RECUT_REQUESTED"}),
        )
        assert sp007.when.evaluate(ctx) is True

    def test_sp007_does_not_fire_without_recut_flag(self, sp_specs: list[RuleSpec]) -> None:
        """SP-007 does not fire on sectioning_complete success without the RECUT_REQUESTED flag."""
        sp007 = next(s for s in sp_specs if s.rule_id == "SP-007")
        ctx = _make_context(
            "SAMPLE_PREP_SECTIONING",
            "sectioning_complete",
            "success",
            flags=frozenset(),
        )
        assert sp007.when.evaluate(ctx) is False

    def test_sp007_clear_flags_includes_recut_requested(self, sp_specs: list[RuleSpec]) -> None:
        """SP-007 action must clear RECUT_REQUESTED."""
        sp007 = next(s for s in sp_specs if s.rule_id == "SP-007")
        assert sp007.action.clear_flags == ("RECUT_REQUESTED",)


# ---------------------------------------------------------------------------
# SP-007 dedicated predicate shape test (BooleanAnd with two children)
# ---------------------------------------------------------------------------


def test_sp007_predicate_is_boolean_and_with_success_and_recut_flag(
    sp_specs: list[RuleSpec],
) -> None:
    """SP-007 when-predicate must be a BooleanAnd with exactly two children:
    Equals(event.outcome, success) and Contains(flags, RECUT_REQUESTED).
    """
    from samantha_server.primitives import BooleanAnd, Contains, Equals

    sp007 = next(s for s in sp_specs if s.rule_id == "SP-007")
    assert isinstance(sp007.when, BooleanAnd), f"expected BooleanAnd, got {type(sp007.when)}"
    assert len(sp007.when.children) == 2
    equals_child, contains_child = sp007.when.children
    assert isinstance(equals_child, Equals)
    assert equals_child.field == "event.outcome"
    assert equals_child.value == "success"
    assert isinstance(contains_child, Contains)
    assert contains_child.field == "flags"
    assert contains_child.value == "RECUT_REQUESTED"


# ---------------------------------------------------------------------------
# C1 — Pin event_type per rule (canonical vocabulary)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rule_id, expected_event_types",
    [
        (
            "SP-001",
            (
                "grossing_complete",
                "processing_complete",
                "embedding_complete",
                "sectioning_complete",
            ),
        ),
        (
            "SP-002",
            ("processing_complete", "embedding_complete", "sectioning_complete"),
        ),
        (
            "SP-003",
            ("processing_complete", "embedding_complete", "sectioning_complete"),
        ),
        ("SP-004", ("sample_prep_qc",)),
        ("SP-005", ("sample_prep_qc",)),
        ("SP-006", ("sample_prep_qc",)),
        ("SP-007", ("sectioning_complete",)),
    ],
)
def test_sp_event_types_match_canonical_vocabulary(
    sp_specs: list[RuleSpec],
    rule_id: str,
    expected_event_types: tuple[str, ...],
) -> None:
    spec = next(s for s in sp_specs if s.rule_id == rule_id)
    assert spec.event_type == expected_event_types


# ---------------------------------------------------------------------------
# C2 — Pin outcome per rule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rule_id, expected_outcome",
    [
        ("SP-001", "advanced_sample_prep_step"),
        ("SP-002", "retried_sample_prep_step"),
        ("SP-003", "aborted_sample_prep_processing_qns"),
        ("SP-004", "advanced_sample_prep_qc_passed"),
        ("SP-005", "retried_sample_prep_sectioning"),
        ("SP-006", "aborted_sample_prep_qc_qns"),
        ("SP-007", "completed_recut_cleared_flag"),
    ],
)
def test_sp_outcomes_match_canonical_vocabulary(
    sp_specs: list[RuleSpec],
    rule_id: str,
    expected_outcome: str,
) -> None:
    spec = next(s for s in sp_specs if s.rule_id == rule_id)
    assert spec.action.outcome == expected_outcome


# ---------------------------------------------------------------------------
# C3 — Pin transition per rule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rule_id, expected_transition",
    [
        ("SP-001", "ADVANCE_SAMPLE_PREP"),
        ("SP-002", "RETRY_SAMPLE_PREP"),
        ("SP-003", "ORDER_TERMINATED_QNS"),
        ("SP-004", "HE_STAINING"),
        ("SP-005", "SAMPLE_PREP_SECTIONING"),
        ("SP-006", "ORDER_TERMINATED_QNS"),
        ("SP-007", "ADVANCE_SAMPLE_PREP"),
    ],
)
def test_sp_transitions_match_canonical_vocabulary(
    sp_specs: list[RuleSpec],
    rule_id: str,
    expected_transition: str,
) -> None:
    spec = next(s for s in sp_specs if s.rule_id == rule_id)
    assert spec.action.transition == expected_transition


# ---------------------------------------------------------------------------
# C4 — Pin schema invariants: severity None, applies_at None, priority >= 0
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rule_id", ["SP-001", "SP-002", "SP-003", "SP-004", "SP-005", "SP-006", "SP-007"]
)
def test_sp_schema_invariants(sp_specs: list[RuleSpec], rule_id: str) -> None:
    spec = next(s for s in sp_specs if s.rule_id == rule_id)
    assert spec.severity is None
    assert spec.applies_at is None
    assert isinstance(spec.priority, int) and spec.priority >= 0


# ---------------------------------------------------------------------------
# C5 — Negative tests: fail_qns must not fire SP-002 or SP-004
# ---------------------------------------------------------------------------


def test_sp002_does_not_fire_on_fail_qns(sp_specs: list[RuleSpec]) -> None:
    """SP-002 fires on fail_retry only; fail_qns must not match."""
    sp002 = next(s for s in sp_specs if s.rule_id == "SP-002")
    ctx = _make_context("SAMPLE_PREP_PROCESSING", "processing_complete", "fail_qns")
    assert sp002.when.evaluate(ctx) is False


def test_sp004_does_not_fire_on_fail_qns(sp_specs: list[RuleSpec]) -> None:
    """SP-004 fires on success only; fail_qns must not match."""
    sp004 = next(s for s in sp_specs if s.rule_id == "SP-004")
    ctx = _make_context("SAMPLE_PREP_QC", "qc_complete", "fail_qns")
    assert sp004.when.evaluate(ctx) is False


# ---------------------------------------------------------------------------
# W1 — SP-001 clear_flags must be empty (per SOP: does not modify flags)
# Per sample_prep.md:204-206 and workflow_states.yaml:666-669,
# RECUT_REQUESTED clears only on recut completion (HE-003 territory),
# not on every advance event.
# ---------------------------------------------------------------------------


def test_sp001_clear_flags_is_empty(sp_specs: list[RuleSpec]) -> None:
    """SP-001 must not clear any flags.

    SP-001 fires on every sample-prep advance event (grossing/processing/
    embedding/sectioning). Per sample_prep.md:204-206, this step does not
    add or remove flags. RECUT_REQUESTED is cleared by SP-007 (priority 0),
    which preempts SP-001 on the recut sectioning path per
    workflow_states.yaml:666-669.
    """
    sp001 = next(s for s in sp_specs if s.rule_id == "SP-001")
    assert sp001.action.clear_flags == ()


# ---------------------------------------------------------------------------
# L10 — SP-005 old values (fail_retry / fail_recut) must be rejected
# ---------------------------------------------------------------------------


def test_sp005_does_not_fire_on_fail_retry(sp_specs: list[RuleSpec]) -> None:
    """SP-005 was collapsed to fire only on fail_tissue_available; fail_retry is rejected."""
    sp005 = next(s for s in sp_specs if s.rule_id == "SP-005")
    ctx = _make_context("SAMPLE_PREP_QC", "sample_prep_qc", "fail_retry")
    assert sp005.when.evaluate(ctx) is False


def test_sp005_does_not_fire_on_fail_recut(sp_specs: list[RuleSpec]) -> None:
    """SP-005 was collapsed to fire only on fail_tissue_available; fail_recut is rejected."""
    sp005 = next(s for s in sp_specs if s.rule_id == "SP-005")
    ctx = _make_context("SAMPLE_PREP_QC", "sample_prep_qc", "fail_recut")
    assert sp005.when.evaluate(ctx) is False
