"""Acceptance gate: replay the full vendored scenario corpus.

Asserts included_accuracy >= 0.995 against tests/fixtures/scenarios/.

This test is the developer-loop accuracy gate. The CI-authoritative regression
anchor (which gates BOTH accuracy AND p99 latency, writes trend files, and
runs on every push) lives at tests/regression/test_eval_anchors.py — both
files share the AccuracyReport produced by replay() so there is one source
of truth for the corpus pass/fail signal.

Known limitations (as of 2026-05-07): none.

Skiplist entries (scenarios excluded from included_accuracy gate): none.

Scenarios that fail the deterministic gate due to known upstream/design issues
should be added to tests/fixtures/scenarios/.skiplist.json with a tracking
issue URL, and recorded here as "SC-XXX (category): one-line summary, see
GH-N." Example shape: "SC-021 (rule_coverage): SP-002 vs SP-003 not
deterministically distinguishable, see #46."
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from samantha_server.scenarios.replay import _DETERMINISTIC_CATEGORIES

VENDORED_DIR = Path(__file__).parent.parent / "fixtures" / "scenarios"
INCLUDED_ACCURACY_THRESHOLD = 0.995


def _make_mock_llm_client() -> MagicMock:
    """Return a minimal mock LLMClient for injection into replay()."""
    from samantha_server.llm.client import LLMClient, LLMResponse

    _canned = LLMResponse(
        text="{}", input_tokens=1, output_tokens=1, model_id="test-model", latency_us=0
    )
    mock = MagicMock(spec=LLMClient)
    mock.model_id = "test-model"
    mock.complete.return_value = _canned
    mock.complete_json.return_value = _canned
    return mock


def _make_dispatch_via_evaluate_patch(rule_index: object) -> object:
    """Return an async function that proxies LLM-path dispatch_event to evaluate().

    LLM-path steps (clinical_query event_type or PENDING_LLM_REVIEW state) are
    handled by the evaluate()-based proxy so the acceptance test can verify corpus
    accuracy without loading MLX. Deterministic steps are delegated to the real
    routing.dispatch_event.

    On the endpoint path, routing.dispatch_event is the call site so
    this must be patched there, not at samantha_server.scenarios.replay.dispatch_event.
    """
    import samantha_server.api.routing as _routing_mod
    from samantha_server.api.event_context import EventDispatchContext
    from samantha_server.api.receipt_emission import emit_receipt
    from samantha_server.engine.action_handlers import apply_runtime_flag_clearing
    from samantha_server.engine.dispatcher import list_applicable_rules
    from samantha_server.engine.evaluator import evaluate
    from samantha_server.engine.transitions import resolve_transition
    from samantha_server.queue.priority import EventPriority

    # Generous TTL so dispatch tokens never expire mid-scenario in CI.
    _DISPATCH_TTL_SEC = 3600

    _orig_dispatch = _routing_mod.dispatch_event

    async def _proxy(ctx, *, session_id, **kwargs):  # type: ignore[no-untyped-def]
        is_llm_step = (
            ctx.current_state == "PENDING_LLM_REVIEW" or ctx.event.event_type == "clinical_query"
        )
        if not is_llm_step:
            return await _orig_dispatch(ctx, session_id=session_id, **kwargs)

        dispatch = list_applicable_rules(
            ctx,
            rule_index,  # type: ignore[arg-type]
            session_id=session_id,
            ttl_sec=_DISPATCH_TTL_SEC,
        )
        decision = evaluate(dispatch, ctx, session_id=session_id)

        # Resolve symbolic transitions (mirrors routing.dispatch_event).
        post_flags = (ctx.flags - set(decision.flags_cleared)) | set(decision.flags_added)
        post_flags = apply_runtime_flag_clearing(decision, ctx.event.event_data, post_flags)
        resolved_state = resolve_transition(
            ctx.current_state,
            decision,
            ctx.event.event_type,
            accumulated_flags=post_flags,
        )
        decision = decision.model_copy(
            update={"next_state": resolved_state, "session_id": session_id}
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

    return _proxy


def test_replay_acceptance_gate() -> None:
    """Full corpus replay must achieve >= 99.5% accuracy on deterministic categories.

    LLM-path steps (clinical_query event_type or PENDING_LLM_REVIEW state) are
    proxied through the deterministic engine via a routing.dispatch_event patch.
    Deterministic steps use the real routing.dispatch_event.
    _llm_client_override is required to avoid loading MLX in CI.
    """
    assert VENDORED_DIR.exists() and any(VENDORED_DIR.glob("**/*.json")), (
        f"Vendored corpus not found at {VENDORED_DIR}; run scripts/vendor_scenarios.py"
    )

    from samantha_server.rules.loader import RuleIndex, load_rule_specs
    from samantha_server.scenarios.replay import replay

    specs_dir = Path(__file__).parents[2] / "samantha_server" / "rules" / "specs"
    rule_index = RuleIndex(load_rule_specs(specs_dir))

    mock_llm = _make_mock_llm_client()

    with patch(
        "samantha_server.api.routing.dispatch_event",
        side_effect=_make_dispatch_via_evaluate_patch(rule_index),
    ):
        report = replay(VENDORED_DIR, rule_index=rule_index, _llm_client_override=mock_llm)

    # Collect all failures for clear error output
    failures = [v for v in report.scenario_verdicts if v.status == "fail"]
    included_failures = [v for v in failures if v.category in _DETERMINISTIC_CATEGORIES]

    failure_details = []
    for v in included_failures:
        for sv in v.step_verdicts:
            if sv.status != "pass":
                failure_details.append(
                    f"  [{v.category}] {v.scenario_id} step {sv.step_index}: {sv.status}\n"
                    f"    expected: state={sv.expected.next_state} "
                    f"rules={list(sv.expected.applied_rules)} "
                    f"flags={list(sv.expected.flags)}\n"
                    f"    predicted: state={sv.predicted.next_state} "
                    f"rules={list(sv.predicted.applied_rules)} "
                    f"flags={list(sv.predicted.flags)}"
                )

    in_bucket_pass = sum(
        1
        for v in report.scenario_verdicts
        if v.category in _DETERMINISTIC_CATEGORIES and v.status == "pass"
    )
    assert report.included_accuracy >= INCLUDED_ACCURACY_THRESHOLD, (
        f"included_accuracy {report.included_accuracy:.4%} < {INCLUDED_ACCURACY_THRESHOLD:.1%}\n"
        f"({report.included_total - in_bucket_pass} in-bucket failures)\n\n"
        f"Per-scenario mismatches:\n" + "\n".join(failure_details)
    )
