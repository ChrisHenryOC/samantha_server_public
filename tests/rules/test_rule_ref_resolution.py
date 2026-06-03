"""Tests for rule_ref resolution — the two-pass loader that inlines referenced predicates.

A1: Happy path — rule_ref nodes are replaced by the referenced rule's when predicate.
A2: Unknown rule_id raises ValueError naming the missing id and offending file.
A3: Cycle detection raises ValueError naming both rule_ids and the word "cycle".
A4: Non-string rule_ref payload raises ValueError.
A5: Transitive resolution (A → B → C) inlines the terminal predicate.
A6: Diamond resolution (A → C and B → C) loads without cycle error.
A7: Null rule_ref payload raises ValueError.
A8: Sibling key alongside rule_ref propagates to build_predicate's "exactly one key" check.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from samantha_server.primitives import BooleanOr, Equals, IsNull, Not
from samantha_server.rules.loader import load_rule_specs

# ---------------------------------------------------------------------------
# Shared YAML helpers
# ---------------------------------------------------------------------------

ACC001_YAML = textwrap.dedent("""\
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
    source: knowledge_base/sops/accessioning.md#3.1
""")

META001_YAML = textwrap.dedent("""\
    rule_id: META-001
    step: ACCESSIONING
    applies_at: null
    event_type: order_received
    severity: ACCEPT
    priority: null
    when:
      not:
        boolean_or:
          - rule_ref: ACC-001
          - equals:
              field: sex
              value: null
    action:
      transition: ACCEPTED
      set_flags: []
      clear_flags: []
      outcome: accessioning_validations_passed
      panel: null
      branch: null
    source: knowledge_base/sops/accessioning.md#3.4
""")


def _make_atomic_rule(rule_id: str, field: str = "patient_name") -> str:
    """Return a minimal valid YAML rule with an IsNull when predicate."""
    return textwrap.dedent(f"""\
        rule_id: {rule_id}
        step: ACCESSIONING
        applies_at: null
        event_type: order_received
        severity: HOLD
        priority: null
        when:
          is_null:
            field: {field}
        action:
          transition: MISSING_INFO_HOLD
          set_flags: []
          clear_flags: []
          outcome: held_placeholder
          panel: null
          branch: null
        source: test
    """)


def _make_rule_with_ref(rule_id: str, ref_id: str) -> str:
    """Return a minimal valid YAML rule whose when is a single rule_ref."""
    return textwrap.dedent(f"""\
        rule_id: {rule_id}
        step: ACCESSIONING
        applies_at: null
        event_type: order_received
        severity: ACCEPT
        priority: null
        when:
          rule_ref: {ref_id}
        action:
          transition: ACCEPTED
          set_flags: []
          clear_flags: []
          outcome: accessioning_validations_passed
          panel: null
          branch: null
        source: test
    """)


# ---------------------------------------------------------------------------
# A1 — Happy path: rule_ref is resolved by inlining the referenced when predicate
# ---------------------------------------------------------------------------


class TestRuleRefResolutionHappyPath:
    def test_load_returns_two_specs(self, tmp_path: Path) -> None:
        (tmp_path / "ACC-001.yaml").write_text(ACC001_YAML)
        (tmp_path / "META-001.yaml").write_text(META001_YAML)
        specs = load_rule_specs(tmp_path)
        assert len(specs) == 2

    def test_meta001_when_is_not_wrapping_boolean_or(self, tmp_path: Path) -> None:
        (tmp_path / "ACC-001.yaml").write_text(ACC001_YAML)
        (tmp_path / "META-001.yaml").write_text(META001_YAML)
        specs = load_rule_specs(tmp_path)
        meta = next(s for s in specs if s.rule_id == "META-001")
        assert isinstance(meta.when, Not)
        assert isinstance(meta.when.child, BooleanOr)

    def test_meta001_boolean_or_has_two_children(self, tmp_path: Path) -> None:
        (tmp_path / "ACC-001.yaml").write_text(ACC001_YAML)
        (tmp_path / "META-001.yaml").write_text(META001_YAML)
        specs = load_rule_specs(tmp_path)
        meta = next(s for s in specs if s.rule_id == "META-001")
        or_node = meta.when.child  # type: ignore[union-attr]
        assert isinstance(or_node, BooleanOr)
        assert len(or_node.children) == 2

    def test_meta001_first_child_is_acc001_predicate(self, tmp_path: Path) -> None:
        """The rule_ref: ACC-001 node is replaced by ACC-001's is_null predicate."""
        (tmp_path / "ACC-001.yaml").write_text(ACC001_YAML)
        (tmp_path / "META-001.yaml").write_text(META001_YAML)
        specs = load_rule_specs(tmp_path)
        meta = next(s for s in specs if s.rule_id == "META-001")
        or_node = meta.when.child  # type: ignore[union-attr]
        first_child = or_node.children[0]  # type: ignore[union-attr]
        assert isinstance(first_child, IsNull)
        assert first_child.field == "patient_name"

    def test_meta001_second_child_is_equals(self, tmp_path: Path) -> None:
        (tmp_path / "ACC-001.yaml").write_text(ACC001_YAML)
        (tmp_path / "META-001.yaml").write_text(META001_YAML)
        specs = load_rule_specs(tmp_path)
        meta = next(s for s in specs if s.rule_id == "META-001")
        or_node = meta.when.child  # type: ignore[union-attr]
        second_child = or_node.children[1]  # type: ignore[union-attr]
        assert isinstance(second_child, Equals)
        assert second_child.field == "sex"
        assert second_child.value is None

    def test_acc001_itself_is_unchanged(self, tmp_path: Path) -> None:
        """Resolving rule_ref in META-001 must not mutate ACC-001's when predicate."""
        (tmp_path / "ACC-001.yaml").write_text(ACC001_YAML)
        (tmp_path / "META-001.yaml").write_text(META001_YAML)
        specs = load_rule_specs(tmp_path)
        acc001 = next(s for s in specs if s.rule_id == "ACC-001")
        assert isinstance(acc001.when, IsNull)
        assert acc001.when.field == "patient_name"


# ---------------------------------------------------------------------------
# A2 — Unknown rule_id raises ValueError naming the missing id and offending file
# ---------------------------------------------------------------------------


class TestUnknownRuleRef:
    def test_unknown_rule_ref_raises_value_error(self, tmp_path: Path) -> None:
        yaml_with_missing_ref = textwrap.dedent("""\
            rule_id: ACC-999
            step: ACCESSIONING
            applies_at: null
            event_type: order_received
            severity: ACCEPT
            priority: null
            when:
              rule_ref: NONEXISTENT-001
            action:
              transition: ACCEPTED
              set_flags: []
              clear_flags: []
              outcome: accessioning_validations_passed
              panel: null
              branch: null
            source: knowledge_base/sops/accessioning.md#3.4
        """)
        (tmp_path / "ACC-999.yaml").write_text(yaml_with_missing_ref)
        with pytest.raises(ValueError, match="NONEXISTENT-001"):
            load_rule_specs(tmp_path)

    def test_unknown_rule_ref_message_names_offending_file(self, tmp_path: Path) -> None:
        yaml_with_missing_ref = textwrap.dedent("""\
            rule_id: ACC-999
            step: ACCESSIONING
            applies_at: null
            event_type: order_received
            severity: ACCEPT
            priority: null
            when:
              rule_ref: NONEXISTENT-001
            action:
              transition: ACCEPTED
              set_flags: []
              clear_flags: []
              outcome: accessioning_validations_passed
              panel: null
              branch: null
            source: knowledge_base/sops/accessioning.md#3.4
        """)
        (tmp_path / "ACC-999.yaml").write_text(yaml_with_missing_ref)
        with pytest.raises(ValueError, match="ACC-999.yaml"):
            load_rule_specs(tmp_path)


# ---------------------------------------------------------------------------
# A3 — Cycle detection raises ValueError with both rule_ids and "cycle"
# ---------------------------------------------------------------------------


class TestCycleDetection:
    def test_mutual_cycle_raises_value_error(self, tmp_path: Path) -> None:
        (tmp_path / "A.yaml").write_text(_make_rule_with_ref("A", "B"))
        (tmp_path / "B.yaml").write_text(_make_rule_with_ref("B", "A"))
        with pytest.raises(ValueError, match="cycle"):
            load_rule_specs(tmp_path)

    def test_self_cycle_raises_value_error(self, tmp_path: Path) -> None:
        (tmp_path / "A.yaml").write_text(_make_rule_with_ref("A", "A"))
        with pytest.raises(ValueError, match="cycle"):
            load_rule_specs(tmp_path)

    def test_mutual_cycle_message_names_both_rule_ids(self, tmp_path: Path) -> None:
        """Cycle message must contain both A and B in traversal order (A -> B -> A)."""
        (tmp_path / "A.yaml").write_text(_make_rule_with_ref("A", "B"))
        (tmp_path / "B.yaml").write_text(_make_rule_with_ref("B", "A"))
        with pytest.raises(ValueError, match=r"A.*B.*A"):
            load_rule_specs(tmp_path)

    def test_self_cycle_message_names_rule_id(self, tmp_path: Path) -> None:
        """Self-cycle message must contain the rule_id twice (A -> A)."""
        (tmp_path / "A.yaml").write_text(_make_rule_with_ref("A", "A"))
        with pytest.raises(ValueError, match=r"A.*->.*A"):
            load_rule_specs(tmp_path)


# ---------------------------------------------------------------------------
# A4 — Non-string rule_ref payload raises ValueError
# ---------------------------------------------------------------------------


class TestRuleRefTypeGuard:
    def test_integer_rule_ref_raises_value_error(self, tmp_path: Path) -> None:
        yaml_with_int_ref = textwrap.dedent("""\
            rule_id: ACC-999
            step: ACCESSIONING
            applies_at: null
            event_type: order_received
            severity: ACCEPT
            priority: null
            when:
              rule_ref: 42
            action:
              transition: ACCEPTED
              set_flags: []
              clear_flags: []
              outcome: accessioning_validations_passed
              panel: null
              branch: null
            source: test
        """)
        (tmp_path / "ACC-999.yaml").write_text(yaml_with_int_ref)
        with pytest.raises(ValueError, match=r"requires a string rule_id.*got 'int'"):
            load_rule_specs(tmp_path)

    def test_mapping_rule_ref_raises_value_error(self, tmp_path: Path) -> None:
        yaml_with_map_ref = textwrap.dedent("""\
            rule_id: ACC-999
            step: ACCESSIONING
            applies_at: null
            event_type: order_received
            severity: ACCEPT
            priority: null
            when:
              rule_ref:
                foo: bar
            action:
              transition: ACCEPTED
              set_flags: []
              clear_flags: []
              outcome: accessioning_validations_passed
              panel: null
              branch: null
            source: test
        """)
        (tmp_path / "ACC-999.yaml").write_text(yaml_with_map_ref)
        with pytest.raises(ValueError, match=r"requires a string rule_id.*got 'dict'"):
            load_rule_specs(tmp_path)


# ---------------------------------------------------------------------------
# A5 — Transitive resolution (A → B → C): A's resolved when equals C's predicate
# ---------------------------------------------------------------------------


class TestTransitiveResolution:
    def test_transitive_ref_resolves_to_terminal_predicate(self, tmp_path: Path) -> None:
        """A → B → C: after load, A's when must be the inlined C predicate."""
        (tmp_path / "C.yaml").write_text(_make_atomic_rule("C", field="patient_name"))
        (tmp_path / "B.yaml").write_text(_make_rule_with_ref("B", "C"))
        (tmp_path / "A.yaml").write_text(_make_rule_with_ref("A", "B"))
        specs = load_rule_specs(tmp_path)
        rule_a = next(s for s in specs if s.rule_id == "A")
        assert isinstance(rule_a.when, IsNull)
        assert rule_a.when.field == "patient_name"

    def test_transitive_ref_all_three_loaded(self, tmp_path: Path) -> None:
        (tmp_path / "C.yaml").write_text(_make_atomic_rule("C", field="patient_name"))
        (tmp_path / "B.yaml").write_text(_make_rule_with_ref("B", "C"))
        (tmp_path / "A.yaml").write_text(_make_rule_with_ref("A", "B"))
        specs = load_rule_specs(tmp_path)
        assert len(specs) == 3


# ---------------------------------------------------------------------------
# A6 — Diamond resolution (A → C and B → C): no false cycle error
# ---------------------------------------------------------------------------


class TestDiamondResolution:
    def test_diamond_ref_loads_without_error(self, tmp_path: Path) -> None:
        """A → C and B → C must load successfully — no cycle error."""
        (tmp_path / "C.yaml").write_text(_make_atomic_rule("C", field="patient_name"))
        (tmp_path / "A.yaml").write_text(_make_rule_with_ref("A", "C"))
        (tmp_path / "B.yaml").write_text(_make_rule_with_ref("B", "C"))
        specs = load_rule_specs(tmp_path)
        assert len(specs) == 3

    def test_diamond_ref_both_resolve_to_same_predicate_shape(self, tmp_path: Path) -> None:
        """Both A and B must inline C's predicate correctly."""
        (tmp_path / "C.yaml").write_text(_make_atomic_rule("C", field="patient_name"))
        (tmp_path / "A.yaml").write_text(_make_rule_with_ref("A", "C"))
        (tmp_path / "B.yaml").write_text(_make_rule_with_ref("B", "C"))
        specs = load_rule_specs(tmp_path)
        rule_a = next(s for s in specs if s.rule_id == "A")
        rule_b = next(s for s in specs if s.rule_id == "B")
        assert isinstance(rule_a.when, IsNull)
        assert rule_a.when.field == "patient_name"
        assert isinstance(rule_b.when, IsNull)
        assert rule_b.when.field == "patient_name"
        # Must be independent objects (deep-copy guarantee).
        assert rule_a.when is not rule_b.when


# ---------------------------------------------------------------------------
# A7 — Null rule_ref payload raises ValueError
# ---------------------------------------------------------------------------


class TestNullRuleRef:
    def test_null_rule_ref_raises_value_error(self, tmp_path: Path) -> None:
        """rule_ref: ~ (YAML null) must raise ValueError with type attribution."""
        yaml_with_null_ref = textwrap.dedent("""\
            rule_id: ACC-999
            step: ACCESSIONING
            applies_at: null
            event_type: order_received
            severity: ACCEPT
            priority: null
            when:
              rule_ref: ~
            action:
              transition: ACCEPTED
              set_flags: []
              clear_flags: []
              outcome: accessioning_validations_passed
              panel: null
              branch: null
            source: test
        """)
        (tmp_path / "ACC-999.yaml").write_text(yaml_with_null_ref)
        with pytest.raises(ValueError, match=r"requires a string rule_id.*got 'NoneType'"):
            load_rule_specs(tmp_path)


# ---------------------------------------------------------------------------
# A8 — Sibling key alongside rule_ref delegates to build_predicate's one-key check
# ---------------------------------------------------------------------------


class TestSiblingKeyRuleRef:
    def test_sibling_key_raises_exactly_one_key_error(self, tmp_path: Path) -> None:
        """A node {rule_ref: X, extra: foo} must raise ValueError matching 'exactly one key'.

        The two-key dict bypasses _substitute_rule_refs's single-key check and
        falls through to build_predicate, which rejects anything that is not a
        one-key mapping.
        """
        yaml_with_sibling = textwrap.dedent("""\
            rule_id: ACC-999
            step: ACCESSIONING
            applies_at: null
            event_type: order_received
            severity: ACCEPT
            priority: null
            when:
              rule_ref: ACC-001
              extra: foo
            action:
              transition: ACCEPTED
              set_flags: []
              clear_flags: []
              outcome: accessioning_validations_passed
              panel: null
              branch: null
            source: test
        """)
        (tmp_path / "ACC-001.yaml").write_text(ACC001_YAML)
        (tmp_path / "ACC-999.yaml").write_text(yaml_with_sibling)
        with pytest.raises(ValueError, match=r"exactly one key"):
            load_rule_specs(tmp_path)
