"""SKILL_UNAVAILABLE and PHI_BOUNDARY refusals silently pass query/llm_review scenarios.

Twin of. Extends the operational-refusal-detection pattern to:
  - STAGE_PRE_SKILL_UNAVAILABLE (SkillLoaderError path)
  - STAGE_PRE_PHI_BOUNDARY (PHIBoundaryError path)

Tests are organized by slice:
  Slice 1: StepStatusValue Literal membership for new statuses.
  Slice 2: query scenario with SkillLoaderError surfaces skill_unavailable (not pass).
  Slice 3: query scenario with PHIBoundaryError surfaces phi_boundary_violation (not pass).
  Slice 4: llm_review scenario parallel treatment for both refusal classes.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, get_args

import pytest

from samantha_server.errors import PHIBoundaryError
from samantha_server.skills.loader import SkillLoaderError

# ---------------------------------------------------------------------------
# Helpers (mirror test_llm_unavailable.py patterns)
# ---------------------------------------------------------------------------


def _write_query_scenario(tmp_path: Path, scenario_id: str) -> None:
    """Write a minimal query fixture with expected order_ids.

    The fixture expects order_ids — so when the handler returns a RefusalTrace
    (no QueryTrace), the content gate MUST fire and the scenario MUST NOT pass.
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
            "order_ids": ["ORD-287-001"],
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
    the handler returns a refusal — not silently pass.
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
                    "patient_name": "Test, GH287",
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


def _raises(exc: Exception) -> Callable[..., Any]:
    """Return a callable that raises ``exc`` regardless of arguments.

    Used as a ``monkeypatch.setattr`` target so handlers see a deterministic
    failure injection at the boundary instead of an inline lambda + generator
    trick per call site. Mirrors its ``_make_failing_llm_stub`` factory
    pattern at the function-attribute level (vs the mock-object level).
    """

    def _fn(*_args: Any, **_kwargs: Any) -> Any:
        raise exc

    return _fn


def _assert_post_loop_diagnostic(
    diagnostic: str | None, *, refusal_reason: str, outcome: str
) -> None:
    """Pin the post-loop query gate's diagnostic shape for SKILL/PHI refusals.

    The post-loop gate emits ``refusal_reason=X outcome=Y latency_us=N`` (in
    that order). For SKILL/PHI refusals, ``underlying_error_type`` is None on
    the RefusalTrace, so ``underlying=`` MUST be absent. Asserts on shape
    rather than exact-equality so the variable ``latency_us`` value (a real
    measurement, host-dependent) doesn't make the test flaky.
    """
    assert diagnostic is not None, "content_diagnostic missing on the refusal step"
    expected_prefix = f"refusal_reason={refusal_reason} outcome={outcome} latency_us="
    assert diagnostic.startswith(expected_prefix), (
        f"diagnostic prefix drift: expected start {expected_prefix!r}, got {diagnostic!r}"
    )
    assert "underlying=" not in diagnostic, (
        f"underlying= must be absent for SKILL/PHI refusals "
        f"(underlying_error_type is None on these RefusalTrace constructions); "
        f"got {diagnostic!r}"
    )


# ---------------------------------------------------------------------------
# Slice 1: StepStatusValue Literal membership
# ---------------------------------------------------------------------------


class TestSlice1StatusLiterals:
    """New StepStatusValue members for SKILL/PHI refusal classes."""

    def test_skill_unavailable_in_step_verdict_status_literal(self) -> None:
        """skill_unavailable must appear in the StepVerdict.status Literal."""
        import typing

        from samantha_server.scenarios.replay import StepVerdict

        hints = typing.get_type_hints(StepVerdict)
        literal_args = get_args(hints["status"])
        assert "skill_unavailable" in literal_args, (
            "'skill_unavailable' missing from StepVerdict.status Literal — add it to replay.py"
        )

    def test_phi_boundary_violation_in_step_verdict_status_literal(self) -> None:
        """phi_boundary_violation must appear in the StepVerdict.status Literal."""
        import typing

        from samantha_server.scenarios.replay import StepVerdict

        hints = typing.get_type_hints(StepVerdict)
        literal_args = get_args(hints["status"])
        assert "phi_boundary_violation" in literal_args, (
            "'phi_boundary_violation' missing from StepVerdict.status Literal — add it to replay.py"
        )


# ---------------------------------------------------------------------------
# Slice 2: query scenario with SkillLoaderError → skill_unavailable (not pass)
# ---------------------------------------------------------------------------


class TestSlice2QuerySkillUnavailable:
    """Query scenario surfaces skill_unavailable when SkillLoaderError raised."""

    def test_query_with_skill_loader_failure_does_not_pass(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A query scenario must NOT pass when load_skill raises SkillLoaderError.

        Pre-fix: the scenario passes because the RefusalTrace gate only checks
        STAGE_PRE_LLM_UNAVAILABLE, and the structural-only check (state/rules/flags)
        matches the expected shape.
        Post-fix: the scenario fails with status='fail'.
        """
        import samantha_server.llm.handlers as _handlers
        from samantha_server import config
        from samantha_server.scenarios.replay import replay

        monkeypatch.setattr(config, "SAMANTHA_LLM_OUTPUT_MODE", "json")
        monkeypatch.setattr(
            _handlers, "load_skill", _raises(SkillLoaderError("test skill failure"))
        )

        _write_query_scenario(tmp_path, "QR-287-SKILL-FAIL")
        report = replay(tmp_path)

        verdicts = [v for v in report.scenario_verdicts if v.scenario_id == "QR-287-SKILL-FAIL"]
        assert len(verdicts) == 1
        verdict = verdicts[0]
        assert verdict.status == "fail", (
            f"Expected scenario status='fail' when skill is unavailable, got {verdict.status!r}"
        )

    def test_query_with_skill_loader_failure_step_status_is_skill_unavailable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """status='skill_unavailable' AND post-loop diagnostic shape on the failing step."""
        import samantha_server.llm.handlers as _handlers
        from samantha_server import config
        from samantha_server.scenarios.replay import replay

        monkeypatch.setattr(config, "SAMANTHA_LLM_OUTPUT_MODE", "json")
        monkeypatch.setattr(
            _handlers, "load_skill", _raises(SkillLoaderError("test skill failure"))
        )

        _write_query_scenario(tmp_path, "QR-287-SKILL-STATUS")
        report = replay(tmp_path)

        verdicts = [v for v in report.scenario_verdicts if v.scenario_id == "QR-287-SKILL-STATUS"]
        assert len(verdicts) == 1
        step_verdicts = verdicts[0].step_verdicts
        llm_step = next((sv for sv in step_verdicts if sv.step_index == 1), None)
        assert llm_step is not None
        assert llm_step.status == "skill_unavailable", (
            f"Expected step status='skill_unavailable', got {llm_step.status!r}"
        )
        _assert_post_loop_diagnostic(
            llm_step.content_diagnostic,
            refusal_reason="STAGE_PRE_SKILL_UNAVAILABLE",
            outcome="refused_skill_unavailable",
        )

    def test_query_with_llm_success_still_passes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression guard: a healthy query path must still produce status='pass'.

        Ensures the operational-refusal-reasons predicate widening does not
        accidentally reclassify successful query outcomes.
        """
        from unittest.mock import MagicMock

        from samantha_server import config
        from samantha_server.llm.client import LLMResponse
        from samantha_server.scenarios.replay import replay

        monkeypatch.setattr(config, "SAMANTHA_LLM_OUTPUT_MODE", "json")

        _write_query_scenario(tmp_path, "QR-287-PASS")
        stub = MagicMock()
        stub.model_id = "stub-model"
        stub.complete_json.return_value = LLMResponse(
            text='{"answer_type": "order_list", "order_ids": ["ORD-287-001"]}',
            input_tokens=10,
            output_tokens=5,
            model_id="stub-model",
            latency_us=1000,
        )

        report = replay(tmp_path, _llm_client_override=stub)
        verdicts = [v for v in report.scenario_verdicts if v.scenario_id == "QR-287-PASS"]
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


# ---------------------------------------------------------------------------
# Slice 3: query scenario with PHIBoundaryError → phi_boundary_violation (not pass)
# ---------------------------------------------------------------------------


class TestSlice3QueryPhiBoundary:
    """Query path surfaces phi_boundary_violation on PHIBoundaryError."""

    def test_query_with_phi_boundary_failure_does_not_pass(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A query scenario must NOT pass when phi_safe raises PHIBoundaryError.

        Pre-fix: silent pass. Post-fix: verdict.status == 'fail'.
        """
        import samantha_server.llm.handlers as _handlers
        from samantha_server import config
        from samantha_server.scenarios.replay import replay

        monkeypatch.setattr(config, "SAMANTHA_LLM_OUTPUT_MODE", "json")
        monkeypatch.setattr(_handlers, "phi_safe", _raises(PHIBoundaryError(age=95)))

        _write_query_scenario(tmp_path, "QR-287-PHI-FAIL")
        report = replay(tmp_path)

        verdicts = [v for v in report.scenario_verdicts if v.scenario_id == "QR-287-PHI-FAIL"]
        assert len(verdicts) == 1
        verdict = verdicts[0]
        assert verdict.status == "fail", (
            f"Expected scenario status='fail' when PHI boundary is violated, got {verdict.status!r}"
        )

    def test_query_with_phi_boundary_failure_step_status_is_phi_boundary_violation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """status='phi_boundary_violation' AND post-loop diagnostic shape on the failing step."""
        import samantha_server.llm.handlers as _handlers
        from samantha_server import config
        from samantha_server.scenarios.replay import replay

        monkeypatch.setattr(config, "SAMANTHA_LLM_OUTPUT_MODE", "json")
        monkeypatch.setattr(_handlers, "phi_safe", _raises(PHIBoundaryError(age=95)))

        _write_query_scenario(tmp_path, "QR-287-PHI-STATUS")
        report = replay(tmp_path)

        verdicts = [v for v in report.scenario_verdicts if v.scenario_id == "QR-287-PHI-STATUS"]
        assert len(verdicts) == 1
        step_verdicts = verdicts[0].step_verdicts
        llm_step = next((sv for sv in step_verdicts if sv.step_index == 1), None)
        assert llm_step is not None
        assert llm_step.status == "phi_boundary_violation", (
            f"Expected step status='phi_boundary_violation', got {llm_step.status!r}"
        )
        _assert_post_loop_diagnostic(
            llm_step.content_diagnostic,
            refusal_reason="STAGE_PRE_PHI_BOUNDARY",
            outcome="refused_phi_boundary",
        )


# ---------------------------------------------------------------------------
# Slice 4: llm_review scenario parallel treatment for SKILL/PHI refusal classes
# ---------------------------------------------------------------------------


class TestSlice4LlmReviewSkillPhi:
    """llm_review per-step gate surfaces correct statuses."""

    def test_llm_review_with_skill_loader_failure_does_not_pass(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An llm_review scenario must NOT pass when load_skill raises SkillLoaderError.

        ScenarioVerdict roll-up regression guard — parallel to slice 2 / 3's
        scenario-level fail assertions. Ensures the per-step skill_unavailable
        status propagates into the aggregate verdict.
        """
        import samantha_server.llm.handlers as _handlers
        from samantha_server import config
        from samantha_server.scenarios.replay import replay

        monkeypatch.setattr(config, "SAMANTHA_LLM_OUTPUT_MODE", "json")
        monkeypatch.setattr(
            _handlers, "load_skill", _raises(SkillLoaderError("test skill failure"))
        )

        _write_llm_review_scenario(tmp_path, "LR-287-SKILL-FAIL")
        report = replay(tmp_path)

        verdicts = [v for v in report.scenario_verdicts if v.scenario_id == "LR-287-SKILL-FAIL"]
        assert len(verdicts) == 1
        verdict = verdicts[0]
        assert verdict.status == "fail", (
            f"Expected scenario status='fail' on llm_review SKILL refusal, got {verdict.status!r}"
        )

    def test_llm_review_with_skill_loader_failure_step_status_is_skill_unavailable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The llm_review step must carry status='skill_unavailable' on SkillLoaderError."""
        import samantha_server.llm.handlers as _handlers
        from samantha_server import config
        from samantha_server.scenarios.replay import replay

        monkeypatch.setattr(config, "SAMANTHA_LLM_OUTPUT_MODE", "json")
        monkeypatch.setattr(
            _handlers, "load_skill", _raises(SkillLoaderError("test skill failure"))
        )

        _write_llm_review_scenario(tmp_path, "LR-287-SKILL-STATUS")
        report = replay(tmp_path)

        verdicts = [v for v in report.scenario_verdicts if v.scenario_id == "LR-287-SKILL-STATUS"]
        assert len(verdicts) == 1
        step_verdicts = verdicts[0].step_verdicts
        # Step 2 is the llm-routed step with llm_disposition annotated.
        llm_step = next((sv for sv in step_verdicts if sv.step_index == 2), None)
        assert llm_step is not None
        assert llm_step.status == "skill_unavailable", (
            f"Expected step status='skill_unavailable', got {llm_step.status!r}"
        )
        # Pin the diagnostic format (no outcome= key — intentional asymmetry with post-loop gate).
        assert llm_step.content_diagnostic == (
            "refusal_reason=STAGE_PRE_SKILL_UNAVAILABLE latency_us=0"
        ), f"Diagnostic format drift: {llm_step.content_diagnostic!r}"

    def test_llm_review_with_phi_boundary_failure_does_not_pass(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An llm_review scenario must NOT pass when phi_safe raises PHIBoundaryError.

        ScenarioVerdict roll-up regression guard for the PHI branch.
        """
        import samantha_server.llm.handlers as _handlers
        from samantha_server import config
        from samantha_server.scenarios.replay import replay

        monkeypatch.setattr(config, "SAMANTHA_LLM_OUTPUT_MODE", "json")
        monkeypatch.setattr(_handlers, "phi_safe", _raises(PHIBoundaryError(age=95)))

        _write_llm_review_scenario(tmp_path, "LR-287-PHI-FAIL")
        report = replay(tmp_path)

        verdicts = [v for v in report.scenario_verdicts if v.scenario_id == "LR-287-PHI-FAIL"]
        assert len(verdicts) == 1
        verdict = verdicts[0]
        assert verdict.status == "fail", (
            f"Expected scenario status='fail' on llm_review PHI refusal, got {verdict.status!r}"
        )

    def test_llm_review_with_phi_boundary_failure_step_status_is_phi_boundary_violation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The llm_review step must carry status='phi_boundary_violation' on PHIBoundaryError."""
        import samantha_server.llm.handlers as _handlers
        from samantha_server import config
        from samantha_server.scenarios.replay import replay

        monkeypatch.setattr(config, "SAMANTHA_LLM_OUTPUT_MODE", "json")
        monkeypatch.setattr(_handlers, "phi_safe", _raises(PHIBoundaryError(age=95)))

        _write_llm_review_scenario(tmp_path, "LR-287-PHI-STATUS")
        report = replay(tmp_path)

        verdicts = [v for v in report.scenario_verdicts if v.scenario_id == "LR-287-PHI-STATUS"]
        assert len(verdicts) == 1
        step_verdicts = verdicts[0].step_verdicts
        llm_step = next((sv for sv in step_verdicts if sv.step_index == 2), None)
        assert llm_step is not None
        assert llm_step.status == "phi_boundary_violation", (
            f"Expected step status='phi_boundary_violation', got {llm_step.status!r}"
        )
        # Pin the diagnostic format (no outcome= key — intentional asymmetry with post-loop gate).
        assert llm_step.content_diagnostic == (
            "refusal_reason=STAGE_PRE_PHI_BOUNDARY latency_us=0"
        ), f"Diagnostic format drift: {llm_step.content_diagnostic!r}"
