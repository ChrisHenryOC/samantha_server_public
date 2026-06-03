"""Tests for samantha_server.scenarios.loader."""

import json
import logging
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "scenarios_test"


def test_load_scenarios_returns_list_of_scenarios() -> None:
    from samantha_server.scenarios.loader import load_scenarios

    results = load_scenarios(FIXTURE_DIR)
    assert isinstance(results, list)
    assert len(results) == 3


def test_load_scenarios_scenario_ids() -> None:
    from samantha_server.scenarios.loader import load_scenarios

    results = load_scenarios(FIXTURE_DIR)
    ids = {s.scenario_id for s in results}
    assert ids == {"SC-T01", "SC-T02", "SC-T03"}


def test_load_scenarios_category_derived_from_subdir() -> None:
    from samantha_server.scenarios.loader import load_scenarios

    results = load_scenarios(FIXTURE_DIR)
    by_id = {s.scenario_id: s for s in results}
    assert by_id["SC-T01"].category == "rule_coverage"
    assert by_id["SC-T02"].category == "multi_rule"
    assert by_id["SC-T03"].category == "accumulated_state"


def test_load_scenarios_steps_parsed() -> None:
    from samantha_server.scenarios.loader import load_scenarios

    results = load_scenarios(FIXTURE_DIR)
    by_id = {s.scenario_id: s for s in results}

    sc_t01 = by_id["SC-T01"]
    assert len(sc_t01.steps) == 1
    step = sc_t01.steps[0]
    assert step.step_index == 1
    assert step.event_type == "order_received"
    assert step.expected_next_state == "ACCEPTED"
    assert step.expected_applied_rules == ("ACC-008",)
    assert step.expected_flags == ()


def test_load_scenarios_multi_step() -> None:
    from samantha_server.scenarios.loader import load_scenarios

    results = load_scenarios(FIXTURE_DIR)
    by_id = {s.scenario_id: s for s in results}

    sc_t03 = by_id["SC-T03"]
    assert len(sc_t03.steps) == 2
    step2 = sc_t03.steps[1]
    assert step2.step_index == 2
    assert step2.event_type == "grossing_complete"
    assert step2.expected_flags == ("MISSING_INFO_PROCEED",)


def test_load_scenarios_steps_are_sorted_by_step_index() -> None:
    from samantha_server.scenarios.loader import load_scenarios

    results = load_scenarios(FIXTURE_DIR)
    for scenario in results:
        indices = [s.step_index for s in scenario.steps]
        assert indices == sorted(indices)


def test_scenario_is_frozen() -> None:
    from samantha_server.scenarios.loader import load_scenarios

    results = load_scenarios(FIXTURE_DIR)
    s = results[0]
    with pytest.raises((AttributeError, TypeError)):
        s.scenario_id = "MUTATED"  # type: ignore[misc]


def test_load_scenarios_empty_dir(tmp_path: Path) -> None:
    from samantha_server.scenarios.loader import load_scenarios

    result = load_scenarios(tmp_path)
    assert result == []


def test_load_scenarios_description() -> None:
    from samantha_server.scenarios.loader import load_scenarios

    results = load_scenarios(FIXTURE_DIR)
    by_id = {s.scenario_id: s for s in results}
    assert by_id["SC-T01"].description == "Fixture: standard accept scenario"


# ---------------------------------------------------------------------------
# L11 — Edge cases: corrupt JSON, missing required key, skip-no-events
# (Updated to match new fail-loud behavior with ScenarioCorpusError)
# ---------------------------------------------------------------------------


def test_load_scenarios_corrupt_json_raises_corpus_empty_error(tmp_path: Path) -> None:
    """A JSON parse error in a file is accumulated; raises ScenarioCorpusError
    with the bad path in malformed_files."""
    from samantha_server.errors import ScenarioCorpusError

    subdir = tmp_path / "rule_coverage"
    subdir.mkdir()
    bad_path = subdir / "bad.json"
    bad_path.write_text("{not valid json")

    with pytest.raises(ScenarioCorpusError) as exc_info:
        from samantha_server.scenarios.loader import load_scenarios

        load_scenarios(tmp_path)

    assert exc_info.value.directory == tmp_path
    assert bad_path in exc_info.value.malformed_files


def test_load_scenarios_missing_required_key_raises_corpus_empty_error(tmp_path: Path) -> None:
    """A scenario file with missing required keys (e.g., scenario_id) is accumulated;
    raises ScenarioCorpusError with the bad path in malformed_files."""
    from samantha_server.errors import ScenarioCorpusError

    subdir = tmp_path / "rule_coverage"
    subdir.mkdir()
    bad_scenario = {
        "events": [
            {
                "step": 1,
                "event_type": "order_received",
                "event_data": {},
                "expected_output": {"next_state": "ACCEPTED"},
            }
        ]
    }
    bad_path = subdir / "bad.json"
    bad_path.write_text(json.dumps(bad_scenario))

    with pytest.raises(ScenarioCorpusError) as exc_info:
        from samantha_server.scenarios.loader import load_scenarios

        load_scenarios(tmp_path)

    assert exc_info.value.directory == tmp_path
    assert bad_path in exc_info.value.malformed_files


def test_load_scenarios_skips_query_shape_file_without_events_key(tmp_path: Path) -> None:
    """Files with a recognizable non-workflow shape (e.g., 'query' key present,
    no 'events') are silently skipped with a DEBUG log — not treated as malformed."""
    subdir = tmp_path / "query"
    subdir.mkdir()
    query_scenario = {"scenario_id": "QS-001", "query": "What is the specimen?"}
    (subdir / "qs-001.json").write_text(json.dumps(query_scenario))

    from samantha_server.scenarios.loader import load_scenarios

    result = load_scenarios(tmp_path)
    assert result == []


# ---------------------------------------------------------------------------
# Slice 2 — New fail-loud tests
# ---------------------------------------------------------------------------


def test_load_scenarios_nonexistent_dir_raises_corpus_empty_error(tmp_path: Path) -> None:
    """load_scenarios(nonexistent_dir) raises ScenarioCorpusError with
    directory set and malformed_files empty."""
    from samantha_server.errors import ScenarioCorpusError

    nonexistent = tmp_path / "nonexistent"

    with pytest.raises(ScenarioCorpusError) as exc_info:
        from samantha_server.scenarios.loader import load_scenarios

        load_scenarios(nonexistent)

    assert exc_info.value.directory == nonexistent
    assert exc_info.value.malformed_files == ()


def test_load_scenarios_one_malformed_file_raises_corpus_empty_error(tmp_path: Path) -> None:
    """A directory with one malformed file raises ScenarioCorpusError
    with that file in malformed_files."""
    from samantha_server.errors import ScenarioCorpusError

    subdir = tmp_path / "rule_coverage"
    subdir.mkdir()
    bad_path = subdir / "bad.json"
    bad_path.write_text("{not valid json")

    with pytest.raises(ScenarioCorpusError) as exc_info:
        from samantha_server.scenarios.loader import load_scenarios

        load_scenarios(tmp_path)

    assert exc_info.value.directory == tmp_path
    assert bad_path in exc_info.value.malformed_files


def test_load_scenarios_mix_good_and_bad_raises_corpus_empty_error(tmp_path: Path) -> None:
    """A directory with one good scenario and one malformed file raises
    ScenarioCorpusError with only the bad path in malformed_files
    (default SCENARIO_LOADER_TOLERATE_MALFORMED=false)."""
    import json as json_mod

    from samantha_server.errors import ScenarioCorpusError

    subdir = tmp_path / "rule_coverage"
    subdir.mkdir()

    # Good scenario
    good_scenario = {
        "scenario_id": "SC-G01",
        "description": "Good scenario",
        "events": [
            {
                "step": 1,
                "event_type": "order_received",
                "event_data": {},
                "expected_output": {
                    "next_state": "ACCEPTED",
                    "applied_rules": ["ACC-008"],
                    "flags": [],
                },
            }
        ],
    }
    (subdir / "good.json").write_text(json_mod.dumps(good_scenario))

    # Bad scenario (missing scenario_id)
    bad_scenario = {
        "events": [
            {
                "step": 1,
                "event_type": "order_received",
                "event_data": {},
                "expected_output": {"next_state": "ACCEPTED"},
            }
        ]
    }
    bad_path = subdir / "bad.json"
    bad_path.write_text(json_mod.dumps(bad_scenario))

    with pytest.raises(ScenarioCorpusError) as exc_info:
        from samantha_server.scenarios.loader import load_scenarios

        load_scenarios(tmp_path)

    assert exc_info.value.directory == tmp_path
    assert bad_path in exc_info.value.malformed_files


def test_load_scenarios_tolerate_malformed_env_skips_bad_returns_good(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """With SCENARIO_LOADER_TOLERATE_MALFORMED=true, a mix of good and bad files
    returns only the good scenarios and emits a WARNING log for the bad file."""
    import json as json_mod

    monkeypatch.setenv("SCENARIO_LOADER_TOLERATE_MALFORMED", "true")

    subdir = tmp_path / "rule_coverage"
    subdir.mkdir()

    good_scenario = {
        "scenario_id": "SC-G01",
        "description": "Good scenario",
        "events": [
            {
                "step": 1,
                "event_type": "order_received",
                "event_data": {},
                "expected_output": {
                    "next_state": "ACCEPTED",
                    "applied_rules": ["ACC-008"],
                    "flags": [],
                },
            }
        ],
    }
    (subdir / "good.json").write_text(json_mod.dumps(good_scenario))

    bad_scenario = {
        "events": [
            {
                "step": 1,
                "event_type": "order_received",
                "event_data": {},
                "expected_output": {"next_state": "ACCEPTED"},
            }
        ]
    }
    bad_path = subdir / "bad.json"
    bad_path.write_text(json_mod.dumps(bad_scenario))

    from samantha_server.scenarios.loader import load_scenarios

    with caplog.at_level(logging.WARNING, logger="samantha_server.scenarios.loader"):
        results = load_scenarios(tmp_path)

    assert len(results) == 1
    assert results[0].scenario_id == "SC-G01"
    assert any("bad.json" in r.message for r in caplog.records)


def test_load_scenarios_query_shape_emits_debug_log(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A recognizably non-workflow file (query shape) emits a DEBUG log line
    under default mode (no exception)."""
    subdir = tmp_path / "query"
    subdir.mkdir()
    query_scenario = {
        "scenario_id": "QS-001",
        "query": "What is the specimen?",
        "database_state": {},
    }
    (subdir / "qs-001.json").write_text(json.dumps(query_scenario))

    from samantha_server.scenarios.loader import load_scenarios

    with caplog.at_level(logging.DEBUG, logger="samantha_server.scenarios.loader"):
        result = load_scenarios(tmp_path)

    assert result == []
    assert any("qs-001.json" in r.message or "QS-001" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# GH-222 PR224 Cluster A: Scenario.expected_query_sequence accessor unit tests
# ---------------------------------------------------------------------------


class TestScenarioExpectedQuerySequence:
    """PR224 Cluster A (#2): dedicated unit tests for expected_query_sequence accessor.

    Mirrors TestScenarioExpectedQueryContent. Each test exercises one contract
    branch of the accessor so regressions are caught before they can silently
    corrupt the assertion path.
    """

    def _make_scenario(
        self,
        raw_expected_output: dict | None,  # type: ignore[type-arg]
    ) -> object:
        from samantha_server.scenarios.loader import Scenario

        return Scenario(
            scenario_id="QR-SEQ-TEST",
            category="query",
            description="sequence accessor test",
            steps=(),
            raw_expected_output=raw_expected_output,
        )

    def test_expected_query_sequence_returns_tuple_from_list(self) -> None:
        """order_ids=['A','B','C'] returns ('A','B','C') as a tuple."""
        scenario = self._make_scenario(
            {"answer_type": "prioritized_list", "order_ids": ["A", "B", "C"]}
        )
        result = scenario.expected_query_sequence  # type: ignore[union-attr]
        assert result == ("A", "B", "C")

    def test_expected_query_sequence_preserves_order(self) -> None:
        """Returned tuple is position-sensitive — element-by-element match required."""
        ids = ["ORD-003", "ORD-001", "ORD-002"]
        scenario = self._make_scenario({"answer_type": "prioritized_list", "order_ids": ids})
        result = scenario.expected_query_sequence  # type: ignore[union-attr]
        assert result is not None
        for i, oid in enumerate(ids):
            assert result[i] == oid, f"Position {i}: expected {oid!r}, got {result[i]!r}"

    def test_expected_query_sequence_returns_none_when_absent(self) -> None:
        """raw_expected_output=None returns None (no expectation)."""
        scenario = self._make_scenario(None)
        assert scenario.expected_query_sequence is None  # type: ignore[union-attr]

    def test_expected_query_sequence_returns_none_when_order_ids_absent(self) -> None:
        """raw_expected_output present but without order_ids key returns None."""
        scenario = self._make_scenario({"answer_type": "prioritized_list"})
        assert scenario.expected_query_sequence is None  # type: ignore[union-attr]

    def test_expected_query_sequence_warns_on_non_list(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """order_ids=42 (non-list) returns None and emits WARNING naming the scenario_id."""
        import logging

        from samantha_server.scenarios.loader import Scenario

        scenario = Scenario(
            scenario_id="QR-SEQ-MALFORMED",
            category="query",
            description="malformed order_ids",
            steps=(),
            raw_expected_output={"answer_type": "prioritized_list", "order_ids": 42},
        )
        with caplog.at_level(logging.WARNING):
            result = scenario.expected_query_sequence
        assert result is None
        warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("QR-SEQ-MALFORMED" in msg for msg in warning_messages), (
            f"Expected WARNING naming scenario_id; got: {warning_messages!r}"
        )

    def test_expected_query_sequence_warns_on_non_string_element(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """order_ids=['A', 2, 'B'] (non-string element) returns None and emits WARNING."""
        import logging

        from samantha_server.scenarios.loader import Scenario

        scenario = Scenario(
            scenario_id="QR-SEQ-BAD-ELEM",
            category="query",
            description="non-string element in order_ids",
            steps=(),
            raw_expected_output={"answer_type": "prioritized_list", "order_ids": ["A", 2, "B"]},
        )
        with caplog.at_level(logging.WARNING):
            result = scenario.expected_query_sequence
        assert result is None
        warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("QR-SEQ-BAD-ELEM" in msg for msg in warning_messages), (
            f"Expected WARNING naming scenario_id; got: {warning_messages!r}"
        )


# ---------------------------------------------------------------------------
# GH-222 PR224 Cluster C (#7): real-fixture integration test
# ---------------------------------------------------------------------------


def test_qr020_loader_to_accessor_path() -> None:
    """PR224 Cluster C (#7): load QR-020 via Scenario.from_path equivalent and verify
    expected_answer_type and expected_query_sequence match the known fixture values.

    Verifies the loader-to-accessor chain against a real corpus fixture. Does NOT
    run the gate end-to-end — no LLM is involved.
    """
    from pathlib import Path

    from samantha_server.scenarios.loader import Scenario, _parse_scenario

    fixture_path = (
        Path(__file__).resolve().parents[1] / "fixtures" / "scenarios" / "query" / "qr-020.json"
    )
    import json

    raw = json.loads(fixture_path.read_text())
    scenario = _parse_scenario(fixture_path, raw)

    assert isinstance(scenario, Scenario)
    assert scenario.expected_answer_type == "prioritized_list"

    expected_seq = scenario.expected_query_sequence
    assert expected_seq is not None, "expected_query_sequence must not be None for QR-020"
    assert expected_seq == (
        "ORD-2004",
        "ORD-2002",
        "ORD-2003",
        "ORD-2005",
        "ORD-2001",
    ), f"QR-020 sequence mismatch: {expected_seq!r}"


def test_empty_expected_output_preserved_as_empty_dict(tmp_path: Path) -> None:
    """A fixture with ``expected_output: {}`` loads as raw_expected_output == {}.

    GH-184 fix-review M9: the loader must NOT coerce an empty dict to None — {}
    means "key present but no expectation defined yet", distinct from absent.
    (Regression guard relocated here from the deleted test_assertions.py in GH-335.)
    """
    from samantha_server.scenarios.loader import load_scenarios

    subdir = tmp_path / "query"
    subdir.mkdir()
    fixture = {
        "scenario_id": "QR-EMPTY-EO",
        "category": "query",
        "description": "empty expected_output",
        "query": "anything",
        "database_state": {"orders": []},
        "expected_output": {},  # empty dict — must NOT be coerced to None
        "events": [
            {
                "step": 1,
                "event_type": "clinical_query",
                "event_data": {"query": "anything", "orders": [], "scenario_id": "QR-EMPTY-EO"},
                "expected_output": {
                    "next_state": "ACCESSIONING",
                    "applied_rules": [],
                    "flags": [],
                    "routing_path": "llm",
                },
            }
        ],
    }
    (subdir / "qr-empty-eo.json").write_text(json.dumps(fixture))

    scenarios = load_scenarios(tmp_path)

    assert len(scenarios) == 1
    assert scenarios[0].raw_expected_output == {}, (
        "empty expected_output: {} must be preserved as {}, not coerced to None"
    )
