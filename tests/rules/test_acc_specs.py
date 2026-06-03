"""End-to-end tests that load the 9 ACC YAML specs from the specs directory.

Verifies rule count, rule_id set, ACC-008 resolution, and aliasing safety.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from samantha_server.models.context import Event, Order, SpecimenContext
from samantha_server.primitives import BooleanAnd, BooleanOr, Equals, InEnum, IsNull, Not
from samantha_server.rules.loader import load_rule_specs
from samantha_server.rules.spec import RuleSpec

_SPECS_DIR = Path(__file__).resolve().parents[2] / "samantha_server" / "rules" / "specs"

_EXPECTED_RULE_IDS = {
    "ACC-001",
    "ACC-002",
    "ACC-003",
    "ACC-004",
    "ACC-005",
    "ACC-006",
    "ACC-007",
    "ACC-008",
    "ACC-009",
    "ACC-010",
    "ACC-011",
    "ACC-012",
}


@pytest.fixture(scope="module")
def acc_specs() -> list[RuleSpec]:
    return [s for s in load_rule_specs(_SPECS_DIR) if s.step == "ACCESSIONING"]


class TestAccSpecsLoad:
    def test_twelve_specs_loaded(self, acc_specs: list[RuleSpec]) -> None:
        assert len(acc_specs) == 12

    def test_all_rule_ids_present(self, acc_specs: list[RuleSpec]) -> None:
        ids = {s.rule_id for s in acc_specs}
        assert ids == _EXPECTED_RULE_IDS


class TestAcc008Resolution:
    def test_acc008_when_is_not_wrapping_boolean_or(self, acc_specs: list[RuleSpec]) -> None:
        acc008 = next(s for s in acc_specs if s.rule_id == "ACC-008")
        assert isinstance(acc008.when, Not)
        assert isinstance(acc008.when.child, BooleanOr)

    def test_acc008_excludes_every_other_acc_rule(self, acc_specs: list[RuleSpec]) -> None:
        """Set-identity invariant: ACC-008's BooleanOr child count must match the
        number of other ACC rules. Catches the failure mode where a future ACC
        rule lands without being added to ACC-008's exclusion list."""
        acc008 = next(s for s in acc_specs if s.rule_id == "ACC-008")
        or_node = acc008.when.child  # type: ignore[union-attr]
        assert isinstance(or_node, BooleanOr)
        non_acc008_count = sum(1 for s in acc_specs if s.rule_id != "ACC-008")
        assert len(or_node.children) == non_acc008_count, (
            f"ACC-008 excludes {len(or_node.children)} rules but {non_acc008_count} "
            f"non-ACC-008 ACC rules exist. Add new rules' rule_refs to ACC-008.yaml."
        )

    def test_acc008_first_child_is_acc001_predicate(self, acc_specs: list[RuleSpec]) -> None:
        """Child 0 of the BooleanOr is the inlined ACC-001 IsNull predicate."""
        acc008 = next(s for s in acc_specs if s.rule_id == "ACC-008")
        or_node = acc008.when.child  # type: ignore[union-attr]
        first_child = or_node.children[0]  # type: ignore[union-attr]
        assert isinstance(first_child, IsNull)
        assert first_child.field == "patient_name"

    def test_acc001_when_is_not_mutated_by_acc008_resolution(
        self, acc_specs: list[RuleSpec]
    ) -> None:
        """Resolving rule_refs in ACC-008 must not alias or mutate ACC-001's predicate."""
        acc001 = next(s for s in acc_specs if s.rule_id == "ACC-001")
        acc008 = next(s for s in acc_specs if s.rule_id == "ACC-008")
        or_node = acc008.when.child  # type: ignore[union-attr]
        inlined_child = or_node.children[0]  # type: ignore[union-attr]
        # They must be equal in value but not the same object (deep-copy guarantee).
        assert isinstance(inlined_child, IsNull)
        assert isinstance(acc001.when, IsNull)
        assert inlined_child.field == acc001.when.field
        assert inlined_child is not acc001.when

    def test_acc008_all_eleven_children_inlined_correctly(self, acc_specs: list[RuleSpec]) -> None:
        """Verify all 11 inlined children of ACC-008's BooleanOr match their source rules.

        ACC-008 references (in order): ACC-001, ACC-002, ACC-003, ACC-004,
        ACC-005, ACC-006, ACC-007, ACC-009, ACC-010, ACC-011, ACC-012.

        children[0] = ACC-001: IsNull(field='patient_name')
        children[1] = ACC-002: IsNull(field='patient_sex')
        children[2] = ACC-003: InEnum — anatomic_site blacklist (GH-234 rewrite)
        children[3] = ACC-004: InEnum — specimen_type blacklist
        children[4] = ACC-005: BooleanAnd — HER2 + Not(Equals fixative)
        children[5] = ACC-006: BooleanAnd — HER2 + BooleanOr of time range
        children[6] = ACC-007: Equals(field='billing_info_present', value=False)
        children[7] = ACC-009: BooleanAnd — HER2 + IsNull(fixation_time_hours)
        children[8] = ACC-010: BooleanAnd — specimen_type LLM-review band
        children[9] = ACC-011: BooleanAnd — anatomic_site LLM-review band (GH-234)
        children[10] = ACC-012: IsNull(field='anatomic_site') (GH-234)
        """
        acc008 = next(s for s in acc_specs if s.rule_id == "ACC-008")
        or_node = acc008.when.child  # type: ignore[union-attr]
        assert isinstance(or_node, BooleanOr)
        children = or_node.children

        # children[0]: ACC-001 — IsNull(field='patient_name')
        c0 = children[0]
        assert isinstance(c0, IsNull)
        assert c0.field == "patient_name"

        # children[1]: ACC-002 — IsNull(field='patient_sex')
        c1 = children[1]
        assert isinstance(c1, IsNull)
        assert c1.field == "patient_sex"

        # children[2]: ACC-003 — InEnum on anatomic_site blacklist (GH-234 rewrite)
        c2 = children[2]
        assert isinstance(c2, InEnum)
        assert c2.field == "anatomic_site"

        # children[3]: ACC-004 — InEnum on specimen_type blacklist
        c3 = children[3]
        assert isinstance(c3, InEnum)
        assert c3.field == "specimen_type"
        assert c3.values == frozenset(
            {
                "fna",
                "fine_needle_aspiration",
                "cytology",
                "cytospin",
                "cell_block",
                "bone_marrow_aspirate",
                "body_fluid",
                "touch_prep",
                "direct_smear",
                "thinprep",
                "surepath",
                "brushing",
                "washing",
                "swab",
            }
        )

        # children[4]: ACC-005 — BooleanAnd (HER2 + not-formalin)
        c4 = children[4]
        assert isinstance(c4, BooleanAnd)

        # children[5]: ACC-006 — BooleanAnd (HER2 + time-range BooleanOr)
        c5 = children[5]
        assert isinstance(c5, BooleanAnd)

        # children[6]: ACC-007 — Equals(field='billing_info_present', value=False)
        c6 = children[6]
        assert isinstance(c6, Equals)
        assert c6.field == "billing_info_present"
        assert c6.value is False

        # children[7]: ACC-009 — BooleanAnd (HER2 + IsNull fixation_time_hours)
        c7 = children[7]
        assert isinstance(c7, BooleanAnd)

        # children[8]: ACC-010 — BooleanAnd (specimen_type LLM-review band)
        c8 = children[8]
        assert isinstance(c8, BooleanAnd)

        # children[9]: ACC-011 — BooleanAnd (anatomic_site LLM-review band, GH-234)
        c9 = children[9]
        assert isinstance(c9, BooleanAnd)

        # children[10]: ACC-012 — IsNull(field='anatomic_site') (GH-234)
        c10 = children[10]
        assert isinstance(c10, IsNull)
        assert c10.field == "anatomic_site"


class TestAcc008EvaluateBehavior:
    """Behavioral round-trip: evaluate() against a SpecimenContext.

    Catches wrong inlining order at runtime by exercising individual inlined
    children plus the full ACC-008 predicate against a clean order.
    """

    def _make_order(
        self,
        *,
        patient_name: str | None = "Jane Doe",
        patient_sex: str | None = "F",
        specimen_type: str = "biopsy",
        anatomic_site: str = "breast",
        fixative: str = "formalin",
        fixation_time_hours: float | None = 24.0,
        billing_info_present: bool = True,
    ) -> Order:
        return Order(
            order_id="ORD-001",
            patient_name=patient_name,
            patient_sex=patient_sex,
            age=None,
            specimen_type=specimen_type,
            anatomic_site=anatomic_site,
            fixative=fixative,
            fixation_time_hours=fixation_time_hours,
            ordered_tests=("HER2",),
            priority="routine",
            billing_info_present=billing_info_present,
        )

    def _make_context(self, order: Order) -> SpecimenContext:
        return SpecimenContext(
            order=order,
            current_state="ACCESSIONING",
            flags=frozenset(),
            event=Event(
                event_type="order_received",
                event_data={},
                step_index=0,
            ),
        )

    def test_null_patient_name_triggers_acc001_inlining(self, acc_specs: list[RuleSpec]) -> None:
        """ACC-001 inlined as children[0]: null patient_name causes ACC-008 to be False."""
        acc008 = next(s for s in acc_specs if s.rule_id == "ACC-008")
        or_node = acc008.when.child  # type: ignore[union-attr]
        # Evaluate only children[0] (the ACC-001 inlined predicate).
        child0 = or_node.children[0]  # type: ignore[union-attr]
        order_with_null_name = self._make_order(patient_name=None)
        ctx = self._make_context(order_with_null_name)
        # IsNull(field='patient_name') must be True when name is None.
        assert child0.evaluate(ctx) is True

    def test_nonnull_patient_name_does_not_trigger_acc001(self, acc_specs: list[RuleSpec]) -> None:
        """ACC-001 inlined as children[0]: non-null patient_name must not trigger it."""
        acc008 = next(s for s in acc_specs if s.rule_id == "ACC-008")
        or_node = acc008.when.child  # type: ignore[union-attr]
        child0 = or_node.children[0]  # type: ignore[union-attr]
        order = self._make_order(patient_name="Jane Doe")
        ctx = self._make_context(order)
        assert child0.evaluate(ctx) is False

    def test_billing_false_triggers_acc007_inlining(self, acc_specs: list[RuleSpec]) -> None:
        """ACC-007 inlined as children[6]: billing_info_present=False triggers condition."""
        acc008 = next(s for s in acc_specs if s.rule_id == "ACC-008")
        or_node = acc008.when.child  # type: ignore[union-attr]
        child6 = or_node.children[6]  # type: ignore[union-attr]
        order_no_billing = self._make_order(billing_info_present=False)
        ctx = self._make_context(order_no_billing)
        # Equals(field='billing_info_present', value=False) should be True.
        assert child6.evaluate(ctx) is True

    def test_billing_true_does_not_trigger_acc007(self, acc_specs: list[RuleSpec]) -> None:
        """ACC-007 inlined as children[6]: billing_info_present=True must not trigger it."""
        acc008 = next(s for s in acc_specs if s.rule_id == "ACC-008")
        or_node = acc008.when.child  # type: ignore[union-attr]
        child6 = or_node.children[6]  # type: ignore[union-attr]
        order = self._make_order(billing_info_present=True)
        ctx = self._make_context(order)
        assert child6.evaluate(ctx) is False

    def test_clean_order_acc008_evaluates_true(self, acc_specs: list[RuleSpec]) -> None:
        """Full ACC-008 round-trip: a clean order with all fields valid evaluates True.

        This is the runtime guard against wrong inlining order or wrong field
        names in any of the 8 referenced ACC rules.  If any child rule's
        predicate fires for a clean order, ACC-008's outer Not(BooleanOr) flips
        to False and this test fails.
        """
        acc008 = next(s for s in acc_specs if s.rule_id == "ACC-008")
        clean_order = self._make_order()
        ctx = self._make_context(clean_order)
        assert acc008.when.evaluate(ctx) is True

    @pytest.mark.parametrize(
        "blacklist_value",
        [
            "fna",
            "FNA",
            "fine_needle_aspiration",
            "cytology",
            "cytospin",
            "cell_block",
            "bone_marrow_aspirate",
            "body_fluid",
            "touch_prep",
            "direct_smear",
            "thinprep",
            "surepath",
            "brushing",
            "washing",
            "swab",
        ],
    )
    def test_acc004_blacklist_rejects_each_cytology_value(
        self, acc_specs: list[RuleSpec], blacklist_value: str
    ) -> None:
        """Every cytology-class entry in the blacklist must trigger ACC-004.

        Includes FNA (uppercase data value — canonicalized at comparison time),
        cytospin, and swab. The rule spec contains only lowercase 'fna';
        case-fold normalization at comparison time handles uppercase inputs.
        """
        acc004 = next(s for s in acc_specs if s.rule_id == "ACC-004")
        order = self._make_order(specimen_type=blacklist_value)
        ctx = self._make_context(order)
        assert acc004.when.evaluate(ctx) is True

    def test_acc004_blacklist_does_not_fire_for_biopsy(self, acc_specs: list[RuleSpec]) -> None:
        """ACC-004 does not fire for histology-compatible specimens."""
        acc004 = next(s for s in acc_specs if s.rule_id == "ACC-004")
        order = self._make_order(specimen_type="biopsy")
        ctx = self._make_context(order)
        assert acc004.when.evaluate(ctx) is False

    def test_acc004_does_not_fire_for_unrecognized_specimen_type(
        self, acc_specs: list[RuleSpec]
    ) -> None:
        """ACC-004 does not fire for unrecognized specimen types.

        Unrecognized specimen types (typo, new procedure, vendor-specific
        name) are not on the cytology blacklist, so ACC-004 evaluates False.
        GH-34's ACC-010 rule now catches these via the fall-through predicate
        and routes them to PENDING_LLM_REVIEW instead of ACCEPTING via ACC-008.
        """
        acc004 = next(s for s in acc_specs if s.rule_id == "ACC-004")
        order = self._make_order(specimen_type="gibberish_xyz")
        ctx = self._make_context(order)
        assert acc004.when.evaluate(ctx) is False

    def test_acc008_does_not_accept_unrecognized_specimen_type(
        self, acc_specs: list[RuleSpec]
    ) -> None:
        """GH-34: unrecognized specimen types now route to ACC-010 (PENDING_LLM_REVIEW),
        not ACC-008 (ACCEPTED). ACC-010 is in ACC-008's exclusion set, so ACC-008
        must evaluate False when specimen_type is not in either whitelist or blacklist.
        """
        acc008 = next(s for s in acc_specs if s.rule_id == "ACC-008")
        order = self._make_order(specimen_type="gibberish_xyz")
        ctx = self._make_context(order)
        assert acc008.when.evaluate(ctx) is False

    def test_acc008_rejects_cytology_class_specimen(self, acc_specs: list[RuleSpec]) -> None:
        """ACC-004 firing must propagate through ACC-008's Not(BooleanOr) to False.

        This is the entire load-bearing change of PR #33: a cytology
        specimen flows through ACC-004 → True, BooleanOr → True,
        Not(BooleanOr) → False, ACC-008 does not ACCEPT.  Without this
        test, a wrong-slot inlining of ACC-004 in ACC-008's BooleanOr
        could silently pass.
        """
        acc008 = next(s for s in acc_specs if s.rule_id == "ACC-008")
        order = self._make_order(specimen_type="fna")
        ctx = self._make_context(order)
        assert acc008.when.evaluate(ctx) is False

    def test_acc005_fires_for_breast_ihc_panel_non_formalin(
        self, acc_specs: list[RuleSpec]
    ) -> None:
        """ACC-005 must fire when ordered_tests=['Breast IHC Panel'] and fixative != formalin.

        T3: verifies the boolean_or branch that treats Breast IHC Panel as implying HER2.
        """
        acc005 = next(s for s in acc_specs if s.rule_id == "ACC-005")
        order = Order(
            order_id="ORD-T3-005",
            patient_name="Jane Doe",
            patient_sex="F",
            age=None,
            specimen_type="biopsy",
            anatomic_site="breast",
            fixative="zinc",  # not formalin — ACC-005 should fire
            fixation_time_hours=24.0,
            ordered_tests=("Breast IHC Panel",),  # no explicit HER2
            priority="routine",
            billing_info_present=True,
        )
        ctx = SpecimenContext(
            order=order,
            current_state="ACCESSIONING",
            flags=frozenset(),
            event=Event(event_type="order_received", event_data={}, step_index=0),
        )
        assert acc005.when.evaluate(ctx) is True

    def test_acc005_does_not_fire_for_her2_panel_with_null_fixative(
        self, acc_specs: list[RuleSpec]
    ) -> None:
        """ACC-005 must NOT fire when fixative is None on a HER2 order.

        GH-105 follow-up (PR #112 review): same bug shape as the ACC-006
        fix in this PR. `Not(Equals(fixative, "formalin"))` evaluates True
        when fixative is None (Equals returns False because the actual is
        None and the expected is non-None), producing a false-positive
        REJECT. Null fixative is missing data, not "wrong fixative" —
        analogous to the ACC-006 boundary with ACC-009.
        """
        acc005 = next(s for s in acc_specs if s.rule_id == "ACC-005")
        order = Order(
            order_id="ORD-PR112-005",
            patient_name="Jane Doe",
            patient_sex="F",
            age=None,
            specimen_type="biopsy",
            anatomic_site="breast",
            fixative=None,
            fixation_time_hours=24.0,
            ordered_tests=("Breast IHC Panel",),
            priority="routine",
            billing_info_present=True,
        )
        ctx = SpecimenContext(
            order=order,
            current_state="ACCESSIONING",
            flags=frozenset(),
            event=Event(event_type="order_received", event_data={}, step_index=0),
        )
        assert acc005.when.evaluate(ctx) is False, (
            "ACC-005 must not fire when fixative is None; null fixative is "
            "missing data, not 'wrong fixative'"
        )

    def test_acc006_does_not_fire_for_explicit_her2_with_null_fixation_time(
        self, acc_specs: list[RuleSpec]
    ) -> None:
        """Symmetric to test_acc006_does_not_fire_for_her2_panel_with_null_fixation_time:
        the same fix must hold for the `HER2` branch of the outer BooleanOr,
        not just the `Breast IHC Panel` branch.
        """
        acc006 = next(s for s in acc_specs if s.rule_id == "ACC-006")
        order = Order(
            order_id="ORD-PR112-006",
            patient_name="Jane Doe",
            patient_sex="F",
            age=None,
            specimen_type="biopsy",
            anatomic_site="breast",
            fixative="formalin",
            fixation_time_hours=None,
            ordered_tests=("HER2",),  # explicit HER2 token, not the panel
            priority="routine",
            billing_info_present=True,
        )
        ctx = SpecimenContext(
            order=order,
            current_state="ACCESSIONING",
            flags=frozenset(),
            event=Event(event_type="order_received", event_data={}, step_index=0),
        )
        assert acc006.when.evaluate(ctx) is False

    def test_acc003_does_not_fire_when_anatomic_site_is_null(
        self, acc_specs: list[RuleSpec]
    ) -> None:
        """GH-234: ACC-003 does NOT fire when anatomic_site is None.

        After the GH-234 rewrite, ACC-003 uses bare in_enum (blacklist of out-of-scope
        organs). in_enum returns False on null (fail-safe semantics), so null anatomic_site
        does NOT trigger ACC-003 (REJECT). Null now routes via ACC-012 (HOLD).
        """
        acc003 = next(s for s in acc_specs if s.rule_id == "ACC-003")
        order = Order(
            order_id="ORD-GH234-003-NULL",
            patient_name="Jane Doe",
            patient_sex="F",
            age=None,
            specimen_type="biopsy",
            anatomic_site=None,
            fixative="formalin",
            fixation_time_hours=24.0,
            ordered_tests=("Breast IHC Panel",),
            priority="routine",
            billing_info_present=True,
        )
        ctx = SpecimenContext(
            order=order,
            current_state="ACCESSIONING",
            flags=frozenset(),
            event=Event(event_type="order_received", event_data={}, step_index=0),
        )
        assert acc003.when.evaluate(ctx) is False

    def test_acc004_does_not_fire_when_specimen_type_is_null(
        self, acc_specs: list[RuleSpec]
    ) -> None:
        """ACC-004 does NOT fire when specimen_type is None — null is not on
        the cytology blacklist (`InEnum` returns False on None). ACC-010 is
        the rule that owns the null/unknown specimen path.
        """
        acc004 = next(s for s in acc_specs if s.rule_id == "ACC-004")
        order = Order(
            order_id="ORD-PR112-004",
            patient_name="Jane Doe",
            patient_sex="F",
            age=None,
            specimen_type=None,
            anatomic_site="breast",
            fixative="formalin",
            fixation_time_hours=24.0,
            ordered_tests=("Breast IHC Panel",),
            priority="routine",
            billing_info_present=True,
        )
        ctx = SpecimenContext(
            order=order,
            current_state="ACCESSIONING",
            flags=frozenset(),
            event=Event(event_type="order_received", event_data={}, step_index=0),
        )
        assert acc004.when.evaluate(ctx) is False

    def test_acc010_fires_when_specimen_type_is_null(self, acc_specs: list[RuleSpec]) -> None:
        """ACC-010 fires when specimen_type is None — both `Not(InEnum)` clauses
        return True (fail-closed `InEnum` returns False, `Not(False)` = True).
        Routes null/unknown specimen to PENDING_LLM_REVIEW. SC-104 depends on
        this routing.
        """
        acc010 = next(s for s in acc_specs if s.rule_id == "ACC-010")
        order = Order(
            order_id="ORD-PR112-010",
            patient_name="Jane Doe",
            patient_sex="F",
            age=None,
            specimen_type=None,
            anatomic_site="breast",
            fixative="formalin",
            fixation_time_hours=24.0,
            ordered_tests=("Breast IHC Panel",),
            priority="routine",
            billing_info_present=True,
        )
        ctx = SpecimenContext(
            order=order,
            current_state="ACCESSIONING",
            flags=frozenset(),
            event=Event(event_type="order_received", event_data={}, step_index=0),
        )
        assert acc010.when.evaluate(ctx) is True

    def test_acc006_fires_for_breast_ihc_panel_short_fixation(
        self, acc_specs: list[RuleSpec]
    ) -> None:
        """ACC-006 must fire when ordered_tests=['Breast IHC Panel'] and fixation < 6h.

        T3: verifies the boolean_or branch that treats Breast IHC Panel as implying HER2.
        """
        acc006 = next(s for s in acc_specs if s.rule_id == "ACC-006")
        order = Order(
            order_id="ORD-T3-006",
            patient_name="Jane Doe",
            patient_sex="F",
            age=None,
            specimen_type="biopsy",
            anatomic_site="breast",
            fixative="formalin",
            fixation_time_hours=2.0,  # < 6h — ACC-006 should fire
            ordered_tests=("Breast IHC Panel",),  # no explicit HER2
            priority="routine",
            billing_info_present=True,
        )
        ctx = SpecimenContext(
            order=order,
            current_state="ACCESSIONING",
            flags=frozenset(),
            event=Event(event_type="order_received", event_data={}, step_index=0),
        )
        assert acc006.when.evaluate(ctx) is True

    def test_acc009_fires_for_breast_ihc_panel_null_fixation_time(
        self, acc_specs: list[RuleSpec]
    ) -> None:
        """ACC-009 fires for Breast IHC Panel with null fixation_time_hours.

        T3: verifies the boolean_or branch that treats Breast IHC Panel as implying HER2.
        """
        acc009 = next(s for s in acc_specs if s.rule_id == "ACC-009")
        order = Order(
            order_id="ORD-T3-009",
            patient_name="Jane Doe",
            patient_sex="F",
            age=None,
            specimen_type="biopsy",
            anatomic_site="breast",
            fixative="formalin",
            fixation_time_hours=None,  # null — ACC-009 should fire
            ordered_tests=("Breast IHC Panel",),  # no explicit HER2
            priority="routine",
            billing_info_present=True,
        )
        ctx = SpecimenContext(
            order=order,
            current_state="ACCESSIONING",
            flags=frozenset(),
            event=Event(event_type="order_received", event_data={}, step_index=0),
        )
        assert acc009.when.evaluate(ctx) is True

    def test_acc006_does_not_fire_for_her2_panel_with_null_fixation_time(
        self, acc_specs: list[RuleSpec]
    ) -> None:
        """ACC-006 must NOT fire when fixation_time_hours is None on a HER2 order.

        GH-105 Slice 1: null fixation is missing data (ACC-009 territory), not
        an out-of-range value.  ACC-006's Not(threshold_gte) and Not(threshold_lte)
        both evaluate to True on None (fail-closed), so Not(False)=True produces a
        false-positive DO_NOT_PROCESS unless a Not(IsNull) guard is added.
        """
        acc006 = next(s for s in acc_specs if s.rule_id == "ACC-006")
        acc009 = next(s for s in acc_specs if s.rule_id == "ACC-009")
        order = Order(
            order_id="ORD-GH105-001",
            patient_name="Jane Doe",
            patient_sex="F",
            age=None,
            specimen_type="biopsy",
            anatomic_site="breast",
            fixative="formalin",
            fixation_time_hours=None,  # null — ACC-009's territory, not ACC-006's
            ordered_tests=("Breast IHC Panel",),
            priority="routine",
            billing_info_present=True,
        )
        ctx = SpecimenContext(
            order=order,
            current_state="ACCESSIONING",
            flags=frozenset(),
            event=Event(event_type="order_received", event_data={}, step_index=0),
        )
        assert acc006.when.evaluate(ctx) is False, (
            "ACC-006 must not fire when fixation_time_hours is None; "
            "null fixation belongs to ACC-009 (MISSING_INFO_HOLD)"
        )
        assert acc009.when.evaluate(ctx) is True, (
            "ACC-009 must fire when fixation_time_hours is None for a HER2 order"
        )

    def test_acc002_field_alignment_with_order_model(self, acc_specs: list[RuleSpec]) -> None:
        """ACC-002 must reference an actual Order field; otherwise it always fires.

        Regression guard: an earlier draft used field='sex' which doesn't exist
        on Order (the model uses 'patient_sex'), making ACC-002 always True via
        the SpecimenContext.field() getattr fallback.
        """
        acc002 = next(s for s in acc_specs if s.rule_id == "ACC-002")
        assert isinstance(acc002.when, IsNull)
        order_with_sex = self._make_order(patient_sex="F")
        ctx = self._make_context(order_with_sex)
        assert acc002.when.evaluate(ctx) is False


# ---------------------------------------------------------------------------
# GH-34 Slice 2: ACC-010 behavioral tests
# ---------------------------------------------------------------------------

# ACC-004 blacklist and ACC-010 whitelist are loaded from the parsed rule specs
# in TestAcc010 and TestSpecimenTypeNoDrift. The frozen sets below are kept
# only for parametrize decorators that need them at collection time.


def _load_acc004_blacklist(acc_specs: list[RuleSpec]) -> frozenset[str]:
    """Load ACC-004's in_enum values (blacklist) from the parsed rule spec."""
    acc004 = next(s for s in acc_specs if s.rule_id == "ACC-004")
    assert isinstance(acc004.when, InEnum), "ACC-004.when must be InEnum"
    return frozenset(acc004.when.values)


def _load_acc010_whitelist(acc_specs: list[RuleSpec]) -> frozenset[str]:
    """Load ACC-010's whitelist (second Not>in_enum) from the parsed rule spec."""
    from samantha_server.primitives import BooleanAnd

    acc010 = next(s for s in acc_specs if s.rule_id == "ACC-010")
    assert isinstance(acc010.when, BooleanAnd), "ACC-010.when must be BooleanAnd"
    # The second child is Not(in_enum(whitelist))
    second_not = acc010.when.children[1]
    assert isinstance(second_not, Not), "ACC-010.when.children[1] must be Not"
    assert isinstance(second_not.child, InEnum), "ACC-010.when.children[1].child must be InEnum"
    return frozenset(second_not.child.values)


# Literal sets used at collection time for parametrize decorators.
# These MUST be kept in sync with the YAML files; the TestSpecimenTypeNoDrift
# class below provides a self-correcting structural assertion.
_ACC_004_BLACKLIST = frozenset(
    {
        "fna",
        "fine_needle_aspiration",
        "cytology",
        "cytospin",
        "cell_block",
        "bone_marrow_aspirate",
        "body_fluid",
        "touch_prep",
        "direct_smear",
        "thinprep",
        "surepath",
        "brushing",
        "washing",
        "swab",
    }
)

# ACC-010 whitelist — must match ACC-010.yaml's whitelist `values:` list exactly.
_ACC_010_WHITELIST = frozenset(
    {
        "biopsy",
        "core_needle_biopsy",
        "lumpectomy",
        "mastectomy",
        "excision",
        "re_excision",
        "vacuum_assisted_biopsy",
        "resection",
    }
)


def _make_acc010_order(specimen_type: str) -> Order:
    """Minimal valid order for ACC-010 predicate evaluation."""
    return Order(
        order_id="ACC010-TEST",
        patient_name="Jane Doe",
        patient_sex="F",
        age=45,
        specimen_type=specimen_type,
        anatomic_site="breast",
        fixative="formalin",
        fixation_time_hours=24.0,
        ordered_tests=("ER",),
        priority="routine",
        billing_info_present=True,
    )


def _make_acc010_ctx(specimen_type: str) -> SpecimenContext:
    return SpecimenContext(
        order=_make_acc010_order(specimen_type),
        current_state="ACCESSIONING",
        flags=frozenset(),
        event=Event(event_type="order_received", event_data={}, step_index=0),
    )


class TestAcc010:
    """GH-34 Slice 2 — ACC-010 (PROCEED → PENDING_LLM_REVIEW) behavioral tests."""

    def test_acc010_fires_on_unrecognized_specimen_type(self, acc_specs: list[RuleSpec]) -> None:
        """ACC-010 must fire on a specimen_type not in either list."""
        acc010 = next(s for s in acc_specs if s.rule_id == "ACC-010")
        ctx = _make_acc010_ctx("frozen_section")
        assert acc010.when.evaluate(ctx) is True

    @pytest.mark.parametrize("whitelist_value", sorted(_ACC_010_WHITELIST))
    def test_acc010_does_not_fire_on_whitelist_values(
        self, acc_specs: list[RuleSpec], whitelist_value: str
    ) -> None:
        """ACC-010 must NOT fire when specimen_type is in the whitelist."""
        acc010 = next(s for s in acc_specs if s.rule_id == "ACC-010")
        ctx = _make_acc010_ctx(whitelist_value)
        assert acc010.when.evaluate(ctx) is False

    @pytest.mark.parametrize("blacklist_value", sorted(_ACC_004_BLACKLIST))
    def test_acc010_does_not_fire_on_blacklist_values(
        self, acc_specs: list[RuleSpec], blacklist_value: str
    ) -> None:
        """ACC-010 must NOT fire when specimen_type is in ACC-004's blacklist."""
        acc010 = next(s for s in acc_specs if s.rule_id == "ACC-010")
        ctx = _make_acc010_ctx(blacklist_value)
        assert acc010.when.evaluate(ctx) is False

    def test_acc010_outcome_is_proceeding_pending_llm_review(
        self, acc_specs: list[RuleSpec]
    ) -> None:
        """ACC-010 action outcome must be 'proceeding_pending_llm_review'."""
        acc010 = next(s for s in acc_specs if s.rule_id == "ACC-010")
        assert acc010.action.outcome == "proceeding_pending_llm_review"

    def test_acc010_transition_is_pending_llm_review(self, acc_specs: list[RuleSpec]) -> None:
        """ACC-010 must transition to PENDING_LLM_REVIEW."""
        acc010 = next(s for s in acc_specs if s.rule_id == "ACC-010")
        assert acc010.action.transition == "PENDING_LLM_REVIEW"

    def test_acc010_severity_is_proceed(self, acc_specs: list[RuleSpec]) -> None:
        """ACC-010 must have PROCEED severity."""
        acc010 = next(s for s in acc_specs if s.rule_id == "ACC-010")
        assert acc010.severity == "PROCEED"

    def test_acc010_sets_llm_review_requested_flag(self, acc_specs: list[RuleSpec]) -> None:
        """ACC-010 must set LLM_REVIEW_REQUESTED."""
        acc010 = next(s for s in acc_specs if s.rule_id == "ACC-010")
        assert "LLM_REVIEW_REQUESTED" in acc010.action.set_flags

    def test_acc010_does_not_fire_on_resection(self, acc_specs: list[RuleSpec]) -> None:
        """C-01: resection is in canonical-fields.json and must be in ACC-010 whitelist.

        A resection specimen bypasses LLM review; ACC-010 must NOT fire on it.
        """
        acc010 = next(s for s in acc_specs if s.rule_id == "ACC-010")
        ctx = _make_acc010_ctx("resection")
        assert acc010.when.evaluate(ctx) is False, (
            "ACC-010 must not fire on 'resection' — it should be in the whitelist."
        )

    def test_acc010_whitelist_and_blacklist_are_disjoint(self, acc_specs: list[RuleSpec]) -> None:
        """M-11: ACC-010 whitelist and ACC-004 blacklist must be disjoint.

        A value in both would cause ACC-010 to silently never fire for that type
        (the blacklist Not() short-circuits first). Self-correcting: loads from specs.
        """
        blacklist = _load_acc004_blacklist(acc_specs)
        whitelist = _load_acc010_whitelist(acc_specs)
        intersection = blacklist & whitelist
        assert not intersection, (
            f"ACC-010 whitelist and ACC-004 blacklist overlap: {sorted(intersection)}. "
            "Remove the duplicates from the ACC-010 whitelist."
        )


class TestSpecimenTypeNoDrift:
    """C-01 + M-11: Structural drift-check between canonical-fields.json and rule specs.

    Invariant: every canonical specimen_type that is not in ACC-004's blacklist
    must be in ACC-010's whitelist. Otherwise the canonical type routes to LLM
    review instead of proceeding directly to ACCEPTED.
    """

    def test_canonical_types_covered_by_blacklist_or_whitelist(
        self, acc_specs: list[RuleSpec]
    ) -> None:
        """canonical_specimen_types ⊆ ACC-004.blacklist ∪ ACC-010.whitelist (no drift).

        Loads canonical-fields.json pick list, ACC-004 blacklist, and ACC-010 whitelist
        from the parsed rule specs. Any canonical specimen_type absent from both lists
        would silently route to LLM review — a waste of LLM calls and a correctness gap.
        """
        import json

        canonical_fields_path = (
            Path(__file__).resolve().parents[2]
            / "samantha_server"
            / "data"
            / "canonical-fields.json"
        )
        canonical_data = json.loads(canonical_fields_path.read_text())
        canonical_types = frozenset(canonical_data["specimen_type"])

        blacklist = _load_acc004_blacklist(acc_specs)
        whitelist = _load_acc010_whitelist(acc_specs)
        covered = blacklist | whitelist

        uncovered = canonical_types - covered
        assert not uncovered, (
            f"Canonical specimen types not in ACC-004 blacklist or ACC-010 whitelist: "
            f"{sorted(uncovered)}. Add them to the appropriate rule's values list."
        )


class TestAcc008WithAcc010:
    """ACC-008 must not fire when ACC-010 fires."""

    def test_acc008_does_not_fire_on_unrecognized_specimen_type_after_acc010(
        self, acc_specs: list[RuleSpec]
    ) -> None:
        """After ACC-010 is added to ACC-008's exclusion set, 'frozen_section'
        must not trigger ACC-008."""
        acc008 = next(s for s in acc_specs if s.rule_id == "ACC-008")
        ctx = _make_acc010_ctx("frozen_section")
        assert acc008.when.evaluate(ctx) is False

    def test_acc008_excludes_every_other_acc_rule_including_acc010(
        self, acc_specs: list[RuleSpec]
    ) -> None:
        """ACC-008's BooleanOr child count must match 9 (all non-ACC-008 rules)."""
        acc008 = next(s for s in acc_specs if s.rule_id == "ACC-008")
        or_node = acc008.when.child  # type: ignore[union-attr]
        assert isinstance(or_node, BooleanOr)
        non_acc008_count = sum(1 for s in acc_specs if s.rule_id != "ACC-008")
        assert len(or_node.children) == non_acc008_count, (
            f"ACC-008 excludes {len(or_node.children)} rules but {non_acc008_count} "
            f"non-ACC-008 ACC rules exist. Add new rules' rule_refs to ACC-008.yaml."
        )


# ---------------------------------------------------------------------------
# GH-234 S1: ACC-003 rewrite (whitelist → blacklist) behavioral tests
# ---------------------------------------------------------------------------


def _make_acc003_ctx(anatomic_site: str | None) -> SpecimenContext:
    """Minimal SpecimenContext for ACC-003 predicate evaluation (S1 tests)."""
    return SpecimenContext(
        order=Order(
            order_id="ACC003-GH234-TEST",
            patient_name="Jane Doe",
            patient_sex="F",
            age=45,
            specimen_type="biopsy",
            anatomic_site=anatomic_site,
            fixative="formalin",
            fixation_time_hours=24.0,
            ordered_tests=("ER",),
            priority="routine",
            billing_info_present=True,
        ),
        current_state="ACCESSIONING",
        flags=frozenset(),
        event=Event(event_type="order_received", event_data={}, step_index=0),
    )


class TestAcc003Blacklist:
    """GH-234 S1 — ACC-003 rewritten from whitelist to explicit blacklist.

    After the rewrite:
    - Blacklist values (lung, liver, colon, brain, prostate) → ACC-003 fires (REJECT).
    - Whitelist values (breast, left breast, right breast, axillary lymph node) → does NOT fire.
    - LLM-review band values (skin overlying breast) → does NOT fire (ACC-011 handles).
    - null → does NOT fire (ACC-012 handles, in_enum fail-safe on null).
    """

    @pytest.mark.parametrize(
        "blacklist_value",
        ["lung", "liver", "colon", "brain", "prostate"],
    )
    def test_acc003_fires_on_blacklist_values(
        self, acc_specs: list[RuleSpec], blacklist_value: str
    ) -> None:
        """ACC-003 must fire (REJECT) for definitively-out-of-scope anatomic sites."""
        acc003 = next(s for s in acc_specs if s.rule_id == "ACC-003")
        ctx = _make_acc003_ctx(blacklist_value)
        assert acc003.when.evaluate(ctx) is True

    @pytest.mark.parametrize(
        "whitelist_value",
        ["breast", "left breast", "right breast", "axillary lymph node"],
    )
    def test_acc003_does_not_fire_on_whitelist_values(
        self, acc_specs: list[RuleSpec], whitelist_value: str
    ) -> None:
        """ACC-003 must NOT fire for breast-workflow anatomic sites (whitelist)."""
        acc003 = next(s for s in acc_specs if s.rule_id == "ACC-003")
        ctx = _make_acc003_ctx(whitelist_value)
        assert acc003.when.evaluate(ctx) is False

    def test_acc003_does_not_fire_on_llm_review_band_value(self, acc_specs: list[RuleSpec]) -> None:
        """ACC-003 must NOT fire on LLM-review band values (ACC-011 handles these)."""
        acc003 = next(s for s in acc_specs if s.rule_id == "ACC-003")
        ctx = _make_acc003_ctx("skin overlying breast")
        assert acc003.when.evaluate(ctx) is False

    def test_acc003_does_not_fire_on_null_anatomic_site(self, acc_specs: list[RuleSpec]) -> None:
        """GH-234: null anatomic_site routes via ACC-012 (HOLD), NOT ACC-003 (REJECT).

        After the rewrite, ACC-003 uses bare in_enum (not Not(in_enum)).
        in_enum returns False on null (fail-safe semantics), so ACC-003 does not fire.
        """
        acc003 = next(s for s in acc_specs if s.rule_id == "ACC-003")
        ctx = _make_acc003_ctx(None)
        assert acc003.when.evaluate(ctx) is False

    def test_acc003_when_is_in_enum_not_wrapped_in_not(self, acc_specs: list[RuleSpec]) -> None:
        """Structural: ACC-003.when must be bare InEnum (blacklist), not Not(InEnum)."""
        acc003 = next(s for s in acc_specs if s.rule_id == "ACC-003")
        assert isinstance(acc003.when, InEnum), (
            "ACC-003.when must be bare InEnum after GH-234 rewrite (not Not(InEnum))"
        )
        assert acc003.when.field == "anatomic_site"

    def test_acc003_severity_is_reject(self, acc_specs: list[RuleSpec]) -> None:
        """ACC-003 severity must remain REJECT."""
        acc003 = next(s for s in acc_specs if s.rule_id == "ACC-003")
        assert acc003.severity == "REJECT"

    def test_acc003_transition_is_do_not_process(self, acc_specs: list[RuleSpec]) -> None:
        """ACC-003 action transition must remain DO_NOT_PROCESS."""
        acc003 = next(s for s in acc_specs if s.rule_id == "ACC-003")
        assert acc003.action.transition == "DO_NOT_PROCESS"

    def test_acc003_outcome_is_rejected_invalid_anatomic_site(
        self, acc_specs: list[RuleSpec]
    ) -> None:
        """ACC-003 action outcome must remain rejected_invalid_anatomic_site."""
        acc003 = next(s for s in acc_specs if s.rule_id == "ACC-003")
        assert acc003.action.outcome == "rejected_invalid_anatomic_site"


# ---------------------------------------------------------------------------
# GH-234 S2: ACC-012 (null anatomic_site → MISSING_INFO_HOLD) tests
# ---------------------------------------------------------------------------


class TestAcc012NullAnatomicSite:
    """GH-234 S2 — ACC-012: null anatomic_site routes to MISSING_INFO_HOLD.

    Mirrors ACC-001 (null patient_name) and ACC-002 (null patient_sex).
    """

    def test_acc012_fires_when_anatomic_site_is_null(self, acc_specs: list[RuleSpec]) -> None:
        """ACC-012 must fire (HOLD) when anatomic_site is null."""
        acc012 = next(s for s in acc_specs if s.rule_id == "ACC-012")
        ctx = _make_acc003_ctx(None)
        assert acc012.when.evaluate(ctx) is True

    @pytest.mark.parametrize(
        "non_null_value",
        ["breast", "lung", "skin overlying breast", "chest wall", "tibia"],
    )
    def test_acc012_does_not_fire_for_non_null_values(
        self, acc_specs: list[RuleSpec], non_null_value: str
    ) -> None:
        """ACC-012 must NOT fire when anatomic_site is non-null."""
        acc012 = next(s for s in acc_specs if s.rule_id == "ACC-012")
        ctx = _make_acc003_ctx(non_null_value)
        assert acc012.when.evaluate(ctx) is False

    def test_acc012_severity_is_hold(self, acc_specs: list[RuleSpec]) -> None:
        """ACC-012 severity must be HOLD."""
        acc012 = next(s for s in acc_specs if s.rule_id == "ACC-012")
        assert acc012.severity == "HOLD"

    def test_acc012_transition_is_missing_info_hold(self, acc_specs: list[RuleSpec]) -> None:
        """ACC-012 action transition must be MISSING_INFO_HOLD."""
        acc012 = next(s for s in acc_specs if s.rule_id == "ACC-012")
        assert acc012.action.transition == "MISSING_INFO_HOLD"

    def test_acc012_outcome_is_held_missing_anatomic_site(self, acc_specs: list[RuleSpec]) -> None:
        """ACC-012 action outcome must be held_missing_anatomic_site."""
        acc012 = next(s for s in acc_specs if s.rule_id == "ACC-012")
        assert acc012.action.outcome == "held_missing_anatomic_site"

    def test_acc012_when_is_is_null(self, acc_specs: list[RuleSpec]) -> None:
        """Structural: ACC-012.when must be IsNull on anatomic_site."""
        acc012 = next(s for s in acc_specs if s.rule_id == "ACC-012")
        assert isinstance(acc012.when, IsNull)
        assert acc012.when.field == "anatomic_site"


# ---------------------------------------------------------------------------
# GH-234 S3: ACC-011 (LLM-review band → PENDING_LLM_REVIEW) tests
# ---------------------------------------------------------------------------

# ACC-003 blacklist and ACC-011 whitelist literal sets — used at collection time
# for parametrize decorators. These are a third copy of the values in the YAML
# files (alongside the YAMLs themselves and the handler constants in handlers.py).
# TestAnatomicSiteNoDrift.test_handler_blacklist_constant_matches_acc003_yaml and
# test_handler_whitelist_constant_matches_acc011_yaml guard the handler constants
# against YAML drift, but these local sets are NOT guarded by any drift test —
# they are owned by this file and must be kept in sync manually.
_ACC_003_BLACKLIST = frozenset({"lung", "liver", "colon", "brain", "prostate"})

_ACC_011_WHITELIST = frozenset({"breast", "left breast", "right breast", "axillary lymph node"})


def _make_acc011_ctx(anatomic_site: str | None) -> SpecimenContext:
    """Minimal SpecimenContext for ACC-011 predicate evaluation."""
    return SpecimenContext(
        order=Order(
            order_id="ACC011-GH234-TEST",
            patient_name="Jane Doe",
            patient_sex="F",
            age=45,
            specimen_type="biopsy",
            anatomic_site=anatomic_site,
            fixative="formalin",
            fixation_time_hours=24.0,
            ordered_tests=("ER",),
            priority="routine",
            billing_info_present=True,
        ),
        current_state="ACCESSIONING",
        flags=frozenset(),
        event=Event(event_type="order_received", event_data={}, step_index=0),
    )


class TestAcc011LlmReviewBand:
    """GH-234 S3 — ACC-011: LLM-review band routes to PENDING_LLM_REVIEW.

    Fires when anatomic_site is non-null, not on ACC-003's blacklist,
    and not on the whitelist (bypasses LLM review for known-good sites).
    """

    @pytest.mark.parametrize(
        "llm_review_value",
        ["skin overlying breast", "chest wall", "tibia"],
    )
    def test_acc011_fires_on_llm_review_band_values(
        self, acc_specs: list[RuleSpec], llm_review_value: str
    ) -> None:
        """ACC-011 must fire for values not in whitelist or blacklist."""
        acc011 = next(s for s in acc_specs if s.rule_id == "ACC-011")
        ctx = _make_acc011_ctx(llm_review_value)
        assert acc011.when.evaluate(ctx) is True

    @pytest.mark.parametrize("whitelist_value", sorted(_ACC_011_WHITELIST))
    def test_acc011_does_not_fire_on_whitelist_values(
        self, acc_specs: list[RuleSpec], whitelist_value: str
    ) -> None:
        """ACC-011 must NOT fire when anatomic_site is in the whitelist."""
        acc011 = next(s for s in acc_specs if s.rule_id == "ACC-011")
        ctx = _make_acc011_ctx(whitelist_value)
        assert acc011.when.evaluate(ctx) is False

    @pytest.mark.parametrize("blacklist_value", sorted(_ACC_003_BLACKLIST))
    def test_acc011_does_not_fire_on_blacklist_values(
        self, acc_specs: list[RuleSpec], blacklist_value: str
    ) -> None:
        """ACC-011 must NOT fire when anatomic_site is in ACC-003's blacklist."""
        acc011 = next(s for s in acc_specs if s.rule_id == "ACC-011")
        ctx = _make_acc011_ctx(blacklist_value)
        assert acc011.when.evaluate(ctx) is False

    def test_acc011_does_not_fire_on_null_anatomic_site(self, acc_specs: list[RuleSpec]) -> None:
        """ACC-011 must NOT fire when anatomic_site is null (ACC-012 handles null)."""
        acc011 = next(s for s in acc_specs if s.rule_id == "ACC-011")
        ctx = _make_acc011_ctx(None)
        assert acc011.when.evaluate(ctx) is False

    def test_acc011_severity_is_proceed(self, acc_specs: list[RuleSpec]) -> None:
        """ACC-011 severity must be PROCEED."""
        acc011 = next(s for s in acc_specs if s.rule_id == "ACC-011")
        assert acc011.severity == "PROCEED"

    def test_acc011_transition_is_pending_llm_review(self, acc_specs: list[RuleSpec]) -> None:
        """ACC-011 action transition must be PENDING_LLM_REVIEW."""
        acc011 = next(s for s in acc_specs if s.rule_id == "ACC-011")
        assert acc011.action.transition == "PENDING_LLM_REVIEW"

    def test_acc011_sets_llm_review_requested_flag(self, acc_specs: list[RuleSpec]) -> None:
        """ACC-011 must set LLM_REVIEW_REQUESTED flag."""
        acc011 = next(s for s in acc_specs if s.rule_id == "ACC-011")
        assert "LLM_REVIEW_REQUESTED" in acc011.action.set_flags

    def test_acc011_outcome_is_proceeding_pending_anatomic_site_review(
        self, acc_specs: list[RuleSpec]
    ) -> None:
        """ACC-011 action outcome must be proceeding_pending_anatomic_site_review."""
        acc011 = next(s for s in acc_specs if s.rule_id == "ACC-011")
        assert acc011.action.outcome == "proceeding_pending_anatomic_site_review"

    def test_acc011_when_is_boolean_and(self, acc_specs: list[RuleSpec]) -> None:
        """Structural: ACC-011.when must be BooleanAnd."""
        acc011 = next(s for s in acc_specs if s.rule_id == "ACC-011")
        assert isinstance(acc011.when, BooleanAnd)

    def test_acc011_blacklist_and_whitelist_are_disjoint(self, acc_specs: list[RuleSpec]) -> None:
        """ACC-011's blacklist and whitelist must be disjoint — no double-listed values."""
        acc011 = next(s for s in acc_specs if s.rule_id == "ACC-011")
        assert isinstance(acc011.when, BooleanAnd)
        # children[1]: Not(in_enum(blacklist))
        blacklist_not = acc011.when.children[1]
        assert isinstance(blacklist_not, Not)
        assert isinstance(blacklist_not.child, InEnum)
        blacklist = frozenset(blacklist_not.child.values)
        # children[2]: Not(in_enum(whitelist))
        whitelist_not = acc011.when.children[2]
        assert isinstance(whitelist_not, Not)
        assert isinstance(whitelist_not.child, InEnum)
        whitelist = frozenset(whitelist_not.child.values)
        intersection = blacklist & whitelist
        assert not intersection, f"ACC-011 blacklist and whitelist overlap: {sorted(intersection)}"


# ---------------------------------------------------------------------------
# GH-234 / PR245 M3: Exactly-one-fires disjointness test (end-to-end)
# ---------------------------------------------------------------------------


def _make_anatomic_site_ctx(anatomic_site: str | None) -> SpecimenContext:
    """Minimal SpecimenContext with a valid order where only anatomic_site-related
    rules can plausibly fire.

    All non-anatomic-site disqualifying conditions are satisfied:
    - patient_name and patient_sex are set (ACC-001, ACC-002 silent)
    - billing_info_present=True (ACC-007 silent)
    - fixation_time_hours set (ACC-009 silent)
    - fixative is formalin (ACC-005, ACC-006 silent)
    - ordered_tests does not include HER2 (ACC-005, ACC-006, ACC-009 silent)
    - specimen_type is in whitelist (ACC-004 silent, ACC-010 silent)
    """
    return SpecimenContext(
        order=Order(
            order_id="M3-DISJOINT-TEST",
            patient_name="Jane Doe",
            patient_sex="F",
            age=50,
            specimen_type="biopsy",
            anatomic_site=anatomic_site,
            fixative="formalin",
            fixation_time_hours=24.0,
            ordered_tests=("ER",),
            priority="routine",
            billing_info_present=True,
        ),
        current_state="ACCESSIONING",
        flags=frozenset(),
        event=Event(event_type="order_received", event_data={}, step_index=0),
    )


@pytest.mark.parametrize(
    "anatomic_site,expected_rule",
    [
        # ACC-008 whitelist: well-known breast-cancer-relevant sites (bypass LLM review)
        ("breast", "ACC-008"),
        ("left breast", "ACC-008"),
        ("right breast", "ACC-008"),
        ("axillary lymph node", "ACC-008"),
        # ACC-003 blacklist: definitively out-of-scope organs → DO_NOT_PROCESS
        ("lung", "ACC-003"),
        ("liver", "ACC-003"),
        ("colon", "ACC-003"),
        ("brain", "ACC-003"),
        ("prostate", "ACC-003"),
        # ACC-011 middle band: non-null, not blacklisted, not whitelisted → PENDING_LLM_REVIEW
        ("skin overlying breast", "ACC-011"),
        ("chest wall", "ACC-011"),
        ("tibia", "ACC-011"),
        # Empty string: non-null (is_null is False) + not in any enum → routes via ACC-011
        # (same as middle-band). Empty string diverges from null: null routes via ACC-012.
        ("", "ACC-011"),
        # ACC-012: null anatomic_site → HOLD
        (None, "ACC-012"),
    ],
)
def test_exactly_one_anatomic_site_rule_fires(
    anatomic_site: str | None,
    expected_rule: str,
    acc_specs: list[RuleSpec],
) -> None:
    """M3: for each anatomic_site value, exactly one of {ACC-003, ACC-008, ACC-011,
    ACC-012} must evaluate True and the rest must evaluate False.

    Pins the disjointness invariant end-to-end across the full anatomic_site
    input space (canonical values + off-vocab + empty string + null).
    """
    ctx = _make_anatomic_site_ctx(anatomic_site)

    anatomic_site_rules = {"ACC-003", "ACC-008", "ACC-011", "ACC-012"}
    rule_results = {
        s.rule_id: s.when.evaluate(ctx) for s in acc_specs if s.rule_id in anatomic_site_rules
    }

    # Exactly one must fire.
    fired = {rule_id for rule_id, result in rule_results.items() if result}
    assert fired == {expected_rule}, (
        f"anatomic_site={anatomic_site!r}: expected only {expected_rule!r} to fire, "
        f"but these fired: {sorted(fired)}. "
        f"Full results: {rule_results}"
    )
