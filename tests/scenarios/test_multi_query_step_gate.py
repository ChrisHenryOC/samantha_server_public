"""Replay gate must apply to ALL query steps, not just the last.

Before this fix, `_last_query_gate_target` in `_replay_scenario_async` was a
single (index, decision) tracker that was overwritten on each query step. When
a scenario had two clinical_query steps where step 1 returned bad JSON (invalid
parse) and step 2 returned good JSON, `_last_query_gate_target` pointed at step 2
only — step 1's failure was invisible to the post-loop gate and the scenario
silently passed.

Fix: replace the single tracker with a list of (index, decision) pairs collected
across all query steps; apply the post-loop gate to every entry.

Slices:
  Slice 1: red test — two-query-step scenario, first call bad JSON, second good →
           current code: scenario passes (gate only applies to step 2).
           After fix: scenario fails with step 1 status=invalid_json.
  Slice 2: single-step query semantics are preserved after the fix.

hermeticity guard: _llm_client_override stubs passed throughout.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_two_query_step_scenario(
    tmp_path: Path,
    scenario_id: str,
    *,
    expected_order_ids: list[str],
) -> None:
    """Write a query scenario with two sequential clinical_query steps.

    Both steps carry expected_order_ids so the content gate applies.
    The scenario starts in ACCESSIONING and stays there across both steps
    (clinical_query does not advance the state).
    """
    subdir = tmp_path / "query"
    subdir.mkdir(parents=True, exist_ok=True)
    fixture = {
        "scenario_id": scenario_id,
        "category": "query",
        "description": f"Multi-query-step fixture {scenario_id}",
        "expected_output": {
            "answer_type": "order_list",
            "order_ids": expected_order_ids,
        },
        "events": [
            {
                "step": 1,
                "event_type": "clinical_query",
                "event_data": {
                    "query": "What orders are pending? (step 1)",
                    "orders": [],
                },
                "expected_output": {
                    "next_state": "ACCESSIONING",
                    "applied_rules": [],
                    "flags": [],
                    "routing_path": "llm",
                },
            },
            {
                "step": 2,
                "event_type": "clinical_query",
                "event_data": {
                    "query": "What orders are pending? (step 2)",
                    "orders": [],
                },
                "expected_output": {
                    "next_state": "ACCESSIONING",
                    "applied_rules": [],
                    "flags": [],
                    "routing_path": "llm",
                },
            },
        ],
    }
    path = subdir / f"{scenario_id.lower()}.json"
    path.write_text(json.dumps(fixture, indent=2))


def _make_stub_bad_first_call(order_id: str) -> MagicMock:
    """Stub where complete_json call 1 returns non-JSON text (parse failure)
    and call 2 returns a valid QueryResponseV1.

    After the fix, the parse failure on call 1 must surface as
    status='invalid_json' for step 1.  Before the fix, the gate only checks
    the last query step (step 2), which passes — the scenario appears to pass.
    """
    from samantha_server.llm.client import LLMResponse

    bad_response = LLMResponse(
        text="this is definitely not valid json (call 1)",
        input_tokens=5,
        output_tokens=10,
        model_id="stub-multi-query",
        latency_us=100,
    )
    good_response = LLMResponse(
        text=json.dumps(
            {"answer_type": "order_list", "order_ids": [order_id], "reasoning": "ok", "caveats": ""}
        ),
        input_tokens=5,
        output_tokens=12,
        model_id="stub-multi-query",
        latency_us=100,
    )

    mock = MagicMock()
    mock.model_id = "stub-multi-query"
    # First call → bad JSON; second call → good JSON.
    mock.complete_json.side_effect = [bad_response, good_response]
    return mock


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMultiQueryStepGate:
    """Replay gate must apply the content check to every query step."""

    def test_first_query_step_bad_json_surfaces_as_fail(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Slice 1 (red → green): first query step with bad JSON must cause scenario fail.

        Before the fix, the post-loop gate only targeted the LAST query step.
        Step 1's parse failure was invisible; the scenario silently passed.
        After the fix, the gate is applied to ALL query steps; step 1's
        parse failure surfaces as status='invalid_json' and the scenario fails.

        Red-proof: this test fails against the pre-fix code because
        `_last_query_gate_target` points at step 2 (which passes) and the gate
        never sees step 1's RefusalTrace(STAGE_PRE_UNPARSEABLE).
        """
        from samantha_server import config
        from samantha_server.scenarios.replay import replay

        monkeypatch.setattr(config, "SAMANTHA_LLM_OUTPUT_MODE", "json")

        scenario_id = "QR-GH385-MULTI-FAIL"
        order_id = "ORD-GH385-001"
        _make_two_query_step_scenario(tmp_path, scenario_id, expected_order_ids=[order_id])

        stub = _make_stub_bad_first_call(order_id)
        report = replay(tmp_path, _llm_client_override=stub)

        verdicts = [v for v in report.scenario_verdicts if v.scenario_id == scenario_id]
        assert len(verdicts) == 1, f"Expected 1 verdict for {scenario_id!r}; got {len(verdicts)}"
        verdict = verdicts[0]

        assert verdict.status == "fail", (
            f"Scenario must fail when step 1 returns bad JSON; got status={verdict.status!r}. "
            "Before the fix, the gate only covers the last query step — step 1's "
            "parse failure is invisible."
        )

        # Step 1 must carry invalid_json status.
        step1 = next((sv for sv in verdict.step_verdicts if sv.step_index == 1), None)
        assert step1 is not None, "Step 1 verdict must exist"
        assert step1.status == "invalid_json", (
            f"Step 1 with bad JSON must have status='invalid_json'; got {step1.status!r}"
        )
        # Pin that the gate did NOT over-apply — step 2's good
        # JSON must still pass its content checks.
        step2 = next((sv for sv in verdict.step_verdicts if sv.step_index == 2), None)
        assert step2 is not None, "Step 2 verdict must exist"
        assert step2.status == "pass", (
            f"Step 2 with good JSON must keep status='pass'; got {step2.status!r}"
        )

    def test_single_step_query_semantics_preserved(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Slice 2: single-step query scenario with matching order_ids still passes.

        Regression guard: the multi-step fix must not break the single-step gate
        that was correct before. Uses the same pattern as test_query_with_llm_success_still_passes
        in test_llm_unavailable.py.
        """
        from samantha_server import config
        from samantha_server.llm.client import LLMResponse
        from samantha_server.scenarios.replay import replay

        monkeypatch.setattr(config, "SAMANTHA_LLM_OUTPUT_MODE", "json")

        subdir = tmp_path / "query"
        subdir.mkdir(parents=True, exist_ok=True)
        scenario_id = "QR-GH385-SINGLE-PASS"
        order_id = "ORD-GH385-SINGLE-001"
        fixture = {
            "scenario_id": scenario_id,
            "category": "query",
            "description": "Single-step regression guard",
            "expected_output": {
                "answer_type": "order_list",
                "order_ids": [order_id],
            },
            "events": [
                {
                    "step": 1,
                    "event_type": "clinical_query",
                    "event_data": {
                        "query": "What orders are pending?",
                        "orders": [],
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
        (subdir / f"{scenario_id.lower()}.json").write_text(json.dumps(fixture))

        stub = MagicMock()
        stub.model_id = "stub-single"
        stub.complete_json.return_value = LLMResponse(
            text=json.dumps(
                {
                    "answer_type": "order_list",
                    "order_ids": [order_id],
                    "reasoning": "ok",
                    "caveats": "",
                }
            ),
            input_tokens=5,
            output_tokens=12,
            model_id="stub-single",
            latency_us=100,
        )

        report = replay(tmp_path, _llm_client_override=stub)
        verdicts = [v for v in report.scenario_verdicts if v.scenario_id == scenario_id]
        assert len(verdicts) == 1
        assert verdicts[0].status == "pass", (
            f"Single-step query with matching order_ids must pass; got {verdicts[0].status!r}"
        )
