"""Tests for samantha_server.scenarios.index — id-keyed scenario index."""

import pytest

from samantha_server.scenarios.loader import Scenario, ScenarioStep


def _make_scenario(scenario_id: str) -> Scenario:
    """Build a minimal Scenario fixture with the given id."""
    return Scenario(
        scenario_id=scenario_id,
        category="rule_coverage",
        description="test scenario",
        steps=(
            ScenarioStep(
                step_index=0,
                event_type="CALL_STARTED",
                event_data={},
                expected_next_state="IN_CALL",
                expected_applied_rules=(),
                expected_flags=(),
            ),
        ),
    )


def test_build_scenario_index_returns_dict_keyed_by_id() -> None:
    from samantha_server.scenarios.index import build_scenario_index

    sc_a = _make_scenario("ACC-001")
    sc_b = _make_scenario("ACC-006")

    index = build_scenario_index([sc_a, sc_b])

    assert index == {"ACC-001": sc_a, "ACC-006": sc_b}


def test_build_scenario_index_raises_on_duplicate_id() -> None:
    from samantha_server.scenarios.index import build_scenario_index

    sc1 = _make_scenario("ACC-001")
    sc2 = _make_scenario("ACC-001")

    with pytest.raises(ValueError, match="ACC-001"):
        build_scenario_index([sc1, sc2])


def test_lookup_scenario_returns_scenario_for_known_id() -> None:
    from samantha_server.scenarios.index import build_scenario_index, lookup_scenario

    sc = _make_scenario("IHC-001")
    index = build_scenario_index([sc])

    result = lookup_scenario("IHC-001", index)

    assert result is sc


def test_lookup_scenario_raises_scenario_not_found_error_for_unknown_id() -> None:
    from samantha_server.errors import ScenarioNotFoundError
    from samantha_server.scenarios.index import build_scenario_index, lookup_scenario

    index = build_scenario_index([_make_scenario("ACC-001")])

    with pytest.raises(ScenarioNotFoundError) as exc_info:
        lookup_scenario("qr-007", index)

    assert exc_info.value.scenario_id == "qr-007"


def test_build_scenario_index_returns_empty_dict_for_empty_iterable() -> None:
    from samantha_server.scenarios.index import build_scenario_index

    assert build_scenario_index([]) == {}
