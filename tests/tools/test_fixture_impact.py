"""Tests for samantha_server.tools.fixture_impact — fixture-window scanner."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from samantha_server.primitives import (
    BooleanAnd,
    BooleanOr,
    Equals,
    InEnum,
    IsNull,
    Not,
    Primitive,
    ThresholdGTE,
)
from samantha_server.rules.spec import RuleSpec
from samantha_server.tools.fixture_impact import Match, ScanResult, format_report, scan_fixtures

# ---------------------------------------------------------------------------
# Helpers — build a minimal RuleSpec in-memory without hitting the file system.
# ---------------------------------------------------------------------------


def _make_rule_spec(when: Primitive, rule_id: str = "ACC-900") -> RuleSpec:
    """Construct a minimal ACCESSIONING RuleSpec with the given predicate."""
    return RuleSpec(
        rule_id=rule_id,
        step="ACCESSIONING",
        applies_at=None,
        event_type=("order_received",),
        severity="HOLD",
        priority=None,
        when=when,
        action={
            "transition": "MISSING_INFO_HOLD",
            "set_flags": [],
            "clear_flags": [],
            "outcome": "held_missing_patient_name",
            "panel": None,
            "branch": None,
        },
        source="test",
    )


# ---------------------------------------------------------------------------
# Fixture: a minimal scenario JSON in a temp fixtures directory.
# ---------------------------------------------------------------------------

_SC_MATCHING = {
    "scenario_id": "SC-TEST-001",
    "category": "rule_coverage",
    "description": "Test scenario that matches a predicate",
    "events": [
        {
            "step": 1,
            "event_type": "order_received",
            "event_data": {
                "patient_name": None,
                "age": 40,
                "sex": "M",
                "specimen_type": "biopsy",
                "anatomic_site": "breast",
                "fixative": "formalin",
                "fixation_time_hours": 24.0,
                "ordered_tests": ["HER2"],
                "priority": "routine",
                "billing_info_present": True,
            },
            "expected_output": {
                "next_state": "MISSING_INFO_HOLD",
                "applied_rules": ["ACC-001"],
                "flags": [],
            },
        }
    ],
}

_SC_NON_MATCHING = {
    "scenario_id": "SC-TEST-002",
    "category": "rule_coverage",
    "description": "Test scenario that does not match",
    "events": [
        {
            "step": 1,
            "event_type": "order_received",
            "event_data": {
                "patient_name": "Smith, John",
                "age": 55,
                "sex": "M",
                "specimen_type": "biopsy",
                "anatomic_site": "breast",
                "fixative": "formalin",
                "fixation_time_hours": 24.0,
                "ordered_tests": [],
                "priority": "routine",
                "billing_info_present": True,
            },
            "expected_output": {
                "next_state": "ACCEPTED",
                "applied_rules": ["ACC-008"],
                "flags": [],
            },
        }
    ],
}


@pytest.fixture()
def fixtures_dir(tmp_path: Path) -> Path:
    """Build a minimal fixtures/scenarios directory with two scenarios."""
    cat_dir = tmp_path / "rule_coverage"
    cat_dir.mkdir(parents=True)
    (cat_dir / "sc-test-001.json").write_text(json.dumps(_SC_MATCHING))
    (cat_dir / "sc-test-002.json").write_text(json.dumps(_SC_NON_MATCHING))
    return tmp_path


# ---------------------------------------------------------------------------
# Slice 3 — scan_fixtures core matching
# ---------------------------------------------------------------------------


class TestScanFixturesCore:
    def test_matching_scenario_is_returned(self, fixtures_dir: Path) -> None:
        # IsNull(patient_name) is True for SC-TEST-001 (patient_name is None)
        rule = _make_rule_spec(IsNull(field="patient_name"))
        result = scan_fixtures(rule, fixtures_dir)
        scenario_ids = [m.scenario_id for m in result.matches]
        assert "SC-TEST-001" in scenario_ids

    def test_non_matching_scenario_not_returned(self, fixtures_dir: Path) -> None:
        rule = _make_rule_spec(IsNull(field="patient_name"))
        result = scan_fixtures(rule, fixtures_dir)
        scenario_ids = [m.scenario_id for m in result.matches]
        assert "SC-TEST-002" not in scenario_ids

    def test_returns_scan_result_with_match_objects(self, fixtures_dir: Path) -> None:
        rule = _make_rule_spec(IsNull(field="patient_name"))
        result = scan_fixtures(rule, fixtures_dir)
        assert isinstance(result, ScanResult)
        assert all(isinstance(m, Match) for m in result.matches)

    def test_returns_tuple_of_matches(self, fixtures_dir: Path) -> None:
        rule = _make_rule_spec(IsNull(field="patient_name"))
        result = scan_fixtures(rule, fixtures_dir)
        assert isinstance(result.matches, tuple)

    def test_missing_fixtures_dir_returns_empty_scan_result(self, tmp_path: Path) -> None:
        rule = _make_rule_spec(IsNull(field="patient_name"))
        result = scan_fixtures(rule, tmp_path / "nonexistent")
        assert isinstance(result, ScanResult)
        assert result.matches == ()
        assert result.skipped == ()


# ---------------------------------------------------------------------------
# Slice 4 — Match captures expected_applied_rules and true_clauses
# ---------------------------------------------------------------------------


class TestMatchContents:
    def test_match_contains_expected_applied_rules(self, fixtures_dir: Path) -> None:
        rule = _make_rule_spec(IsNull(field="patient_name"))
        result = scan_fixtures(rule, fixtures_dir)
        match = next(m for m in result.matches if m.scenario_id == "SC-TEST-001")
        assert match.expected_applied_rules == ("ACC-001",)

    def test_match_contains_category(self, fixtures_dir: Path) -> None:
        rule = _make_rule_spec(IsNull(field="patient_name"))
        result = scan_fixtures(rule, fixtures_dir)
        match = next(m for m in result.matches if m.scenario_id == "SC-TEST-001")
        assert match.category == "rule_coverage"

    def test_boolean_and_true_clauses_are_atomics_only(self, fixtures_dir: Path) -> None:
        # BooleanAnd(Equals(specimen_type, "biopsy"), ThresholdGTE(fixation_time_hours, 6.0))
        # Both clauses are true for SC-TEST-001 (specimen_type=biopsy, fixation_time=24h)
        rule = _make_rule_spec(
            BooleanAnd(
                children=(
                    Equals(field="specimen_type", value="biopsy"),
                    ThresholdGTE(field="fixation_time_hours", value=6.0),
                )
            )
        )
        result = scan_fixtures(rule, fixtures_dir)
        match = next(m for m in result.matches if m.scenario_id == "SC-TEST-001")
        # Both atomic clauses should appear; the BooleanAnd combinator itself should not.
        assert 'Equals(specimen_type, "biopsy")' in match.true_clauses
        assert "ThresholdGTE(fixation_time_hours, 6.0)" in match.true_clauses
        # BooleanAnd is not an atomic — it must not appear by name.
        assert not any("BooleanAnd" in c for c in match.true_clauses)

    def test_isnull_true_clause_has_no_value(self, fixtures_dir: Path) -> None:
        rule = _make_rule_spec(IsNull(field="patient_name"))
        result = scan_fixtures(rule, fixtures_dir)
        match = next(m for m in result.matches if m.scenario_id == "SC-TEST-001")
        assert match.true_clauses == ("IsNull(patient_name)",)


# ---------------------------------------------------------------------------
# Slice — Not(...) predicate true_clauses (High #3)
# ---------------------------------------------------------------------------


class TestNotPredicateTrueClauses:
    def test_not_isnull_match_has_nonempty_true_clauses(self, fixtures_dir: Path) -> None:
        # Not(IsNull(patient_name)) is True when patient_name is non-null.
        # SC-TEST-002 has patient_name = "Smith, John".
        rule = _make_rule_spec(Not(child=IsNull(field="patient_name")))
        result = scan_fixtures(rule, fixtures_dir)
        matched_ids = [m.scenario_id for m in result.matches]
        assert "SC-TEST-002" in matched_ids
        match = next(m for m in result.matches if m.scenario_id == "SC-TEST-002")
        # true_clauses must be non-empty and contain a meaningful representation
        assert len(match.true_clauses) > 0
        assert any("Not" in c for c in match.true_clauses)
        assert any("IsNull" in c for c in match.true_clauses)
        assert any("patient_name" in c for c in match.true_clauses)

    def test_not_isnull_match_clause_format(self, fixtures_dir: Path) -> None:
        rule = _make_rule_spec(Not(child=IsNull(field="patient_name")))
        result = scan_fixtures(rule, fixtures_dir)
        match = next(m for m in result.matches if m.scenario_id == "SC-TEST-002")
        assert "Not(IsNull(patient_name))" in match.true_clauses

    def test_not_predicate_no_match_when_field_is_null(self, fixtures_dir: Path) -> None:
        # SC-TEST-001 has patient_name = None -> IsNull is True -> Not is False -> no match
        rule = _make_rule_spec(Not(child=IsNull(field="patient_name")))
        result = scan_fixtures(rule, fixtures_dir)
        matched_ids = [m.scenario_id for m in result.matches]
        assert "SC-TEST-001" not in matched_ids


class TestBooleanOrTrueClauses:
    def test_boolean_or_short_circuit_trace_documents_first_true_only(
        self, fixtures_dir: Path
    ) -> None:
        # BooleanOr short-circuits on first true. SC-TEST-001 has specimen_type=biopsy
        # (first child true) and fixative=formalin (second child also true, but not traced).
        # This test verifies the current (short-circuit) behaviour of trace().
        rule = _make_rule_spec(
            BooleanOr(
                children=(
                    Equals(field="specimen_type", value="biopsy"),
                    Equals(field="fixative", value="formalin"),
                )
            )
        )
        result = scan_fixtures(rule, fixtures_dir)
        match = next(m for m in result.matches if m.scenario_id == "SC-TEST-001")
        # First atom is true — it appears in true_clauses.
        assert 'Equals(specimen_type, "biopsy")' in match.true_clauses
        # BooleanOr short-circuits: second atom is not traced even though also true.
        # Document this as the current engine behaviour.
        # (If both appear here, update to assert both — it means trace changed.)


class TestBooleanAndTrueClauses:
    def test_boolean_and_false_produces_no_match(self, fixtures_dir: Path) -> None:
        # BooleanAnd(Equals(specimen_type, "cytology"), Equals(fixative, "formalin"))
        # SC-TEST-001 has specimen_type=biopsy, so first child is false -> no match.
        rule = _make_rule_spec(
            BooleanAnd(
                children=(
                    Equals(field="specimen_type", value="cytology"),
                    Equals(field="fixative", value="formalin"),
                )
            )
        )
        result = scan_fixtures(rule, fixtures_dir)
        matched_ids = [m.scenario_id for m in result.matches]
        assert "SC-TEST-001" not in matched_ids

    def test_boolean_and_both_true_both_clauses_surface(self, fixtures_dir: Path) -> None:
        # BooleanAnd(Equals(specimen_type, "biopsy"), Equals(fixative, "formalin"))
        # SC-TEST-001: both true -> both clauses in true_clauses.
        rule = _make_rule_spec(
            BooleanAnd(
                children=(
                    Equals(field="specimen_type", value="biopsy"),
                    Equals(field="fixative", value="formalin"),
                )
            )
        )
        result = scan_fixtures(rule, fixtures_dir)
        match = next(m for m in result.matches if m.scenario_id == "SC-TEST-001")
        assert 'Equals(specimen_type, "biopsy")' in match.true_clauses
        assert 'Equals(fixative, "formalin")' in match.true_clauses


class TestInEnumTrueClauses:
    def test_in_enum_true_clause_format(self, fixtures_dir: Path) -> None:
        # InEnum(specimen_type, {"biopsy", "cytology"}) is True for SC-TEST-001 (biopsy).
        rule = _make_rule_spec(
            InEnum(field="specimen_type", values=frozenset({"biopsy", "cytology"}))
        )
        result = scan_fixtures(rule, fixtures_dir)
        match = next(m for m in result.matches if m.scenario_id == "SC-TEST-001")
        # Clause must be sorted: ["biopsy", "cytology"] alphabetically.
        assert 'InEnum(specimen_type, ["biopsy", "cytology"])' in match.true_clauses


# ---------------------------------------------------------------------------
# Slice 5 — format_report (takes ScanResult)
# ---------------------------------------------------------------------------


def _make_scan_result(
    matches: list[Match] | None = None, skipped: list[str] | None = None
) -> ScanResult:
    return ScanResult(
        matches=tuple(matches or []),
        skipped=tuple(skipped or []),
    )


class TestFormatReport:
    def test_no_matches_returns_no_fixture_message(self) -> None:
        report = format_report("ACC-011", _make_scan_result())
        assert report == "ACC-011 predicate matches no existing fixtures."

    def test_with_matches_starts_with_would_newly_match(self) -> None:
        matches = [
            Match(
                scenario_id="SC-036",
                category="rule_coverage",
                expected_applied_rules=("ACC-008",),
                true_clauses=(
                    'Equals(specimen_type, "biopsy")',
                    "ThresholdGTE(fixation_time_hours, 6.0)",
                ),
            )
        ]
        report = format_report("ACC-011", _make_scan_result(matches))
        assert report.startswith("ACC-011 predicate would newly match:")

    def test_report_includes_scenario_id_line(self) -> None:
        matches = [
            Match(
                scenario_id="SC-036",
                category="rule_coverage",
                expected_applied_rules=("ACC-008",),
                true_clauses=('Equals(specimen_type, "biopsy")',),
            )
        ]
        report = format_report("ACC-011", _make_scan_result(matches))
        assert "SC-036" in report
        assert "ACC-008" in report

    def test_report_includes_true_clauses(self) -> None:
        matches = [
            Match(
                scenario_id="SC-036",
                category="rule_coverage",
                expected_applied_rules=("ACC-008",),
                true_clauses=(
                    'Equals(specimen_type, "biopsy")',
                    "ThresholdGTE(fixation_time_hours, 6.0)",
                ),
            )
        ]
        report = format_report("ACC-011", _make_scan_result(matches))
        assert 'Equals(specimen_type, "biopsy")' in report
        assert "ThresholdGTE(fixation_time_hours, 6.0)" in report

    def test_multiple_matches_rendered_in_given_order(self) -> None:
        # format_report renders matches in the order scan_result.matches provides;
        # scan_fixtures guarantees sort by scenario_id.
        matches = [
            Match(
                scenario_id="SC-036",
                category="rule_coverage",
                expected_applied_rules=("ACC-008",),
                true_clauses=('Equals(specimen_type, "biopsy")',),
            ),
            Match(
                scenario_id="SC-082",
                category="rule_coverage",
                expected_applied_rules=("ACC-001",),
                true_clauses=("IsNull(patient_name)",),
            ),
        ]
        report = format_report("ACC-011", _make_scan_result(matches))
        idx_036 = report.index("SC-036")
        idx_082 = report.index("SC-082")
        assert idx_036 < idx_082  # order is preserved as supplied

    def test_format_report_includes_skipped_count_when_nonempty(self) -> None:
        result = _make_scan_result(skipped=["SC-BAD-001"])
        report = format_report("ACC-011", result)
        assert "1 scenario(s) skipped" in report
        assert "SC-BAD-001" in report

    def test_format_report_omits_skipped_line_when_empty(self) -> None:
        result = _make_scan_result()
        report = format_report("ACC-011", result)
        assert "skipped" not in report

    def test_format_report_uses_scenario_expects_wording(self) -> None:
        matches = [
            Match(
                scenario_id="SC-036",
                category="rule_coverage",
                expected_applied_rules=("ACC-008",),
                true_clauses=('Equals(specimen_type, "biopsy")',),
            )
        ]
        report = format_report("ACC-011", _make_scan_result(matches))
        assert "scenario expects" in report
        assert "currently applied" not in report


# ---------------------------------------------------------------------------
# Slice 6 — CLI main(argv)
# ---------------------------------------------------------------------------

_CANDIDATE_CLI_YAML = textwrap.dedent("""\
    rule_id: ACC-901
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
    source: test/cli
""")


class TestCLIMain:
    def test_main_exits_zero_and_prints_report(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from samantha_server.tools.fixture_impact import main

        # Write the candidate rule YAML.
        candidate = tmp_path / "ACC-901.yaml"
        candidate.write_text(_CANDIDATE_CLI_YAML)

        # Use real specs dir and real fixtures dir so the tool exercises its full path.
        real_specs_dir = Path(__file__).parent.parent.parent / "samantha_server" / "rules" / "specs"
        real_fixtures_dir = Path(__file__).parent.parent / "fixtures" / "scenarios"

        ret = main(
            [
                str(candidate),
                "--specs-dir",
                str(real_specs_dir),
                "--fixtures-dir",
                str(real_fixtures_dir),
            ]
        )
        assert ret == 0
        captured = capsys.readouterr()
        # Output should contain the rule_id and either "predicate matches" or "predicate would"
        assert "ACC-901" in captured.out
        assert "predicate" in captured.out

    def test_main_invalid_candidate_exits_one(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from samantha_server.tools.fixture_impact import main

        # A YAML that fails validation.
        bad_yaml = tmp_path / "bad.yaml"
        bad_yaml.write_text("not: a: rule: [")

        real_specs_dir = Path(__file__).parent.parent.parent / "samantha_server" / "rules" / "specs"
        real_fixtures_dir = Path(__file__).parent.parent / "fixtures" / "scenarios"

        ret = main(
            [
                str(bad_yaml),
                "--specs-dir",
                str(real_specs_dir),
                "--fixtures-dir",
                str(real_fixtures_dir),
            ]
        )
        assert ret == 1


# ---------------------------------------------------------------------------
# Fix 8 — non-ACCESSIONING candidate warning in main()
# ---------------------------------------------------------------------------

_CANDIDATE_IHC_STEP_YAML = textwrap.dedent("""\
    rule_id: SP-901
    step: SAMPLE_PREP
    applies_at: null
    event_type: processing_complete
    severity: null
    priority: 1
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
    source: test/cli-non-accessioning
""")


class TestCLIMainNonAccessioning:
    def test_main_warns_on_non_accessioning_candidate(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from samantha_server.tools.fixture_impact import main

        candidate = tmp_path / "SP-901.yaml"
        candidate.write_text(_CANDIDATE_IHC_STEP_YAML)

        real_specs_dir = Path(__file__).parent.parent.parent / "samantha_server" / "rules" / "specs"
        real_fixtures_dir = Path(__file__).parent.parent / "fixtures" / "scenarios"

        ret = main(
            [
                str(candidate),
                "--specs-dir",
                str(real_specs_dir),
                "--fixtures-dir",
                str(real_fixtures_dir),
            ]
        )
        assert ret == 0
        captured = capsys.readouterr()
        assert "Warning" in captured.err
        assert "SAMPLE_PREP" in captured.err
        assert "ACCESSIONING" in captured.err


# ---------------------------------------------------------------------------
# Fix 9 — TypeError is caught in scan_fixtures context-build except clause
# ---------------------------------------------------------------------------


class TestScanFixturesTypeError:
    def test_scan_fixtures_skips_typeerror_during_synthesis(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A TypeError raised during Order synthesis must be caught and skipped."""
        from samantha_server.tools import fixture_impact as fi_mod

        def _raise_type_error(scenario_id: str, event_data: object) -> object:
            raise TypeError("simulated type error")

        monkeypatch.setattr(fi_mod, "_synthesize_order", _raise_type_error)

        # Write a fixture so scan_fixtures has something to iterate.
        cat_dir = tmp_path / "rule_coverage"
        cat_dir.mkdir(parents=True)
        (cat_dir / "sc-type-error.json").write_text(json.dumps(_SC_MATCHING))

        rule = _make_rule_spec(IsNull(field="patient_name"))
        result = scan_fixtures(rule, tmp_path)
        # No crash; the scenario is skipped.
        assert result.matches == ()


# ---------------------------------------------------------------------------
# Fix 11 — field=None in _collect_true_atomic_clauses
# ---------------------------------------------------------------------------


class TestCollectTrueAtomicClausesNoneField:
    def test_none_field_renders_without_field_prefix(self) -> None:
        """A PrimitiveTrace with field=None must not produce 'Equals(None, ...)' output."""
        from samantha_server.primitives.trace import PrimitiveTrace
        from samantha_server.tools.fixture_impact import _collect_true_atomic_clauses

        trace = PrimitiveTrace(
            primitive="Equals",
            field=None,
            expected="some_value",
            actual="some_value",
            result=True,
        )
        clauses = _collect_true_atomic_clauses(trace)
        assert len(clauses) == 1
        # Must not contain "None"
        assert "None" not in clauses[0]
        # Must contain the expected value
        assert "some_value" in clauses[0]
