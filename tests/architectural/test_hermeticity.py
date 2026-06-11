"""Architectural guard: the default suite must stay hermetic.

The container audit found 10 default-suite tests reaching the real
``OMLXClient`` constructor probe through ``replay()`` because they omitted
``_llm_client_override``. The fixes stubbed those tests, but nothing
prevented the regression class from returning with the next new test.

``replay()`` funnels every real-client construction through the single
seam ``_build_llm_client_for_replay``; the autouse conftest fixture
``_forbid_real_replay_llm_client`` makes that seam raise for any test not
marked ``live_llm`` / ``local_omlx``. The tests here pin the guard itself.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _write_minimal_query_scenario(tmp_path: Path) -> None:
    """Write one LLM-path (category=query) scenario so replay() wants a client."""
    subdir = tmp_path / "query"
    subdir.mkdir(parents=True, exist_ok=True)
    fixture = {
        "scenario_id": "QR-HERMETIC-GUARD",
        "category": "query",
        "description": "Hermeticity-guard fixture",
        "events": [
            {
                "step": 1,
                "event_type": "clinical_query",
                "event_data": {"query": "Which orders are pending?"},
                "expected_output": {
                    "next_state": "ACCESSIONING",
                    "applied_rules": [],
                    "flags": [],
                    "routing_path": "llm",
                },
            }
        ],
    }
    (subdir / "qr-hermetic-guard.json").write_text(json.dumps(fixture, indent=2))


def test_unmarked_replay_without_override_is_refused(tmp_path: Path) -> None:
    """An unmarked test that lets replay() build a real LLM client fails loudly.

    This is the guard the container-audit regression class needs: without it, a new
    test omitting ``_llm_client_override`` passes on the lab host (live oMLX)
    and only fails in a fresh container.
    """
    from samantha_server.scenarios.replay import replay

    _write_minimal_query_scenario(tmp_path)

    with pytest.raises(AssertionError, match="hermeticity guard"):
        replay(tmp_path)


def test_replay_with_override_passes_the_guard(tmp_path: Path) -> None:
    """The guard must not interfere with properly stubbed replay() calls."""
    from samantha_server.scenarios.replay import replay

    _write_minimal_query_scenario(tmp_path)

    stub = MagicMock()
    stub.model_id = "stub-model"
    # The scenario will fail its content assertions against a bare stub —
    # that is fine; the guard property under test is only that no
    # AssertionError("hermeticity guard") is raised.
    report = replay(tmp_path, _llm_client_override=stub)
    assert report is not None
