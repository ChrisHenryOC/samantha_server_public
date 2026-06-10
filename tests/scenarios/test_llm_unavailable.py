"""LLM-unavailable refusals silently pass query/llm_review scenarios.

Tests are organized by slice:
  Slice 1: query scenario with LLM failure surfaces llm_unavailable (not pass).
  Slice 2: underlying_error_type threaded through RefusalTrace into content_diagnostic.
  Slice 3: llm_review scenario with LLM failure surfaces llm_unavailable (not pass).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import get_args
from unittest.mock import MagicMock

import pytest

from samantha_server.errors import LLMInferenceError, LLMTimeoutError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_query_scenario(tmp_path: Path, scenario_id: str) -> None:
    """Write a minimal query fixture with expected order_ids.

    The fixture expects order_ids — so when the LLM fails and returns a
    RefusalTrace (no QueryTrace), the content gate MUST fire and the
    scenario MUST NOT pass.
    """
    subdir = tmp_path / "query"
    subdir.mkdir(parents=True, exist_ok=True)
    fixture = {
        "scenario_id": scenario_id,
        "category": "query",
        "description": f"Query fixture {scenario_id}",
        "query": "What orders are pending?",
        "database_state": {"orders": []},
        "expected_output": {
            "answer_type": "order_list",
            "order_ids": ["ORD-285-001"],
        },
        "events": [
            {
                "step": 1,
                "event_type": "clinical_query",
                "event_data": {
                    "query": "What orders are pending?",
                    "orders": [],
                    "scenario_id": scenario_id,
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
    path = subdir / f"{scenario_id.lower()}.json"
    path.write_text(json.dumps(fixture, indent=2))


def _write_llm_review_scenario(tmp_path: Path, scenario_id: str) -> None:
    """Write a minimal llm_review fixture with expected llm_disposition.

    The step annotates llm_disposition so the content gate must fire when
    the LLM is unavailable — not silently pass.
    """
    subdir = tmp_path / "llm_review"
    subdir.mkdir(parents=True, exist_ok=True)
    fixture = {
        "scenario_id": scenario_id,
        "category": "llm_review",
        "description": f"llm_review fixture {scenario_id}",
        "events": [
            {
                "step": 1,
                "event_type": "order_received",
                "event_data": {
                    "patient_name": "Test, GH285",
                    "age": 45,
                    "sex": "F",
                    "specimen_type": "intraoperative_consult",
                    "anatomic_site": "breast",
                    "fixative": "formalin",
                    "fixation_time_hours": 24.0,
                    "ordered_tests": ["ER", "HER2"],
                    "priority": "stat",
                    "billing_info_present": True,
                },
                "expected_output": {
                    "next_state": "PENDING_LLM_REVIEW",
                    "applied_rules": ["ACC-010"],
                    "flags": ["LLM_REVIEW_REQUESTED"],
                    "routing_path": "deterministic",
                },
            },
            {
                "step": 2,
                "event_type": "order_received",
                "event_data": {},
                "expected_output": {
                    "next_state": "PENDING_HUMAN_REVIEW",
                    "applied_rules": [],
                    "flags": [],
                    "routing_path": "llm",
                    "llm_disposition": "escalated",
                },
            },
        ],
    }
    path = subdir / f"{scenario_id.lower()}.json"
    path.write_text(json.dumps(fixture, indent=2))


def _make_failing_llm_stub(exc: Exception) -> MagicMock:
    """Return a stub LLMClient whose complete() and complete_json() raise exc."""
    mock = MagicMock()
    mock.model_id = "stub-model"
    mock.complete.side_effect = exc
    mock.complete_json.side_effect = exc
    return mock


def _inference_error() -> LLMInferenceError:
    return LLMInferenceError(model_id="stub-model", cause="test inference failure")


def _timeout_error() -> LLMTimeoutError:
    return LLMTimeoutError(model_id="stub-model", timeout_us=5_000_000)


# ---------------------------------------------------------------------------
# Slice 1: query scenario with LLM failure → llm_unavailable (not pass)
# ---------------------------------------------------------------------------


class TestSlice1QueryLlmUnavailable:
    """Query scenario surfaces llm_unavailable when LLM raises."""

    def test_llm_unavailable_in_step_verdict_status_literal(self) -> None:
        """llm_unavailable must appear in the StepVerdict.status Literal."""
        import typing

        from samantha_server.scenarios.replay import StepVerdict

        hints = typing.get_type_hints(StepVerdict)
        literal_args = get_args(hints["status"])
        assert "llm_unavailable" in literal_args, (
            "'llm_unavailable' missing from StepVerdict.status Literal — add it to replay.py"
        )

    def test_query_with_llm_failure_does_not_pass(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A query scenario must NOT pass when the LLM raises LLMInferenceError.

        Pre-fix: the scenario passes because the RefusalTrace gate is skipped and
        the structural-only check (state/rules/flags) matches the expected shape.
        Post-fix: the scenario fails with status='fail'.
        """
        from samantha_server import config
        from samantha_server.scenarios.replay import replay

        monkeypatch.setattr(config, "SAMANTHA_LLM_OUTPUT_MODE", "json")

        _write_query_scenario(tmp_path, "QR-285-FAIL")
        stub = _make_failing_llm_stub(_inference_error())
        report = replay(tmp_path, _llm_client_override=stub)

        verdicts = [v for v in report.scenario_verdicts if v.scenario_id == "QR-285-FAIL"]
        assert len(verdicts) == 1
        verdict = verdicts[0]
        assert verdict.status == "fail", (
            f"Expected scenario status='fail' when LLM is unavailable, got {verdict.status!r}"
        )

    def test_query_with_llm_failure_step_status_is_llm_unavailable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The failing step must carry status='llm_unavailable' when the LLM raises."""
        from samantha_server import config
        from samantha_server.scenarios.replay import replay

        monkeypatch.setattr(config, "SAMANTHA_LLM_OUTPUT_MODE", "json")

        _write_query_scenario(tmp_path, "QR-285-STATUS")
        stub = _make_failing_llm_stub(_inference_error())
        report = replay(tmp_path, _llm_client_override=stub)

        verdicts = [v for v in report.scenario_verdicts if v.scenario_id == "QR-285-STATUS"]
        assert len(verdicts) == 1
        step_verdicts = verdicts[0].step_verdicts
        # The clinical_query step (step_index=1) should be llm_unavailable
        llm_step = next((sv for sv in step_verdicts if sv.step_index == 1), None)
        assert llm_step is not None
        assert llm_step.status == "llm_unavailable", (
            f"Expected step status='llm_unavailable', got {llm_step.status!r}"
        )

    def test_query_with_llm_success_still_passes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pass-surface regression guard (review #1).

        When complete_json() returns a valid response whose order_ids match
        the fixture, the scenario passes with status='pass'. If a future
        refactor accidentally broadens the refusal-detection predicate (e.g.,
        drops the refusal_reason guard), a healthy scenario could regress
        to 'llm_unavailable' and this test would catch it.
        """
        from samantha_server import config
        from samantha_server.llm.client import LLMResponse
        from samantha_server.scenarios.replay import replay

        monkeypatch.setattr(config, "SAMANTHA_LLM_OUTPUT_MODE", "json")

        _write_query_scenario(tmp_path, "QR-285-PASS")
        stub = MagicMock()
        stub.model_id = "stub-model"
        stub.complete_json.return_value = LLMResponse(
            text='{"answer_type": "order_list", "order_ids": ["ORD-285-001"]}',
            input_tokens=10,
            output_tokens=5,
            model_id="stub-model",
            latency_us=1000,
        )

        report = replay(tmp_path, _llm_client_override=stub)
        verdicts = [v for v in report.scenario_verdicts if v.scenario_id == "QR-285-PASS"]
        assert len(verdicts) == 1
        verdict = verdicts[0]
        assert verdict.status == "pass", (
            f"Healthy LLM response must produce status='pass', got {verdict.status!r}"
        )
        llm_step = next((sv for sv in verdict.step_verdicts if sv.step_index == 1), None)
        assert llm_step is not None
        assert llm_step.status == "pass", (
            f"Healthy LLM step must carry status='pass', got {llm_step.status!r}"
        )

    def test_mismatch_query_response_takes_precedence_over_llm_unavailable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Precedence regression guard (review #2).

        When the LLM succeeds but returns the WRONG order_ids, the content
        gate must surface 'mismatch_query_response' (a model-wrong-answer
        signal), NOT 'llm_unavailable' (a model-broken signal). The two are
        mutually exclusive by construction today — a successful complete_json
        yields a QueryTrace, not a RefusalTrace — but the invariant is
        unpinned without this test. A future change that emitted both trace
        types from the same handler call could collapse the distinction.
        """
        from samantha_server import config
        from samantha_server.llm.client import LLMResponse
        from samantha_server.scenarios.replay import replay

        monkeypatch.setattr(config, "SAMANTHA_LLM_OUTPUT_MODE", "json")

        _write_query_scenario(tmp_path, "QR-285-WRONG")
        stub = MagicMock()
        stub.model_id = "stub-model"
        # Fixture expects ["ORD-285-001"]; stub returns a different ID.
        stub.complete_json.return_value = LLMResponse(
            text='{"answer_type": "order_list", "order_ids": ["ORD-285-WRONG"]}',
            input_tokens=10,
            output_tokens=5,
            model_id="stub-model",
            latency_us=1000,
        )

        report = replay(tmp_path, _llm_client_override=stub)
        verdicts = [v for v in report.scenario_verdicts if v.scenario_id == "QR-285-WRONG"]
        assert len(verdicts) == 1
        llm_step = next((sv for sv in verdicts[0].step_verdicts if sv.step_index == 1), None)
        assert llm_step is not None
        assert llm_step.status == "mismatch_query_response", (
            "Wrong order_ids on a successful LLM call must surface as "
            f"mismatch_query_response, not llm_unavailable. Got {llm_step.status!r}."
        )


# ---------------------------------------------------------------------------
# Slice 2: underlying_error_type threaded through to content_diagnostic
# ---------------------------------------------------------------------------


class TestSlice2UnderlyingErrorType:
    """underlying_error_type appears in content_diagnostic."""

    def test_refusal_trace_accepts_underlying_error_type(self) -> None:
        """RefusalTrace must accept an optional underlying_error_type field."""
        from samantha_server.engine.decision import RefusalTrace

        trace = RefusalTrace(
            refusal_reason="STAGE_PRE_LLM_UNAVAILABLE",
            refusal_stage="PRE",
            judge_verdict=None,
            underlying_error_type="LLMTimeoutError",
        )
        assert trace.underlying_error_type == "LLMTimeoutError"

    def test_refusal_trace_underlying_error_type_defaults_none(self) -> None:
        """RefusalTrace must default underlying_error_type to None (backward compat)."""
        from samantha_server.engine.decision import RefusalTrace

        trace = RefusalTrace(
            refusal_reason="STAGE_PRE_LLM_UNAVAILABLE",
            refusal_stage="PRE",
            judge_verdict=None,
        )
        assert trace.underlying_error_type is None

    def test_refusal_trace_rejects_unknown_underlying_error_type(self) -> None:
        """RefusalTrace.underlying_error_type must be a Literal of known
        LLMClientError subclass names (review #3). Free-form strings would let
        a future LLMClientError subclass leak through diagnostics without
        intent. Pydantic must raise on any value not in the Literal set.
        """
        from pydantic import ValidationError

        from samantha_server.engine.decision import RefusalTrace

        with pytest.raises(ValidationError):
            RefusalTrace(
                refusal_reason="STAGE_PRE_LLM_UNAVAILABLE",
                refusal_stage="PRE",
                judge_verdict=None,
                underlying_error_type="SomeFutureUntypedError",  # not in Literal
            )

    def test_llm_timeout_error_type_name_in_content_diagnostic(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """content_diagnostic for an llm_unavailable step must include the error type name.

        Privacy boundary: only the type name is included, not str(exc).
        """
        from samantha_server import config
        from samantha_server.scenarios.replay import replay

        monkeypatch.setattr(config, "SAMANTHA_LLM_OUTPUT_MODE", "json")

        _write_query_scenario(tmp_path, "QR-285-ERRTYPE")
        stub = _make_failing_llm_stub(_timeout_error())
        report = replay(tmp_path, _llm_client_override=stub)

        verdicts = [v for v in report.scenario_verdicts if v.scenario_id == "QR-285-ERRTYPE"]
        assert len(verdicts) == 1
        step_verdicts = verdicts[0].step_verdicts
        llm_step = next((sv for sv in step_verdicts if sv.step_index == 1), None)
        assert llm_step is not None
        assert llm_step.status == "llm_unavailable"
        diagnostic = llm_step.content_diagnostic
        assert diagnostic is not None, "content_diagnostic must be set on llm_unavailable steps"
        assert "LLMTimeoutError" in diagnostic, (
            f"Expected 'LLMTimeoutError' in content_diagnostic, got {diagnostic!r}"
        )
        # Privacy boundary: only the type name is included, not the full str(exc).
        # The exception message includes model_id and timeout details — those must
        # not appear (they can contain operator config details or indirectly echo
        # prompt metadata).
        assert "timed out after" not in diagnostic, (
            "Exception message text must NOT appear in content_diagnostic (privacy boundary)"
        )


# ---------------------------------------------------------------------------
# Slice 3: llm_review scenario with LLM failure → llm_unavailable (not pass)
# ---------------------------------------------------------------------------


class TestSlice3LlmReviewLlmUnavailable:
    """llm_review scenario surfaces llm_unavailable when LLM raises."""

    def test_llm_review_with_llm_failure_does_not_pass(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An llm_review scenario must NOT pass when the LLM raises LLMInferenceError.

        The step annotates llm_disposition='escalated'. When the LLM fails, the
        engine returns a RefusalTrace(STAGE_PRE_LLM_UNAVAILABLE). The per-step
        content gate must detect this and mark the step llm_unavailable.
        """
        from samantha_server import config
        from samantha_server.scenarios.replay import replay

        monkeypatch.setattr(config, "SAMANTHA_LLM_OUTPUT_MODE", "json")

        _write_llm_review_scenario(tmp_path, "LR-285-FAIL")
        stub = _make_failing_llm_stub(_inference_error())
        report = replay(tmp_path, _llm_client_override=stub)

        verdicts = [v for v in report.scenario_verdicts if v.scenario_id == "LR-285-FAIL"]
        assert len(verdicts) == 1
        verdict = verdicts[0]
        assert verdict.status == "fail", (
            f"Expected scenario status='fail' when LLM is unavailable, got {verdict.status!r}"
        )

    def test_llm_review_with_llm_failure_step_status_is_llm_unavailable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The llm_review step must carry status='llm_unavailable' when the LLM raises."""
        from samantha_server import config
        from samantha_server.scenarios.replay import replay

        monkeypatch.setattr(config, "SAMANTHA_LLM_OUTPUT_MODE", "json")

        _write_llm_review_scenario(tmp_path, "LR-285-STATUS")
        stub = _make_failing_llm_stub(_inference_error())
        report = replay(tmp_path, _llm_client_override=stub)

        verdicts = [v for v in report.scenario_verdicts if v.scenario_id == "LR-285-STATUS"]
        assert len(verdicts) == 1
        step_verdicts = verdicts[0].step_verdicts
        # Step 2 is the llm-routed step with llm_disposition annotated.
        llm_step = next((sv for sv in step_verdicts if sv.step_index == 2), None)
        assert llm_step is not None
        assert llm_step.status == "llm_unavailable", (
            f"Expected step status='llm_unavailable', got {llm_step.status!r}"
        )
        # Pin the per-step llm_review diagnostic format
        # explicitly so a regression that drops `underlying=` or shifts the
        # field order fails loudly. The query gate adds `outcome=`; this
        # gate intentionally omits it (asymmetry documented in replay.py).
        assert llm_step.content_diagnostic == (
            "refusal_reason=STAGE_PRE_LLM_UNAVAILABLE latency_us=0 underlying=LLMInferenceError"
        ), f"Diagnostic format drift: {llm_step.content_diagnostic!r}"

    def test_llm_model_load_error_surfaces_in_diagnostic(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """LLMModelLoadError is the third LLMClientError
        subclass; the other two are covered by sibling tests. Adding this one
        guards against a future handler refactor that splits model-load to
        its own branch — without explicit coverage that path could land
        without the underlying_error_type thread.
        """
        from samantha_server import config
        from samantha_server.errors import LLMModelLoadError
        from samantha_server.scenarios.replay import replay

        monkeypatch.setattr(config, "SAMANTHA_LLM_OUTPUT_MODE", "json")

        _write_llm_review_scenario(tmp_path, "LR-285-MODEL-LOAD")
        stub = _make_failing_llm_stub(
            LLMModelLoadError(model_path="stub-model", cause="weights missing")
        )
        report = replay(tmp_path, _llm_client_override=stub)

        verdicts = [v for v in report.scenario_verdicts if v.scenario_id == "LR-285-MODEL-LOAD"]
        assert len(verdicts) == 1
        llm_step = next((sv for sv in verdicts[0].step_verdicts if sv.step_index == 2), None)
        assert llm_step is not None
        assert llm_step.status == "llm_unavailable"
        assert (
            llm_step.content_diagnostic is not None
            and "underlying=LLMModelLoadError" in llm_step.content_diagnostic
        ), f"Expected LLMModelLoadError in diagnostic, got {llm_step.content_diagnostic!r}"
