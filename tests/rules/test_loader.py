"""Tests for load_rule_spec_from_dict, load_rule_specs, and duplicate rule_id detection."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from samantha_server.primitives import (
    BooleanAnd,
    BooleanOr,
    Contains,
)
from samantha_server.rules.loader import (
    load_candidate_rule_spec,
    load_rule_spec_from_dict,
    load_rule_specs,
)
from samantha_server.rules.spec import RuleSpec

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

ACC006_DICT = {
    "rule_id": "ACC-006",
    "step": "ACCESSIONING",
    "applies_at": None,
    "event_type": "order_received",
    "severity": "REJECT",
    "priority": None,
    "when": {
        "boolean_and": [
            {"contains": {"field": "ordered_tests", "value": "HER2"}},
            {
                "boolean_or": [
                    {"not": {"threshold_gte": {"field": "fixation_time_hours", "value": 6.0}}},
                    {"not": {"threshold_lte": {"field": "fixation_time_hours", "value": 72.0}}},
                ]
            },
        ]
    },
    "action": {
        "transition": "DO_NOT_PROCESS",
        "set_flags": [],
        "clear_flags": [],
        "outcome": "rejected_fixation_out_of_tolerance",
        "panel": None,
        "branch": None,
    },
    "source": "knowledge_base/rules/fixation_requirements.md#ACC-006",
}

ACC001_YAML = textwrap.dedent("""\
    rule_id: ACC-001
    step: ACCESSIONING
    applies_at: null
    event_type: order_received
    severity: REJECT
    priority: null
    when:
      is_null:
        field: patient_name
    action:
      transition: DO_NOT_PROCESS
      set_flags: []
      clear_flags: []
      outcome: rejected_missing_patient_name
      panel: null
      branch: null
    source: knowledge_base/rules/required_fields.md#ACC-001
""")

ACC002_YAML = textwrap.dedent("""\
    rule_id: ACC-002
    step: ACCESSIONING
    applies_at: null
    event_type: order_received
    severity: HOLD
    priority: null
    when:
      equals:
        field: billing_info_present
        value: false
    action:
      transition: HOLD
      set_flags: [BILLING_HOLD]
      clear_flags: []
      outcome: held_billing_incomplete
      panel: null
      branch: null
    source: knowledge_base/rules/billing.md#ACC-002
""")

INVALID_YAML = "this: is: not: valid: yaml: ["


# ---------------------------------------------------------------------------
# Slice 8 — load_rule_spec_from_dict end-to-end (ACC-006 round-trip)
# ---------------------------------------------------------------------------


class TestLoadRuleSpecFromDict:
    def test_acc006_round_trip(self) -> None:
        spec = load_rule_spec_from_dict(ACC006_DICT)
        assert isinstance(spec, RuleSpec)
        assert spec.rule_id == "ACC-006"
        assert spec.step == "ACCESSIONING"
        assert spec.severity == "REJECT"
        assert spec.priority is None
        assert spec.action.outcome == "rejected_fixation_out_of_tolerance"
        assert spec.action.transition == "DO_NOT_PROCESS"

    def test_model_validate_model_dump_round_trip(self) -> None:
        """RuleSpec.model_validate(spec.model_dump()) preserves the predicate tree shape.

        model_dump() produces the Primitive union as a plain dict.
        The `when` field validator (_build_predicate_from_mapping) then reconstructs
        the tree by passing the dict through build_predicate — which requires the
        dump to carry the YAML-format one-key mapping, not the raw model fields.
        We therefore reconstruct using model_dump(mode='python') and pass
        the Primitive object directly, which _build_predicate_from_mapping
        lets through as-is.
        """
        spec = load_rule_spec_from_dict(ACC006_DICT)
        data = spec.model_dump()
        data["when"] = spec.when
        restored = RuleSpec.model_validate(data)
        assert restored.rule_id == spec.rule_id
        assert restored.step == spec.step
        assert restored.severity == spec.severity
        assert isinstance(restored.when, BooleanAnd)
        assert len(restored.when.children) == 2
        assert isinstance(restored.when.children[0], Contains)
        assert isinstance(restored.when.children[1], BooleanOr)
        assert restored.action.transition == spec.action.transition


# ---------------------------------------------------------------------------
# Slice 9 — load_rule_specs(directory)
# ---------------------------------------------------------------------------


class TestLoadRuleSpecs:
    def test_loads_two_valid_yaml_files(self, tmp_path: Path) -> None:
        (tmp_path / "ACC-001.yaml").write_text(ACC001_YAML)
        (tmp_path / "ACC-002.yaml").write_text(ACC002_YAML)
        specs = load_rule_specs(tmp_path)
        assert len(specs) == 2
        ids = {s.rule_id for s in specs}
        assert ids == {"ACC-001", "ACC-002"}

    def test_ignores_non_yaml_files(self, tmp_path: Path) -> None:
        (tmp_path / "ACC-001.yaml").write_text(ACC001_YAML)
        (tmp_path / "README.txt").write_text("not a rule")
        (tmp_path / "notes.json").write_text("{}")
        specs = load_rule_specs(tmp_path)
        assert len(specs) == 1

    def test_accepts_yml_extension(self, tmp_path: Path) -> None:
        (tmp_path / "ACC-001.yml").write_text(ACC001_YAML)
        specs = load_rule_specs(tmp_path)
        assert len(specs) == 1
        assert specs[0].rule_id == "ACC-001"

    def test_invalid_yaml_raises_with_filename(self, tmp_path: Path) -> None:
        (tmp_path / "bad.yaml").write_text(INVALID_YAML)
        with pytest.raises(ValueError, match="bad.yaml"):
            load_rule_specs(tmp_path)

    def test_invalid_yaml_preserves_problem_mark(self, tmp_path: Path) -> None:
        (tmp_path / "bad.yaml").write_text(INVALID_YAML)
        with pytest.raises(ValueError) as exc_info:
            load_rule_specs(tmp_path)
        cause = exc_info.value.__cause__
        assert cause is not None
        assert hasattr(cause, "problem_mark")

    def test_empty_yaml_file_raises_value_error(self, tmp_path: Path) -> None:
        (tmp_path / "empty.yaml").write_text("")
        with pytest.raises(ValueError, match="expected a YAML mapping"):
            load_rule_specs(tmp_path)

    def test_yaml_file_with_only_null_raises_value_error(self, tmp_path: Path) -> None:
        (tmp_path / "null.yaml").write_text("---\n")
        with pytest.raises(ValueError, match="expected a YAML mapping"):
            load_rule_specs(tmp_path)

    def test_yaml_file_that_is_a_list_raises_value_error(self, tmp_path: Path) -> None:
        (tmp_path / "list.yaml").write_text("- item1\n- item2\n")
        with pytest.raises(ValueError, match="expected a YAML mapping"):
            load_rule_specs(tmp_path)

    def test_missing_directory_raises_value_error(self, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist"
        with pytest.raises(ValueError, match="does_not_exist"):
            load_rule_specs(missing)

    def test_non_directory_path_raises_value_error(self, tmp_path: Path) -> None:
        not_a_dir = tmp_path / "a_file.txt"
        not_a_dir.write_text("hello")
        with pytest.raises(ValueError, match="a_file.txt"):
            load_rule_specs(not_a_dir)

    def test_unexpected_attribute_error_from_validator_escapes_loader(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An AttributeError from a buggy validator must escape, not be reframed."""
        (tmp_path / "ACC-001.yaml").write_text(ACC001_YAML)

        from samantha_server.rules import loader as loader_mod

        def _buggy_load(data: object) -> object:
            raise AttributeError("buggy validator")

        monkeypatch.setattr(loader_mod, "load_rule_spec_from_dict", _buggy_load)
        with pytest.raises(AttributeError, match="buggy validator"):
            load_rule_specs(tmp_path)

    def test_empty_directory_returns_empty_list(self, tmp_path: Path) -> None:
        specs = load_rule_specs(tmp_path)
        assert specs == []

    def test_invalid_spec_raises_with_filename(self, tmp_path: Path) -> None:
        broken = textwrap.dedent("""\
            rule_id: ACC-BAD
            step: NOT_A_REAL_STEP
            applies_at: null
            event_type: order_received
            severity: REJECT
            priority: null
            when:
              is_null:
                field: patient_name
            action:
              transition: DO_NOT_PROCESS
              set_flags: []
              clear_flags: []
              outcome: test
              panel: null
              branch: null
            source: test
        """)
        (tmp_path / "ACC-BAD.yaml").write_text(broken)
        with pytest.raises(Exception, match="ACC-BAD.yaml"):
            load_rule_specs(tmp_path)


# ---------------------------------------------------------------------------
# Slice 10 — Duplicate rule_id rejected
# ---------------------------------------------------------------------------


class TestDuplicateRuleId:
    def test_duplicate_rule_id_in_two_files_raises(self, tmp_path: Path) -> None:
        (tmp_path / "ACC-001a.yaml").write_text(ACC001_YAML)
        (tmp_path / "ACC-001b.yaml").write_text(ACC001_YAML)
        with pytest.raises(ValueError, match=r"first seen in ACC-001a\.yaml"):
            load_rule_specs(tmp_path)

    def test_unique_rule_ids_do_not_raise(self, tmp_path: Path) -> None:
        (tmp_path / "ACC-001.yaml").write_text(ACC001_YAML)
        (tmp_path / "ACC-002.yaml").write_text(ACC002_YAML)
        specs = load_rule_specs(tmp_path)
        assert len(specs) == 2


# ---------------------------------------------------------------------------
# Slice 11 — Pass-1 validation: missing required keys raise with filename
# ---------------------------------------------------------------------------

_MISSING_WHEN_YAML = textwrap.dedent("""\
    rule_id: ACC-NOWHEN
    step: ACCESSIONING
    applies_at: null
    event_type: order_received
    severity: HOLD
    priority: null
    action:
      transition: MISSING_INFO_HOLD
      set_flags: []
      clear_flags: []
      outcome: held_missing_patient_name
      panel: null
      branch: null
    source: test
""")

_MISSING_RULE_ID_YAML = textwrap.dedent("""\
    step: ACCESSIONING
    applies_at: null
    event_type: order_received
    severity: HOLD
    priority: null
    when:
      is_null:
        field: patient_name
    action:
      transition: MISSING_INFO_HOLD
      set_flags: []
      clear_flags: []
      outcome: held_missing_patient_name
      panel: null
      branch: null
    source: test
""")

_EMPTY_RULE_ID_YAML = textwrap.dedent("""\
    rule_id: "   "
    step: ACCESSIONING
    applies_at: null
    event_type: order_received
    severity: HOLD
    priority: null
    when:
      is_null:
        field: patient_name
    action:
      transition: MISSING_INFO_HOLD
      set_flags: []
      clear_flags: []
      outcome: held_missing_patient_name
      panel: null
      branch: null
    source: test
""")


class TestPassOneValidation:
    def test_missing_when_raises_value_error(self, tmp_path: Path) -> None:
        (tmp_path / "ACC-NOWHEN.yaml").write_text(_MISSING_WHEN_YAML)
        with pytest.raises(ValueError, match="ACC-NOWHEN.yaml"):
            load_rule_specs(tmp_path)

    def test_missing_when_message_names_key(self, tmp_path: Path) -> None:
        (tmp_path / "ACC-NOWHEN.yaml").write_text(_MISSING_WHEN_YAML)
        with pytest.raises(ValueError, match="when"):
            load_rule_specs(tmp_path)

    def test_missing_rule_id_raises_value_error(self, tmp_path: Path) -> None:
        (tmp_path / "no-rule-id.yaml").write_text(_MISSING_RULE_ID_YAML)
        with pytest.raises(ValueError, match="no-rule-id.yaml"):
            load_rule_specs(tmp_path)

    def test_missing_rule_id_message_names_key(self, tmp_path: Path) -> None:
        (tmp_path / "no-rule-id.yaml").write_text(_MISSING_RULE_ID_YAML)
        with pytest.raises(ValueError, match="rule_id"):
            load_rule_specs(tmp_path)

    def test_empty_rule_id_raises_value_error(self, tmp_path: Path) -> None:
        (tmp_path / "empty-rule-id.yaml").write_text(_EMPTY_RULE_ID_YAML)
        with pytest.raises(ValueError, match="empty-rule-id.yaml"):
            load_rule_specs(tmp_path)

    def test_empty_rule_id_message_names_key(self, tmp_path: Path) -> None:
        (tmp_path / "empty-rule-id.yaml").write_text(_EMPTY_RULE_ID_YAML)
        with pytest.raises(ValueError, match="rule_id"):
            load_rule_specs(tmp_path)


# ---------------------------------------------------------------------------
# Slice 1 — load_candidate_rule_spec: no rule_refs
# ---------------------------------------------------------------------------

_REAL_SPECS_DIR = Path(__file__).parent.parent.parent / "samantha_server" / "rules" / "specs"

_CANDIDATE_NO_RULE_REF_YAML = textwrap.dedent("""\
    rule_id: ACC-900
    step: ACCESSIONING
    applies_at: null
    event_type: order_received
    severity: HOLD
    priority: null
    when:
      is_null:
        field: patient_name
    action:
      transition: MISSING_INFO_HOLD
      set_flags: []
      clear_flags: []
      outcome: held_missing_patient_name
      panel: null
      branch: null
    source: test/candidate
""")


class TestLoadCandidateRuleSpec:
    def test_returns_rule_spec_with_correct_rule_id(self, tmp_path: Path) -> None:
        candidate = tmp_path / "ACC-900.yaml"
        candidate.write_text(_CANDIDATE_NO_RULE_REF_YAML)
        spec = load_candidate_rule_spec(candidate, _REAL_SPECS_DIR)
        assert spec.rule_id == "ACC-900"
        assert isinstance(spec, RuleSpec)


# ---------------------------------------------------------------------------
# Slice 2 — load_candidate_rule_spec: with rule_ref resolved
# ---------------------------------------------------------------------------

_CANDIDATE_WITH_RULE_REF_YAML = textwrap.dedent("""\
    rule_id: ACC-901
    step: ACCESSIONING
    applies_at: null
    event_type: order_received
    severity: HOLD
    priority: null
    when:
      rule_ref: ACC-001
    action:
      transition: MISSING_INFO_HOLD
      set_flags: []
      clear_flags: []
      outcome: held_missing_patient_name
      panel: null
      branch: null
    source: test/candidate-with-rule-ref
""")


class TestLoadCandidateRuleSpecWithRuleRef:
    def test_rule_ref_is_resolved_to_isnull_predicate(self, tmp_path: Path) -> None:
        from samantha_server.primitives import IsNull

        candidate = tmp_path / "ACC-901.yaml"
        candidate.write_text(_CANDIDATE_WITH_RULE_REF_YAML)
        spec = load_candidate_rule_spec(candidate, _REAL_SPECS_DIR)
        assert isinstance(spec.when, IsNull)
        assert spec.when.field == "patient_name"


# ---------------------------------------------------------------------------
# Slice — load_candidate_rule_spec: rule_id collision guard
# ---------------------------------------------------------------------------

_CANDIDATE_COLLIDING_RULE_ID_YAML = textwrap.dedent("""\
    rule_id: ACC-001
    step: ACCESSIONING
    applies_at: null
    event_type: order_received
    severity: HOLD
    priority: null
    when:
      is_null:
        field: patient_name
    action:
      transition: MISSING_INFO_HOLD
      set_flags: []
      clear_flags: []
      outcome: held_missing_patient_name
      panel: null
      branch: null
    source: test/candidate-collision
""")

_CANDIDATE_UNKNOWN_RULE_REF_YAML = textwrap.dedent("""\
    rule_id: ACC-902
    step: ACCESSIONING
    applies_at: null
    event_type: order_received
    severity: HOLD
    priority: null
    when:
      rule_ref: ACC-NONEXISTENT
    action:
      transition: MISSING_INFO_HOLD
      set_flags: []
      clear_flags: []
      outcome: held_missing_patient_name
      panel: null
      branch: null
    source: test/candidate-unknown-ref
""")


class TestLoadCandidateRuleSpecCollisionGuard:
    def test_load_candidate_rule_spec_raises_on_rule_id_collision(self, tmp_path: Path) -> None:
        """Candidate with rule_id matching an existing spec must raise ValueError."""
        candidate = tmp_path / "ACC-001-modified.yaml"
        candidate.write_text(_CANDIDATE_COLLIDING_RULE_ID_YAML)
        with pytest.raises(ValueError) as exc_info:
            load_candidate_rule_spec(candidate, _REAL_SPECS_DIR)
        msg = str(exc_info.value)
        assert "ACC-001-modified.yaml" in msg
        assert "ACC-001" in msg

    def test_load_candidate_rule_spec_raises_on_unknown_rule_ref(self, tmp_path: Path) -> None:
        """Candidate referencing an unknown rule_id via rule_ref must raise ValueError."""
        candidate = tmp_path / "ACC-902.yaml"
        candidate.write_text(_CANDIDATE_UNKNOWN_RULE_REF_YAML)
        with pytest.raises(ValueError, match="ACC-NONEXISTENT"):
            load_candidate_rule_spec(candidate, _REAL_SPECS_DIR)


# ---------------------------------------------------------------------------
# Fix 1 — loader._SEVERITY_ORDER IS dispatch_constants.SEVERITY_ORDER
# ---------------------------------------------------------------------------


class TestSeverityOrderSingleSource:
    def test_loader_severity_order_is_dispatch_constants_severity_order(self) -> None:
        """The alias _SEVERITY_ORDER is removed; loader now
        references SEVERITY_ORDER from dispatch_constants directly.  Assert identity
        via the module's public name to confirm no rebinding occurs at import time.
        """
        from samantha_server.engine import dispatch_constants
        from samantha_server.rules import loader as loader_mod

        assert loader_mod.SEVERITY_ORDER is dispatch_constants.SEVERITY_ORDER


class TestRuleIndexContainment:
    """PR205 review #3: RuleIndex.__contains__ is O(1) via internal frozenset."""

    def test_known_rule_id_returns_true(self) -> None:
        from samantha_server.rules.loader import RuleIndex, load_rule_specs

        index = RuleIndex(load_rule_specs(_REAL_SPECS_DIR))
        # Pick any known rule_id from the real corpus.
        known_id = index.all_rules[0].rule_id
        assert known_id in index

    def test_unknown_rule_id_returns_false(self) -> None:
        from samantha_server.rules.loader import RuleIndex, load_rule_specs

        index = RuleIndex(load_rule_specs(_REAL_SPECS_DIR))
        assert "DOES-NOT-EXIST-999" not in index

    def test_empty_index_returns_false_for_any_id(self) -> None:
        from samantha_server.rules.loader import RuleIndex

        index = RuleIndex([])
        assert "ACC-001" not in index
