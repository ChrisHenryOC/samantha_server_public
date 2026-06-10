"""Scenario.user_role field and fixture loader validation.

Mirrors tests/scenarios/test_prompt_timestamp.py for the user_role field.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE_STEP = {
    "step": 1,
    "event_type": "clinical_query",
    "event_data": {
        "query": "Which orders should I do first?",
    },
    "expected_output": {
        "next_state": "ACCESSIONING",
        "applied_rules": [],
        "flags": [],
        "routing_path": "llm",
    },
}


def _write_fixture(tmp_path: Path, raw: dict[str, Any]) -> Path:
    p = tmp_path / "test_scenario.json"
    p.write_text(json.dumps(raw))
    return p


def _make_scenario_raw(*, user_role: str | None = None) -> dict[str, Any]:
    raw: dict = {
        "scenario_id": "QR-TEST",
        "category": "query",
        "description": "Test scenario",
        "events": [_BASE_STEP],
    }
    if user_role is not None:
        raw["user_role"] = user_role
    return raw


# ---------------------------------------------------------------------------
# S7 — valid role parses through
# ---------------------------------------------------------------------------


def test_scenario_carries_valid_user_role(tmp_path: Path) -> None:
    """Scenario loaded with a valid user_role carries it verbatim."""
    from samantha_server.scenarios.loader import _parse_scenario

    for role in ("accessioner", "histotech", "pathologist", "lab_manager"):
        raw = _make_scenario_raw(user_role=role)
        path = _write_fixture(tmp_path, raw)
        scenario = _parse_scenario(path, raw)
        assert scenario.user_role == role, f"user_role {role!r} did not round-trip"


def test_scenario_user_role_none_when_absent(tmp_path: Path) -> None:
    """Scenario without user_role key has user_role=None."""
    from samantha_server.scenarios.loader import _parse_scenario

    raw = _make_scenario_raw()
    path = _write_fixture(tmp_path, raw)
    scenario = _parse_scenario(path, raw)

    assert scenario.user_role is None


def test_scenario_invalid_user_role_raises(tmp_path: Path) -> None:
    """Invalid user_role string raises ValueError at load time."""
    from samantha_server.scenarios.loader import _parse_scenario

    raw = _make_scenario_raw(user_role="nurse")
    path = _write_fixture(tmp_path, raw)

    with pytest.raises(ValueError, match="user_role"):
        _parse_scenario(path, raw)


# ---------------------------------------------------------------------------
# S8 PR243 review — loader-level conflict detection
# ---------------------------------------------------------------------------


def _make_scenario_with_step_role(*, scenario_role: str | None, step_role: str) -> dict[str, Any]:
    """Build a raw scenario dict where both scenario-level and step-level user_role are set."""
    step: dict[str, Any] = {**_BASE_STEP}
    event_data = dict(step["event_data"])
    event_data["user_role"] = step_role
    step = {**step, "event_data": event_data}
    raw: dict[str, Any] = {
        "scenario_id": "QR-CONFLICT",
        "category": "query",
        "description": "Conflict test",
        "events": [step],
    }
    if scenario_role is not None:
        raw["user_role"] = scenario_role
    return raw


def test_loader_raises_on_conflicting_user_roles(tmp_path: Path) -> None:
    """S8: scenario-level and step-level user_role conflict → ValueError at load time."""
    from samantha_server.scenarios.loader import _parse_scenario

    raw = _make_scenario_with_step_role(scenario_role="pathologist", step_role="histotech")
    path = _write_fixture(tmp_path, raw)

    with pytest.raises(ValueError, match="user_role"):
        _parse_scenario(path, raw)


def test_loader_allows_matching_user_roles(tmp_path: Path) -> None:
    """S8: scenario-level and step-level user_role match exactly → load succeeds."""
    from samantha_server.scenarios.loader import _parse_scenario

    raw = _make_scenario_with_step_role(scenario_role="pathologist", step_role="pathologist")
    path = _write_fixture(tmp_path, raw)
    scenario = _parse_scenario(path, raw)
    assert scenario.user_role == "pathologist"


def test_loader_scenario_only_role_inherits(tmp_path: Path) -> None:
    """S8: scenario-level role set, step has no user_role → scenario.user_role is set."""
    from samantha_server.scenarios.loader import _parse_scenario

    raw = _make_scenario_raw(user_role="lab_manager")
    path = _write_fixture(tmp_path, raw)
    scenario = _parse_scenario(path, raw)
    assert scenario.user_role == "lab_manager"


def test_loader_step_only_role_loads(tmp_path: Path) -> None:
    """S8: step-level user_role set, no scenario-level → loads without error."""
    from samantha_server.scenarios.loader import _parse_scenario

    raw = _make_scenario_with_step_role(scenario_role=None, step_role="accessioner")
    path = _write_fixture(tmp_path, raw)
    # No scenario-level role — just loads successfully
    scenario = _parse_scenario(path, raw)
    assert scenario.user_role is None
