"""Tests for RuleSpec, ActionSpec, PanelSpec, and ConditionalMarker Pydantic models."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from samantha_server.primitives import BooleanAnd, Contains, IsNull, Not, ThresholdGTE
from samantha_server.rules.spec import ActionSpec, ConditionalMarker, PanelSpec, RuleSpec

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_action() -> dict[str, Any]:
    return {
        "transition": "DO_NOT_PROCESS",
        "set_flags": [],
        "clear_flags": [],
        "outcome": "rejected_missing_patient",
        "panel": None,
        "branch": None,
    }


def _minimal_accessioning_spec() -> dict[str, Any]:
    return {
        "rule_id": "ACC-001",
        "step": "ACCESSIONING",
        "applies_at": None,
        "event_type": "order_received",
        "severity": "REJECT",
        "priority": None,
        "when": {"is_null": {"field": "patient_name"}},
        "action": _minimal_action(),
        "source": "knowledge_base/rules/required_fields.md#ACC-001",
    }


# ---------------------------------------------------------------------------
# Slice 4 — RuleSpec required fields
# ---------------------------------------------------------------------------


class TestRuleSpecRequiredFields:
    def test_minimal_valid_accessioning_spec_parses(self) -> None:
        spec = RuleSpec(**_minimal_accessioning_spec())
        assert spec.rule_id == "ACC-001"
        assert spec.step == "ACCESSIONING"
        assert spec.event_type == ("order_received",)
        assert spec.severity == "REJECT"
        assert spec.priority is None
        assert isinstance(spec.when, IsNull)

    def test_missing_rule_id_raises(self) -> None:
        data = _minimal_accessioning_spec()
        del data["rule_id"]
        with pytest.raises(ValidationError, match="rule_id"):
            RuleSpec(**data)

    def test_missing_step_raises(self) -> None:
        data = _minimal_accessioning_spec()
        del data["step"]
        with pytest.raises(ValidationError, match="step"):
            RuleSpec(**data)

    def test_missing_event_type_raises(self) -> None:
        data = _minimal_accessioning_spec()
        del data["event_type"]
        with pytest.raises(ValidationError, match="event_type"):
            RuleSpec(**data)

    def test_missing_when_raises(self) -> None:
        data = _minimal_accessioning_spec()
        del data["when"]
        with pytest.raises(ValidationError, match="when"):
            RuleSpec(**data)

    def test_missing_action_raises(self) -> None:
        data = _minimal_accessioning_spec()
        del data["action"]
        with pytest.raises(ValidationError, match="action"):
            RuleSpec(**data)

    def test_missing_source_raises(self) -> None:
        data = _minimal_accessioning_spec()
        del data["source"]
        with pytest.raises(ValidationError, match="source"):
            RuleSpec(**data)

    def test_extra_key_raises(self) -> None:
        data = _minimal_accessioning_spec()
        data["not_a_real_field"] = "surprise"
        with pytest.raises(ValidationError):
            RuleSpec(**data)

    def test_rule_spec_is_frozen(self) -> None:
        spec = RuleSpec(**_minimal_accessioning_spec())
        # Pydantic v2 raises ValidationError (not TypeError) on frozen model mutation.
        with pytest.raises(ValidationError):
            spec.rule_id = "MUTATED"  # type: ignore[misc]

    def test_when_field_stores_primitive_not_dict(self) -> None:
        spec = RuleSpec(**_minimal_accessioning_spec())
        assert isinstance(spec.when, IsNull)


# ---------------------------------------------------------------------------
# Slice 5 — ActionSpec schema and PanelSpec
# ---------------------------------------------------------------------------


class TestActionSpec:
    def test_minimal_action_parses(self) -> None:
        action = ActionSpec(**_minimal_action())
        assert action.transition == "DO_NOT_PROCESS"
        assert action.set_flags == ()
        assert action.clear_flags == ()
        assert action.outcome == "rejected_missing_patient"
        assert action.panel is None
        assert action.branch is None

    def test_missing_transition_raises(self) -> None:
        data = _minimal_action()
        del data["transition"]
        with pytest.raises(ValidationError, match="transition"):
            ActionSpec(**data)

    def test_missing_set_flags_raises(self) -> None:
        data = _minimal_action()
        del data["set_flags"]
        with pytest.raises(ValidationError, match="set_flags"):
            ActionSpec(**data)

    def test_missing_clear_flags_raises(self) -> None:
        data = _minimal_action()
        del data["clear_flags"]
        with pytest.raises(ValidationError, match="clear_flags"):
            ActionSpec(**data)

    def test_missing_outcome_raises(self) -> None:
        data = _minimal_action()
        del data["outcome"]
        with pytest.raises(ValidationError, match="outcome"):
            ActionSpec(**data)

    def test_panel_and_branch_are_optional(self) -> None:
        data = _minimal_action()
        del data["panel"]
        del data["branch"]
        action = ActionSpec(**data)
        assert action.panel is None
        assert action.branch is None

    def test_extra_key_in_action_raises(self) -> None:
        data = _minimal_action()
        data["ghost_key"] = "surprise"
        with pytest.raises(ValidationError):
            ActionSpec(**data)

    def test_set_flags_with_values(self) -> None:
        data = _minimal_action()
        data["set_flags"] = ["HOLD_FIXATION", "NEEDS_REVIEW"]
        action = ActionSpec(**data)
        assert action.set_flags == ("HOLD_FIXATION", "NEEDS_REVIEW")
        assert isinstance(action.set_flags, tuple)

    def test_branch_non_null(self) -> None:
        data = _minimal_action()
        data["branch"] = "cleared"
        action = ActionSpec(**data)
        assert action.branch == "cleared"

    def test_set_flags_clear_flags_overlap_raises(self) -> None:
        data = _minimal_action()
        data["set_flags"] = ["HOLD", "NEEDS_REVIEW"]
        data["clear_flags"] = ["HOLD", "BILLING_HOLD"]
        with pytest.raises(ValidationError, match="HOLD"):
            ActionSpec(**data)

    def test_set_flags_clear_flags_no_overlap_passes(self) -> None:
        data = _minimal_action()
        data["set_flags"] = ["NEEDS_REVIEW"]
        data["clear_flags"] = ["BILLING_HOLD"]
        action = ActionSpec(**data)
        assert "NEEDS_REVIEW" in action.set_flags
        assert "BILLING_HOLD" in action.clear_flags

    def test_set_flags_duplicates_raises(self) -> None:
        data = _minimal_action()
        data["set_flags"] = ["HOLD", "HOLD"]
        with pytest.raises(ValidationError, match="set_flags"):
            ActionSpec(**data)

    def test_clear_flags_duplicates_raises(self) -> None:
        data = _minimal_action()
        data["clear_flags"] = ["BILLING_HOLD", "BILLING_HOLD"]
        with pytest.raises(ValidationError, match="clear_flags"):
            ActionSpec(**data)


class TestPanelSpec:
    def test_panel_spec_with_base_and_conditional(self) -> None:
        panel_data = {
            "base": ["ER", "PR", "HER2", "Ki-67"],
            "conditional": [
                {
                    "marker": "HER2",
                    "when": {"contains": {"field": "ordered_tests", "value": "HER2"}},
                }
            ],
        }
        panel = PanelSpec(**panel_data)
        assert panel.base == ("ER", "PR", "HER2", "Ki-67")
        assert isinstance(panel.base, tuple)
        assert len(panel.conditional) == 1
        cm = panel.conditional[0]
        assert isinstance(cm, ConditionalMarker)
        assert cm.marker == "HER2"
        assert isinstance(cm.when, Contains)

    def test_panel_spec_empty_conditional(self) -> None:
        panel = PanelSpec(base=["ER", "PR"], conditional=[])
        assert panel.base == ("ER", "PR")
        assert isinstance(panel.base, tuple)
        assert panel.conditional == ()
        assert isinstance(panel.conditional, tuple)

    def test_conditional_marker_when_is_primitive(self) -> None:
        from samantha_server.primitives import Equals

        cm = ConditionalMarker(
            marker="ER",
            when={"equals": {"field": "ordered_tests", "value": "ER"}},
        )
        assert isinstance(cm.when, Equals)

    def test_action_with_panel_spec(self) -> None:
        data = _minimal_action()
        data["panel"] = {
            "base": ["ER", "PR"],
            "conditional": [],
        }
        action = ActionSpec(**data)
        assert isinstance(action.panel, PanelSpec)
        assert action.panel.base == ("ER", "PR")
        assert isinstance(action.panel.base, tuple)


# ---------------------------------------------------------------------------
# Slice 6 — Unknown step rejected
# ---------------------------------------------------------------------------


def _spec_for_step(step: str) -> dict[str, Any]:
    data = _minimal_accessioning_spec()
    data["step"] = step
    if step != "ACCESSIONING":
        data["severity"] = None
        data["priority"] = 1
    if step == "IHC":
        data["applies_at"] = "IHC_STAINING"
    return data


class TestUnknownStep:
    @pytest.mark.parametrize(
        "step",
        [
            "ACCESSIONING",
            "SAMPLE_PREP",
            "HE_QC",
            "PATHOLOGIST_HE_REVIEW",
            "IHC",
            "RESULTING",
        ],
    )
    def test_valid_step_parses(self, step: str) -> None:
        spec = RuleSpec(**_spec_for_step(step))
        assert spec.step == step

    def test_unknown_step_raises(self) -> None:
        data = _minimal_accessioning_spec()
        data["step"] = "NOT_A_REAL_STEP"
        with pytest.raises(ValidationError, match="NOT_A_REAL_STEP"):
            RuleSpec(**data)

    def test_lowercase_step_raises(self) -> None:
        data = _minimal_accessioning_spec()
        data["step"] = "accessioning"
        with pytest.raises(ValidationError):
            RuleSpec(**data)


# ---------------------------------------------------------------------------
# Slice 7 — Step-conditional severity / priority / applies_at matrix
# ---------------------------------------------------------------------------


class TestStepConditionalInvariants:
    def test_accessioning_requires_severity(self) -> None:
        data = _minimal_accessioning_spec()
        data["severity"] = None
        with pytest.raises(ValidationError, match="severity"):
            RuleSpec(**data)

    def test_accessioning_rejects_priority(self) -> None:
        data = _minimal_accessioning_spec()
        data["priority"] = 1
        with pytest.raises(ValidationError, match="priority"):
            RuleSpec(**data)

    def test_accessioning_invalid_severity_value_raises(self) -> None:
        data = _minimal_accessioning_spec()
        data["severity"] = "CRITICAL"
        with pytest.raises(ValidationError):
            RuleSpec(**data)

    def test_non_accessioning_requires_priority(self) -> None:
        data = _minimal_accessioning_spec()
        data["step"] = "SAMPLE_PREP"
        data["severity"] = None
        data["priority"] = None
        with pytest.raises(ValidationError, match="priority"):
            RuleSpec(**data)

    def test_non_accessioning_rejects_severity(self) -> None:
        data = _minimal_accessioning_spec()
        data["step"] = "SAMPLE_PREP"
        data["severity"] = "REJECT"
        data["priority"] = 1
        with pytest.raises(ValidationError, match="severity"):
            RuleSpec(**data)

    def test_non_accessioning_priority_must_be_non_negative(self) -> None:
        """Priority 0 is now valid (SP-007 uses it to preempt SP-001); negative is rejected."""
        data = _minimal_accessioning_spec()
        data["step"] = "SAMPLE_PREP"
        data["severity"] = None
        data["priority"] = -1
        with pytest.raises(ValidationError, match="priority"):
            RuleSpec(**data)

    def test_non_accessioning_priority_zero_is_accepted(self) -> None:
        """Priority 0 is the minimum valid value for non-ACCESSIONING steps.

        Pinned by SP-007 which uses priority 0 to preempt SP-001 (priority 1)
        on the recut path. Negative priorities are rejected by
        ``test_non_accessioning_priority_must_be_non_negative`` above.
        """
        data = _minimal_accessioning_spec()
        data["step"] = "SAMPLE_PREP"
        data["severity"] = None
        data["priority"] = 0
        spec = RuleSpec(**data)
        assert spec.priority == 0

    def test_ihc_requires_applies_at(self) -> None:
        data = _minimal_accessioning_spec()
        data["step"] = "IHC"
        data["severity"] = None
        data["priority"] = 1
        data["applies_at"] = None
        with pytest.raises(ValidationError, match="applies_at"):
            RuleSpec(**data)

    def test_non_ihc_rejects_applies_at(self) -> None:
        data = _minimal_accessioning_spec()
        data["applies_at"] = "IHC_STAINING"
        with pytest.raises(ValidationError, match="applies_at"):
            RuleSpec(**data)

    def test_ihc_with_applies_at_parses(self) -> None:
        data = _minimal_accessioning_spec()
        data["step"] = "IHC"
        data["severity"] = None
        data["priority"] = 1
        data["applies_at"] = "IHC_STAINING"
        spec = RuleSpec(**data)
        assert spec.applies_at == "IHC_STAINING"

    def test_acc006_complex_when_builds_correctly(self) -> None:
        data = _minimal_accessioning_spec()
        data["rule_id"] = "ACC-006"
        data["severity"] = "REJECT"
        data["when"] = {
            "boolean_and": [
                {"contains": {"field": "ordered_tests", "value": "HER2"}},
                {
                    "boolean_or": [
                        {
                            "not": {
                                "threshold_gte": {
                                    "field": "fixation_time_hours",
                                    "value": 6.0,
                                }
                            }
                        },
                        {
                            "not": {
                                "threshold_lte": {
                                    "field": "fixation_time_hours",
                                    "value": 72.0,
                                }
                            }
                        },
                    ]
                },
            ]
        }
        spec = RuleSpec(**data)
        assert isinstance(spec.when, BooleanAnd)
        assert isinstance(spec.when.children[0], Contains)
        not_node = spec.when.children[1].children[0]  # type: ignore[union-attr]
        assert isinstance(not_node, Not)
        assert isinstance(not_node.child, ThresholdGTE)

    def test_ihc_empty_applies_at_raises(self) -> None:
        data = _minimal_accessioning_spec()
        data["step"] = "IHC"
        data["severity"] = None
        data["priority"] = 1
        data["applies_at"] = ""
        with pytest.raises(ValidationError, match="applies_at"):
            RuleSpec(**data)

    def test_non_ihc_empty_applies_at_raises(self) -> None:
        data = _minimal_accessioning_spec()
        data["applies_at"] = ""
        with pytest.raises(ValidationError, match="applies_at"):
            RuleSpec(**data)


# ---------------------------------------------------------------------------
# Slice A — event_type normalization to tuple[str, ...]
# ---------------------------------------------------------------------------


class TestEventTypeNormalization:
    def test_list_input_becomes_tuple(self) -> None:
        """A1: list-form event_type is stored as tuple of strings in order."""
        data = _minimal_accessioning_spec()
        data["event_type"] = ["processing_complete", "embedding_complete"]
        spec = RuleSpec(**data)
        assert spec.event_type == ("processing_complete", "embedding_complete")

    def test_single_string_normalized_to_length_one_tuple(self) -> None:
        """A2: a single string is wrapped in a length-1 tuple."""
        data = _minimal_accessioning_spec()
        data["event_type"] = "order_received"
        spec = RuleSpec(**data)
        assert spec.event_type == ("order_received",)

    def test_empty_list_raises_value_error(self) -> None:
        """A4: empty event_type list is invalid."""
        data = _minimal_accessioning_spec()
        data["event_type"] = []
        with pytest.raises(ValidationError, match="event_type"):
            RuleSpec(**data)

    def test_non_string_element_in_list_raises(self) -> None:
        """A5: list element that is not a string raises ValueError."""
        data = _minimal_accessioning_spec()
        data["event_type"] = ["valid", 42]
        with pytest.raises(ValidationError, match="event_type"):
            RuleSpec(**data)

    def test_invalid_type_raises(self) -> None:
        """Passing an integer for event_type raises ValueError."""
        data = _minimal_accessioning_spec()
        data["event_type"] = 99
        with pytest.raises(ValidationError, match="event_type"):
            RuleSpec(**data)

    def test_three_event_types_stored_as_tuple(self) -> None:
        """Three-element list (SP-001 shape) stored in order."""
        data = _minimal_accessioning_spec()
        data["event_type"] = [
            "processing_complete",
            "embedding_complete",
            "sectioning_complete",
        ]
        spec = RuleSpec(**data)
        assert spec.event_type == (
            "processing_complete",
            "embedding_complete",
            "sectioning_complete",
        )
