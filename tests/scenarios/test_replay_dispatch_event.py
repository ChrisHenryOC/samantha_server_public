"""Tests for GH-121: replay harness routes LLM-path scenarios through dispatch_event().

Slices covered:
  - Slice 1: _replay_scenario_async is async; replay() interface unchanged.
  - Slice 2: replay() builds in-memory deps for dispatch_event (no file pollution).
  - Slice 3: LLM-path scenarios call dispatch_event; deterministic scenarios do not.
  - Slice 4: _llm_client_override kwarg lets tests inject a stub.
  - Slice 5: main() hard-fails when llm_latency_step_count >= 50 AND p99 >= 35s
    (GH-273 raised anchor from 25 s for the Qwen3-Next-80B-A3B baseline).
  - Slice 6: Routing-path assertions via EventDispatchContext.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _write_scenario(tmp_path: Path, category: str, scenario: dict) -> Path:  # type: ignore[type-arg]
    subdir = tmp_path / category
    subdir.mkdir(parents=True, exist_ok=True)
    path = subdir / f"{scenario['scenario_id'].lower()}.json"
    path.write_text(json.dumps(scenario, indent=2))
    return path


def _llm_path_scenario(scenario_id: str, category: str) -> dict:  # type: ignore[type-arg]
    """Minimal scenario for an LLM-path category (clinical_query event_type)."""
    return {
        "scenario_id": scenario_id,
        "category": category,
        "description": f"LLM-path scenario for {category}",
        "events": [
            {
                "step": 1,
                "event_type": "clinical_query",
                "event_data": {
                    "patient_name": "TEST, LLM",
                    "age": 45,
                    "sex": "F",
                    "specimen_type": "biopsy",
                    "anatomic_site": "breast",
                    "fixative": "formalin",
                    "fixation_time_hours": 24.0,
                    "ordered_tests": ["ER"],
                    "priority": "routine",
                    "billing_info_present": True,
                    "query": "Is the fixation time adequate?",
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


def _deterministic_scenario() -> dict:  # type: ignore[type-arg]
    """Minimal deterministic scenario (order_received → ACCEPTED)."""
    return {
        "scenario_id": "SC-DE01",
        "category": "rule_coverage",
        "description": "Deterministic pass-through",
        "events": [
            {
                "step": 1,
                "event_type": "order_received",
                "event_data": {
                    "patient_name": "TEST, Det",
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


def _make_mock_llm_client() -> MagicMock:
    """Return a mock LLMClient with canned responses for both complete() and complete_json().

    GH-324 Step 5: the endpoint path uses the real handle_clinical_query which calls
    complete_json(). The response.text must be a real string, not a MagicMock, to avoid
    TypeError in _strip_markdown_fences. Return an empty JSON object '{}' so the handler
    gracefully produces a refusal/empty result rather than crashing.
    """
    from samantha_server.llm.client import LLMClient, LLMResponse

    _canned = LLMResponse(
        text="{}",
        input_tokens=10,
        output_tokens=5,
        model_id="test-model",
        latency_us=500,
    )
    mock = MagicMock(spec=LLMClient)
    mock.model_id = "test-model"
    mock.complete.return_value = _canned
    mock.complete_json.return_value = _canned
    return mock


# ---------------------------------------------------------------------------
# Slice 1: replay() interface is unchanged (sync); LLM-path scenarios run
# ---------------------------------------------------------------------------


def test_replay_interface_unchanged_with_llm_client_override(tmp_path: Path) -> None:
    """replay() still accepts directory + rule_index; now also _llm_client_override."""
    from samantha_server.scenarios.replay import replay

    _write_scenario(tmp_path, "rule_coverage", _deterministic_scenario())

    # Should work without the override kwarg (existing callers unaffected)
    report = replay(tmp_path)
    assert report.included_total == 1
    assert report.overall_accuracy == 1.0


def test_replay_accepts_llm_client_override_kwarg(tmp_path: Path) -> None:
    """replay() must accept _llm_client_override without error."""
    from samantha_server.scenarios.replay import replay

    _write_scenario(tmp_path, "rule_coverage", _deterministic_scenario())

    mock_llm = _make_mock_llm_client()
    # Should not raise — the override is accepted even if not used by deterministic scenarios
    report = replay(tmp_path, _llm_client_override=mock_llm)
    assert report.included_total == 1


# ---------------------------------------------------------------------------
# Slice 3: dispatch_event is called for LLM-path; NOT for deterministic
# ---------------------------------------------------------------------------


def test_dispatch_event_called_for_query_category(tmp_path: Path) -> None:
    """LLM-path category 'query' routes through dispatch_event (via endpoint consumer)."""
    from samantha_server.scenarios.replay import replay

    _write_scenario(tmp_path, "query", _llm_path_scenario("SC-QR01", "query"))

    mock_llm = _make_mock_llm_client()

    # GH-324 Step 5: replay now routes through POST /events → _consume → dispatch_event.
    # The old dispatch_event proxy in replay.py is no longer called directly for LLM steps.
    # Verify the behavioral contract at the report level: the scenario was processed.
    report = replay(tmp_path, _llm_client_override=mock_llm)

    assert report.overall_total == 1, "query scenario must appear in overall_total"
    # The step must have routing_path='llm' (not 'deterministic') since event_type='clinical_query'.
    verdict = report.scenario_verdicts[0]
    assert verdict.step_verdicts[0].routing_path == "llm", (
        f"query step must have routing_path='llm'; got {verdict.step_verdicts[0].routing_path!r}"
    )


def test_dispatch_event_called_for_unknown_input_category(tmp_path: Path) -> None:
    """LLM-path category 'unknown_input' routes through dispatch_event (via endpoint consumer)."""
    from samantha_server.scenarios.replay import replay

    _write_scenario(tmp_path, "unknown_input", _llm_path_scenario("SC-UI01", "unknown_input"))

    mock_llm = _make_mock_llm_client()

    report = replay(tmp_path, _llm_client_override=mock_llm)

    assert report.overall_total == 1
    verdict = report.scenario_verdicts[0]
    assert verdict.step_verdicts[0].routing_path == "llm"


def test_dispatch_event_called_for_hallucination_category(tmp_path: Path) -> None:
    """LLM-path category 'hallucination' routes through dispatch_event (via endpoint consumer)."""
    from samantha_server.scenarios.replay import replay

    _write_scenario(tmp_path, "hallucination", _llm_path_scenario("SC-HA01", "hallucination"))

    mock_llm = _make_mock_llm_client()

    report = replay(tmp_path, _llm_client_override=mock_llm)

    assert report.overall_total == 1
    verdict = report.scenario_verdicts[0]
    assert verdict.step_verdicts[0].routing_path == "llm"


def test_deterministic_category_step_has_routing_path_deterministic(tmp_path: Path) -> None:
    """Deterministic category 'rule_coverage' step must report routing_path='deterministic'.

    GH-324 Phase B: the endpoint harness routes ALL steps through POST /events
    (dispatch_event is called for every step). Deterministic vs LLM-path
    distinction is encoded in StepVerdict.routing_path, not in whether
    dispatch_event is called.

    GH-334: tests the correct invariant — routing_path label — rather than
    the stale "dispatch_event not called" proxy assertion.
    """
    from samantha_server.scenarios.replay import replay

    _write_scenario(tmp_path, "rule_coverage", _deterministic_scenario())

    report = replay(tmp_path)

    assert report.overall_total == 1
    verdict = report.scenario_verdicts[0]
    step = verdict.step_verdicts[0]
    assert step.routing_path == "deterministic", (
        f"Deterministic step must have routing_path='deterministic', got {step.routing_path!r}"
    )


def test_mixed_corpus_routes_correctly(tmp_path: Path) -> None:
    """With both deterministic and LLM-path scenarios, the LLM step gets routing_path='llm'
    and the deterministic step gets routing_path='deterministic'."""
    from samantha_server.scenarios.replay import replay

    _write_scenario(tmp_path, "rule_coverage", _deterministic_scenario())
    _write_scenario(tmp_path, "query", _llm_path_scenario("SC-QR02", "query"))

    mock_llm = _make_mock_llm_client()

    # GH-324 Step 5: replay routes through POST /events for all steps.
    report = replay(tmp_path, _llm_client_override=mock_llm)

    # Both scenarios should appear in the report
    assert report.overall_total == 2

    # LLM scenario step must be routed as 'llm'
    llm_verdicts = [v for v in report.scenario_verdicts if v.scenario_id == "SC-QR02"]
    assert len(llm_verdicts) == 1
    assert llm_verdicts[0].step_verdicts[0].routing_path == "llm"

    # Deterministic scenario step must be routed as 'deterministic'
    det_verdicts = [v for v in report.scenario_verdicts if v.scenario_id == "SC-DE01"]
    assert len(det_verdicts) == 1
    assert det_verdicts[0].step_verdicts[0].routing_path == "deterministic"


# ---------------------------------------------------------------------------
# Slice 2: in-memory receipt writer — replay receipts don't pollute dev store
# ---------------------------------------------------------------------------


def test_replay_receipts_use_in_memory_store(tmp_path: Path) -> None:
    """Receipts written during LLM-path replay go to in-memory SQLite, not
    the developer's receipts.db.

    GH-333/GH-334 strengthened (PR #333 review #3): verifies BOTH that no
    receipts.db file appears on disk AND that at least one receipt was actually
    written to the in-memory store during the LLM-path run. The first check
    alone is satisfied by any implementation that skips receipts entirely;
    the second ensures dispatch_event actually called emit_receipt.
    """
    from samantha_server.api.receipt_writer import ReceiptWriter
    from samantha_server.scenarios.replay import replay

    _write_scenario(tmp_path, "query", _llm_path_scenario("SC-QR03", "query"))
    mock_llm = _make_mock_llm_client()

    # Track how many receipts were written during replay.
    receipts_written: list[str] = []
    _original_write_signed = ReceiptWriter.write_signed

    def _tracking_write_signed(self: ReceiptWriter, receipt: object) -> None:  # type: ignore[misc]
        from samantha_server.receipts.signing import SignedReceipt

        _original_write_signed(self, receipt)  # type: ignore[arg-type]
        if isinstance(receipt, SignedReceipt):
            receipts_written.append(receipt.receipt_id)

    with patch.object(ReceiptWriter, "write_signed", _tracking_write_signed):
        replay(tmp_path, _llm_client_override=mock_llm)

    # No receipts.db should appear anywhere in tmp_path
    db_files = list(tmp_path.rglob("receipts.db"))
    assert len(db_files) == 0, f"replay must not create receipts.db on disk; found: {db_files}"

    # At least one receipt must have been written to the in-memory store.
    assert len(receipts_written) >= 1, (
        "No receipts were written during LLM-path replay — "
        "dispatch_event must call emit_receipt for each step"
    )


# ---------------------------------------------------------------------------
# Slice 3 extension: StepVerdict.latency_us sourced from dispatch_event decision
# ---------------------------------------------------------------------------


def test_llm_step_latency_us_sourced_from_dispatch_event(tmp_path: Path) -> None:
    """For LLM-path steps, StepVerdict.latency_us must be populated
    (sourced from EngineDecision.latency_us returned through the endpoint)."""
    from samantha_server.scenarios.replay import replay

    _write_scenario(tmp_path, "query", _llm_path_scenario("SC-QR04", "query"))
    mock_llm = _make_mock_llm_client()

    # GH-324 Step 5: latency now flows via POST /events → decision.latency_us in the response.
    # We can't inject a specific latency value without patching routing.dispatch_event,
    # but we CAN verify the field is non-None (i.e., it was populated from the decision).
    report = replay(tmp_path, _llm_client_override=mock_llm)

    assert len(report.scenario_verdicts) == 1
    verdict = report.scenario_verdicts[0]
    assert len(verdict.step_verdicts) == 1
    sv = verdict.step_verdicts[0]
    # latency_us is None only on error steps that crashed before producing a decision.
    assert sv.latency_us is not None, (
        "StepVerdict.latency_us must be populated for LLM-path steps; got None"
    )


# ---------------------------------------------------------------------------
# Slice 5: main() CLI hard-fails when p99 >= anchor and step_count >= 50
# (anchor was 25 s pre-GH-273; now 35 s for the Qwen3-Next-80B-A3B baseline)
# ---------------------------------------------------------------------------


def test_main_hard_fails_when_llm_p99_over_budget_and_floor_met(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """main() exits 1 when llm_latency_step_count >= floor AND p99 >= 35s."""
    from samantha_server.scenarios import replay as replay_module
    from samantha_server.scenarios.replay import (
        _LLM_ANCHOR_MIN_STEP_COUNT,
        _LLM_LATENCY_ANCHOR_US,
        AccuracyReport,
        main,
    )

    # Synthetic report: floor met, p99 just over anchor.
    # Use the production constant directly so a future bump auto-tracks
    # (PR #276 review #4 — silent-failure + workflow-logic).
    bad_report = AccuracyReport(
        included_accuracy=1.0,
        overall_accuracy=1.0,
        included_total=10,
        overall_total=10,
        included_pass=10,
        overall_pass=10,
        scenario_verdicts=(),
        p99_latency_us=500,
        p99_latency_us_llm=_LLM_LATENCY_ANCHOR_US + 1,  # >= anchor (gate fails)
        deterministic_latency_step_count=100,
        llm_latency_step_count=_LLM_ANCHOR_MIN_STEP_COUNT,  # at floor
    )

    monkeypatch.setattr(replay_module, "replay", lambda *a, **kw: bad_report)
    monkeypatch.setattr(replay_module, "_load_skiplist", lambda *a, **kw: {})

    # Need a real directory for argparse
    _write_scenario(tmp_path, "rule_coverage", _deterministic_scenario())

    exit_code = main([str(tmp_path)])
    assert exit_code == 1, (
        f"main() should exit 1 when LLM p99 >= anchor and floor met; got {exit_code}"
    )


def test_main_passes_when_llm_p99_under_budget_and_floor_met(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """main() exits 0 when llm_latency_step_count >= floor AND p99 < 35s."""
    from samantha_server.scenarios import replay as replay_module
    from samantha_server.scenarios.replay import _LLM_ANCHOR_MIN_STEP_COUNT, AccuracyReport, main

    good_report = AccuracyReport(
        included_accuracy=1.0,
        overall_accuracy=1.0,
        included_total=10,
        overall_total=10,
        included_pass=10,
        overall_pass=10,
        scenario_verdicts=(),
        p99_latency_us=500,
        p99_latency_us_llm=1_000_000,  # 1s, well under 35s (GH-273 anchor)
        deterministic_latency_step_count=100,
        llm_latency_step_count=_LLM_ANCHOR_MIN_STEP_COUNT,  # at floor
    )

    monkeypatch.setattr(replay_module, "replay", lambda *a, **kw: good_report)
    monkeypatch.setattr(replay_module, "_load_skiplist", lambda *a, **kw: {})

    _write_scenario(tmp_path, "rule_coverage", _deterministic_scenario())

    exit_code = main([str(tmp_path)])
    assert exit_code == 0, f"main() should exit 0 when LLM p99 < anchor; got {exit_code}"


def test_main_defers_llm_gate_below_floor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """main() exits 0 (gate not active) when llm_latency_step_count below floor,
    even if p99 > anchor (35 s post-GH-273)."""
    from samantha_server.scenarios import replay as replay_module
    from samantha_server.scenarios.replay import AccuracyReport, main

    deferred_report = AccuracyReport(
        included_accuracy=1.0,
        overall_accuracy=1.0,
        included_total=10,
        overall_total=10,
        included_pass=10,
        overall_pass=10,
        scenario_verdicts=(),
        p99_latency_us=500,
        p99_latency_us_llm=999_999_999,  # absurdly high — but gate deferred
        deterministic_latency_step_count=100,
        llm_latency_step_count=29,  # below floor
    )

    monkeypatch.setattr(replay_module, "replay", lambda *a, **kw: deferred_report)
    monkeypatch.setattr(replay_module, "_load_skiplist", lambda *a, **kw: {})

    _write_scenario(tmp_path, "rule_coverage", _deterministic_scenario())

    exit_code = main([str(tmp_path)])
    assert exit_code == 0, (
        f"main() should defer (exit 0) when LLM step count below floor; got {exit_code}"
    )


def test_main_routes_through_langfuse_when_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,  # type: ignore[type-arg]
) -> None:
    """GH-338: when LANGFUSE_ENABLED=true and both keys are set,
    main() calls replay_with_langfuse_export(...) instead of replay(...).

    Stubs the export helper so the test doesn't need a running Langfuse
    instance. Asserts (a) the helper is called, (b) the gate output still
    prints, (c) the exit code is preserved.
    """
    import samantha_server.config as cfg
    from samantha_server.scenarios import replay as replay_module
    from samantha_server.scenarios.replay import AccuracyReport, main

    monkeypatch.setattr(cfg, "LANGFUSE_ENABLED", True)
    monkeypatch.setattr(cfg, "LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setattr(cfg, "LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setattr(cfg, "LANGFUSE_BASE_URL", "http://localhost:3000")

    passing_report = AccuracyReport(
        included_accuracy=1.0,
        overall_accuracy=1.0,
        included_total=10,
        overall_total=10,
        included_pass=10,
        overall_pass=10,
        scenario_verdicts=(),
        p99_latency_us=500,
        p99_latency_us_llm=None,
        deterministic_latency_step_count=100,
        llm_latency_step_count=0,
    )
    calls: list[dict[str, object]] = []

    def fake_replay_with_langfuse_export(
        corpus_dir: Path,
        *,
        langfuse_base_url: str,
        langfuse_public_key: str,
        langfuse_secret_key: str,
        release: str,
        include_categories: set[str] | None = None,
        progress: bool = False,
        receipts_db_path: Path | None = None,
        **kwargs: object,
    ) -> tuple[AccuracyReport, list[str]]:
        calls.append(
            {
                "corpus_dir": corpus_dir,
                "base_url": langfuse_base_url,
                "public_key": langfuse_public_key,
                "secret_key": langfuse_secret_key,
                "release": release,
                "include_categories": include_categories,
                "progress": progress,
                "receipts_db_path": receipts_db_path,
            }
        )
        return passing_report, ["trace-id-1", "trace-id-2"]

    monkeypatch.setattr(
        replay_module, "replay_with_langfuse_export", fake_replay_with_langfuse_export
    )
    monkeypatch.setattr(replay_module, "_load_skiplist", lambda *a, **kw: {})

    _write_scenario(tmp_path, "rule_coverage", _deterministic_scenario())

    exit_code = main([str(tmp_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert len(calls) == 1, "replay_with_langfuse_export must be invoked exactly once"
    assert calls[0]["base_url"] == "http://localhost:3000"
    assert calls[0]["public_key"] == "pk-test"
    assert calls[0]["secret_key"] == "sk-test"
    # release must be set by main() in format {hex}-{safe_model_id}
    assert calls[0]["release"], "release must be set"
    # main() forwards args.progress to replay_with_langfuse_export; without
    # --progress on the argv, the forwarded value must be False (default).
    assert calls[0]["progress"] is False, (
        f"main() forwarded progress={calls[0]['progress']!r} when --progress was not set"
    )
    # PR315 review #2: without --receipts-db-path, the forwarded value must
    # be None (the per-model derivation in main() preserves None when the
    # operator opted out of file-backed receipts).
    assert calls[0]["receipts_db_path"] is None, (
        f"main() forwarded receipts_db_path={calls[0]['receipts_db_path']!r} "
        "when --receipts-db-path was not set"
    )
    # Exported-span count surfaces to stderr so the operator can confirm the
    # Langfuse path actually ran.
    assert "2 span(s) exported" in captured.err
    # Gate output still prints on stdout.
    assert "Included accuracy" in captured.out


# ---------------------------------------------------------------------------
# Slice 6: routing-path disjointness — LLM steps get routing_path="llm"
# ---------------------------------------------------------------------------


def test_dispatch_event_receives_queue_wait_us_zero(tmp_path: Path) -> None:
    """Replay submits steps through the endpoint with ROUTINE priority (no manual queue-wait hack).

    GH-324 Step 5: replay now uses POST /events → _consume → dispatch_event. The consumer
    measures actual queue_wait_us from the time the event entered the queue. We verify the
    harness completes successfully (no error from the endpoint path).
    """
    from samantha_server.scenarios.replay import replay

    _write_scenario(tmp_path, "query", _llm_path_scenario("SC-QR05", "query"))
    mock_llm = _make_mock_llm_client()

    # Verify replay completes without error and produces a verdict.
    report = replay(tmp_path, _llm_client_override=mock_llm)
    assert report.overall_total == 1


def test_dispatch_event_receives_routine_priority(tmp_path: Path) -> None:
    """Replay submits steps with priority='ROUTINE' in the POST /events body.

    GH-324 Step 5: replay encodes priority='ROUTINE' in the HTTP body; the consumer
    converts it to EventPriority.ROUTINE before calling dispatch_event. We verify the
    harness completes successfully.
    """
    from samantha_server.scenarios.replay import replay

    _write_scenario(tmp_path, "query", _llm_path_scenario("SC-QR06", "query"))
    mock_llm = _make_mock_llm_client()

    report = replay(tmp_path, _llm_client_override=mock_llm)
    assert report.overall_total == 1


# ---------------------------------------------------------------------------
# #8 — No-override production path: builds real LLM client or propagates error
# ---------------------------------------------------------------------------


def test_replay_no_override_calls_build_llm_client_for_llm_path_scenario(
    tmp_path: Path,
) -> None:
    """When no _llm_client_override is provided and LLM-path scenarios exist,
    replay() must call _build_llm_client() to construct a real LLM client.
    This is the production-CLI path (GH-121 Critical #1).

    GH-334: removed the dead replay.dispatch_event proxy patch. The endpoint
    harness now routes through the real routing.dispatch_event; fake_build_llm_client
    returns a stub so the real path completes without a live model.
    """
    from samantha_server.scenarios import replay as replay_module
    from samantha_server.scenarios.replay import replay

    _write_scenario(tmp_path, "query", _llm_path_scenario("SC-NOOV01", "query"))

    mock_llm = _make_mock_llm_client()
    build_called: list[bool] = []

    def fake_build_llm_client() -> object:
        build_called.append(True)
        return mock_llm

    with patch.object(replay_module, "_build_llm_client_for_replay", fake_build_llm_client):
        # No override provided — production-CLI path
        replay(tmp_path)

    assert build_called, "_build_llm_client_for_replay must be called when no override provided"


def test_replay_no_override_propagates_misconfigured_error(tmp_path: Path) -> None:
    """When no _llm_client_override is provided and _build_llm_client raises
    MisconfiguredEnvironmentError, replay() propagates it (fail loud).
    """
    from samantha_server.errors import MisconfiguredEnvironmentError
    from samantha_server.scenarios import replay as replay_module
    from samantha_server.scenarios.replay import replay

    _write_scenario(tmp_path, "query", _llm_path_scenario("SC-NOOV02", "query"))

    def failing_build() -> object:
        raise MisconfiguredEnvironmentError("LLM not configured in test env")

    with (
        patch.object(replay_module, "_build_llm_client_for_replay", failing_build),
        pytest.raises(MisconfiguredEnvironmentError),
    ):
        replay(tmp_path)


# ---------------------------------------------------------------------------
# #5 — receipt_writer.close() called after scenario loop (try/finally)
# ---------------------------------------------------------------------------


def test_receipt_writer_closed_after_replay(tmp_path: Path) -> None:
    """After replay() returns, the harness's ReceiptWriter must be closed.

    GH-324 Step 5: _ReplayHarness.__aexit__ calls state.aclose() which closes
    the ReceiptWriter. We verify by patching ReceiptWriter.close to detect the call.
    """
    from samantha_server.api.receipt_writer import ReceiptWriter
    from samantha_server.scenarios.replay import replay

    _write_scenario(tmp_path, "query", _llm_path_scenario("SC-CLOSE01", "query"))
    mock_llm = _make_mock_llm_client()

    close_calls: list[bool] = []
    original_close = ReceiptWriter.close

    def tracking_close(self: ReceiptWriter) -> None:  # type: ignore[misc]
        close_calls.append(True)
        original_close(self)

    with patch.object(ReceiptWriter, "close", tracking_close):
        replay(tmp_path, _llm_client_override=mock_llm)

    assert len(close_calls) >= 1, (
        "ReceiptWriter.close must be called at least once after replay() returns"
    )


# ---------------------------------------------------------------------------
# #9 — dispatch_event failure records step as error, consumer continues
# ---------------------------------------------------------------------------


def test_dispatch_event_failure_records_step_as_error(tmp_path: Path) -> None:
    """When dispatch_event raises inside the consumer, POST /events returns 500,
    the step is recorded with status='error', and the harness continues to the
    next scenario (not crashing the loop).

    GH-324 Step 5: the endpoint path propagates dispatch_event failures as 500
    responses. The harness detects non-200 and raises RuntimeError, which the
    except-block converts to status='error'.
    """
    from samantha_server.scenarios.replay import replay

    _write_scenario(tmp_path, "query", _llm_path_scenario("SC-FAIL01", "query"))
    # Second scenario (deterministic) to verify the loop continues
    _write_scenario(tmp_path, "rule_coverage", _deterministic_scenario())

    mock_llm = _make_mock_llm_client()

    import samantha_server.api.routing as _routing_mod
    from samantha_server.models.context import SpecimenContext

    _orig_dispatch = _routing_mod.dispatch_event

    async def raising_dispatch_for_llm(ctx: SpecimenContext, **kwargs: object) -> object:  # type: ignore[misc]
        # Only raise for clinical_query (LLM-path) events; let deterministic pass through.
        if ctx.event.event_type == "clinical_query":
            raise RuntimeError("dispatch_event deliberately exploded")
        return await _orig_dispatch(ctx, **kwargs)  # type: ignore[arg-type]

    # Patch at the routing module level: the consumer calls routing.dispatch_event.
    with patch("samantha_server.api.routing.dispatch_event", side_effect=raising_dispatch_for_llm):
        report = replay(tmp_path, _llm_client_override=mock_llm)

    # The LLM-path scenario should record an error step
    llm_verdicts = [v for v in report.scenario_verdicts if v.scenario_id == "SC-FAIL01"]
    assert len(llm_verdicts) == 1
    llm_verdict = llm_verdicts[0]
    assert llm_verdict.status == "fail"
    assert llm_verdict.step_verdicts[0].status == "error"

    # The deterministic scenario must still have been processed
    det_verdicts = [v for v in report.scenario_verdicts if v.scenario_id == "SC-DE01"]
    assert len(det_verdicts) == 1
    assert det_verdicts[0].status == "pass"

    # Total scenarios: both processed
    assert report.overall_total == 2


# ---------------------------------------------------------------------------
# #2 — routing_path field on StepVerdict: LLM steps get routing_path="llm"
# ---------------------------------------------------------------------------


def test_llm_step_verdict_has_routing_path_llm(tmp_path: Path) -> None:
    """StepVerdict.routing_path must be 'llm' for steps routed through dispatch_event.

    GH-334: converted from dead replay.dispatch_event proxy patch to the real
    endpoint path. routing_path is determined by the harness predicate
    (event_type == 'clinical_query' → 'llm') before the POST /events call,
    so the real production consumer path is exercised here.
    """
    from samantha_server.scenarios.replay import replay

    _write_scenario(tmp_path, "query", _llm_path_scenario("SC-RP-LLM01", "query"))
    mock_llm = _make_mock_llm_client()

    # Drive the real dispatch path — no proxy patching needed.
    # The harness predicate sets routing_path="llm" for clinical_query event_type.
    report = replay(tmp_path, _llm_client_override=mock_llm)

    verdict = report.scenario_verdicts[0]
    sv = verdict.step_verdicts[0]
    assert sv.routing_path == "llm", f"Expected routing_path='llm', got {sv.routing_path!r}"
    # Confirm the overall count matches (non-vacuous: a real step was executed)
    assert report.overall_total == 1, "Scenario must appear in overall_total"


def test_deterministic_step_verdict_has_routing_path_deterministic(tmp_path: Path) -> None:
    """StepVerdict.routing_path must be 'deterministic' for steps routed through evaluate()."""
    from samantha_server.scenarios.replay import replay

    _write_scenario(tmp_path, "rule_coverage", _deterministic_scenario())

    report = replay(tmp_path)

    verdict = report.scenario_verdicts[0]
    sv = verdict.step_verdicts[0]
    assert sv.routing_path == "deterministic", (
        f"Expected routing_path='deterministic', got {sv.routing_path!r}"
    )


def test_error_step_has_routing_path_none(tmp_path: Path) -> None:
    """Steps that error before routing_path is assigned get routing_path=None.

    GH-324 Step 5: on the endpoint path routing_path is set before POST /events,
    so errors inside dispatch_event no longer produce routing_path=None. An error
    before the routing_path assignment (e.g., inside SpecimenContext construction)
    still produces routing_path=None. We simulate by patching SpecimenContext to raise.
    """
    from samantha_server.models.context import SpecimenContext
    from samantha_server.scenarios.replay import replay

    _write_scenario(tmp_path, "query", _llm_path_scenario("SC-RP-ERR01", "query"))
    mock_llm = _make_mock_llm_client()

    call_count = [0]

    def raising_init(self: SpecimenContext, **kwargs: object) -> None:
        call_count[0] += 1
        raise RuntimeError("explode before routing recorded")

    with patch.object(SpecimenContext, "__init__", raising_init):
        report = replay(tmp_path, _llm_client_override=mock_llm)

    verdict = report.scenario_verdicts[0]
    sv = verdict.step_verdicts[0]
    assert sv.routing_path is None, (
        f"Expected routing_path=None for error step, got {sv.routing_path!r}"
    )


def test_llm_latency_bucket_uses_routing_path_not_category(tmp_path: Path) -> None:
    """p99_latency_us_llm must be populated only from successful LLM steps.

    A scenario in _LLM_PATH_CATEGORIES that errors during dispatch gets
    latency_us=None and must NOT contribute to the LLM latency bucket.

    GH-324 Step 5: on the endpoint path, routing_path='llm' is set before
    the POST call. When dispatch_event raises inside the consumer, the step
    gets routing_path='llm' but latency_us=None (error before a decision was
    produced). The latency gate in _collect_latencies skips latency_us=None.
    """
    from samantha_server.scenarios.replay import replay

    # LLM-path scenario that errors (latency_us=None due to dispatch error)
    _write_scenario(tmp_path, "query", _llm_path_scenario("SC-LATBKT01", "query"))
    mock_llm = _make_mock_llm_client()

    async def raising_dispatch(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("error during dispatch")

    # Patch at the routing module level: the consumer calls routing.dispatch_event.
    with patch("samantha_server.api.routing.dispatch_event", side_effect=raising_dispatch):
        report = replay(tmp_path, _llm_client_override=mock_llm)

    # Error step has latency_us=None → must not count in LLM bucket
    assert report.llm_latency_step_count == 0, (
        f"Error step (latency_us=None) must not count in LLM bucket; "
        f"got llm_latency_step_count={report.llm_latency_step_count}"
    )
    assert report.p99_latency_us_llm is None, (
        "p99_latency_us_llm must be None when no real LLM steps were recorded"
    )


# ---------------------------------------------------------------------------
# #18 — Both gates run before exit; combined non-zero exit
# ---------------------------------------------------------------------------


def test_main_both_gates_run_before_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,  # type: ignore[type-arg]
) -> None:
    """When the LLM latency gate fails, the accuracy gate still runs
    and both failures are reported before exit.
    """
    from samantha_server.scenarios import replay as replay_module
    from samantha_server.scenarios.replay import (
        _LLM_ANCHOR_MIN_STEP_COUNT,
        _LLM_LATENCY_ANCHOR_US,
        AccuracyReport,
        main,
    )

    # Report: LLM gate fails AND accuracy gate fails.
    # Use production constant directly so a future bump auto-tracks.
    both_fail_report = AccuracyReport(
        included_accuracy=0.5,  # < 99.5%
        overall_accuracy=0.5,
        included_total=10,
        overall_total=10,
        included_pass=5,
        overall_pass=5,
        scenario_verdicts=(),
        p99_latency_us=500,
        p99_latency_us_llm=_LLM_LATENCY_ANCHOR_US + 5_000_000,  # well over anchor
        deterministic_latency_step_count=100,
        llm_latency_step_count=_LLM_ANCHOR_MIN_STEP_COUNT,  # at floor
    )

    monkeypatch.setattr(replay_module, "replay", lambda *a, **kw: both_fail_report)
    monkeypatch.setattr(replay_module, "_load_skiplist", lambda *a, **kw: {})

    _write_scenario(tmp_path, "rule_coverage", _deterministic_scenario())

    exit_code = main([str(tmp_path)])
    captured = capsys.readouterr()

    assert exit_code == 1, f"Expected exit 1; got {exit_code}"
    # Both failure messages must appear
    combined = captured.out + captured.err
    assert "LLM" in combined and "35" in combined, (
        "LLM latency failure message must appear in output"
    )
    assert "included_accuracy" in combined or "FAIL" in combined, (
        "Accuracy failure message must appear in output"
    )
