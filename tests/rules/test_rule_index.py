"""Tests for RuleIndex — bucketing, sorting, and duplicate detection."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from samantha_server.rules.loader import RuleIndex, load_rule_spec_from_dict, load_rule_specs
from samantha_server.rules.spec import RuleSpec

_SPECS_DIR = Path(__file__).resolve().parents[2] / "samantha_server" / "rules" / "specs"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_spec(
    rule_id: str,
    step: str,
    severity: str | None = None,
    priority: int | None = None,
    applies_at: str | None = None,
) -> RuleSpec:
    data: dict[str, Any] = {
        "rule_id": rule_id,
        "step": step,
        "applies_at": applies_at,
        "event_type": "order_received",
        "severity": severity,
        "priority": priority,
        "when": {"is_null": {"field": "patient_name"}},
        "action": {
            "transition": "DO_NOT_PROCESS",
            "set_flags": [],
            "clear_flags": [],
            "outcome": "test_outcome",
            "panel": None,
            "branch": None,
        },
        "source": f"test#{rule_id}",
    }
    return load_rule_spec_from_dict(data)


# ---------------------------------------------------------------------------
# Slice 11 — RuleIndex
# ---------------------------------------------------------------------------


class TestRuleIndex:
    def test_rules_by_step_groups_correctly(self) -> None:
        specs = [
            _make_spec("ACC-001", "ACCESSIONING", severity="REJECT"),
            _make_spec("ACC-002", "ACCESSIONING", severity="HOLD"),
            _make_spec("SP-001", "SAMPLE_PREP", priority=1),
        ]
        index = RuleIndex(specs)
        assert len(index.rules_by_step["ACCESSIONING"]) == 2
        assert len(index.rules_by_step["SAMPLE_PREP"]) == 1
        assert "HE_QC" not in index.rules_by_step

    def test_accessioning_sorted_by_severity_hierarchy(self) -> None:
        specs = [
            _make_spec("ACC-103", "ACCESSIONING", severity="ACCEPT"),
            _make_spec("ACC-102", "ACCESSIONING", severity="PROCEED"),
            _make_spec("ACC-101", "ACCESSIONING", severity="HOLD"),
            _make_spec("ACC-100", "ACCESSIONING", severity="REJECT"),
        ]
        index = RuleIndex(specs)
        sorted_rules = index.rules_by_step["ACCESSIONING"]
        severities = [r.severity for r in sorted_rules]
        assert severities == ["REJECT", "HOLD", "PROCEED", "ACCEPT"]

    def test_non_accessioning_sorted_by_priority_ascending(self) -> None:
        specs = [
            _make_spec("SP-003", "SAMPLE_PREP", priority=3),
            _make_spec("SP-001", "SAMPLE_PREP", priority=1),
            _make_spec("SP-002", "SAMPLE_PREP", priority=2),
        ]
        index = RuleIndex(specs)
        sorted_rules = index.rules_by_step["SAMPLE_PREP"]
        priorities = [r.priority for r in sorted_rules]
        assert priorities == [1, 2, 3]

    def test_rules_by_applies_at_contains_ihc_rules(self) -> None:
        specs = [
            _make_spec("IHC-001", "IHC", priority=1, applies_at="IHC_STAINING"),
            _make_spec("IHC-002", "IHC", priority=2, applies_at="IHC_STAINING"),
            _make_spec("IHC-003", "IHC", priority=1, applies_at="IHC_READING"),
        ]
        index = RuleIndex(specs)
        assert len(index.rules_by_applies_at["IHC_STAINING"]) == 2
        assert len(index.rules_by_applies_at["IHC_READING"]) == 1

    def test_rules_by_applies_at_is_empty_when_no_ihc_rules(self) -> None:
        specs = [
            _make_spec("ACC-001", "ACCESSIONING", severity="REJECT"),
        ]
        index = RuleIndex(specs)
        assert index.rules_by_applies_at == {}

    def test_all_rules_returns_all_specs(self) -> None:
        specs = [
            _make_spec("ACC-001", "ACCESSIONING", severity="REJECT"),
            _make_spec("SP-001", "SAMPLE_PREP", priority=1),
            _make_spec("IHC-001", "IHC", priority=1, applies_at="IHC_STAINING"),
        ]
        index = RuleIndex(specs)
        assert len(index.all_rules) == 3

    def test_duplicate_rule_id_raises(self) -> None:
        specs = [
            _make_spec("ACC-001", "ACCESSIONING", severity="REJECT"),
            _make_spec("ACC-001", "ACCESSIONING", severity="HOLD"),
        ]
        with pytest.raises(ValueError, match="ACC-001"):
            RuleIndex(specs)

    def test_ihc_rules_by_applies_at_sorted_by_priority(self) -> None:
        specs = [
            _make_spec("IHC-003", "IHC", priority=3, applies_at="IHC_STAINING"),
            _make_spec("IHC-001", "IHC", priority=1, applies_at="IHC_STAINING"),
            _make_spec("IHC-002", "IHC", priority=2, applies_at="IHC_STAINING"),
        ]
        index = RuleIndex(specs)
        priorities = [r.priority for r in index.rules_by_applies_at["IHC_STAINING"]]
        assert priorities == [1, 2, 3]

    def test_empty_spec_list_creates_empty_index(self) -> None:
        index = RuleIndex([])
        assert index.rules_by_step == {}
        assert index.rules_by_applies_at == {}
        assert index.all_rules == []

    def test_single_non_accessioning_rule_index(self) -> None:
        specs = [_make_spec("SP-001", "SAMPLE_PREP", priority=1)]
        index = RuleIndex(specs)
        assert len(index.rules_by_step["SAMPLE_PREP"]) == 1
        assert index.rules_by_step["SAMPLE_PREP"][0].rule_id == "SP-001"
        assert index.rules_by_applies_at == {}

    def test_applies_at_rule_under_non_ihc_step_raises(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            _make_spec("ACC-001", "ACCESSIONING", severity="REJECT", applies_at="IHC_STAINING")

    def test_duplicate_priority_in_step_bucket_raises(self) -> None:
        """Two non-IHC, non-ACCESSIONING rules in the same step with the same priority
        would have filesystem-/insertion-order-dependent dispatch order — flag at index
        construction time and name both rule_ids in the error message."""
        specs = [
            _make_spec("SP-001", "SAMPLE_PREP", priority=1),
            _make_spec("SP-007", "SAMPLE_PREP", priority=1),
        ]
        with pytest.raises(ValueError, match="priority 1") as exc_info:
            RuleIndex(specs)
        msg = str(exc_info.value)
        assert "SP-001" in msg
        assert "SP-007" in msg
        assert "SAMPLE_PREP" in msg

    def test_duplicate_priority_in_applies_at_bucket_raises(self) -> None:
        """Two IHC rules sharing applies_at and priority — same silent-collision risk as
        the by_step case; the dispatcher uses the by_applies_at bucket for IHC."""
        specs = [
            _make_spec("IHC-001", "IHC", priority=1, applies_at="IHC_STAINING"),
            _make_spec("IHC-002", "IHC", priority=1, applies_at="IHC_STAINING"),
        ]
        with pytest.raises(ValueError, match="priority 1") as exc_info:
            RuleIndex(specs)
        msg = str(exc_info.value)
        assert "IHC-001" in msg
        assert "IHC-002" in msg
        assert "IHC_STAINING" in msg

    def test_ihc_rules_can_share_priority_across_applies_at(self) -> None:
        """IHC rules with the same priority but different applies_at must not trigger the
        guard — they go to different dispatch buckets, so no collision exists."""
        specs = [
            _make_spec("IHC-001", "IHC", priority=1, applies_at="IHC_STAINING"),
            _make_spec("IHC-006", "IHC", priority=1, applies_at="IHC_SCORING"),
        ]
        index = RuleIndex(specs)  # must not raise
        assert len(index.rules_by_applies_at["IHC_STAINING"]) == 1
        assert len(index.rules_by_applies_at["IHC_SCORING"]) == 1

    def test_ihc_step_omitted_from_rules_by_step(self) -> None:
        """IHC dispatches via rules_by_applies_at; the by_step["IHC"] bucket is unused
        and would silently merge cross-applies_at rules with by-design priority overlap.
        Construction omits the IHC entry so future iteration of rules_by_step can't
        silently observe the merged list."""
        specs = [
            _make_spec("IHC-001", "IHC", priority=1, applies_at="IHC_STAINING"),
            _make_spec("IHC-006", "IHC", priority=1, applies_at="IHC_SCORING"),
        ]
        index = RuleIndex(specs)
        assert "IHC" not in index.rules_by_step
        # IHC rules still appear via the dispatcher's path.
        assert len(index.rules_by_applies_at["IHC_STAINING"]) == 1
        assert len(index.rules_by_applies_at["IHC_SCORING"]) == 1
        # all_rules still carries them.
        assert {r.rule_id for r in index.all_rules} == {"IHC-001", "IHC-006"}

    def test_accessioning_with_null_priorities_does_not_raise(self) -> None:
        """ACCESSIONING rules dispatch by severity, not priority; their priority field is
        always None per the Pydantic invariant. The duplicate-priority guard must skip
        the ACC bucket so two ACC rules with null priority don't trip a false positive.
        Pin this so a future refactor that moves the guard call site can't silently
        start raising on the real corpus."""
        specs = [
            _make_spec("ACC-001", "ACCESSIONING", severity="REJECT"),
            _make_spec("ACC-002", "ACCESSIONING", severity="HOLD"),
        ]
        index = RuleIndex(specs)  # must not raise
        assert len(index.rules_by_step["ACCESSIONING"]) == 2


# ---------------------------------------------------------------------------
# Fix 2 — RuleIndex invariant violations raise ValueError, not AssertionError
# ---------------------------------------------------------------------------


class TestRuleIndexInvariantRaisesValueError:
    def test_applies_at_on_non_ihc_step_raises_value_error(self) -> None:
        """RuleIndex must raise ValueError (not AssertionError) when
        a non-IHC rule carries applies_at.  Pydantic already blocks this at spec
        construction, so we inject a spec via object.__setattr__ to bypass the frozen
        model and test RuleIndex's own guard.
        """
        spec = _make_spec("SP-001", "SAMPLE_PREP", priority=1)
        # Bypass Pydantic frozen model to inject an invalid applies_at value
        object.__setattr__(spec, "applies_at", "IHC_STAINING")
        with pytest.raises(ValueError, match="applies_at"):
            RuleIndex([spec])

    def test_null_priority_on_non_acc_step_raises_value_error(self) -> None:
        """RuleIndex must raise ValueError (not AssertionError) when
        a non-ACCESSIONING rule has priority=None.  Pydantic blocks this at spec
        construction, so we inject it to test RuleIndex's own guard.
        """
        spec = _make_spec("SP-001", "SAMPLE_PREP", priority=1)
        object.__setattr__(spec, "priority", None)
        with pytest.raises(ValueError, match="priority"):
            RuleIndex([spec])


class TestRuleIndexInvariantsUnderOptimizedMode:
    def test_applies_at_invariant_enforced_under_optimize_flag(self) -> None:
        """Fix 2 (-O semantics): applies_at invariant must fire even when Python
        is run with -O (which strips assert statements).  Today it silently passes
        under -O because the guard is an `assert`.  After the fix it becomes an
        explicit if-raise ValueError so -O cannot suppress it.
        """
        import subprocess
        import sys
        import textwrap

        snippet = textwrap.dedent("""\
            from samantha_server.rules.loader import RuleIndex, load_rule_spec_from_dict
            spec = load_rule_spec_from_dict({
                'rule_id': 'SP-001', 'step': 'SAMPLE_PREP', 'applies_at': None,
                'event_type': 'order_received', 'severity': None, 'priority': 1,
                'when': {'is_null': {'field': 'patient_name'}},
                'action': {
                    'transition': 'DO_NOT_PROCESS', 'set_flags': [],
                    'clear_flags': [], 'outcome': 'test_outcome',
                    'panel': None, 'branch': None,
                },
                'source': 'test',
            })
            object.__setattr__(spec, 'applies_at', 'IHC_STAINING')
            try:
                RuleIndex([spec])
            except ValueError:
                print('ValueError')
            except AssertionError:
                print('AssertionError')
            else:
                print('no_error')
        """)
        result = subprocess.run(
            [sys.executable, "-O", "-c", snippet],
            capture_output=True,
            text=True,
        )
        # Assert returncode first so an import failure is diagnosable.
        assert result.returncode == 0, (
            f"Subprocess exited {result.returncode}; stderr: {result.stderr[:400]!r}"
        )
        output = result.stdout.strip()
        assert output == "ValueError", (
            f"Under -O, expected ValueError but got: {output!r} (stderr: {result.stderr[:200]!r})"
        )

    def test_null_priority_invariant_enforced_under_optimize_flag(self) -> None:
        """Fix 2 (-O semantics): priority=None invariant must fire even under -O."""
        import subprocess
        import sys
        import textwrap

        snippet = textwrap.dedent("""\
            from samantha_server.rules.loader import RuleIndex, load_rule_spec_from_dict
            spec = load_rule_spec_from_dict({
                'rule_id': 'SP-001', 'step': 'SAMPLE_PREP', 'applies_at': None,
                'event_type': 'order_received', 'severity': None, 'priority': 1,
                'when': {'is_null': {'field': 'patient_name'}},
                'action': {
                    'transition': 'DO_NOT_PROCESS', 'set_flags': [],
                    'clear_flags': [], 'outcome': 'test_outcome',
                    'panel': None, 'branch': None,
                },
                'source': 'test',
            })
            object.__setattr__(spec, 'priority', None)
            try:
                RuleIndex([spec])
            except ValueError:
                print('ValueError')
            except AssertionError:
                print('AssertionError')
            else:
                print('no_error')
        """)
        result = subprocess.run(
            [sys.executable, "-O", "-c", snippet],
            capture_output=True,
            text=True,
        )
        # Assert returncode first so an import failure is diagnosable.
        assert result.returncode == 0, (
            f"Subprocess exited {result.returncode}; stderr: {result.stderr[:400]!r}"
        )
        output = result.stdout.strip()
        assert output == "ValueError", (
            f"Under -O, expected ValueError but got: {output!r} (stderr: {result.stderr[:200]!r})"
        )


# ---------------------------------------------------------------------------
# RuleIndex.known_event_types caching
# (review fix #4)
# ---------------------------------------------------------------------------


def test_rule_index_known_event_types_matches_fresh_scan() -> None:
    """RuleIndex.known_event_types == get_known_event_types(index).

    Pins the byte-for-byte equivalence so the cached value is proven to be
    derived from the same algorithm as the original per-dispatch scan.
    """
    from samantha_server.engine.transitions import get_known_event_types

    specs = load_rule_specs(_SPECS_DIR)
    index = RuleIndex(specs)

    cached = index.known_event_types
    fresh = get_known_event_types(index)

    assert cached == fresh, (
        f"RuleIndex.known_event_types must equal get_known_event_types(index); "
        f"diff={cached.symmetric_difference(fresh)}"
    )


def test_rule_index_known_event_types_identity_across_calls() -> None:
    """RuleIndex.known_event_types returns the same object on each access.

    Identity (is) check confirms the frozenset is computed once and cached,
    not rebuilt on every access.
    """
    specs = load_rule_specs(_SPECS_DIR)
    index = RuleIndex(specs)

    first = index.known_event_types
    second = index.known_event_types

    assert first is second, (
        "RuleIndex.known_event_types must return the same object on repeated "
        "access (computed once in __init__, not rebuilt)"
    )
