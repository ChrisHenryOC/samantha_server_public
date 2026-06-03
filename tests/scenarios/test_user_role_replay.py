"""GH-227 S8: Replay propagates Scenario.user_role into event_data.

Tests that when a scenario carries user_role, the ctx passed to
dispatch_event has user_role injected in event.event_data.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


def _write_scenario(tmp_path: Path, category: str, scenario: dict[str, Any]) -> Path:
    subdir = tmp_path / category
    subdir.mkdir(parents=True, exist_ok=True)
    path = subdir / f"{scenario['scenario_id'].lower()}.json"
    path.write_text(json.dumps(scenario, indent=2))
    return path


def _make_llm_path_scenario_with_role(role: str) -> dict[str, Any]:
    return {
        "scenario_id": "QR-ROLE-01",
        "category": "query",
        "description": "Test user_role injection",
        "user_role": role,
        "events": [
            {
                "step": 1,
                "event_type": "clinical_query",
                "event_data": {
                    "patient_name": "TEST, ROLE",
                    "age": 45,
                    "sex": "F",
                    "specimen_type": "biopsy",
                    "anatomic_site": "breast",
                    "fixative": "formalin",
                    "fixation_time_hours": 24.0,
                    "ordered_tests": ["ER"],
                    "priority": "routine",
                    "billing_info_present": True,
                    "query": "What is on my worklist?",
                },
                "expected_output": {
                    "next_state": "ACCESSIONING",
                    "applied_rules": [],
                    "flags": [],
                    "routing_path": "llm",
                },
            }
        ],
    }


def _make_fake_dispatch_result() -> tuple[MagicMock, MagicMock, MagicMock]:
    from samantha_server.api.event_context import EventDispatchContext
    from samantha_server.engine.decision import EngineDecision
    from samantha_server.receipts.signing import SignedReceipt

    fake_decision = MagicMock(spec=EngineDecision)
    fake_decision.applied_rule_id = None
    fake_decision.also_matched = ()
    fake_decision.flags_cleared = ()
    fake_decision.flags_added = ()
    fake_decision.next_state = "ACCESSIONING"
    fake_decision.latency_us = 500
    fake_decision.decision_traces = ()

    fake_ctx = MagicMock(spec=EventDispatchContext)
    fake_ctx.routing_path = "llm"

    fake_receipt = MagicMock(spec=SignedReceipt)

    return (fake_decision, fake_ctx, fake_receipt)


@pytest.mark.parametrize("role", ["accessioner", "histotech", "pathologist", "lab_manager"])
def test_replay_injects_user_role_into_event_data(tmp_path: Path, role: str) -> None:
    """L9: Replay passes Scenario.user_role into event.event_data for each valid role.

    GH-324 Step 5: user_role injection now flows through the endpoint path.
    The consumer (app.py _consume) injects user_role from the queue payload into
    ctx.event.event_data before calling dispatch_event. We intercept at the
    routing.dispatch_event level to capture the ctx with user_role already injected.
    """
    from samantha_server.llm.client import LLMClient, LLMResponse
    from samantha_server.scenarios.replay import replay

    _write_scenario(tmp_path, "query", _make_llm_path_scenario_with_role(role))

    mock_llm = MagicMock(spec=LLMClient)
    mock_llm.model_id = "test-model"
    _canned = LLMResponse(
        text="{}", input_tokens=1, output_tokens=1, model_id="test-model", latency_us=0
    )
    mock_llm.complete.return_value = _canned
    mock_llm.complete_json.return_value = _canned

    captured_ctxs: list[Any] = []
    import samantha_server.api.routing as _routing_mod

    _orig_dispatch = _routing_mod.dispatch_event

    async def _capturing_dispatch(ctx: Any, **kwargs: Any) -> Any:
        captured_ctxs.append(ctx)
        return await _orig_dispatch(ctx, **kwargs)

    with patch(
        "samantha_server.api.routing.dispatch_event",
        side_effect=_capturing_dispatch,
    ):
        replay(tmp_path, _llm_client_override=mock_llm)

    assert len(captured_ctxs) == 1, f"dispatch_event must be called once; got {len(captured_ctxs)}"
    ctx = captured_ctxs[0]
    # user_role must appear in event.event_data (injected by _consume from queue payload)
    assert ctx.event.event_data.get("user_role") == role, (
        f"Expected user_role={role!r} in event_data; got: {dict(ctx.event.event_data)}"
    )


def test_replay_absent_user_role_leaves_event_data_unchanged(tmp_path: Path) -> None:
    """When Scenario.user_role is None, event_data is not mutated.

    GH-324 Step 5: user_role=None means no injection at the consumer level;
    verified by capturing ctx at routing.dispatch_event.
    """
    from samantha_server.llm.client import LLMClient, LLMResponse
    from samantha_server.scenarios.replay import replay

    scenario = _make_llm_path_scenario_with_role("pathologist")
    # Remove user_role to make it absent
    del scenario["user_role"]
    _write_scenario(tmp_path, "query", scenario)

    mock_llm = MagicMock(spec=LLMClient)
    mock_llm.model_id = "test-model"
    _canned = LLMResponse(
        text="{}", input_tokens=1, output_tokens=1, model_id="test-model", latency_us=0
    )
    mock_llm.complete.return_value = _canned
    mock_llm.complete_json.return_value = _canned

    captured_ctxs: list[Any] = []
    import samantha_server.api.routing as _routing_mod

    _orig_dispatch = _routing_mod.dispatch_event

    async def _capturing_dispatch(ctx: Any, **kwargs: Any) -> Any:
        captured_ctxs.append(ctx)
        return await _orig_dispatch(ctx, **kwargs)

    with patch(
        "samantha_server.api.routing.dispatch_event",
        side_effect=_capturing_dispatch,
    ):
        replay(tmp_path, _llm_client_override=mock_llm)

    assert len(captured_ctxs) == 1
    ctx = captured_ctxs[0]
    # user_role must NOT appear in event_data when Scenario.user_role is None
    assert "user_role" not in dict(ctx.event.event_data), (
        f"user_role must not be injected when Scenario.user_role is None; "
        f"got: {dict(ctx.event.event_data)}"
    )
