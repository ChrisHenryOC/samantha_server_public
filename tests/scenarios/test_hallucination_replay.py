"""H-08: Hallucination scenario replay tests.

test_hallucination_scenario_deterministic_steps replays each scenario's
deterministic steps via replay() to validate them against the deterministic
engine. Multi-state transitions that use macro states (ADVANCE_SAMPLE_PREP,
etc.) are resolved by the replay harness.

LLM-dependent variants (live judge calls) are not exercised in this file;
they would require a local model and are out of scope for the CI test suite.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

HALLUCINATION_FIXTURE_DIR = (
    Path(__file__).parent.parent / "fixtures" / "scenarios" / "hallucination"
)

_HALLUCINATION_SCENARIOS = sorted(HALLUCINATION_FIXTURE_DIR.glob("sc-*.json"))


def _make_mock_llm_client() -> MagicMock:
    """Return a minimal mock LLMClient for injection into replay()."""
    from samantha_server.llm.client import LLMClient

    mock = MagicMock(spec=LLMClient)
    mock.model_id = "test-model"
    return mock


@pytest.fixture(scope="module")
def rule_index() -> object:
    from samantha_server.rules.loader import RuleIndex, load_rule_specs

    specs_dir = Path(__file__).resolve().parents[2] / "samantha_server" / "rules" / "specs"
    return RuleIndex(load_rule_specs(specs_dir))


# ---------------------------------------------------------------------------
# Full multi-step replay via replay()
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fixture_path",
    _HALLUCINATION_SCENARIOS,
    ids=[p.stem for p in _HALLUCINATION_SCENARIOS],
)
def test_hallucination_scenario_deterministic_steps(
    fixture_path: Path,
    rule_index: object,
) -> None:
    """Each hallucination scenario's deterministic steps must match expected output.

    Replays through replay(). The hallucination fixtures use order_received
    event_type. Because category='hallucination' is an LLM-path category,
    _llm_client_override is required to avoid loading MLX in CI.

    dispatch_event is patched to delegate to the deterministic evaluate()
    path so the fixture assertions (applied_rules, next_state) remain valid —
    these scenarios were authored against the deterministic engine; the mock
    dispatch correctly forwards to it.
    """
    from samantha_server.engine.dispatcher import list_applicable_rules
    from samantha_server.engine.evaluator import evaluate
    from samantha_server.queue.priority import EventPriority
    from samantha_server.scenarios.replay import replay

    # Generous TTL so dispatch tokens never expire mid-scenario in CI.
    _DISPATCH_TTL_SEC = 3600
    mock_llm = _make_mock_llm_client()

    async def _dispatch_via_evaluate(ctx, *, session_id, **kwargs):  # type: ignore[no-untyped-def]
        """Proxy dispatch_event to evaluate() for deterministic scenarios.

        GH-334: emit_receipt() must be called before returning so the harness's
        fetch_payload_json() can reconstruct the EngineDecision from the
        in-memory receipt store. Without this, the fallback-free harness
        (post GH-334 fallback removal) would raise on missing receipts.
        """
        from samantha_server.api.event_context import EventDispatchContext
        from samantha_server.api.receipt_emission import emit_receipt
        from samantha_server.engine.action_handlers import apply_runtime_flag_clearing
        from samantha_server.engine.transitions import resolve_transition

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
        decision = decision.model_copy(update={"next_state": resolved_state})

        # Stamp session_id (mirrors routing.dispatch_event behaviour).
        stamped = decision.model_copy(update={"session_id": session_id})

        receipt_writer = kwargs["receipt_writer"]
        write_lock = kwargs["write_lock"]
        counters = kwargs["counters"]

        receipt = await emit_receipt(
            stamped,
            session_id,
            receipt_writer=receipt_writer,
            write_lock=write_lock,
            counters=counters,
        )

        fake_ctx = MagicMock(spec=EventDispatchContext)
        fake_ctx.routing_path = "llm"
        fake_ctx.priority = EventPriority.ROUTINE

        return (stamped, fake_ctx, receipt)

    # Copy just this fixture into a temp dir under a "hallucination/" subdir
    with tempfile.TemporaryDirectory() as tmpdir:
        dest = Path(tmpdir) / "hallucination"
        dest.mkdir()
        shutil.copy(fixture_path, dest / fixture_path.name)

        with patch(
            "samantha_server.api.routing.dispatch_event",
            side_effect=_dispatch_via_evaluate,
        ):
            report = replay(Path(tmpdir), rule_index=rule_index, _llm_client_override=mock_llm)  # type: ignore[arg-type]

    assert len(report.scenario_verdicts) == 1, (
        f"Expected 1 verdict from replay for {fixture_path.name}, "
        f"got {len(report.scenario_verdicts)}"
    )
    verdict = report.scenario_verdicts[0]

    failures = [
        f"step {sv.step_index}: status={sv.status!r}, "
        f"expected next_state={sv.expected.next_state!r}, "
        f"predicted next_state={sv.predicted.next_state!r}"
        for sv in verdict.step_verdicts
        if sv.status != "pass"
    ]

    if failures:
        pytest.fail(
            f"{verdict.scenario_id} hallucination replay failures:\n"
            + "\n".join(f"  - {f}" for f in failures)
        )
