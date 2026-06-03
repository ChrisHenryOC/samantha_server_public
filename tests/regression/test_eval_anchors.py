"""Regression anchors for the deterministic rule engine eval harness.

CI gate: pull_request (all branches) and push to main.

Asserts three anchors against the vendored scenario corpus:
  1. included_accuracy >= 0.995  (broad-bucket pass rate: deterministic +
     LLM-path categories, minus skiplist entries)
  2. p99_latency_us < 10_000     (10ms p99 over deterministic-bucket steps,
     consumed from AccuracyReport.p99_latency_us — single source of truth
     with the harness, not a separate inline computation)
  3. p99_latency_us_llm < 35_000_000  (35s p99 over LLM-path-bucket steps,
     gated only when LLM sample count >= _P99_LLM_SAMPLE_FLOOR=50; deferred
     as an informational notice when below the floor)

A JSON trend file is written to results/regression/<timestamp>.json AFTER
the gate decision is computed so `gate_passed` reflects all three anchors
(not just the first two).

GH-90: LLM-path anchor added; bucket broadened.

Population scope note:
- AccuracyReport.p99_latency_us aggregates over deterministic-category,
  non-skiplisted steps. Keeping the deterministic gate clean preserves the
  engine-perf signal against I/O or LLM-call creep.
- AccuracyReport.p99_latency_us_llm aggregates over LLM-path-category,
  non-skiplisted steps. Not merged with the deterministic gate per GH-90
  do-not-merge invariant.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict
from unittest.mock import MagicMock, patch

from samantha_server.scenarios.replay import AccuracyReport

VENDORED_DIR = Path(__file__).parent.parent / "fixtures" / "scenarios"
INCLUDED_ACCURACY_THRESHOLD = 0.995
P99_LATENCY_US_THRESHOLD = 10_000
P99_LATENCY_US_LLM_THRESHOLD = 35_000_000  # 35 seconds — Qwen3-Next-80B-A3B baseline (GH-273)

# Floor below which the percentile degenerates from a real statistic to a
# max-of-tail estimate. The full vendored corpus today is over a thousand
# deterministic-bucket steps, so this floor only fires if the corpus is
# misconfigured (under-loaded or excessively skiplisted).
_P99_SAMPLE_FLOOR = 100

# Smaller floor for the LLM-path bucket: LLM samples are slower/scarcer.
# Today the LLM bucket carries the hallucination scenarios plus the six
# unknown_input scenarios (SC-100..SC-105 — all six non-skiplisted after
# GH-105), expanded to step granularity by multi-step fixtures, plus the
# 27 query scenarios (PR #180 wireup). Theoretical ceiling is ~30
# LLM-routed steps across the corpus.
#
# POC compromise: lowered 50 -> 30 in PR #180 to match the CLI floor at
# ``samantha_server/scenarios/replay.py:_LLM_ANCHOR_MIN_STEP_COUNT``. The
# two surfaces of the same logical anchor must agree, otherwise the
# nightly CI regression gate stays deferred while the demo CLI enforces
# (or vice versa). p99 at n=30 is statistically thin — closer to a
# max-of-tail than a real percentile — and should be raised back to ~100
# once the LLM corpus grows. Track via GH-181.
_P99_LLM_SAMPLE_FLOOR = 30


class TrendSummary(TypedDict):
    """Shape of `results/regression/<timestamp>.json`. Stable contract for
    downstream trend-aggregation tooling."""

    timestamp: str
    included_accuracy: float
    p99_latency_us: int
    p99_latency_us_llm: int | None
    step_count: int
    included_pass: int
    included_total: int
    gate_passed: bool


_REQUIRED_TREND_KEYS = frozenset(TrendSummary.__annotations__.keys())


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
    handled by the evaluate()-based proxy so the regression test can verify corpus
    accuracy without loading MLX. Deterministic steps are delegated to the real
    routing.dispatch_event.

    On the endpoint path (GH-324), routing.dispatch_event is the call site so
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


# ---------------------------------------------------------------------------
# Pure gate evaluator (extracted so unit tests can drive every branch directly)
# ---------------------------------------------------------------------------


def _evaluate_anchors(report: AccuracyReport) -> tuple[list[str], str | None]:
    """Run all three anchor checks against *report*.

    Returns ``(failures, info_notice)``.

    - ``failures`` collects every gate violation. The test asserts
      ``not failures`` so a single CI run reports all broken anchors at once.
    - ``info_notice`` is non-None when the LLM-path latency gate is
      deferred (sample count below the floor). Deferral is a soft state,
      not a failure — but it is **not** a silent pass: callers print the
      notice so the deferral is visible in CI logs.

    The LLM-gate enforcement uses ``report.llm_latency_step_count`` rather
    than re-walking ``report.scenario_verdicts``: that count is computed
    inside ``replay()`` against the same skiplist filter that produced the
    p99, so the floor check and the p99 population can never disagree.
    """
    failures: list[str] = []

    if report.included_accuracy < INCLUDED_ACCURACY_THRESHOLD:
        failures.append(
            f"included_accuracy {report.included_accuracy:.4%} "
            f"< {INCLUDED_ACCURACY_THRESHOLD:.1%} "
            f"({report.included_pass}/{report.included_total} in-bucket scenarios pass)"
        )

    if report.p99_latency_us is None:
        failures.append("p99_latency_us is None — no deterministic latency samples")
    elif report.p99_latency_us >= P99_LATENCY_US_THRESHOLD:
        failures.append(f"p99_latency_us {report.p99_latency_us} >= {P99_LATENCY_US_THRESHOLD}")

    info_notice: str | None = None
    llm_count = report.llm_latency_step_count
    if llm_count < _P99_LLM_SAMPLE_FLOOR:
        info_notice = (
            f"p99_latency_us_llm gate deferred — n={llm_count} < "
            f"floor={_P99_LLM_SAMPLE_FLOOR}; gate will be enforced once the "
            f"LLM-path corpus grows to >= {_P99_LLM_SAMPLE_FLOOR} steps"
        )
    elif report.p99_latency_us_llm is None:
        # Floor met but the p99 population is empty — only happens if the
        # llm_latency_step_count and the p99 sample list ever drift apart
        # (e.g. a future refactor splits their filters). Surface loudly.
        failures.append(
            f"p99_latency_us_llm is None despite llm_latency_step_count={llm_count} "
            f">= floor={_P99_LLM_SAMPLE_FLOOR} — sample-count / p99-population mismatch"
        )
    elif report.p99_latency_us_llm >= P99_LATENCY_US_LLM_THRESHOLD:
        failures.append(
            f"p99_latency_us_llm {report.p99_latency_us_llm} >= {P99_LATENCY_US_LLM_THRESHOLD}"
        )

    return failures, info_notice


# ---------------------------------------------------------------------------
# Regression gate (the CI anchor)
# ---------------------------------------------------------------------------


def test_eval_regression_anchors() -> None:
    """Regression gate: included_accuracy >= 99.5% AND p99 latency < 10ms.

    Primary CI gate. Writes `results/regression/<timestamp>.json` with a
    `gate_passed: bool` field reflecting all three anchors (deterministic +
    LLM-path), so failed-run trend files are distinguishable from passing-run
    trend files even when the LLM anchor is the only one broken.

    LLM-path scenarios (hallucination, unknown_input) are proxied through the
    deterministic engine via a dispatch_event patch. _llm_client_override is
    required to avoid loading MLX in CI.
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

    # Critical: empty/non-loading corpus must not pass vacuously.
    # `included_total == 0` makes `included_accuracy = 1.0` per replay.py's
    # `else 1.0` fallback, which would silently bypass the accuracy gate.
    assert report.included_total > 0, (
        f"Empty in-bucket corpus at {VENDORED_DIR} — "
        "vendored scenarios may have failed to load or all are skiplisted"
    )

    # Sample-floor guard for the deterministic p99. The count comes from
    # AccuracyReport directly (computed against the same skiplist filter as
    # p99_latency_us), so the floor check can't drift from the p99 population.
    assert report.deterministic_latency_step_count >= _P99_SAMPLE_FLOOR, (
        f"Only {report.deterministic_latency_step_count} deterministic-bucket "
        f"latency samples; below the n>={_P99_SAMPLE_FLOOR} floor for a real p99. "
        "Corpus likely under-loaded or over-skiplisted."
    )

    failures, info_notice = _evaluate_anchors(report)
    if info_notice is not None:
        # Visible in pytest -s output and CI logs. Deferral is not a failure.
        print(f"[INFO] {info_notice}")

    # Compute gate_passed AFTER all three anchors are evaluated so the trend
    # file reflects the LLM-path verdict (GH-90 review H-01).
    gate_passed = not failures

    timestamp = datetime.now(UTC).isoformat()
    summary: TrendSummary = {
        "timestamp": timestamp,
        "included_accuracy": report.included_accuracy,
        "p99_latency_us": report.p99_latency_us if report.p99_latency_us is not None else 0,
        "p99_latency_us_llm": report.p99_latency_us_llm,
        "step_count": sum(len(v.step_verdicts) for v in report.scenario_verdicts),
        "included_pass": report.included_pass,
        "included_total": report.included_total,
        "gate_passed": gate_passed,
    }

    results_dir = Path(__file__).parent.parent.parent / "results" / "regression"
    results_dir.mkdir(parents=True, exist_ok=True)
    trend_path = results_dir / f"{timestamp.replace(':', '-')}.json"
    trend_path.write_text(json.dumps(summary, indent=2))

    # Trend file shape: enforce that every required key is present (subset
    # check tolerates additive future fields without breaking older runs).
    on_disk = json.loads(trend_path.read_text())
    assert on_disk.keys() >= _REQUIRED_TREND_KEYS

    assert not failures, "\n".join(failures)
