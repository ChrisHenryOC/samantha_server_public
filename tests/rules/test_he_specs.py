"""End-to-end tests that load the 9 HE YAML specs from the specs directory.

Mirrors the structure of tests/rules/test_sp_specs.py.  Tests were written
TDD-style: each rule's tests turned green one at a time as the corresponding
YAML was authored.

Two event_type families:
  - HE-001..HE-004 fire on `he_qc` events (current_state HE_QC).
  - HE-005..HE-009 fire on `pathologist_he_review` events
    (current_state PATHOLOGIST_HE_REVIEW).

Outcome and diagnosis literal values are pinned to the values that appear
in the imported scenarios (see the upstream POC repo's scenarios/), not
the descriptive labels in inventory.md/categorization.md, so that the
rules actually fire when scenarios are replayed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from samantha_server.models.context import Event, Order, SpecimenContext
from samantha_server.primitives import Contains, Equals, InEnum
from samantha_server.rules.loader import RuleIndex, load_rule_specs
from samantha_server.rules.spec import RuleSpec

_SPECS_DIR = Path(__file__).resolve().parents[2] / "samantha_server" / "rules" / "specs"

_HE_QC_RULE_IDS = {"HE-001", "HE-002", "HE-003", "HE-004"}
_HE_REVIEW_RULE_IDS = {"HE-005", "HE-006", "HE-007", "HE-008", "HE-009"}
_EXPECTED_RULE_IDS = _HE_QC_RULE_IDS | _HE_REVIEW_RULE_IDS


@pytest.fixture(scope="module")
def he_specs() -> list[RuleSpec]:
    return [s for s in load_rule_specs(_SPECS_DIR) if s.step in {"HE_QC", "PATHOLOGIST_HE_REVIEW"}]


# ---------------------------------------------------------------------------
# A — Loading and inventory invariants
# ---------------------------------------------------------------------------


class TestHeSpecsLoad:
    def test_nine_specs_loaded(self, he_specs: list[RuleSpec]) -> None:
        assert len(he_specs) == 9

    def test_all_rule_ids_present(self, he_specs: list[RuleSpec]) -> None:
        ids = {s.rule_id for s in he_specs}
        assert ids == _EXPECTED_RULE_IDS

    def test_he_qc_step_assigned(self, he_specs: list[RuleSpec]) -> None:
        ids = {s.rule_id for s in he_specs if s.step == "HE_QC"}
        assert ids == _HE_QC_RULE_IDS

    def test_pathologist_review_step_assigned(self, he_specs: list[RuleSpec]) -> None:
        ids = {s.rule_id for s in he_specs if s.step == "PATHOLOGIST_HE_REVIEW"}
        assert ids == _HE_REVIEW_RULE_IDS

    def test_he_qc_priorities_are_sequential(self, he_specs: list[RuleSpec]) -> None:
        priorities = sorted(s.priority for s in he_specs if s.step == "HE_QC")
        assert priorities == [1, 2, 3, 4]

    def test_review_priorities_are_sequential(self, he_specs: list[RuleSpec]) -> None:
        priorities = sorted(s.priority for s in he_specs if s.step == "PATHOLOGIST_HE_REVIEW")
        assert priorities == [1, 2, 3, 4, 5]


# ---------------------------------------------------------------------------
# B — Predicate shape
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rule_id, expected_predicate_type, expected_field, expected_value_or_values",
    [
        ("HE-001", "equals", "event.outcome", "pass"),
        ("HE-002", "equals", "event.outcome", "fail_restain"),
        ("HE-003", "equals", "event.outcome", "fail_recut"),
        ("HE-004", "equals", "event.outcome", "fail_qns"),
        ("HE-005", "equals", "event.diagnosis", "invasive_carcinoma"),
        ("HE-006", "equals", "event.diagnosis", "dcis"),
        (
            "HE-007",
            "in_enum",
            "event.diagnosis",
            frozenset({"suspicious_atypical", "lobular_carcinoma_in_situ"}),
        ),
        ("HE-008", "equals", "event.diagnosis", "benign"),
        ("HE-009", "equals", "event.diagnosis", "recut_requested"),
    ],
)
def test_each_predicate_loads_with_expected_shape(
    he_specs: list[RuleSpec],
    rule_id: str,
    expected_predicate_type: str,
    expected_field: str,
    expected_value_or_values: object,
) -> None:
    spec = next(s for s in he_specs if s.rule_id == rule_id)
    if expected_predicate_type == "equals":
        assert isinstance(spec.when, Equals)
        assert spec.when.field == expected_field
        assert spec.when.value == expected_value_or_values
    elif expected_predicate_type == "in_enum":
        assert isinstance(spec.when, InEnum)
        assert spec.when.field == expected_field
        assert spec.when.values == expected_value_or_values


# ---------------------------------------------------------------------------
# C1 — Pin event_type per rule (canonical vocabulary)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rule_id, expected_event_types",
    [
        ("HE-001", ("he_qc",)),
        ("HE-002", ("he_qc",)),
        ("HE-003", ("he_qc",)),
        ("HE-004", ("he_qc",)),
        ("HE-005", ("pathologist_he_review",)),
        ("HE-006", ("pathologist_he_review",)),
        ("HE-007", ("pathologist_he_review",)),
        ("HE-008", ("pathologist_he_review",)),
        ("HE-009", ("pathologist_he_review",)),
    ],
)
def test_he_event_types_match_canonical_vocabulary(
    he_specs: list[RuleSpec],
    rule_id: str,
    expected_event_types: tuple[str, ...],
) -> None:
    spec = next(s for s in he_specs if s.rule_id == rule_id)
    assert spec.event_type == expected_event_types


# ---------------------------------------------------------------------------
# C2 — Pin outcome per rule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rule_id, expected_outcome",
    [
        ("HE-001", "advanced_he_qc_passed"),
        ("HE-002", "restained_he_qc_failed"),
        ("HE-003", "recut_he_qc_failed"),
        ("HE-004", "aborted_he_qc_qns"),
        ("HE-005", "proceeded_ihc_invasive_carcinoma"),
        ("HE-006", "proceeded_ihc_dcis"),
        ("HE-007", "proceeded_ihc_pathologist_panel"),
        ("HE-008", "cancelled_ihc_benign"),
        ("HE-009", "requested_recuts"),
    ],
)
def test_he_outcomes_match_canonical_vocabulary(
    he_specs: list[RuleSpec],
    rule_id: str,
    expected_outcome: str,
) -> None:
    spec = next(s for s in he_specs if s.rule_id == rule_id)
    assert spec.action.outcome == expected_outcome


# ---------------------------------------------------------------------------
# C3 — Pin transition per rule (from samantha-public workflow_states.yaml)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rule_id, expected_transition",
    [
        ("HE-001", "PATHOLOGIST_HE_REVIEW"),
        ("HE-002", "HE_STAINING"),
        ("HE-003", "SAMPLE_PREP_SECTIONING"),
        ("HE-004", "ORDER_TERMINATED_QNS"),
        ("HE-005", "IHC_STAINING"),
        ("HE-006", "IHC_STAINING"),
        ("HE-007", "IHC_STAINING"),
        ("HE-008", "RESULTING"),
        ("HE-009", "SAMPLE_PREP_SECTIONING"),
    ],
)
def test_he_transitions_match_canonical_vocabulary(
    he_specs: list[RuleSpec],
    rule_id: str,
    expected_transition: str,
) -> None:
    spec = next(s for s in he_specs if s.rule_id == rule_id)
    assert spec.action.transition == expected_transition


# ---------------------------------------------------------------------------
# C4 — Pin schema invariants: severity None, applies_at None, priority >= 1
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rule_id", sorted(_EXPECTED_RULE_IDS))
def test_he_schema_invariants(he_specs: list[RuleSpec], rule_id: str) -> None:
    spec = next(s for s in he_specs if s.rule_id == rule_id)
    assert spec.severity is None
    assert spec.applies_at is None
    assert isinstance(spec.priority, int) and spec.priority >= 1


# ---------------------------------------------------------------------------
# D — Behavioral round-trip: evaluate() against a SpecimenContext
# ---------------------------------------------------------------------------


def _make_order(*, ordered_tests: tuple[str, ...] = ("HE",)) -> Order:
    return Order(
        order_id="ORD-TEST",
        patient_name="Jane Doe",
        patient_sex="F",
        age=None,
        specimen_type="biopsy",
        anatomic_site="breast",
        fixative="formalin",
        fixation_time_hours=24.0,
        ordered_tests=ordered_tests,
        priority="routine",
        billing_info_present=True,
    )


def _qc_context(outcome: str) -> SpecimenContext:
    return SpecimenContext(
        order=_make_order(),
        current_state="HE_QC",
        flags=frozenset(),
        event=Event(event_type="he_qc", event_data={"outcome": outcome}, step_index=0),
    )


def _review_context(diagnosis: str) -> SpecimenContext:
    return SpecimenContext(
        order=_make_order(),
        current_state="PATHOLOGIST_HE_REVIEW",
        flags=frozenset(),
        event=Event(
            event_type="pathologist_he_review",
            event_data={"diagnosis": diagnosis},
            step_index=0,
        ),
    )


class TestHeQcEvaluateBehavior:
    """HE-001..HE-004 fire on he_qc events with current_state HE_QC."""

    def test_he001_fires_on_pass(self, he_specs: list[RuleSpec]) -> None:
        rule = next(s for s in he_specs if s.rule_id == "HE-001")
        assert rule.when.evaluate(_qc_context("pass")) is True

    def test_he001_does_not_fire_on_fail_restain(self, he_specs: list[RuleSpec]) -> None:
        rule = next(s for s in he_specs if s.rule_id == "HE-001")
        assert rule.when.evaluate(_qc_context("fail_restain")) is False

    def test_he002_fires_on_fail_restain(self, he_specs: list[RuleSpec]) -> None:
        rule = next(s for s in he_specs if s.rule_id == "HE-002")
        assert rule.when.evaluate(_qc_context("fail_restain")) is True

    def test_he002_does_not_fire_on_pass(self, he_specs: list[RuleSpec]) -> None:
        rule = next(s for s in he_specs if s.rule_id == "HE-002")
        assert rule.when.evaluate(_qc_context("pass")) is False

    def test_he003_fires_on_fail_recut(self, he_specs: list[RuleSpec]) -> None:
        rule = next(s for s in he_specs if s.rule_id == "HE-003")
        assert rule.when.evaluate(_qc_context("fail_recut")) is True

    def test_he003_does_not_fire_on_pass(self, he_specs: list[RuleSpec]) -> None:
        rule = next(s for s in he_specs if s.rule_id == "HE-003")
        assert rule.when.evaluate(_qc_context("pass")) is False

    def test_he004_fires_on_fail_qns(self, he_specs: list[RuleSpec]) -> None:
        rule = next(s for s in he_specs if s.rule_id == "HE-004")
        assert rule.when.evaluate(_qc_context("fail_qns")) is True

    def test_he004_does_not_fire_on_fail_restain(self, he_specs: list[RuleSpec]) -> None:
        rule = next(s for s in he_specs if s.rule_id == "HE-004")
        assert rule.when.evaluate(_qc_context("fail_restain")) is False


class TestHeReviewEvaluateBehavior:
    """HE-005..HE-009 fire on pathologist_he_review events."""

    def test_he005_fires_on_invasive_carcinoma(self, he_specs: list[RuleSpec]) -> None:
        rule = next(s for s in he_specs if s.rule_id == "HE-005")
        assert rule.when.evaluate(_review_context("invasive_carcinoma")) is True

    def test_he005_does_not_fire_on_dcis(self, he_specs: list[RuleSpec]) -> None:
        rule = next(s for s in he_specs if s.rule_id == "HE-005")
        assert rule.when.evaluate(_review_context("dcis")) is False

    def test_he006_fires_on_dcis(self, he_specs: list[RuleSpec]) -> None:
        rule = next(s for s in he_specs if s.rule_id == "HE-006")
        assert rule.when.evaluate(_review_context("dcis")) is True

    def test_he006_does_not_fire_on_invasive_carcinoma(self, he_specs: list[RuleSpec]) -> None:
        rule = next(s for s in he_specs if s.rule_id == "HE-006")
        assert rule.when.evaluate(_review_context("invasive_carcinoma")) is False

    def test_he007_fires_on_suspicious_atypical(self, he_specs: list[RuleSpec]) -> None:
        rule = next(s for s in he_specs if s.rule_id == "HE-007")
        assert rule.when.evaluate(_review_context("suspicious_atypical")) is True

    def test_he007_fires_on_lobular_carcinoma_in_situ(self, he_specs: list[RuleSpec]) -> None:
        """HE-007 covers two diagnosis labels — both observed in the scenario corpus."""
        rule = next(s for s in he_specs if s.rule_id == "HE-007")
        assert rule.when.evaluate(_review_context("lobular_carcinoma_in_situ")) is True

    def test_he007_does_not_fire_on_benign(self, he_specs: list[RuleSpec]) -> None:
        rule = next(s for s in he_specs if s.rule_id == "HE-007")
        assert rule.when.evaluate(_review_context("benign")) is False

    def test_he008_fires_on_benign(self, he_specs: list[RuleSpec]) -> None:
        rule = next(s for s in he_specs if s.rule_id == "HE-008")
        assert rule.when.evaluate(_review_context("benign")) is True

    def test_he008_does_not_fire_on_invasive_carcinoma(self, he_specs: list[RuleSpec]) -> None:
        rule = next(s for s in he_specs if s.rule_id == "HE-008")
        assert rule.when.evaluate(_review_context("invasive_carcinoma")) is False

    def test_he009_fires_on_recut_requested(self, he_specs: list[RuleSpec]) -> None:
        rule = next(s for s in he_specs if s.rule_id == "HE-009")
        assert rule.when.evaluate(_review_context("recut_requested")) is True

    def test_he009_does_not_fire_on_benign(self, he_specs: list[RuleSpec]) -> None:
        rule = next(s for s in he_specs if s.rule_id == "HE-009")
        assert rule.when.evaluate(_review_context("benign")) is False


# ---------------------------------------------------------------------------
# E — Action-handler boundary fixtures
#     HE-006 panel (DCIS with conditional HER2) and HE-009 set_flags
#     are the two non-trivial action shapes introduced by Step 8.
# ---------------------------------------------------------------------------


class TestHe005Panel:
    """HE-005 ships the full standard panel (no conditional markers)."""

    def test_panel_base_is_full_standard(self, he_specs: list[RuleSpec]) -> None:
        spec = next(s for s in he_specs if s.rule_id == "HE-005")
        assert spec.action.panel is not None
        assert spec.action.panel.base == ("ER", "PR", "HER2", "Ki-67")

    def test_panel_has_no_conditional_markers(self, he_specs: list[RuleSpec]) -> None:
        spec = next(s for s in he_specs if s.rule_id == "HE-005")
        assert spec.action.panel is not None
        assert spec.action.panel.conditional == ()


class TestHe006Panel:
    """HE-006 is the canonical action-handler-boundary example.

    Predicate matches every DCIS diagnosis (just `Equals(diagnosis,'dcis')`);
    the panel block expresses the HER2-conditional inclusion as a structured
    PanelSpec so the action handler (deferred per phase-1 plan § 6) can
    construct the runtime panel without rule-specific Python.
    """

    def test_panel_base_has_er_and_pr_only(self, he_specs: list[RuleSpec]) -> None:
        spec = next(s for s in he_specs if s.rule_id == "HE-006")
        assert spec.action.panel is not None
        assert spec.action.panel.base == ("ER", "PR")

    def test_panel_conditionally_adds_her2_when_ordered(self, he_specs: list[RuleSpec]) -> None:
        """The single conditional marker is HER2 keyed on Contains(ordered_tests, HER2)."""
        spec = next(s for s in he_specs if s.rule_id == "HE-006")
        assert spec.action.panel is not None
        assert len(spec.action.panel.conditional) == 1
        cond = spec.action.panel.conditional[0]
        assert cond.marker == "HER2"
        assert isinstance(cond.when, Contains)
        assert cond.when.field == "ordered_tests"
        assert cond.when.value == "her2"

    def test_predicate_stays_simple_equals(self, he_specs: list[RuleSpec]) -> None:
        """Predicate is `Equals(diagnosis,'dcis')` — HER2 logic lives in the panel block.

        Pinned per decision-gate.md § "Action handler boundary" and phase-1
        plan § 6 ("HE-006 HER2-conditional panel construction"): the
        predicate must NOT be split into HE-006a/HE-006b, and the panel
        construction is deferred to the action handler.
        """
        spec = next(s for s in he_specs if s.rule_id == "HE-006")
        assert isinstance(spec.when, Equals)
        assert spec.when.field == "event.diagnosis"
        assert spec.when.value == "dcis"


class TestHe007PanelOmitted:
    """HE-007 routes to IHC but the pathologist specifies markers.

    breast_ihc_panels.md HE-007: "The system routes the order but does not
    select which markers to run." Expressed as panel=null so downstream
    code knows there is no rule-driven panel to construct.
    """

    def test_panel_is_null(self, he_specs: list[RuleSpec]) -> None:
        spec = next(s for s in he_specs if s.rule_id == "HE-007")
        assert spec.action.panel is None


class TestHe008ClearFlagsExample:
    """HE-008 introduces the `clear_flags:` field shape.

    Per the issue body, HE-008 is the canonical clear_flags example.  No
    scenario in the imported corpus exercises a non-empty value (every
    HE-008 scenario expects flags=[]), so the example is the field's
    presence and structure, not a non-trivial flag list.  The stand-alone
    panel field is None (CANCEL_IHC_BENIGN does not construct a panel).
    """

    def test_clear_flags_field_is_present_and_empty(self, he_specs: list[RuleSpec]) -> None:
        spec = next(s for s in he_specs if s.rule_id == "HE-008")
        assert spec.action.clear_flags == ()

    def test_set_flags_field_is_empty(self, he_specs: list[RuleSpec]) -> None:
        spec = next(s for s in he_specs if s.rule_id == "HE-008")
        assert spec.action.set_flags == ()

    def test_panel_is_null(self, he_specs: list[RuleSpec]) -> None:
        spec = next(s for s in he_specs if s.rule_id == "HE-008")
        assert spec.action.panel is None


class TestHe009SetsRecutFlag:
    """HE-009 is the only HE rule that sets a flag (RECUT_REQUESTED).

    Pinned by SC-046 which expects flags=['RECUT_REQUESTED'] after HE-009 fires.
    """

    def test_set_flags_contains_recut_requested(self, he_specs: list[RuleSpec]) -> None:
        spec = next(s for s in he_specs if s.rule_id == "HE-009")
        assert spec.action.set_flags == ("RECUT_REQUESTED",)

    def test_clear_flags_is_empty(self, he_specs: list[RuleSpec]) -> None:
        spec = next(s for s in he_specs if s.rule_id == "HE-009")
        assert spec.action.clear_flags == ()


# ---------------------------------------------------------------------------
# F — Field-alignment regression guard
#     Mirrors test_acc002_field_alignment_with_order_model: a typo in the
#     `field:` name silently fails-closed via SpecimenContext.field()'s
#     getattr fallback.  Pin event.outcome and event.diagnosis lookups.
# ---------------------------------------------------------------------------


def test_he001_event_outcome_lookup_resolves(he_specs: list[RuleSpec]) -> None:
    """If HE-001's `field:` is misspelled (e.g. event.outcomes), the predicate
    silently returns None and never fires.  This test asserts the lookup
    resolves a real value."""
    rule = next(s for s in he_specs if s.rule_id == "HE-001")
    ctx = _qc_context("pass")
    assert ctx.field("event.outcome") == "pass"
    assert rule.when.evaluate(ctx) is True


def test_he005_event_diagnosis_lookup_resolves(he_specs: list[RuleSpec]) -> None:
    rule = next(s for s in he_specs if s.rule_id == "HE-005")
    ctx = _review_context("invasive_carcinoma")
    assert ctx.field("event.diagnosis") == "invasive_carcinoma"
    assert rule.when.evaluate(ctx) is True


def test_he002_event_outcome_lookup_resolves(he_specs: list[RuleSpec]) -> None:
    rule = next(s for s in he_specs if s.rule_id == "HE-002")
    ctx = _qc_context("fail_restain")
    assert ctx.field("event.outcome") == "fail_restain"
    assert rule.when.evaluate(ctx) is True


def test_he008_event_diagnosis_lookup_resolves(he_specs: list[RuleSpec]) -> None:
    rule = next(s for s in he_specs if s.rule_id == "HE-008")
    ctx = _review_context("benign")
    assert ctx.field("event.diagnosis") == "benign"
    assert rule.when.evaluate(ctx) is True


# ---------------------------------------------------------------------------
# G — HE-006 conditional-panel evaluate() round-trip
#     The HER2 conditional inside HE-006's PanelSpec is the most
#     load-bearing sub-predicate in this PR; verifying it only by shape
#     would silently pass a `field:` typo (e.g. event.ordered_tests).
# ---------------------------------------------------------------------------


def _dcis_review_context(ordered_tests: tuple[str, ...]) -> SpecimenContext:
    return SpecimenContext(
        order=_make_order(ordered_tests=ordered_tests),
        current_state="PATHOLOGIST_HE_REVIEW",
        flags=frozenset(),
        event=Event(
            event_type="pathologist_he_review",
            event_data={"diagnosis": "dcis"},
            step_index=0,
        ),
    )


class TestHe006ConditionalEvaluatesAgainstContext:
    """Behaviorally verify HER2 inclusion logic — not just shape."""

    def test_her2_condition_true_when_her2_ordered(self, he_specs: list[RuleSpec]) -> None:
        spec = next(s for s in he_specs if s.rule_id == "HE-006")
        assert spec.action.panel is not None
        cond = spec.action.panel.conditional[0]
        ctx = _dcis_review_context(ordered_tests=("HE", "HER2"))
        assert cond.when.evaluate(ctx) is True

    def test_her2_condition_false_when_her2_not_ordered(self, he_specs: list[RuleSpec]) -> None:
        spec = next(s for s in he_specs if s.rule_id == "HE-006")
        assert spec.action.panel is not None
        cond = spec.action.panel.conditional[0]
        ctx = _dcis_review_context(ordered_tests=("HE",))
        assert cond.when.evaluate(ctx) is False


# ---------------------------------------------------------------------------
# H — Outcome uniqueness across the HE family
#     Guards against a future rename collapsing two distinct outcomes
#     (e.g. proceeded_ihc_dcis vs proceeded_ihc_invasive_carcinoma) into
#     the same string, which the per-rule outcome tests would not catch.
# ---------------------------------------------------------------------------


def test_he_outcomes_are_unique_across_family(he_specs: list[RuleSpec]) -> None:
    outcomes = [s.action.outcome for s in he_specs]
    assert len(outcomes) == len(set(outcomes))


# ---------------------------------------------------------------------------
# I — RuleIndex dispatch order within each step
#     test_rule_index.py covers the generic priority sort; this test pins
#     the exact dispatch order so a same-step priority swap fails loudly.
# ---------------------------------------------------------------------------


def test_he_qc_dispatch_order(he_specs: list[RuleSpec]) -> None:
    index = RuleIndex(he_specs)
    ids = [r.rule_id for r in index.rules_by_step["HE_QC"]]
    assert ids == ["HE-001", "HE-002", "HE-003", "HE-004"]


def test_pathologist_he_review_dispatch_order(he_specs: list[RuleSpec]) -> None:
    index = RuleIndex(he_specs)
    ids = [r.rule_id for r in index.rules_by_step["PATHOLOGIST_HE_REVIEW"]]
    assert ids == ["HE-005", "HE-006", "HE-007", "HE-008", "HE-009"]
