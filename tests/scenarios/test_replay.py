"""Tests for samantha_server.scenarios.replay."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "scenarios_test"


def _write_scenario(tmp_path: Path, category: str, scenario: dict) -> Path:  # type: ignore[type-arg]
    subdir = tmp_path / category
    subdir.mkdir(parents=True, exist_ok=True)
    path = subdir / f"{scenario['scenario_id'].lower()}.json"
    path.write_text(json.dumps(scenario, indent=2))
    return path


def _make_mock_llm_client() -> MagicMock:
    """Return a mock LLMClient with canned responses for both complete() and complete_json().

    The endpoint path uses the real LLM handlers which call
    complete() or complete_json(). Both methods must return a real LLMResponse
    with a string text field to avoid TypeError in _strip_markdown_fences.
    Returns '{}' so handlers produce a refusal/empty result rather than crashing.
    """
    from samantha_server.llm.client import LLMClient, LLMResponse

    _canned = LLMResponse(
        text="{}",
        input_tokens=1,
        output_tokens=1,
        model_id="test-model",
        latency_us=0,
    )
    mock = MagicMock(spec=LLMClient)
    mock.model_id = "test-model"
    mock.complete.return_value = _canned
    mock.complete_json.return_value = _canned
    return mock


def _make_fake_dispatch_result(
    latency_us: int = 100,
    next_state: str = "ACCESSIONING",
    applied_rule_id: str | None = None,
) -> object:
    """Return a (decision, ctx, receipt) tuple suitable for mocking dispatch_event.

    EngineDecision must be a real Pydantic model (not MagicMock)
    so it can round-trip through the endpoint's model_dump(mode="json") /
    model_validate serialization. EventDispatchContext and SignedReceipt remain
    MagicMock since only their .routing_path and .receipt_id attributes are used.
    """
    import hashlib

    from samantha_server.api.event_context import EventDispatchContext
    from samantha_server.engine.decision import EngineDecision
    from samantha_server.queue.priority import EventPriority
    from samantha_server.receipts.signing import SignedReceipt

    decision = EngineDecision(
        applied_rule_id=applied_rule_id,
        also_matched=(),
        flags_cleared=(),
        flags_added=(),
        next_state=next_state,
        outcome="pass",
        dispatched_rule_ids=(),
        event_input_hash=hashlib.sha256(b"fake").hexdigest(),
        primitive_traces={},
        latency_us=latency_us,
        decision_traces=(),
    )

    fake_ctx = MagicMock(spec=EventDispatchContext)
    fake_ctx.routing_path = "llm"
    fake_ctx.priority = EventPriority.ROUTINE

    fake_receipt = MagicMock(spec=SignedReceipt)
    fake_receipt.receipt_id = "fake-receipt-id"

    return (decision, fake_ctx, fake_receipt)


async def _fake_dispatch_with_receipt(
    session_id: str,
    latency_us: int = 100,
    next_state: str = "ACCESSIONING",
    applied_rule_id: str | None = None,
    **kwargs: object,
) -> object:
    """Like _make_fake_dispatch_result but calls emit_receipt so the in-memory
    store has the receipt persisted.

    The fallback WARNING branch in replay.py was removed; any
    dispatch_event stub that skips emit_receipt now raises. Use this helper
    in _selective_fake functions that intercept LLM-path steps.
    """
    import hashlib

    from samantha_server.api.event_context import EventDispatchContext
    from samantha_server.api.receipt_emission import emit_receipt
    from samantha_server.engine.decision import EngineDecision
    from samantha_server.queue.priority import EventPriority

    decision = EngineDecision(
        applied_rule_id=applied_rule_id,
        also_matched=(),
        flags_cleared=(),
        flags_added=(),
        next_state=next_state,
        outcome="pass",
        dispatched_rule_ids=(),
        event_input_hash=hashlib.sha256(b"fake").hexdigest(),
        primitive_traces={},
        latency_us=latency_us,
        decision_traces=(),
        session_id=session_id,
    )

    fake_ctx = MagicMock(spec=EventDispatchContext)
    fake_ctx.routing_path = "llm"
    fake_ctx.priority = EventPriority.ROUTINE

    receipt = await emit_receipt(
        decision,
        session_id,
        receipt_writer=kwargs["receipt_writer"],  # type: ignore[arg-type]
        write_lock=kwargs["write_lock"],  # type: ignore[arg-type]
        counters=kwargs["counters"],  # type: ignore[arg-type]
    )

    return (decision, fake_ctx, receipt)


def _all_pass_scenario() -> dict:  # type: ignore[type-arg]
    return {
        "scenario_id": "SC-RP01",
        "category": "rule_coverage",
        "description": "All steps pass",
        "events": [
            {
                "step": 1,
                "event_type": "order_received",
                "event_data": {
                    "patient_name": "TEST, Alice",
                    "age": 45,
                    "sex": "F",
                    "specimen_type": "biopsy",
                    "anatomic_site": "breast",
                    "fixative": "formalin",
                    "fixation_time_hours": 24.0,
                    "ordered_tests": ["Breast IHC Panel"],
                    "priority": "routine",
                    "billing_info_present": True,
                },
                "expected_output": {
                    "next_state": "ACCEPTED",
                    "applied_rules": ["ACC-008"],
                    "flags": [],
                    "routing_path": "deterministic",
                },
            }
        ],
    }


def _state_mismatch_scenario() -> dict:  # type: ignore[type-arg]
    """A scenario where the expected state won't match what the engine returns."""
    return {
        "scenario_id": "SC-RP02",
        "category": "rule_coverage",
        "description": "State mismatch — expected wrong state",
        "events": [
            {
                "step": 1,
                "event_type": "order_received",
                "event_data": {
                    "patient_name": "TEST, Bob",
                    "age": 55,
                    "sex": "M",
                    "specimen_type": "biopsy",
                    "anatomic_site": "breast",
                    "fixative": "formalin",
                    "fixation_time_hours": 24.0,
                    "ordered_tests": ["Breast IHC Panel"],
                    "priority": "routine",
                    "billing_info_present": True,
                },
                "expected_output": {
                    "next_state": "DO_NOT_PROCESS",  # wrong — should be ACCEPTED
                    "applied_rules": ["ACC-008"],
                    "flags": [],
                    "routing_path": "deterministic",
                },
            }
        ],
    }


def _rules_mismatch_scenario() -> dict:  # type: ignore[type-arg]
    return {
        "scenario_id": "SC-RP03",
        "category": "rule_coverage",
        "description": "Rules mismatch — wrong rule expected",
        "events": [
            {
                "step": 1,
                "event_type": "order_received",
                "event_data": {
                    "patient_name": "TEST, Carol",
                    "age": 45,
                    "sex": "F",
                    "specimen_type": "biopsy",
                    "anatomic_site": "breast",
                    "fixative": "formalin",
                    "fixation_time_hours": 24.0,
                    "ordered_tests": ["Breast IHC Panel"],
                    "priority": "routine",
                    "billing_info_present": True,
                },
                "expected_output": {
                    "next_state": "ACCEPTED",
                    "applied_rules": ["ACC-001"],  # wrong — should be ACC-008
                    "flags": [],
                    "routing_path": "deterministic",
                },
            }
        ],
    }


def _flags_mismatch_scenario() -> dict:  # type: ignore[type-arg]
    return {
        "scenario_id": "SC-RP04",
        "category": "rule_coverage",
        "description": "Flags mismatch — wrong flags expected",
        "events": [
            {
                "step": 1,
                "event_type": "order_received",
                "event_data": {
                    "patient_name": "TEST, Dan",
                    "age": 45,
                    "sex": "F",
                    "specimen_type": "biopsy",
                    "anatomic_site": "breast",
                    "fixative": "formalin",
                    "fixation_time_hours": 24.0,
                    "ordered_tests": ["Breast IHC Panel"],
                    "priority": "routine",
                    "billing_info_present": True,
                },
                "expected_output": {
                    "next_state": "ACCEPTED",
                    "applied_rules": ["ACC-008"],
                    "flags": ["MISSING_INFO_PROCEED"],  # wrong — no flags expected
                    "routing_path": "deterministic",
                },
            }
        ],
    }


def _advance_sample_prep_scenario() -> dict:  # type: ignore[type-arg]
    return {
        "scenario_id": "SC-RP05",
        "category": "accumulated_state",
        "description": "ADVANCE_SAMPLE_PREP resolution",
        "events": [
            {
                "step": 1,
                "event_type": "order_received",
                "event_data": {
                    "patient_name": "TEST, Eve",
                    "age": 45,
                    "sex": "F",
                    "specimen_type": "biopsy",
                    "anatomic_site": "breast",
                    "fixative": "formalin",
                    "fixation_time_hours": 24.0,
                    "ordered_tests": ["Breast IHC Panel"],
                    "priority": "routine",
                    "billing_info_present": True,
                },
                "expected_output": {
                    "next_state": "ACCEPTED",
                    "applied_rules": ["ACC-008"],
                    "flags": [],
                    "routing_path": "deterministic",
                },
            },
            {
                "step": 2,
                "event_type": "grossing_complete",
                "event_data": {"outcome": "success"},
                "expected_output": {
                    "next_state": "SAMPLE_PREP_PROCESSING",
                    "applied_rules": ["SP-001"],
                    "flags": [],
                    "routing_path": "deterministic",
                },
            },
        ],
    }


# NOTE (L5): The HE_STAINING → HE_QC pass-through is tested directly in
# test_transitions.py::test_passthrough_he_staining_to_he_qc via resolve_transition().
# A replay-level test for this path would require seeding current_state, which the harness
# does not support. The transitions unit test is the appropriate location.


def test_replay_progress_emits_per_scenario_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """progress=True prints `[i/N] <scenario_id> <status> (<n.n>s)` per scenario.

    Acceptance for the progress flag: each completed scenario produces exactly
    one stdout line carrying its 1-based index, the post-filter total, the
    scenario_id, the verdict, and an elapsed-seconds reading. Off by default.
    """
    import re

    from samantha_server.scenarios.replay import replay

    _write_scenario(tmp_path, "rule_coverage", _all_pass_scenario())
    _write_scenario(tmp_path, "rule_coverage", _state_mismatch_scenario())

    capsys.readouterr()  # discard any prior output
    replay(tmp_path, progress=True)
    out = capsys.readouterr().out
    progress_lines = [line for line in out.splitlines() if re.match(r"^\[\d+/\d+\] ", line)]
    assert len(progress_lines) == 2, f"expected 2 progress lines, got {len(progress_lines)}:\n{out}"
    # Patterns deliberately permissive on scenario_id (\S+) and status (\w+) so
    # the test does not pin a specific corpus prefix or status vocabulary. The
    # production line is `f"[{i}/{total}] {scenario_id} {status} ({elapsed:.1f}s)"`;
    # any prefix (LR-, QR-, SC-, SP-, …) and any future ScenarioVerdict.status
    # literal (e.g. anything may add) is accepted.
    pat = re.compile(
        r"^\[(?P<i>\d+)/(?P<n>\d+)\] (?P<sid>\S+) (?P<status>\w+) "
        r"\((?P<elapsed>\d+\.\d+)s\)$"
    )
    seen_indices = []
    for line in progress_lines:
        m = pat.match(line)
        assert m, f"line does not match progress format: {line!r}"
        assert m.group("n") == "2"
        seen_indices.append(int(m.group("i")))
    assert seen_indices == [1, 2], f"indices out of order: {seen_indices}"


def test_replay_progress_off_by_default(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """With progress unset (default), no `[i/N]` lines reach stdout."""
    import re

    from samantha_server.scenarios.replay import replay

    _write_scenario(tmp_path, "rule_coverage", _all_pass_scenario())

    capsys.readouterr()
    replay(tmp_path)
    out = capsys.readouterr().out
    assert not any(re.match(r"^\[\d+/\d+\] ", line) for line in out.splitlines())


def test_replay_progress_total_reflects_post_filter_count(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``N`` in `[i/N]` reflects the post-filter scenario count, not the corpus size.

    Documents the contract from ``_replay_all_async`` — ``include_categories``
    is applied by the caller before scenarios reach the loop, so progress
    lines see only the filtered set. If a future change moved filtering
    inside the loop, ``N`` would still match the loop's view (correct), but
    if filtering ever vanished, every scenario would inflate ``N`` and this
    test would fail.
    """
    import re

    from samantha_server.scenarios.replay import replay

    # 2 rule_coverage + 1 accumulated_state = 3 total, only 2 after filter.
    _write_scenario(tmp_path, "rule_coverage", _all_pass_scenario())
    _write_scenario(tmp_path, "rule_coverage", _state_mismatch_scenario())
    _write_scenario(tmp_path, "accumulated_state", _advance_sample_prep_scenario())

    capsys.readouterr()
    replay(tmp_path, progress=True, include_categories={"rule_coverage"})
    out = capsys.readouterr().out
    progress_lines = [line for line in out.splitlines() if re.match(r"^\[\d+/\d+\] ", line)]
    assert len(progress_lines) == 2, (
        f"expected 2 lines after rule_coverage filter, got {len(progress_lines)}:\n{out}"
    )
    for line in progress_lines:
        m = re.match(r"^\[\d+/(?P<n>\d+)\] ", line)
        assert m and m.group("n") == "2", f"N must equal post-filter count (2), got line: {line!r}"


def test_replay_all_pass_scenario(tmp_path: Path) -> None:
    from samantha_server.scenarios.replay import replay

    _write_scenario(tmp_path, "rule_coverage", _all_pass_scenario())

    report = replay(tmp_path)
    assert report.included_total == 1
    assert len(report.scenario_verdicts) == 1
    verdict = report.scenario_verdicts[0]
    assert verdict.status == "pass"
    assert report.included_accuracy == 1.0


def test_replay_state_mismatch(tmp_path: Path) -> None:
    from samantha_server.scenarios.replay import replay

    _write_scenario(tmp_path, "rule_coverage", _state_mismatch_scenario())

    report = replay(tmp_path)
    verdict = report.scenario_verdicts[0]
    assert verdict.status == "fail"
    step_verdict = verdict.step_verdicts[0]
    assert step_verdict.status == "mismatch_state"


def test_replay_rules_mismatch(tmp_path: Path) -> None:
    from samantha_server.scenarios.replay import replay

    _write_scenario(tmp_path, "rule_coverage", _rules_mismatch_scenario())

    report = replay(tmp_path)
    verdict = report.scenario_verdicts[0]
    assert verdict.status == "fail"
    step_verdict = verdict.step_verdicts[0]
    assert step_verdict.status == "mismatch_rules"


def test_replay_flags_mismatch(tmp_path: Path) -> None:
    from samantha_server.scenarios.replay import replay

    _write_scenario(tmp_path, "rule_coverage", _flags_mismatch_scenario())

    report = replay(tmp_path)
    verdict = report.scenario_verdicts[0]
    assert verdict.status == "fail"
    step_verdict = verdict.step_verdicts[0]
    assert step_verdict.status == "mismatch_flags"


def test_replay_advance_sample_prep(tmp_path: Path) -> None:
    from samantha_server.scenarios.replay import replay

    _write_scenario(tmp_path, "accumulated_state", _advance_sample_prep_scenario())

    report = replay(tmp_path)
    verdict = report.scenario_verdicts[0]
    assert verdict.status == "pass", [sv.status for sv in verdict.step_verdicts]


def test_replay_threads_unemitted_vocab_flag_forward_without_crash(tmp_path: Path) -> None:
    """The corpus-replay flag-threading path must accept
    a vocabulary-valid flag in `expected_flags` even when no engine rule emits
    it. The harness sets `flags = frozenset(step.expected_flags)` after each
    step (`_replay_scenario_async`); the next step's `SpecimenContext`
    construction must succeed.

    Before widened `VALID_FLAGS`, SC-092 step 2 errored at Pydantic
    validation when step 1's expected `FIXATION_WARNING` reached the
    `SpecimenContext` constructor. This test pins that the threading path
    no longer crashes for any flag now in the vocabulary.
    """
    from samantha_server.scenarios.replay import replay

    scenario = {
        "scenario_id": "SC-VOCAB01",
        "category": "accumulated_state",
        "description": "step 1 expected_flags carries FIXATION_WARNING; step 2 must not crash",
        "events": [
            {
                "step": 1,
                "event_type": "order_received",
                "event_data": {
                    "patient_name": "TEST, Vocab",
                    "age": 50,
                    "sex": "F",
                    "specimen_type": "biopsy",
                    "anatomic_site": "breast",
                    "fixative": "formalin",
                    "fixation_time_hours": 7.0,
                    "ordered_tests": ["Breast IHC Panel"],
                    "priority": "routine",
                    "billing_info_present": True,
                },
                "expected_output": {
                    "next_state": "ACCEPTED",
                    "applied_rules": ["ACC-008"],
                    "flags": ["FIXATION_WARNING"],
                    "routing_path": "deterministic",
                },
            },
            {
                "step": 2,
                "event_type": "grossing_complete",
                "event_data": {"outcome": "success"},
                "expected_output": {
                    "next_state": "SAMPLE_PREP_PROCESSING",
                    "applied_rules": ["SP-001"],
                    "flags": ["FIXATION_WARNING"],
                    "routing_path": "deterministic",
                },
            },
        ],
    }
    _write_scenario(tmp_path, "accumulated_state", scenario)

    report = replay(tmp_path)
    verdict = report.scenario_verdicts[0]
    # Both steps must reach a verdict — neither errors at SpecimenContext.
    assert len(verdict.step_verdicts) == 2
    statuses = [sv.status for sv in verdict.step_verdicts]
    assert "error" not in statuses, statuses
    # Step 1 mismatches on flags (engine doesn't emit FIXATION_WARNING).
    # Step 2 passes because the harness threads the EXPECTED flag forward,
    # so the engine sees it on input and SP-001's set_flags=[]/clear_flags=[]
    # leaves it on output, matching expected.
    assert verdict.step_verdicts[0].status == "mismatch_flags"
    assert verdict.step_verdicts[1].status == "pass"


# HE_STAINING → HE_QC pass-through is tested in test_transitions.py directly.
# See test_passthrough_he_staining_to_he_qc there. No replay-level test needed.


def test_two_consecutive_replay_calls_use_same_rule_index(tmp_path: Path) -> None:
    """Two consecutive replay() calls without explicit rule_index reuse the same instance (P1)."""
    from samantha_server.scenarios import replay as replay_module

    # Reset cache to ensure a clean test
    replay_module._DEFAULT_RULE_INDEX = None

    _write_scenario(tmp_path, "rule_coverage", _all_pass_scenario())

    # Intercept _get_default_rule_index calls to track identity
    original = replay_module._get_default_rule_index
    captured_indices = []

    def tracking_get_index():
        idx = original()
        captured_indices.append(id(idx))
        return idx

    replay_module._get_default_rule_index = tracking_get_index  # type: ignore[assignment]
    try:
        replay_module.replay(tmp_path)
        replay_module.replay(tmp_path)
    finally:
        replay_module._get_default_rule_index = original

    assert len(captured_indices) == 2
    assert captured_indices[0] == captured_indices[1], (
        "Two consecutive replay() calls should reuse the same RuleIndex singleton"
    )


def test_accuracy_report_has_included_pass_and_overall_pass(tmp_path: Path) -> None:
    """AccuracyReport must expose included_pass and overall_pass fields (Q2)."""
    from samantha_server.scenarios.replay import replay

    _write_scenario(tmp_path, "rule_coverage", _all_pass_scenario())
    report = replay(tmp_path)

    assert report.included_pass == 1
    assert report.overall_pass == 1


def test_accuracy_report_has_p99_latency_us(tmp_path: Path) -> None:
    """AccuracyReport.p99_latency_us is a non-None int when at least one
    deterministic-category, non-skiplisted step is replayed.

    The value itself can be 0 on fast hosts where evaluation finishes in
    under 1µs (StepVerdict.latency_us truncates via ``// 1_000``); the
    contract is `>= 0`, not `> 0`. The acceptance / regression gates pin
    the upper bound (< 10ms); this test pins only the field's presence
    and integral type.
    """
    from samantha_server.scenarios.replay import replay

    _write_scenario(tmp_path, "rule_coverage", _all_pass_scenario())
    report = replay(tmp_path)

    assert isinstance(report.p99_latency_us, int)


def test_accuracy_report_p99_latency_us_is_none_for_empty_corpus(tmp_path: Path) -> None:
    """Empty deterministic bucket → no latency samples → p99_latency_us is None
    (rather than 0, which would falsely satisfy a < threshold check)."""
    from samantha_server.scenarios.replay import replay

    # No scenarios written; bucket is empty.
    report = replay(tmp_path)

    assert report.p99_latency_us is None


def test_replay_included_vs_overall_accuracy(tmp_path: Path) -> None:
    from samantha_server.scenarios.replay import replay

    # One in-bucket scenario (passes), one out-of-bucket scenario (irrelevant to included).
    # Hallucination is now in-bucket; use llm_review which remains out-of-bucket.
    _write_scenario(tmp_path, "rule_coverage", _all_pass_scenario())
    oob = {
        "scenario_id": "SC-RP07",
        "category": "llm_review",
        "description": "Out of bucket — llm_review stays out of included_accuracy",
        "events": [
            {
                "step": 1,
                "event_type": "order_received",
                "event_data": {
                    "patient_name": "TEST, Ghost",
                    "age": 45,
                    "sex": "F",
                    "specimen_type": "biopsy",
                    "anatomic_site": "breast",
                    "fixative": "formalin",
                    "fixation_time_hours": 24.0,
                    "ordered_tests": ["Breast IHC Panel"],
                    "priority": "routine",
                    "billing_info_present": True,
                },
                "expected_output": {
                    "next_state": "DO_NOT_PROCESS",  # wrong — causes fail
                    "applied_rules": [],
                    "flags": [],
                    "routing_path": "deterministic",
                },
            }
        ],
    }
    _write_scenario(tmp_path, "llm_review", oob)

    report = replay(tmp_path)
    assert report.included_total == 1
    assert report.overall_total == 2
    assert report.included_accuracy == 1.0
    assert report.overall_accuracy < 1.0


def test_replay_uses_expected_state_for_next_step(tmp_path: Path) -> None:
    """Step failures should not cascade — next step uses expected not predicted state."""
    from samantha_server.scenarios.replay import replay

    # Step 1: expects DO_NOT_PROCESS (wrong — engine returns ACCEPTED)
    # Step 2: grossing_complete — should still run against ACCEPTED (expected from step 1)
    scenario = {
        "scenario_id": "SC-RP08",
        "category": "rule_coverage",
        "description": "State failure isolation — no cascade",
        "events": [
            {
                "step": 1,
                "event_type": "order_received",
                "event_data": {
                    "patient_name": "TEST, Helen",
                    "age": 45,
                    "sex": "F",
                    "specimen_type": "biopsy",
                    "anatomic_site": "breast",
                    "fixative": "formalin",
                    "fixation_time_hours": 24.0,
                    "ordered_tests": ["Breast IHC Panel"],
                    "priority": "routine",
                    "billing_info_present": True,
                },
                "expected_output": {
                    "next_state": "DO_NOT_PROCESS",  # intentionally wrong
                    "applied_rules": ["ACC-008"],
                    "flags": [],
                    "routing_path": "deterministic",
                },
            },
            {
                "step": 2,
                "event_type": "grossing_complete",
                "event_data": {"outcome": "success"},
                "expected_output": {
                    "next_state": "SAMPLE_PREP_PROCESSING",
                    "applied_rules": ["SP-001"],
                    "flags": [],
                    "routing_path": "deterministic",
                },
            },
        ],
    }
    _write_scenario(tmp_path, "rule_coverage", scenario)

    report = replay(tmp_path)
    verdict = report.scenario_verdicts[0]
    # Step 1 fails (state mismatch). Step 2 runs against expected next_state of step 1.
    # That means step 2 uses "DO_NOT_PROCESS" as current_state (from the wrong expected).
    # That means grossing_complete at DO_NOT_PROCESS won't match SP-001 either.
    # The key invariant is: step 2 must NOT be impacted by the predicted state from step 1.
    # Both steps may fail, but for independent reasons.
    step1 = verdict.step_verdicts[0]
    assert step1.status == "mismatch_state"


# ---------------------------------------------------------------------------
# W3 / T4 — Skiplist validation: empty/null URL raises ValueError
# S2 — Orphan skiplist entries (scenario passes) emit a warning to stderr
# ---------------------------------------------------------------------------


def _write_skiplist(tmp_path: Path, skipped: dict) -> None:  # type: ignore[type-arg]
    (tmp_path / ".skiplist.json").write_text(json.dumps({"_comment": "test", "skipped": skipped}))


def test_skiplist_empty_url_raises(tmp_path: Path) -> None:
    """_load_skiplist must raise ValueError when any entry has an empty URL."""
    from samantha_server.scenarios.replay import _load_skiplist

    _write_skiplist(tmp_path, {"SC-999": ""})

    with pytest.raises(ValueError, match="SC-999"):
        _load_skiplist(tmp_path)


def test_skiplist_null_url_raises(tmp_path: Path) -> None:
    """_load_skiplist must raise ValueError when any entry has a null URL."""
    from samantha_server.scenarios.replay import _load_skiplist

    _write_skiplist(tmp_path, {"SC-999": None})

    with pytest.raises(ValueError, match="SC-999"):
        _load_skiplist(tmp_path)


def test_skiplist_valid_url_does_not_raise(tmp_path: Path) -> None:
    """_load_skiplist with valid URLs must not raise."""
    from samantha_server.scenarios.replay import _load_skiplist

    _write_skiplist(tmp_path, {"SC-001": "https://github.com/org/repo/issues/1"})
    result = _load_skiplist(tmp_path)
    assert result == {"SC-001": "https://github.com/org/repo/issues/1"}


def test_orphan_skiplist_entry_warns_to_stderr(tmp_path: Path, capsys) -> None:
    """main() emits a warning to stderr when a skiplisted scenario passes."""
    from samantha_server.scenarios.replay import main

    # Write a passing scenario
    _write_scenario(tmp_path, "rule_coverage", _all_pass_scenario())
    # Skiplist SC-RP01 (which will pass)
    _write_skiplist(tmp_path, {"SC-RP01": "https://github.com/org/repo/issues/99"})

    main([str(tmp_path)])

    captured = capsys.readouterr()
    assert "orphan" in captured.err.lower() or "SC-RP01" in captured.err


# ---------------------------------------------------------------------------
# S1 — Re-raise for in-bucket categories; log traceback for out-of-bucket
# ---------------------------------------------------------------------------


def _bad_data_scenario(scenario_id: str, category: str) -> dict:  # type: ignore[type-arg]
    """A scenario with malformed event_data shaped to break Order construction.

    Originally relied on a missing required field (specimen_type) to trip
    the Pydantic validator. After relaxed those fields to str | None
    and switched _build_order to .get() accessors, the harness no longer
    raises on missing keys — the bad-shape lever moved to ordered_tests:
    a non-iterable value here makes _build_order's tuple(ordered_tests_raw)
    raise TypeError, which is the failure mode this test pair exercises.
    """
    return {
        "scenario_id": scenario_id,
        "category": category,
        "description": "Deliberately broken scenario to trigger engine error",
        "events": [
            {
                "step": 1,
                "event_type": "order_received",
                "event_data": {
                    "patient_name": "TEST, Error",
                    "age": 45,
                    "sex": "F",
                    "specimen_type": "biopsy",
                    "anatomic_site": "breast",
                    "fixative": "formalin",
                    "fixation_time_hours": 24.0,
                    # Non-iterable ordered_tests — tuple(42) raises TypeError
                    # inside _build_order.
                    "ordered_tests": 42,
                    "priority": "routine",
                    "billing_info_present": True,
                },
                "expected_output": {
                    "next_state": "ACCEPTED",
                    "applied_rules": [],
                    "flags": [],
                    "routing_path": "deterministic",
                },
            }
        ],
    }


def test_exception_in_deterministic_category_reraises(tmp_path: Path) -> None:
    """Exceptions in in-bucket (deterministic) categories must re-raise, not be swallowed.

    S1: bare except should re-raise for _DETERMINISTIC_CATEGORIES so engine bugs
    are not silently hidden as dispatch_empty results.
    """
    import pytest

    from samantha_server.scenarios.replay import replay

    _write_scenario(tmp_path, "rule_coverage", _bad_data_scenario("SC-ERR01", "rule_coverage"))

    # The bad scenario carries a non-iterable ordered_tests; _build_order's
    # tuple(...) raises TypeError. Any exception is re-raised (not swallowed)
    # for in-bucket categories.
    with pytest.raises(TypeError):
        replay(tmp_path)


def test_deterministic_exception_recorded_when_reraise_disabled(
    tmp_path: Path, capsys: object
) -> None:
    """`re_raise_on_deterministic_error=False` (parity-replay path) converts a
    deterministic-category exception into a `status="error"` StepVerdict and
    keeps the sweep going instead of halting on the first bad scenario.

    This is what makes the parity smoke surface every gap in one pass — a
    single unmodelled flag (e.g. SC-092's FIXATION_WARNING /) must
    not collapse the diff to one finding.
    """
    from samantha_server.scenarios.replay import replay

    _write_scenario(tmp_path, "rule_coverage", _bad_data_scenario("SC-ERR03", "rule_coverage"))
    # A second scenario that would pass — proves the sweep continues past the error.
    _write_scenario(
        tmp_path,
        "rule_coverage",
        {
            "scenario_id": "SC-ERR04",
            "category": "rule_coverage",
            "description": "well-formed scenario after a deterministic error",
            "events": [
                {
                    "step": 1,
                    "event_type": "order_received",
                    "event_data": {
                        "patient_name": "TEST, Continuation",
                        "age": 50,
                        "sex": "F",
                        "specimen_type": "biopsy",
                        "anatomic_site": "breast",
                        "fixative": "formalin",
                        "fixation_time_hours": 24.0,
                        "ordered_tests": ["Breast IHC Panel"],
                        "priority": "routine",
                        "billing_info_present": True,
                    },
                    "expected_output": {
                        "next_state": "ACCEPTED",
                        "applied_rules": ["ACC-008"],
                        "flags": [],
                        "routing_path": "deterministic",
                    },
                }
            ],
        },
    )

    report = replay(tmp_path, re_raise_on_deterministic_error=False)

    # Assert both scenarios reached the report so a regression
    # that silently dropped one would be caught.
    assert len(report.scenario_verdicts) == 2
    by_id = {v.scenario_id: v for v in report.scenario_verdicts}
    err_step = by_id["SC-ERR03"].step_verdicts[0]
    assert err_step.status == "error"
    # The error happened before routing was determined
    # (`_build_order` raises before either dispatch path commits), so the
    # composer's _step_routing_counts must bin this into errored_pre_routing.
    assert err_step.routing_path is None
    assert by_id["SC-ERR04"].status == "pass"
    # The default (CI) path still re-raises — flag did not silently leak.
    with pytest.raises(TypeError):
        replay(tmp_path)


def test_exception_in_out_of_bucket_category_records_error_and_prints_traceback(
    tmp_path: Path,
    capsys,
) -> None:
    """Exceptions in non-_DETERMINISTIC_CATEGORIES record status='error' and print traceback.

    S1: categories not in _DETERMINISTIC_CATEGORIES (which controls exception re-raise)
    should not crash the harness but should log the traceback so silent failures are visible.
    Note: hallucination is now in _INCLUDED_CATEGORIES (for accuracy counting) but still
    not in _DETERMINISTIC_CATEGORIES, so errors still log-and-continue.

    The bad-data scenario raises TypeError in _build_order before dispatch_event
    is reached, so _llm_client_override is required (to build deps) but the mock
    LLM is never actually called.
    """
    from samantha_server.scenarios.replay import replay

    _write_scenario(tmp_path, "hallucination", _bad_data_scenario("SC-ERR02", "hallucination"))

    mock_llm = _make_mock_llm_client()
    report = replay(tmp_path, _llm_client_override=mock_llm)
    verdict = report.scenario_verdicts[0]
    # Should not raise; should record an error status
    assert verdict.status == "fail"
    assert verdict.step_verdicts[0].status == "error"

    captured = capsys.readouterr()
    assert "Traceback" in captured.err or len(captured.err) > 0


# ---------------------------------------------------------------------------
# Slice 1 — StepVerdict.latency_us
# ---------------------------------------------------------------------------


def test_step_verdict_has_latency_us_for_passing_step(tmp_path: Path) -> None:
    """Every StepVerdict on a successful step must carry latency_us as a non-negative int."""
    from samantha_server.scenarios.replay import replay

    _write_scenario(tmp_path, "rule_coverage", _all_pass_scenario())

    report = replay(tmp_path)
    verdict = report.scenario_verdicts[0]
    assert len(verdict.step_verdicts) > 0
    for sv in verdict.step_verdicts:
        # Real measurement may truncate to 0 on fast hosts (// 1_000), so accept >= 0
        # rather than > 0; the contract is "int | None" with None meaning no-measurement.
        assert sv.latency_us is not None, "Successful step must carry a latency measurement"
        assert isinstance(sv.latency_us, int)
        assert sv.latency_us >= 0


def test_step_verdict_latency_us_is_none_for_error_steps(tmp_path: Path) -> None:
    """Error steps (no decision produced) must have latency_us == None sentinel.

    The bad-data scenario errors in _build_order before dispatch_event is
    reached. _llm_client_override is required to build deps without loading MLX;
    the mock LLM client is never actually called.
    """
    from samantha_server.scenarios.replay import replay

    _write_scenario(tmp_path, "hallucination", _bad_data_scenario("SC-LAT01", "hallucination"))

    mock_llm = _make_mock_llm_client()
    report = replay(tmp_path, _llm_client_override=mock_llm)
    verdict = report.scenario_verdicts[0]
    error_steps = [sv for sv in verdict.step_verdicts if sv.status == "error"]
    assert len(error_steps) > 0
    for sv in error_steps:
        assert sv.latency_us is None, (
            f"Error step latency_us should be None sentinel, got {sv.latency_us}"
        )


# ---------------------------------------------------------------------------
# Slice 2 — bucket broadening: _INCLUDED_CATEGORIES
# ---------------------------------------------------------------------------


def _hallucination_pass_scenario() -> dict:  # type: ignore[type-arg]
    """A hallucination-category scenario using clinical_query event_type.

    Uses clinical_query so it exercises the LLM path via dispatch_event.
    Tests that use this scenario must patch dispatch_event and pass
    _llm_client_override=_make_mock_llm_client() to avoid loading MLX.

    The expected_output reflects the LLM-path response: no rule fires
    (applied_rules=[]), state stays ACCESSIONING (no transition), and
    routing_path='llm'. This is the proper LLM-path hallucination scenario.
    """
    return {
        "scenario_id": "SC-RP10",
        "category": "hallucination",
        "description": "Hallucination scenario — clinical_query LLM path (should pass with mock)",
        "events": [
            {
                "step": 1,
                "event_type": "clinical_query",
                "event_data": {
                    "patient_name": "TEST, Hal",
                    "age": 45,
                    "sex": "F",
                    "specimen_type": "biopsy",
                    "anatomic_site": "breast",
                    "fixative": "formalin",
                    "fixation_time_hours": 24.0,
                    "ordered_tests": ["Breast IHC Panel"],
                    "priority": "routine",
                    "billing_info_present": True,
                    "query": "Is fixation time adequate?",
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


def _unknown_input_order_received_scenario() -> dict:  # type: ignore[type-arg]
    """An unknown_input-category scenario whose
    corpus expectation is deterministic (ACC-004 fires; LLM never called).
    Mirrors SC-100's shape: an FNA specimen that's incompatible with the
    histology workflow.

    Important subtlety — `unknown_input` is in `_LLM_PATH_CATEGORIES` so
    deps DO get built up-front, but the per-step routing predicate must
    still send `order_received` events through `evaluate()`, not
    `dispatch_event`."""
    return {
        "scenario_id": "SC-RP-UI-DET",
        "category": "unknown_input",
        "description": "FNA specimen → DO_NOT_PROCESS via ACC-004 (deterministic)",
        "events": [
            {
                "step": 1,
                "event_type": "order_received",
                "event_data": {
                    "patient_name": "TEST, Unknown",
                    "age": 50,
                    "sex": "F",
                    "specimen_type": "fna",
                    "anatomic_site": "breast",
                    "fixative": "formalin",
                    "fixation_time_hours": 24.0,
                    "ordered_tests": ["Breast IHC Panel"],
                    "priority": "routine",
                    "billing_info_present": True,
                },
                "expected_output": {
                    "next_state": "DO_NOT_PROCESS",
                    "applied_rules": ["ACC-004"],
                    "flags": [],
                    "routing_path": "deterministic",
                },
            }
        ],
    }


def test_unknown_input_order_received_routes_to_deterministic_engine(tmp_path: Path) -> None:
    """The `unknown_input + order_received` shape (SC-100/SC-103)
    must route through `evaluate()` despite the category being in
    `_LLM_PATH_CATEGORIES`. Mirrors `test_hallucination_order_received_*`
    but with the `unknown_input` deps-built path."""
    from samantha_server.scenarios.replay import replay

    _write_scenario(tmp_path, "unknown_input", _unknown_input_order_received_scenario())

    mock_llm = _make_mock_llm_client()
    report = replay(tmp_path, _llm_client_override=mock_llm)

    verdict = report.scenario_verdicts[0]
    step = verdict.step_verdicts[0]
    assert step.status == "pass", (
        f"Expected pass; got {step.status}. expected_rules="
        f"{step.expected.applied_rules} predicted_rules={step.predicted.applied_rules}"
    )
    assert step.routing_path == "deterministic"
    assert verdict.status == "pass"


def test_routing_path_mismatch_fires_when_corpus_annotates_llm_but_engine_runs_deterministic(
    tmp_path: Path,
) -> None:
    """When the corpus annotates `routing_path: "llm"` for a
    step but the harness's per-step predicate routes through `evaluate()`, the
    verdict must be `mismatch_routing_path`. This catches the latent class of
    divergences that motivated M1 (predicate omitting `PreflightMissing`) — any
    such divergence surfaces as a clean mismatch instead of a misleading pass.
    """
    from samantha_server.scenarios.replay import replay

    # Scenario whose state/rules/flags would all match the deterministic
    # engine's output for an `order_received` event, but whose corpus
    # annotation says it should have routed via LLM.
    scenario = {
        "scenario_id": "SC-RP-ROUTING",
        "category": "unknown_input",
        "description": "Corpus says routing_path=llm, but order_received → ACC-008 deterministic",
        "events": [
            {
                "step": 1,
                "event_type": "order_received",
                "event_data": {
                    "patient_name": "TEST, Routing",
                    "age": 50,
                    "sex": "F",
                    "specimen_type": "biopsy",
                    "anatomic_site": "breast",
                    "fixative": "formalin",
                    "fixation_time_hours": 24.0,
                    "ordered_tests": ["Breast IHC Panel"],
                    "priority": "routine",
                    "billing_info_present": True,
                },
                "expected_output": {
                    "next_state": "ACCEPTED",
                    "applied_rules": ["ACC-008"],
                    "flags": [],
                    # Wrong on purpose — the corpus says LLM but the engine
                    # will route deterministic for order_received in
                    # ACCESSIONING state. This MUST surface as a mismatch.
                    "routing_path": "llm",
                },
            }
        ],
    }
    _write_scenario(tmp_path, "unknown_input", scenario)

    mock_llm = _make_mock_llm_client()
    report = replay(tmp_path, _llm_client_override=mock_llm)
    step = report.scenario_verdicts[0].step_verdicts[0]
    assert step.status == "mismatch_routing_path", (
        f"Expected mismatch_routing_path; got {step.status}. "
        f"expected_routing_path=llm, actual routing_path={step.routing_path}"
    )
    assert step.routing_path == "deterministic"


def test_routing_path_unannotated_fixture_does_not_mismatch(tmp_path: Path) -> None:
    """A fixture without `routing_path` in expected_output
    (older corpora) must not trigger mismatch_routing_path — the comparator
    treats `expected_routing_path is None` as 'don't compare'."""
    from samantha_server.scenarios.replay import replay

    scenario = {
        "scenario_id": "SC-RP-NOROUTING",
        "category": "rule_coverage",
        "description": "Older fixture without routing_path annotation",
        "events": [
            {
                "step": 1,
                "event_type": "order_received",
                "event_data": {
                    "patient_name": "TEST, NoRoute",
                    "age": 50,
                    "sex": "F",
                    "specimen_type": "biopsy",
                    "anatomic_site": "breast",
                    "fixative": "formalin",
                    "fixation_time_hours": 24.0,
                    "ordered_tests": ["Breast IHC Panel"],
                    "priority": "routine",
                    "billing_info_present": True,
                },
                "expected_output": {
                    "next_state": "ACCEPTED",
                    "applied_rules": ["ACC-008"],
                    "flags": [],
                    # Deliberately no routing_path key
                },
            }
        ],
    }
    _write_scenario(tmp_path, "rule_coverage", scenario)

    report = replay(tmp_path)
    step = report.scenario_verdicts[0].step_verdicts[0]
    assert step.status == "pass"


def _hallucination_order_received_scenario() -> dict:  # type: ignore[type-arg]
    """A hallucination-category scenario whose corpus expectation is
    deterministic (ACC-008 fires; LLM is never called). Mirrors SC-106's shape:
    extra clinical context the rules ignore, but `event_type='order_received'`
    that the deterministic engine handles directly.

    The current harness (legacy fix) force-routes every step in an
    LLM-path-category scenario through `dispatch_event`, which raises
    `NotImplementedError` for `order_received`. That mis-attributes a
    deterministically-handlable scenario as an LLM-path error.
    """
    return {
        "scenario_id": "SC-RP-HAL-DET",
        "category": "hallucination",
        "description": (
            "Hallucination-category scenario whose corpus expectation is "
            "deterministic (extra clinical context, but ACC-008 fires)"
        ),
        "events": [
            {
                "step": 1,
                "event_type": "order_received",
                "event_data": {
                    "patient_name": "TEST, Halu",
                    "age": 50,
                    "sex": "F",
                    "specimen_type": "biopsy",
                    "anatomic_site": "breast",
                    "fixative": "formalin",
                    "fixation_time_hours": 24.0,
                    "ordered_tests": ["Breast IHC Panel"],
                    "priority": "routine",
                    "billing_info_present": True,
                    "extra_clinical_context": "BRCA1 mutation, prior mastectomy",
                },
                "expected_output": {
                    "next_state": "ACCEPTED",
                    "applied_rules": ["ACC-008"],
                    "flags": [],
                    "routing_path": "deterministic",
                },
            }
        ],
    }


def _llm_review_handoff_scenario() -> dict:  # type: ignore[type-arg]
    """A 2-step scenario that exercises the legitimate LLM-review
    handoff path. Step 1 fires ACC-010 (unrecognized specimen type) →
    `PENDING_LLM_REVIEW`. Step 2's input current_state is
    `PENDING_LLM_REVIEW`; the new routing predicate must route step 2
    through `dispatch_event()` so `handle_pending_llm_review` runs.

    Uses `unknown_input` category (in `_LLM_PATH_CATEGORIES`) so deps are
    built up-front. Without that, `has_llm_path` is False and the predicate's
    deps-gate suppresses dispatch_event regardless of state — which is correct
    behaviour for purely-deterministic-category scenarios that can't reach
    PENDING_LLM_REVIEW anyway.
    """
    return {
        "scenario_id": "SC-RP-LLM-HANDOFF",
        "category": "unknown_input",
        "description": "ACC-010 unknown specimen → PENDING_LLM_REVIEW; next step routes to LLM",
        "events": [
            {
                "step": 1,
                "event_type": "order_received",
                "event_data": {
                    "patient_name": "TEST, LlmHandoff",
                    "age": 50,
                    "sex": "F",
                    "specimen_type": "novel_specimen_type",  # not in whitelist or blacklist
                    "anatomic_site": "breast",
                    "fixative": "formalin",
                    "fixation_time_hours": 24.0,
                    "ordered_tests": ["Breast IHC Panel"],
                    "priority": "routine",
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
                "event_type": "specimen_review_complete",
                "event_data": {"reviewer_decision": "accept_as_biopsy"},
                "expected_output": {
                    "next_state": "ACCEPTED",
                    "applied_rules": [],
                    # The mock dispatch doesn't change flags, and the threaded
                    # accumulator carries LLM_REVIEW_REQUESTED forward from step 1's
                    # accumulated state. Align expected with the mock's behavior so
                    # the test pins routing_path/status, not flag-clearance semantics
                    # (a real handler would clear the flag; a real-handler test
                    # belongs elsewhere).
                    "flags": ["LLM_REVIEW_REQUESTED"],
                    "routing_path": "llm",
                },
            },
        ],
    }


def test_step_after_pending_llm_review_routes_through_dispatch(tmp_path: Path) -> None:
    """When a deterministic rule lands the order in
    `PENDING_LLM_REVIEW`, the next step must route through `dispatch_event()`
    so `handle_pending_llm_review` runs. The new routing predicate must NOT
    suppress this — it's the legitimate LLM-handoff path the predicate is
    designed to preserve."""
    import samantha_server.api.routing as _routing_mod
    from samantha_server.scenarios.replay import replay

    # Write under unknown_input/ so the category derived by load_scenarios
    # (parent directory name) puts the scenario in _LLM_PATH_CATEGORIES,
    # which is what gates deps construction up-front.
    _write_scenario(tmp_path, "unknown_input", _llm_review_handoff_scenario())

    mock_llm = _make_mock_llm_client()
    captured_routing: list[str] = []
    _orig_dispatch = _routing_mod.dispatch_event

    async def _selective_fake(ctx, *, session_id, **kwargs):  # type: ignore[no-untyped-def]
        # Intercept only LLM-routed steps; delegate deterministic steps to real dispatch.
        if ctx.current_state == "PENDING_LLM_REVIEW" or ctx.event.event_type == "clinical_query":
            captured_routing.append("llm")
            return await _fake_dispatch_with_receipt(
                session_id, latency_us=100, next_state="ACCEPTED", **kwargs
            )
        return await _orig_dispatch(ctx, session_id=session_id, **kwargs)

    with patch("samantha_server.api.routing.dispatch_event", side_effect=_selective_fake):
        report = replay(tmp_path, _llm_client_override=mock_llm)

    verdict = report.scenario_verdicts[0]
    # Step 1: deterministic; ACC-010 should fire and land in PENDING_LLM_REVIEW.
    step1 = verdict.step_verdicts[0]
    assert step1.routing_path == "deterministic", (
        f"Step 1 should be deterministic; got {step1.routing_path}"
    )
    # Step 2: must route through dispatch_event — the predicate's gate (a).
    step2 = verdict.step_verdicts[1]
    assert step2.routing_path == "llm", (
        f"Step 2 should route through dispatch_event when current_state is "
        f"PENDING_LLM_REVIEW; got {step2.routing_path}"
    )
    # Pin status separately so a garbled mock-dispatch
    # return that produced a state mismatch wouldn't slip past the
    # routing-path-only assertion.
    assert step2.status == "pass", (
        f"Step 2 should pass; got {step2.status}. expected_state="
        f"{step2.expected.next_state} predicted_state={step2.predicted.next_state}"
    )
    assert captured_routing == ["llm"], "dispatch_event must fire exactly once"


def test_routing_predicate_both_clauses_true_routes_through_dispatch(tmp_path: Path) -> None:
    """The routing predicate uses logical OR. A step where both
    clauses are simultaneously true (current_state == PENDING_LLM_REVIEW
    AND event_type == clinical_query) must route through dispatch_event.
    Pins the OR semantics so a future refactor swapping `or` for `xor`
    wouldn't slip through."""
    import samantha_server.api.routing as _routing_mod
    from samantha_server.scenarios.replay import replay

    scenario = {
        "scenario_id": "SC-RP-BOTH",
        "category": "unknown_input",
        "description": "Step 1 lands in PENDING_LLM_REVIEW; step 2 is clinical_query from there",
        "events": [
            {
                "step": 1,
                "event_type": "order_received",
                "event_data": {
                    "patient_name": "TEST, Both",
                    "age": 50,
                    "sex": "F",
                    "specimen_type": "novel_specimen_type",
                    "anatomic_site": "breast",
                    "fixative": "formalin",
                    "fixation_time_hours": 24.0,
                    "ordered_tests": ["Breast IHC Panel"],
                    "priority": "routine",
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
                # Both clauses simultaneously true: state IS PENDING_LLM_REVIEW
                # AND event_type IS clinical_query.
                "step": 2,
                "event_type": "clinical_query",
                "event_data": {"query": "Is this acceptable?"},
                "expected_output": {
                    "next_state": "ACCEPTED",
                    "applied_rules": [],
                    "flags": [],
                    "routing_path": "llm",
                },
            },
        ],
    }
    _write_scenario(tmp_path, "unknown_input", scenario)

    mock_llm = _make_mock_llm_client()
    captured: list[str] = []
    _orig_dispatch = _routing_mod.dispatch_event

    async def _selective_fake(ctx, **kwargs):  # type: ignore[no-untyped-def]
        if ctx.current_state == "PENDING_LLM_REVIEW" or ctx.event.event_type == "clinical_query":
            captured.append("llm")
            return _make_fake_dispatch_result(latency_us=100, next_state="ACCEPTED")
        return await _orig_dispatch(ctx, **kwargs)

    with patch("samantha_server.api.routing.dispatch_event", side_effect=_selective_fake):
        report = replay(tmp_path, _llm_client_override=mock_llm)

    step2 = report.scenario_verdicts[0].step_verdicts[1]
    assert step2.routing_path == "llm"
    assert captured == ["llm"], "dispatch_event must fire exactly once for step 2"


def test_routing_predicate_state_oscillation_routes_correctly(tmp_path: Path) -> None:
    """A 3-step scenario where the state oscillates
    PENDING_LLM_REVIEW → resolved → back to PENDING_LLM_REVIEW. The mutable
    `current_state` loop variable must produce LLM routing on both
    PENDING_LLM_REVIEW steps and deterministic routing on the resolved
    step. A future memoization refactor that cached `step_needs_llm_dispatch`
    would silently break this."""
    from samantha_server.scenarios.replay import replay

    scenario = {
        "scenario_id": "SC-RP-OSC",
        "category": "unknown_input",
        "description": "Oscillate between PENDING_LLM_REVIEW and resolved states",
        "events": [
            {
                "step": 1,
                "event_type": "order_received",
                "event_data": {
                    "patient_name": "TEST, Osc",
                    "age": 50,
                    "sex": "F",
                    "specimen_type": "novel_specimen_type",
                    "anatomic_site": "breast",
                    "fixative": "formalin",
                    "fixation_time_hours": 24.0,
                    "ordered_tests": ["Breast IHC Panel"],
                    "priority": "routine",
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
                "event_type": "specimen_review_complete",
                "event_data": {"reviewer_decision": "request_more_info"},
                "expected_output": {
                    # Returns to a state that's not PENDING_LLM_REVIEW.
                    "next_state": "ACCESSIONING",
                    "applied_rules": [],
                    "flags": [],
                    "routing_path": "llm",
                },
            },
            {
                "step": 3,
                "event_type": "specimen_review_complete",
                "event_data": {"reviewer_decision": "needs_pathologist"},
                "expected_output": {
                    "next_state": "PENDING_LLM_REVIEW",
                    "applied_rules": [],
                    "flags": [],
                    # Step 3's input current_state is ACCESSIONING (from
                    # step 2's expected_next_state). Event is
                    # specimen_review_complete (not clinical_query). Neither
                    # gate fires → deterministic. This is the predicate's
                    # honest behaviour; the corpus annotation captures the
                    # divergence if it matters.
                    "routing_path": "deterministic",
                },
            },
        ],
    }
    _write_scenario(tmp_path, "unknown_input", scenario)

    import samantha_server.api.routing as _routing_mod

    mock_llm = _make_mock_llm_client()
    routing_calls: list[str] = []
    _orig_dispatch = _routing_mod.dispatch_event

    async def _selective_fake(ctx, *, session_id, **kwargs):  # type: ignore[no-untyped-def]
        if ctx.current_state == "PENDING_LLM_REVIEW" or ctx.event.event_type == "clinical_query":
            routing_calls.append("llm")
            return await _fake_dispatch_with_receipt(
                session_id, latency_us=100, next_state="ACCESSIONING", **kwargs
            )
        return await _orig_dispatch(ctx, session_id=session_id, **kwargs)

    with patch("samantha_server.api.routing.dispatch_event", side_effect=_selective_fake):
        report = replay(tmp_path, _llm_client_override=mock_llm)

    verdicts = report.scenario_verdicts[0].step_verdicts
    assert verdicts[0].routing_path == "deterministic", (
        f"Step 1 (order_received from ACCESSIONING): {verdicts[0].routing_path}"
    )
    assert verdicts[1].routing_path == "llm", (
        f"Step 2 (specimen_review_complete from PENDING_LLM_REVIEW): {verdicts[1].routing_path}"
    )
    assert verdicts[2].routing_path == "deterministic", (
        f"Step 3 (specimen_review_complete from ACCESSIONING): {verdicts[2].routing_path}"
    )
    # dispatch_event fires exactly once — for step 2.
    assert routing_calls == ["llm"]


def test_hallucination_order_received_routes_to_deterministic_engine(tmp_path: Path) -> None:
    """/ Option A: the harness must route order_received events to
    `evaluate()` regardless of scenario category. Force-routing through
    `dispatch_event` for every step in an LLM-path category mis-attributes
    deterministically-handlable corpus fixtures (SC-106..SC-113, SC-100,
    SC-103) as LLM-path errors. The category label means "this scenario
    *could* route to LLM at some step" (e.g., via ACC-010 →
    PENDING_LLM_REVIEW), not "every step goes through dispatch_event."
    """
    from samantha_server.scenarios.replay import replay

    _write_scenario(tmp_path, "hallucination", _hallucination_order_received_scenario())

    # Provide a mock LLM client because deps construction still happens
    # up-front (has_llm_path is True), but no LLM call should fire.
    mock_llm = _make_mock_llm_client()
    report = replay(tmp_path, _llm_client_override=mock_llm)

    verdict = report.scenario_verdicts[0]
    step = verdict.step_verdicts[0]
    # Pre-fix: dispatch_event raises NotImplementedError → status="error".
    # Post-fix: evaluate() runs → ACC-008 → status="pass".
    assert step.status == "pass", (
        f"Expected pass; got {step.status}. expected_rules="
        f"{step.expected.applied_rules} predicted_rules={step.predicted.applied_rules}"
    )
    assert step.routing_path == "deterministic"
    assert verdict.status == "pass"


def test_included_categories_contains_llm_path_categories() -> None:
    """_INCLUDED_CATEGORIES must include hallucination, unknown_input, and query."""
    from samantha_server.scenarios.replay import _INCLUDED_CATEGORIES

    assert "hallucination" in _INCLUDED_CATEGORIES
    assert "unknown_input" in _INCLUDED_CATEGORIES
    assert "query" in _INCLUDED_CATEGORIES


def test_deterministic_categories_unchanged() -> None:
    """_DETERMINISTIC_CATEGORIES must still contain only the three deterministic categories."""
    from samantha_server.scenarios.replay import _DETERMINISTIC_CATEGORIES

    assert (
        frozenset({"rule_coverage", "multi_rule", "accumulated_state"}) == _DETERMINISTIC_CATEGORIES
    )


def test_included_categories_is_superset_of_deterministic() -> None:
    """_INCLUDED_CATEGORIES must be a superset of _DETERMINISTIC_CATEGORIES."""
    from samantha_server.scenarios.replay import _DETERMINISTIC_CATEGORIES, _INCLUDED_CATEGORIES

    assert _DETERMINISTIC_CATEGORIES <= _INCLUDED_CATEGORIES


def test_hallucination_scenario_counted_in_included_total(tmp_path: Path) -> None:
    """After bucket broadening, hallucination scenarios count in included_total.

    Uses clinical_query event_type so dispatch_event is exercised via mock.
    _llm_client_override is required to avoid loading MLX in CI.

     review M4: explicitly assert dispatch_event was invoked for the
    hallucination step's clinical_query event. The earlier shape only
    checked included_pass==2, which would still pass if the new routing
    predicate accidentally fell back to evaluate() for clinical_query and
    happened to produce a matching deterministic verdict. This pins the
    routing predicate's clinical_query branch (gate b).
    """
    from samantha_server.scenarios.replay import replay

    _write_scenario(tmp_path, "rule_coverage", _all_pass_scenario())
    _write_scenario(tmp_path, "hallucination", _hallucination_pass_scenario())

    import samantha_server.api.routing as _routing_mod

    mock_llm = _make_mock_llm_client()
    captured_routing: list[str] = []
    _orig_dispatch = _routing_mod.dispatch_event

    async def _selective_fake(ctx, *, session_id, **kwargs):  # type: ignore[no-untyped-def]
        if ctx.current_state == "PENDING_LLM_REVIEW" or ctx.event.event_type == "clinical_query":
            captured_routing.append("llm")
            return await _fake_dispatch_with_receipt(
                session_id, latency_us=100, next_state="ACCESSIONING", **kwargs
            )
        return await _orig_dispatch(ctx, session_id=session_id, **kwargs)

    with patch("samantha_server.api.routing.dispatch_event", side_effect=_selective_fake):
        report = replay(tmp_path, _llm_client_override=mock_llm)

    # Both scenarios should be in included_total now.
    assert report.included_total == 2, f"Expected 2, got {report.included_total}"
    assert report.included_pass == 2
    # dispatch_event must have fired exactly once — for the
    # hallucination scenario's clinical_query step. The rule_coverage scenario
    # is order_received and should NOT have routed through dispatch.
    assert captured_routing == ["llm"], (
        f"dispatch_event must fire exactly once (for clinical_query); got {captured_routing}"
    )
    # Pin routing_path on the hallucination step so a future
    # predicate inversion can't slip through with an evaluated-and-passed shape.
    hallucination_verdict = next(v for v in report.scenario_verdicts if v.scenario_id == "SC-RP10")
    assert hallucination_verdict.step_verdicts[0].routing_path == "llm"


# ---------------------------------------------------------------------------
# Slice 3 — p99_latency_us_llm computation
# ---------------------------------------------------------------------------


def test_p99_latency_us_llm_is_none_when_no_llm_path_scenarios(tmp_path: Path) -> None:
    """When only deterministic-category scenarios are present, p99_latency_us_llm is None."""
    from samantha_server.scenarios.replay import replay

    _write_scenario(tmp_path, "rule_coverage", _all_pass_scenario())

    report = replay(tmp_path)

    assert report.p99_latency_us_llm is None


def test_p99_latency_us_llm_is_non_none_when_hallucination_scenarios_present(
    tmp_path: Path,
) -> None:
    """When hallucination scenarios are present and routed via dispatch_event,
    p99_latency_us_llm is a non-None int.

    Uses clinical_query event_type + mock dispatch_event so the step gets
    routing_path='llm' and contributes to the LLM latency bucket.
    _llm_client_override is required to avoid loading MLX in CI.
    """
    from samantha_server.scenarios.replay import replay

    _write_scenario(tmp_path, "rule_coverage", _all_pass_scenario())
    _write_scenario(tmp_path, "hallucination", _hallucination_pass_scenario())

    import samantha_server.api.routing as _routing_mod

    mock_llm = _make_mock_llm_client()
    _orig_dispatch = _routing_mod.dispatch_event

    async def _selective_fake(ctx, *, session_id, **kwargs):  # type: ignore[no-untyped-def]
        if ctx.current_state == "PENDING_LLM_REVIEW" or ctx.event.event_type == "clinical_query":
            return await _fake_dispatch_with_receipt(
                session_id, latency_us=200, next_state="ACCESSIONING", **kwargs
            )
        return await _orig_dispatch(ctx, session_id=session_id, **kwargs)

    with patch("samantha_server.api.routing.dispatch_event", side_effect=_selective_fake):
        report = replay(tmp_path, _llm_client_override=mock_llm)

    assert isinstance(report.p99_latency_us_llm, int)
    assert report.p99_latency_us_llm >= 0


def test_p99_latency_us_llm_does_not_include_deterministic_latencies(tmp_path: Path) -> None:
    """p99_latency_us and p99_latency_us_llm must be sourced from separate buckets.

    Asserts via the per-bucket sample counts (routing_path-keyed): the
    deterministic scenario contributes one step to the deterministic bucket
    (routing_path='deterministic'); the LLM-path scenario contributes one step
    to the LLM bucket (routing_path='llm'). A regression that merged the two
    populations would either double-count or zero one of the buckets.

    Uses clinical_query event_type + mock dispatch_event.
    _llm_client_override is required to avoid loading MLX in CI.
    """
    from samantha_server.scenarios.replay import replay

    _write_scenario(tmp_path, "rule_coverage", _all_pass_scenario())
    _write_scenario(tmp_path, "hallucination", _hallucination_pass_scenario())

    import samantha_server.api.routing as _routing_mod

    mock_llm = _make_mock_llm_client()
    _orig_dispatch = _routing_mod.dispatch_event

    async def _selective_fake(ctx, *, session_id, **kwargs):  # type: ignore[no-untyped-def]
        if ctx.current_state == "PENDING_LLM_REVIEW" or ctx.event.event_type == "clinical_query":
            return await _fake_dispatch_with_receipt(
                session_id, latency_us=300, next_state="ACCESSIONING", **kwargs
            )
        return await _orig_dispatch(ctx, session_id=session_id, **kwargs)

    with patch("samantha_server.api.routing.dispatch_event", side_effect=_selective_fake):
        report = replay(tmp_path, _llm_client_override=mock_llm)

    assert report.p99_latency_us is not None
    assert report.p99_latency_us_llm is not None
    # Bucket counts come from routing_path on each StepVerdict.
    assert report.deterministic_latency_step_count == 1
    assert report.llm_latency_step_count == 1


# ---------------------------------------------------------------------------
# — replay determinism (L-05): two consecutive calls produce identical
# AccuracyReport values, including the new p99_latency_us_llm and the per-bucket
# step counts.
# ---------------------------------------------------------------------------


def test_replay_is_deterministic_across_consecutive_calls(tmp_path: Path) -> None:
    """Two consecutive replay() calls on the same corpus produce equal reports.

    Uses clinical_query event_type for the hallucination scenario + mock
    dispatch_event so the test runs without MLX.
    """
    from samantha_server.scenarios.replay import replay

    _write_scenario(tmp_path, "rule_coverage", _all_pass_scenario())
    _write_scenario(tmp_path, "hallucination", _hallucination_pass_scenario())

    import samantha_server.api.routing as _routing_mod

    mock_llm = _make_mock_llm_client()
    _orig_dispatch = _routing_mod.dispatch_event

    async def _selective_fake(ctx, **kwargs):  # type: ignore[no-untyped-def]
        if ctx.current_state == "PENDING_LLM_REVIEW" or ctx.event.event_type == "clinical_query":
            return _make_fake_dispatch_result(latency_us=150, next_state="ACCESSIONING")
        return await _orig_dispatch(ctx, **kwargs)

    with patch("samantha_server.api.routing.dispatch_event", side_effect=_selective_fake):
        a = replay(tmp_path, _llm_client_override=mock_llm)
        b = replay(tmp_path, _llm_client_override=mock_llm)

    # Compare every field that downstream consumers (CI gate, trend file) read.
    # latency_us values may legitimately differ run-to-run by a few microseconds,
    # so we check structural equivalence rather than ``a == b`` directly.
    assert a.included_accuracy == b.included_accuracy
    assert a.overall_accuracy == b.overall_accuracy
    assert a.included_pass == b.included_pass
    assert a.included_total == b.included_total
    assert a.deterministic_latency_step_count == b.deterministic_latency_step_count
    assert a.llm_latency_step_count == b.llm_latency_step_count
    assert tuple(v.scenario_id for v in a.scenario_verdicts) == tuple(
        v.scenario_id for v in b.scenario_verdicts
    )
    assert tuple(v.status for v in a.scenario_verdicts) == tuple(
        v.status for v in b.scenario_verdicts
    )


def test_step_verdict_latency_us_present_for_dispatch_empty_steps(tmp_path: Path) -> None:
    """dispatch_empty steps still produce a decision; latency_us must be present (non-None)."""
    from samantha_server.scenarios.replay import replay

    # Use the all-pass scenario but override the expected applied_rules so the step
    # mismatches; dispatch_empty requires a step where no rule is dispatched. The simpler
    # path: run the corpus and look for any dispatch_empty verdict.
    _write_scenario(tmp_path, "rule_coverage", _all_pass_scenario())
    report = replay(tmp_path)
    all_steps = [sv for v in report.scenario_verdicts for sv in v.step_verdicts]
    # Sanity: this corpus produces a mix of statuses; the contract we pin is that
    # any non-error step carries a latency measurement, not None.
    non_error = [sv for sv in all_steps if sv.status != "error"]
    assert len(non_error) > 0
    for sv in non_error:
        assert sv.latency_us is not None, (
            f"Non-error step ({sv.status}) must carry latency_us; got None"
        )


# ---------------------------------------------------------------------------
# — LLM client must be built for isolated `llm_review`-only corpora
# ---------------------------------------------------------------------------


def _llm_review_only_scenario() -> dict:  # type: ignore[type-arg]
    """Mirror of `_llm_review_handoff_scenario` but with category=llm_review.

    When the corpus contains only llm_review scenarios, the LLM
    client must still be constructed so step 2 (which lands in
    PENDING_LLM_REVIEW after ACC-010) routes through `dispatch_event()`.
    """
    return {
        "scenario_id": "SC-RP-LR-ONLY",
        "category": "llm_review",
        "description": "ACC-010 → PENDING_LLM_REVIEW; step 2 must route through LLM dispatch",
        "events": [
            {
                "step": 1,
                "event_type": "order_received",
                "event_data": {
                    "patient_name": "TEST, LrOnly",
                    "age": 50,
                    "sex": "F",
                    "specimen_type": "novel_specimen_type",
                    "anatomic_site": "breast",
                    "fixative": "formalin",
                    "fixation_time_hours": 24.0,
                    "ordered_tests": ["Breast IHC Panel"],
                    "priority": "routine",
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
                "event_type": "specimen_review_complete",
                "event_data": {"reviewer_decision": "accept_as_biopsy"},
                # applied_rules is empty: this is a routing-only assertion
                # (the fake dispatch returns a decision with no rule applied);
                # the test pins that step 2 reaches dispatch_event, not which
                # rule fires there.
                "expected_output": {
                    "next_state": "ACCEPTED",
                    "applied_rules": [],
                    "flags": ["LLM_REVIEW_REQUESTED"],
                    "routing_path": "llm",
                },
            },
        ],
    }


def test_replay_initializes_llm_client_for_llm_review_only_corpus(tmp_path: Path) -> None:
    """An llm_review-only corpus must build LLM deps so step 2
    routes through `dispatch_event()`. Before the fix, has_llm_path was
    False (llm_review missing from the client-init set), deps stayed
    None, and step 2 silently fell through to evaluate() — leaving the
    order in PENDING_LLM_REVIEW and producing a vacuous 0.0s no-op."""
    from samantha_server.scenarios.replay import replay

    _write_scenario(tmp_path, "llm_review", _llm_review_only_scenario())

    import samantha_server.api.routing as _routing_mod

    mock_llm = _make_mock_llm_client()
    captured_routing: list[str] = []
    _orig_dispatch = _routing_mod.dispatch_event

    async def _selective_fake(ctx, **kwargs):  # type: ignore[no-untyped-def]
        if ctx.current_state == "PENDING_LLM_REVIEW" or ctx.event.event_type == "clinical_query":
            captured_routing.append("llm")
            return _make_fake_dispatch_result(latency_us=100, next_state="ACCEPTED")
        return await _orig_dispatch(ctx, **kwargs)

    with patch("samantha_server.api.routing.dispatch_event", side_effect=_selective_fake):
        report = replay(tmp_path, _llm_client_override=mock_llm)

    # Step 2 must have routed through dispatch_event — the bug being fixed.
    assert captured_routing == ["llm"], (
        "dispatch_event must fire for step 2 of an llm_review-only corpus; "
        f"got {captured_routing} (deps was likely None because llm_review "
        "was missing from the LLM-client-init category set)"
    )
    step2 = report.scenario_verdicts[0].step_verdicts[1]
    assert step2.routing_path == "llm", (
        f"Step 2 should route through dispatch_event; got {step2.routing_path}"
    )


def test_replay_mixed_llm_review_and_query_corpus_dispatches_both(tmp_path: Path) -> None:
    """A corpus containing BOTH an `llm_review` scenario
    (routes via PENDING_LLM_REVIEW gate) and a `query` scenario (routes via
    clinical_query event gate) must build deps once and dispatch both LLM
    steps. Pins that the `any(...)` short-circuit in the new
    `_LLM_CLIENT_CATEGORIES` gate doesn't accidentally skip deps construction
    when the first match is on either category."""
    from samantha_server.scenarios.replay import replay

    _write_scenario(tmp_path, "llm_review", _llm_review_only_scenario())

    query_scenario = {
        "scenario_id": "SC-RP-Q-MIX",
        "category": "query",
        "description": "Query scenario — clinical_query routes through dispatch_event",
        "events": [
            {
                "step": 1,
                "event_type": "clinical_query",
                "event_data": {
                    "patient_name": "TEST, Mix",
                    "age": 45,
                    "sex": "F",
                    "specimen_type": "biopsy",
                    "anatomic_site": "breast",
                    "fixative": "formalin",
                    "fixation_time_hours": 24.0,
                    "ordered_tests": ["Breast IHC Panel"],
                    "priority": "routine",
                    "billing_info_present": True,
                    "query": "Is fixation time adequate?",
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
    _write_scenario(tmp_path, "query", query_scenario)

    import samantha_server.api.routing as _routing_mod

    mock_llm = _make_mock_llm_client()
    captured_routing: list[str] = []
    _orig_dispatch = _routing_mod.dispatch_event

    async def _selective_fake(ctx, **kwargs):  # type: ignore[no-untyped-def]
        if ctx.current_state == "PENDING_LLM_REVIEW" or ctx.event.event_type == "clinical_query":
            captured_routing.append("llm")
            return _make_fake_dispatch_result(latency_us=100, next_state="ACCESSIONING")
        return await _orig_dispatch(ctx, **kwargs)

    with patch("samantha_server.api.routing.dispatch_event", side_effect=_selective_fake):
        replay(tmp_path, _llm_client_override=mock_llm)

    # Two LLM-routed steps total: query step 1 + llm_review step 2.
    assert len(captured_routing) == 2, (
        f"dispatch_event must fire for both the query clinical_query step and "
        f"the llm_review PENDING_LLM_REVIEW handoff step; got {captured_routing}"
    )


def test_llm_client_categories_covers_all_llm_routed_fixture_categories() -> None:
    """Every fixture category with a `routing_path: llm` step
    must be in `_LLM_CLIENT_CATEGORIES`. Catches future categories that
    route through the LLM without being declared (silent no-op risk).

    Scope limitations (deliberate):
      1. Walks only `tests/fixtures/scenarios/` — vendored or external
         corpora passed to `replay(directory=...)` are not scanned.
      2. Detects LLM routing only via `expected_output.routing_path == "llm"`
         in fixture JSON. Categories that enter `dispatch_event()` solely via
         the `PENDING_LLM_REVIEW` state gate or the `clinical_query` event
         gate but whose fixtures omit `routing_path: llm` will not register.
         New categories must include at least one such attested step in their
         test fixtures to be caught by this guard.
    """
    from samantha_server.scenarios.replay import _LLM_CLIENT_CATEGORIES

    fixtures_root = Path(__file__).parent.parent / "fixtures" / "scenarios"
    assert fixtures_root.is_dir(), f"fixtures dir not found at {fixtures_root}"

    llm_routed_categories: set[str] = set()
    for category_dir in fixtures_root.iterdir():
        if not category_dir.is_dir():
            continue
        category = category_dir.name
        for path in category_dir.glob("*.json"):
            scenario = json.loads(path.read_text())
            for event in scenario.get("events", []):
                expected = event.get("expected_output", {})
                if expected.get("routing_path") == "llm":
                    llm_routed_categories.add(category)
                    break

    missing = llm_routed_categories - _LLM_CLIENT_CATEGORIES
    assert not missing, (
        f"Categories with routing_path=llm fixtures but missing from "
        f"_LLM_CLIENT_CATEGORIES: {sorted(missing)}. Isolated --include-category "
        f"sweeps of these would silently skip LLM-client init."
    )


# ---------------------------------------------------------------------------
# Gate-correctness gaps — winner-first applied_rules ordering
# ---------------------------------------------------------------------------


def _wrong_winner_multi_rule_scenario() -> dict:  # type: ignore[type-arg]
    """A multi-rule scenario where the fixture has the wrong winner at position 0.

    The engine fires ACC-003 (REJECT) as winner and ACC-001 (HOLD) as
    also_matched — predicted_rules = ("ACC-003", "ACC-001"). The fixture
    deliberately annotates ["ACC-001", "ACC-003"] (HOLD first), which is the
    wrong ordering. With set-equality this passes silently; with winner-first
    comparison it must surface as mismatch_rules.
    """
    return {
        "scenario_id": "SC-GH231-01",
        "category": "multi_rule",
        "description": "F-13: wrong winner at position 0 — must be mismatch_rules",
        "events": [
            {
                "step": 1,
                "event_type": "order_received",
                "event_data": {
                    "patient_name": None,
                    "age": 62,
                    "sex": "F",
                    "specimen_type": "biopsy",
                    "anatomic_site": "lung",
                    "fixative": "formalin",
                    "fixation_time_hours": 24.0,
                    "ordered_tests": ["Breast IHC Panel"],
                    "priority": "routine",
                    "billing_info_present": True,
                },
                "expected_output": {
                    # ACC-001 (HOLD) listed first — wrong, engine emits ACC-003 (REJECT) as winner
                    "next_state": "DO_NOT_PROCESS",
                    "applied_rules": ["ACC-001", "ACC-003"],
                    "flags": [],
                    "routing_path": "deterministic",
                },
            }
        ],
    }


def test_step_verdict_flags_wrong_winner_at_position_0(tmp_path: Path) -> None:
    """A fixture with the HOLD rule at position 0 and the REJECT winner
    at position 1 must produce mismatch_rules.

    Before the gate used set-equality, which silently accepted any ordering.
    After position 0 is compared exactly (winner check) so this must fail.
    """
    from samantha_server.scenarios.replay import replay

    _write_scenario(tmp_path, "multi_rule", _wrong_winner_multi_rule_scenario())

    report = replay(tmp_path)
    verdict = report.scenario_verdicts[0]
    assert verdict.status == "fail"
    step_verdict = verdict.step_verdicts[0]
    assert step_verdict.status == "mismatch_rules", (
        f"Expected mismatch_rules (wrong winner at position 0), got {step_verdict.status!r}"
    )
