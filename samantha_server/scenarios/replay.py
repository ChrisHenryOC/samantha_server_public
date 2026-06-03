"""Scenario replay harness — runs scenarios through the rule engine.

GH-338: every step is routed through the production endpoint path (POST /events
→ priority queue → _consume → dispatch_event → emit_receipt) via
``_ReplayHarness``. The legacy direct-dispatch path (deps/tracer) has been
removed.

Per-step routing (GH-171): routing (deterministic vs LLM-path) is decided inside
the endpoint consumer. The ``routing_path`` field on each StepVerdict reflects
the per-step predicate:
  - ``"llm"`` when ``current_state == "PENDING_LLM_REVIEW"`` or
    ``event_type == "clinical_query"``
  - ``"deterministic"`` otherwise

Scenario category (``_LLM_CLIENT_CATEGORIES``) controls whether an LLM client
is built up-front. It does NOT force every step through the LLM path; a
hallucination or unknown-input fixture whose corpus expectation is a
deterministic rule (e.g. ACC-008 / ACC-004 / ACC-009) resolves through the
engine as designed.

Public API:
    replay(directory, *, rule_index=None, _llm_client_override=None)
        -> AccuracyReport
    replay_with_langfuse_export(directory, *, langfuse_base_url, langfuse_public_key,
        langfuse_secret_key, release, ...) -> tuple[AccuracyReport, list[str]]

The included accuracy bucket (categories contributing to the 99.5% target):
    Deterministic: rule_coverage / multi_rule / accumulated_state
    LLM-path (re-admitted per GH-90): hallucination / unknown_input / query

Out-of-bucket categories (not in included_accuracy or latency anchors):
    llm_review

The bucket is controlled by _INCLUDED_CATEGORIES (accuracy/latency gate) and
_DETERMINISTIC_CATEGORIES (exception re-raise gate). See their docstrings.

Architectural note: replay.py intentionally imports from samantha_server.api and
samantha_server.observability so that the _ReplayHarness (which wraps the real
/events endpoint) can exercise the same dispatch_event() chokepoint as production.
The deterministic-purity allowlist has entries for:
  ("samantha_server.scenarios.replay", "samantha_server.api.receipt_writer")
  ("samantha_server.scenarios.replay", "samantha_server.api.lifespan")
  ("samantha_server.scenarios.replay", "samantha_server.observability.counters")
  ("samantha_server.scenarios.replay", "samantha_server.observability.otel")
See tests/architectural/test_deterministic_purity.py.

These imports are deferred (inside function bodies) to avoid collection-time
side effects (samantha_server.api.routing → samantha_server.llm.handlers →
samantha_server.config).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import dataclasses
import hashlib
import json
import logging
import os
import sys
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Literal

# PR210 Low #8: opentelemetry.trace lifted to module top so the
# private-attribute coupling (_TRACER_PROVIDER) is visible at import
# time rather than buried in two function bodies. opentelemetry-api
# has no problematic collection-time side effects (unlike
# samantha_server.api.* / .llm.* which are deferred below for that
# reason).
import opentelemetry.trace as _otel_trace_module

from samantha_server.engine.decision import (
    _DISPOSITION_TO_TRACE_VERB,
    _REFUSAL_REASONS,
    EngineDecision,
    LLMReviewTrace,
    QueryTrace,
    RefusalTrace,
)
from samantha_server.engine.transitions import _SYMBOLIC_TOKENS
from samantha_server.models.context import VALID_FLAGS, VALID_STATES, Event, Order, SpecimenContext
from samantha_server.scenarios.loader import Scenario, ScenarioStep, load_scenarios

if TYPE_CHECKING:
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from samantha_server.llm.client import LLMClient
    from samantha_server.rules.loader import RuleIndex

_logger = logging.getLogger(__name__)


# Categories whose pass/fail contributes to the 99.5% regression target via
# the deterministic engine. Kept as a separate name from _INCLUDED_CATEGORIES
# because _replay_scenario uses it to gate exception re-raise vs log-and-continue:
# non-deterministic categories (LLM-path) should never re-raise on error so that
# degenerate inputs don't crash the harness. _DETERMINISTIC_CATEGORIES controls
# that exception-handling invariant; _INCLUDED_CATEGORIES controls bucket membership.
# Cross-reference: docs/plans/phase-1-implementation.md § "Step 12 — Accuracy bucket".
_DETERMINISTIC_CATEGORIES: frozenset[str] = frozenset(
    {"rule_coverage", "multi_rule", "accumulated_state"}
)

# LLM-path categories re-admitted to the included_accuracy denominator per GH-90.
# These categories are in-bucket for accuracy counting but NOT in _DETERMINISTIC_CATEGORIES,
# so exceptions in their scenarios are still logged-and-continued (not re-raised).
_LLM_PATH_CATEGORIES: frozenset[str] = frozenset({"query", "unknown_input", "hallucination"})

# GH-228: categories whose presence in a corpus requires up-front LLM-client
# construction. Strict superset of _LLM_PATH_CATEGORIES — adds `llm_review`,
# which routes step 2 through dispatch_event() (PENDING_LLM_REVIEW handoff)
# but remains OUT of _INCLUDED_CATEGORIES (out-of-bucket for accuracy). The
# two roles are separate on purpose: bucket membership controls the accuracy
# denominator; this set controls deps-construction at the top of replay().
# Without `llm_review` here, an isolated `--include-category=llm_review`
# sweep silently bypassed LLM init and produced 0.0s vacuous-pass results.
_LLM_CLIENT_CATEGORIES: frozenset[str] = _LLM_PATH_CATEGORIES | frozenset({"llm_review"})

# GH-171 / PR #179 review L2: the per-step routing predicate's gates. Named
# constants instead of inline literals so the routing contract is grep-able
# and a refactor that retypes either gate updates one site, not multiple.
_LLM_REVIEW_PENDING_STATE: Final[str] = "PENDING_LLM_REVIEW"
_LLM_TYPED_EVENT: Final[str] = "clinical_query"

# Union of all categories that contribute to included_accuracy and included_total.
# Use this constant for bucket-membership checks (accuracy numerator/denominator).
# Use _DETERMINISTIC_CATEGORIES for exception-handling gating in _replay_scenario.
_INCLUDED_CATEGORIES: frozenset[str] = _DETERMINISTIC_CATEGORIES | _LLM_PATH_CATEGORIES

# Lazy singleton cache for the default RuleIndex (loaded from the specs directory).
# Tests that need a fresh index can pass rule_index= explicitly to replay().
_DEFAULT_RULE_INDEX: RuleIndex | None = None

# Hard CI gate constants for the LLM-path latency anchor (GH-121 / Phase 3 Step 6).
# The gate is active only when llm_latency_step_count >= _LLM_ANCHOR_MIN_STEP_COUNT.
# Below the floor the gate is informational (not a hard failure).
# 35 s — GH-273 re-tuned for Qwen3-Next-80B-A3B baseline (observed p99
# 28.7 s + ~20% headroom). Bumped from the 25 s Gemma-era anchor; see
# docs/eval-anchors.md § 3.
_LLM_LATENCY_ANCHOR_US: Final[int] = 35_000_000
_LLM_ANCHOR_MIN_STEP_COUNT: Final[int] = 30

# GH-194 Slice 5: SHA-256 hash of an empty byte string.
# Used to detect empty LLM responses in the query content gate:
# a QueryTrace with response_text_hash == _SHA256_EMPTY indicates the
# model returned an empty string (no content to parse). PR205 Low #9:
# computed at import time rather than hard-coded so a future change to
# the hash function or canonical encoding can't drift this constant.
_SHA256_EMPTY: Final[str] = hashlib.sha256(b"").hexdigest()

# GH-209 / PR211 review #3: cap a list of identifiers (e.g., order_ids) to 5
# items for display in the CLI report's content_diagnostic, with a `...+N more`
# suffix on overflow. Module-scoped (not a closure) so tests can import it
# directly and pin the truncation contract — previously this was duplicated
# in three test closures because the production version was unimportable.
_FMT_ID_LIST_CAP: Final[int] = 5


def _fmt_id_list(items: list[str]) -> str:
    """Format ``items`` as a list literal, capped at ``_FMT_ID_LIST_CAP`` items.

    Empty list renders as ``[]``. Lists at or under the cap render normally.
    Lists over the cap render the first ``_FMT_ID_LIST_CAP`` items followed by
    ``, ...+N more]`` where N is the overflow count.

    Used by the post-loop query content gate in ``_replay_scenario_async`` to
    render ``mismatch_query_response`` diagnostics. Callers must pass a sorted
    list so the rendering is deterministic across runs.
    """
    if len(items) <= _FMT_ID_LIST_CAP:
        return str(items)
    overflow = len(items) - _FMT_ID_LIST_CAP
    return str(items[:_FMT_ID_LIST_CAP])[:-1] + f", ...+{overflow} more]"


# OTLP flush/shutdown timeout for replay_to_langfuse(). 10s is enough to
# drain a typical batch and short enough that the caller gets quick
# feedback when the endpoint is wrong. Default SDK shutdown timeout is
# 30s — too long for an interactive CLI run.
_FLUSH_TIMEOUT_MS: Final[int] = 10_000

# Operational refusal-reason constants (StepStatusValue-coupled) live near
# StepStatusValue further below — see _REFUSAL_REASON_TO_STATUS and
# _OPERATIONAL_REFUSAL_REASONS. They couldn't be hoisted up here without
# forward-referencing StepStatusValue or duplicating its Literal members.


def _build_llm_client_for_replay() -> LLMClient:
    """Build a real LLM client for replay by mirroring api.lifespan._build_llm_client.

    Called by replay() when no _llm_client_override is provided and LLM-path
    scenarios are present. Failures surface as MisconfiguredEnvironmentError —
    fail loud; no silent fallback to the deterministic path.

    The import of api.lifespan._build_llm_client is lazy so that deterministic-only
    corpora never load MLX at all.
    """
    from samantha_server.api.lifespan import _build_llm_client

    return _build_llm_client()


@contextlib.contextmanager
def _model_scope(model_id: str) -> Iterator[None]:
    """Scope cfg.LLM_MODEL_PATH and cfg.LLM_MODEL_NAME to *model_id* for the
    duration of the ``with`` block, then restore both on exit (even on
    exception).

    Used by both _warm_up_models() and the per-(sweep,model) sweep body in
    main() to avoid duplicating the same save/restore dance in two places
    (review #4 — Beck "say it once").
    """
    # Lazy import: deterministic-only corpora with --no-warm-up never load
    # config (preserves the existing module-load contract).
    import samantha_server.config as cfg

    _prev_path = cfg.LLM_MODEL_PATH
    _prev_name = cfg.LLM_MODEL_NAME
    cfg.LLM_MODEL_PATH = model_id
    cfg.LLM_MODEL_NAME = model_id
    try:
        yield
    finally:
        cfg.LLM_MODEL_PATH = _prev_path
        cfg.LLM_MODEL_NAME = _prev_name


def _warm_up_model(model_id: str) -> None:
    """Issue one trivial completion against *model_id* so oMLX loads its
    weights before any timed scenario runs (GH-283).

    Without this, the first LLM-routed scenario per model pays a 60-120s
    cold-load tax — oMLX serializes model-load behind the request, so the
    request hits the wire fast (low ``pre_send_us``) but the server never
    responds within the read timeout (``server_elapsed_us=never``). Same
    timing-log shape as GH-229's stale-keepalive hang, different cause; the
    GH-282 keepalive fix doesn't address this because the bottleneck is on
    the server side.

    ``MisconfiguredEnvironmentError`` propagates — `_build_llm_client_for_replay()`'s
    contract is "fail loud; no silent fallback," and swallowing this typed
    error would produce a false-green on deterministic-only corpora where
    the sweep never re-tries the build (review #2). All other ``Exception``s
    (transport errors, transient LLM failures, model-not-loaded responses)
    are logged with full message and swallowed so the timed sweep can
    surface them authoritatively (review #5).

    PHI boundary: the warm-up prompt is a fixed inert string.

    Caller is responsible for entering _model_scope(model_id) first.
    """
    from samantha_server.errors import MisconfiguredEnvironmentError

    try:
        client = _build_llm_client_for_replay()
        # max_tokens=1 is sufficient — oMLX loads weights during the prefill
        # pass regardless of decode-token request size (review #9).
        client.complete(prompt="ok", max_tokens=1)
        print(f"Warmed up {model_id}", file=sys.stderr)
    except MisconfiguredEnvironmentError:
        # Config-level breakage affects every model; propagate so the
        # operator sees the failure on the first warm-up attempt rather
        # than after a deterministic-only false-green (review #2).
        raise
    except Exception as exc:  # noqa: BLE001
        # Transport / runtime warm-up failure — log full detail and let the
        # timed sweep surface any persistent issue with full context.
        print(
            f"Warm-up failed for {model_id}: {type(exc).__name__}: {exc} "
            f"(continuing; sweep will surface)",
            file=sys.stderr,
        )


def _collect_latencies(
    verdicts: list[ScenarioVerdict],
    skiplist: Mapping[str, str],
) -> tuple[list[int], list[int]]:
    """Return (deterministic_latencies, llm_latencies) sampled from verdicts.

    Latency bucket membership is conditioned on the routing path *actually taken*
    (StepVerdict.routing_path), not on scenario category. This ensures that:
    - Steps that errored before dispatch (routing_path=None) don't count.
    - Deterministic fallback timings never populate the LLM bucket.
    - Category-keying is replaced by routing_path-keying (GH-121 Critical #2).

    Skiplisted scenarios and error steps (latency_us is None) are excluded.
    """
    det_latencies: list[int] = []
    llm_latencies: list[int] = []
    for v in verdicts:
        if v.scenario_id in skiplist:
            continue
        for sv in v.step_verdicts:
            if sv.latency_us is None:
                continue
            if sv.routing_path == "deterministic":
                det_latencies.append(sv.latency_us)
            elif sv.routing_path == "llm":
                llm_latencies.append(sv.latency_us)
            # routing_path=None (error before dispatch) → excluded from both buckets
    return det_latencies, llm_latencies


def _p99(samples: list[int]) -> int | None:
    """Inclusive nearest-rank 99th percentile.

    Returns ``None`` for an empty input (so the gate can't be silently
    satisfied by a zero default). For non-empty input, returns
    ``sorted(samples)[max(0, round(0.99 * (n-1)))]`` — the value at the 99th
    percentile rank.
    """
    if not samples:
        return None
    sorted_samples = sorted(samples)
    return sorted_samples[max(0, round(0.99 * (len(sorted_samples) - 1)))]


def _get_default_rule_index() -> RuleIndex:
    """Return the module-level cached RuleIndex, building it on first call."""
    global _DEFAULT_RULE_INDEX
    if _DEFAULT_RULE_INDEX is None:
        from pathlib import Path as _Path

        from samantha_server.rules.loader import RuleIndex as _RuleIndex
        from samantha_server.rules.loader import load_rule_specs

        specs_dir = _Path(__file__).parent.parent / "rules" / "specs"
        _DEFAULT_RULE_INDEX = _RuleIndex(load_rule_specs(specs_dir))
    return _DEFAULT_RULE_INDEX


@dataclass(frozen=True)
class ExpectedOutput:
    next_state: str
    applied_rules: tuple[str, ...]
    flags: tuple[str, ...]


@dataclass(frozen=True)
class PredictedOutput:
    next_state: str
    applied_rules: tuple[str, ...]
    flags: tuple[str, ...]


# PR #286 finding #4: extracted shared alias prevents drift between
# StepVerdict.status and _status_for_step's return-type annotation (both
# previously inlined the same Literal block).
StepStatusValue = Literal[
    "pass",
    "mismatch_state",
    "mismatch_rules",
    "mismatch_flags",
    "mismatch_routing_path",
    "dispatch_empty",
    "error",
    # GH-194: content-gate statuses
    "mismatch_query_response",
    "mismatch_disposition",
    "invalid_json",
    "empty_response",
    "hallucinated_rule",
    "hallucinated_flag",
    "hallucinated_state",
    # GH-285: LLM-unavailable refusal on a content-annotated step
    "llm_unavailable",
    # GH-287: SKILL_UNAVAILABLE / PHI_BOUNDARY refusals on a content-annotated step
    "skill_unavailable",
    "phi_boundary_violation",
]

# GH-287: operational pre-LLM refusal reasons that should fail loudly through
# the content gate, with each mapped to its StepStatusValue. The dict is the
# source of truth; _OPERATIONAL_REFUSAL_REASONS is derived from it so the two
# can't drift. Typed against _REFUSAL_REASONS so mypy catches typos against the
# controlled vocabulary in samantha_server.engine.decision.
_REFUSAL_REASON_TO_STATUS: dict[
    _REFUSAL_REASONS,
    Literal["llm_unavailable", "skill_unavailable", "phi_boundary_violation"],
] = {
    "STAGE_PRE_LLM_UNAVAILABLE": "llm_unavailable",
    "STAGE_PRE_SKILL_UNAVAILABLE": "skill_unavailable",
    "STAGE_PRE_PHI_BOUNDARY": "phi_boundary_violation",
}
_OPERATIONAL_REFUSAL_REASONS: frozenset[_REFUSAL_REASONS] = frozenset(
    _REFUSAL_REASON_TO_STATUS.keys()
)


@dataclass(frozen=True)
class StepVerdict:
    scenario_id: str
    step_index: int
    status: StepStatusValue
    expected: ExpectedOutput
    predicted: PredictedOutput
    # perf_counter_ns delta in microseconds; None on error steps where no decision was produced.
    # Note: a real measurement may also truncate to 0 on hosts where rule evaluation completes
    # in under 1 µs (the // 1_000 conversion in evaluator.py); use ``is None`` to test for
    # the no-measurement sentinel rather than ``== 0``.
    latency_us: int | None = None
    # The routing path actually taken for this step. Set to "deterministic" when evaluate()
    # was used, "llm" when dispatch_event() was used, and None when the step errored before
    # routing was determined. None steps are excluded from both p99 latency buckets.
    # GH-121 Critical #2: bucket membership is path-keyed, not category-keyed.
    routing_path: Literal["deterministic", "llm"] | None = None
    # GH-209: human-readable hint at what content disagreed when a content-gate
    # status fires. None on structural mismatches and on passing steps.
    content_diagnostic: str | None = None


@dataclass(frozen=True)
class ScenarioVerdict:
    scenario_id: str
    category: str
    status: Literal["pass", "fail"]
    step_verdicts: tuple[StepVerdict, ...]


@dataclass(frozen=True)
class AccuracyReport:
    included_accuracy: float
    overall_accuracy: float
    included_total: int
    overall_total: int
    included_pass: int
    overall_pass: int
    scenario_verdicts: tuple[ScenarioVerdict, ...]
    # 99th-percentile per-decision latency in microseconds across deterministic-category,
    # non-skiplisted scenarios. None when the bucket has no latency samples (so a `<`
    # comparison can't be silently satisfied by a zero default). Pins Phase 1 plan
    # § 5 / Step 15 — the acceptance gate enforces this against a 10ms ceiling.
    p99_latency_us: int | None
    # 99th-percentile per-decision latency in microseconds across LLM-path-category,
    # non-skiplisted scenarios. None when the LLM bucket has no latency samples.
    # Sourced separately from `p99_latency_us` per the GH-90 do-not-merge invariant:
    # the deterministic anchor stays clean as the engine-perf canary while this field
    # captures the LLM-path budget (35 s p99 target at production-tier model — GH-273).
    # Bucket membership is routing_path-keyed (not category-keyed) per GH-121 Critical #2.
    p99_latency_us_llm: int | None
    # Per-bucket sample counts (non-skiplisted, non-error step verdicts). These are
    # the *exact* populations that fed the corresponding p99 fields above; consumers
    # should compare against ``_P99_SAMPLE_FLOOR`` / ``_P99_LLM_SAMPLE_FLOOR`` rather
    # than re-walking ``scenario_verdicts`` (re-walking risks drifting from the
    # skiplist filter applied here).
    deterministic_latency_step_count: int = 0
    llm_latency_step_count: int = 0


@dataclass
class _ReplayHarness:
    """Long-lived harness that routes every replay step through the REAL /events endpoint.

    GH-324 Phase B, Step 5 — replaces the direct dispatch_event() call with
    POST /events → priority queue → _consume consumer → dispatch_event → receipt.
    Built once per replay() run (analogous to _ReplayDeps), torn down via aclose().

    Construction is split: __init__ accepts config; __aenter__ builds the
    app/consumer/client (must run inside an event loop). Use as an async
    context manager::

        async with _ReplayHarness(...) as harness:
            resp = await harness.client.post("/events", ...)

    Fields
    ------
    rule_index:
        The RuleIndex passed to the harness's AppState.
    has_llm_path:
        When False, injects a no-op stub LLMClient so no oMLX/MLX load occurs.
        When True, uses the provided llm_client (must not be None).
    llm_client:
        Real LLM client when has_llm_path=True; None otherwise (stub injected).
    receipts_db_path:
        Optional path for receipt persistence (forwarded to ReceiptWriter.open).
        None uses in-memory SQLite so replay receipts never pollute the dev store.
    scenarios:
        The list of scenarios (used to build the scenario_index on AppState).

    Runtime fields (populated by __aenter__):
    state:      The constructed AppState.
    client:     httpx.AsyncClient with ASGITransport bound to the FastAPI app.
    _rbac_key:  The RBAC HMAC key used for signing auth tokens; also patched
                into _get_rbac_hmac_key so the app verifies them.
    """

    # Configuration (set in __init__ / provided at construction)
    rule_index: RuleIndex
    has_llm_path: bool
    llm_client: LLMClient | None
    receipts_db_path: Path | None
    scenarios: list[Scenario]

    # Runtime state (populated by __aenter__)
    state: Any = dataclasses.field(default=None)
    client: Any = dataclasses.field(default=None)
    _rbac_key: bytes = dataclasses.field(default_factory=lambda: _generate_replay_rbac_key())
    _patch_ctx: Any = dataclasses.field(default=None)

    async def __aenter__(self) -> _ReplayHarness:
        """Build AppState, start consumer, open httpx client, patch RBAC key."""
        import sqlite3 as _sqlite3
        import unittest.mock as _mock

        import httpx
        from fastapi import FastAPI

        import samantha_server.config as cfg
        from samantha_server.api.app import RequestIDMiddleware, _BodyCapMiddleware, _consume
        from samantha_server.api.events import register_events_routes
        from samantha_server.api.lifespan import AppState
        from samantha_server.api.rbac import register_rbac_exception_handlers
        from samantha_server.api.receipt_writer import ReceiptWriter
        from samantha_server.observability.cached_probe import make_langfuse_stub_probe
        from samantha_server.observability.counters import CounterRegistry
        from samantha_server.queue.priority import PriorityEventQueue
        from samantha_server.receipts.store import _SCHEMA_SQL
        from samantha_server.scenarios.index import build_scenario_index
        from samantha_server.skills.loader import discover

        # --- LLM client ---
        if self.has_llm_path:
            assert self.llm_client is not None, "llm_client must be provided when has_llm_path=True"
            effective_llm = self.llm_client
        else:
            effective_llm = _make_stub_llm_client()

        # --- ReceiptWriter ---
        if self.receipts_db_path is not None:
            receipt_writer = ReceiptWriter.open(self.receipts_db_path)
        else:
            # In-memory store: ephemeral receipts, no dev-store pollution.
            from samantha_server.receipts.store import init_schema as _init_schema

            _conn = _sqlite3.connect(":memory:", check_same_thread=False)
            _init_schema(_conn)
            receipt_writer = ReceiptWriter(_conn)

        # --- Read-only audit connection (AppState requires it) ---
        audit_conn = _sqlite3.connect(":memory:", check_same_thread=False)
        audit_conn.executescript(_SCHEMA_SQL)
        audit_conn.commit()
        audit_conn.execute("PRAGMA query_only=1")

        # --- Scenario index ---
        scenario_index = build_scenario_index(self.scenarios)

        # --- AppState ---
        state = AppState(
            rule_index=self.rule_index,
            scenario_index=scenario_index,
            skill_index=discover(),
            llm_client=effective_llm,
            receipt_writer=receipt_writer,
            receipt_write_lock=asyncio.Lock(),
            counters=CounterRegistry(),
            langfuse_probe=make_langfuse_stub_probe(),
            commit_sha="replay",
            queue=PriorityEventQueue(maxsize=512),
            receipt_audit_conn=audit_conn,
            is_draining=False,
        )
        self.state = state

        # --- FastAPI app ---
        app = FastAPI()
        register_rbac_exception_handlers(app)
        register_events_routes(app)
        app.add_middleware(_BodyCapMiddleware, max_request_body_bytes=cfg.MAX_REQUEST_BODY_BYTES)
        app.add_middleware(RequestIDMiddleware)
        app.state.engine = state

        # --- Start consumer task ---
        state.consumer_task = asyncio.create_task(_consume(state), name="replay-harness-consumer")

        # --- Patch RBAC key so the app verifies our signed tokens ---
        self._patch_ctx = _mock.patch(
            "samantha_server.api.rbac._get_rbac_hmac_key",
            return_value=self._rbac_key,
        )
        self._patch_ctx.start()

        # --- httpx async client ---
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://replay-harness",
        )
        await self.client.__aenter__()

        return self

    async def __aexit__(self, *_: object) -> None:
        """Tear down: close httpx client, cancel consumer, close state."""
        try:
            if self.client is not None:
                await self.client.__aexit__(None, None, None)
        finally:
            if self._patch_ctx is not None:
                self._patch_ctx.stop()
            if self.state is not None:
                await self.state.aclose()

    def make_auth_headers(self) -> dict[str, str]:
        """Return Authorization headers signed with this harness's RBAC key."""
        import time as _time

        from samantha_server.api.rbac import _sign_token

        now = int(_time.time())
        token = _sign_token("events:submit", now, now + 3600, hmac_key=self._rbac_key)
        return {"Authorization": f"Bearer {token}"}


def _generate_replay_rbac_key() -> bytes:
    """Generate a fresh 32-byte RBAC HMAC key for one replay harness lifetime.

    Uses os.urandom for entropy. Each _ReplayHarness instance gets its own key
    so concurrent harnesses (not present today but possible in tests) can't
    share tokens.
    """
    return os.urandom(32)


def _make_stub_llm_client() -> LLMClient:
    """Return a silent no-op LLMClient stub for deterministic-only replay.

    Used by _ReplayHarness when has_llm_path=False. Deterministic-category
    scenarios resolve through the engine without invoking the LLM, so this
    stub keeps a deterministic-only sweep backend-free (no oMLX/MLX load). It
    returns a minimal canned response rather than raising, so that if a step
    ever reaches the production clarification path (e.g. a missing required
    field) the consumer can still complete the dispatch and produce a verdict
    instead of crashing the harness.

    PHI boundary: the stub response text is a fixed inert string.
    """
    import unittest.mock as _mock

    from samantha_server.llm.client import LLMClient as _LLMClient
    from samantha_server.llm.client import LLMResponse as _LLMResponse

    stub = _mock.MagicMock(spec=_LLMClient)
    stub.model_id = "stub-no-llm"
    stub.complete.return_value = _LLMResponse(
        text="{}",
        input_tokens=1,
        output_tokens=1,
        model_id="stub-no-llm",
        latency_us=0,
    )
    stub.complete_json.return_value = _LLMResponse(
        text="{}",
        input_tokens=1,
        output_tokens=1,
        model_id="stub-no-llm",
        latency_us=0,
    )
    return stub


def _build_order(scenario_id: str, event_data: Mapping[str, Any]) -> Order:
    """Extract Order fields from the first event's event_data."""
    ordered_tests_raw = event_data.get("ordered_tests") or []
    return Order(
        order_id=scenario_id,
        patient_name=event_data.get("patient_name"),
        patient_sex=event_data.get("sex"),
        age=event_data.get("age"),
        specimen_type=event_data.get("specimen_type"),
        anatomic_site=event_data.get("anatomic_site"),
        fixative=event_data.get("fixative"),
        fixation_time_hours=event_data.get("fixation_time_hours"),
        ordered_tests=tuple(ordered_tests_raw),
        priority=event_data.get("priority"),
        billing_info_present=bool(event_data.get("billing_info_present", False)),
    )


def _verdict_for_step(
    scenario_id: str,
    step: ScenarioStep,
    decision: EngineDecision,
    predicted_next_state: str,
    accumulated_flags: frozenset[str],
    routing_path: Literal["deterministic", "llm"],
    *,
    rule_index: RuleIndex,
) -> StepVerdict:
    """Compare predicted outputs against expected outputs and return a StepVerdict.

    GH-194: Precedence order for status assignment (highest to lowest):
      hallucinated_state > hallucinated_rule > hallucinated_flag >
      invalid_json > mismatch_disposition / mismatch_query_response >
      mismatch_state > mismatch_rules > mismatch_flags >
      mismatch_routing_path > pass
    """
    expected = ExpectedOutput(
        next_state=step.expected_next_state,
        applied_rules=step.expected_applied_rules,
        flags=step.expected_flags,
    )

    # Predicted rules: applied_rule_id + also_matched (multi-rule ACCESSIONING step)
    if decision.applied_rule_id is not None:
        predicted_rules: tuple[str, ...] = (decision.applied_rule_id,) + decision.also_matched
    else:
        predicted_rules = ()

    predicted = PredictedOutput(
        next_state=predicted_next_state,
        applied_rules=predicted_rules,
        flags=tuple(sorted(accumulated_flags)),
    )

    # --- GH-194 Slice 3: hallucination checks (highest precedence) ----------
    # Check 1: engine returned a state that isn't in the vocabulary and isn't
    # a known symbolic token (ADVANCE_SAMPLE_PREP, RESOLVE_MISSING_INFO, etc.)
    # that resolve_transition will expand into a concrete state.
    if decision.next_state not in VALID_STATES and decision.next_state not in _SYMBOLIC_TOKENS:
        status: StepStatusValue = "hallucinated_state"
        return StepVerdict(
            scenario_id=scenario_id,
            step_index=step.step_index,
            status=status,
            expected=expected,
            predicted=predicted,
            latency_us=decision.latency_us,
            routing_path=routing_path,
            content_diagnostic=f"predicted_state={decision.next_state} (not in VALID_STATES)",
        )

    # Check 2: engine attributed a rule_id that doesn't exist in the index.
    # Covers both applied_rule_id and also_matched (secondary rule IDs in
    # multi-rule steps) — an invented also_matched id would otherwise surface
    # as mismatch_rules instead of the more specific hallucinated_rule.
    for _rid in (decision.applied_rule_id, *decision.also_matched):
        if _rid is not None and _rid not in rule_index:
            _is_also_matched = _rid != decision.applied_rule_id
            _rule_qualifier = (
                " (also_matched, not in rule_index)" if _is_also_matched else " (not in rule_index)"
            )
            return StepVerdict(
                scenario_id=scenario_id,
                step_index=step.step_index,
                status="hallucinated_rule",
                expected=expected,
                predicted=predicted,
                latency_us=decision.latency_us,
                routing_path=routing_path,
                content_diagnostic=f"predicted_rule_id={_rid}{_rule_qualifier}",
            )

    # Check 3: engine added a flag that isn't in the valid-flags vocabulary.
    for flag in decision.flags_added:
        if flag not in VALID_FLAGS:
            status = "hallucinated_flag"
            return StepVerdict(
                scenario_id=scenario_id,
                step_index=step.step_index,
                status=status,
                expected=expected,
                predicted=predicted,
                latency_us=decision.latency_us,
                routing_path=routing_path,
                content_diagnostic=f"predicted_flag={flag} (not in VALID_FLAGS)",
            )

    # --- GH-194 Slice 4: LLM-review content gate (per-step) -----------------
    # Applies when routing_path == 'llm' AND the step carries an expected
    # disposition (step.llm_disposition is not None).
    if routing_path == "llm" and step.llm_disposition is not None:
        llm_review_trace: LLMReviewTrace | None = next(
            (t for t in decision.decision_traces if isinstance(t, LLMReviewTrace)),
            None,
        )
        if llm_review_trace is not None:
            # parse_failure takes precedence over disposition mismatch.
            if llm_review_trace.parse_failure is not None:
                status = "invalid_json"
                return StepVerdict(
                    scenario_id=scenario_id,
                    step_index=step.step_index,
                    status=status,
                    expected=expected,
                    predicted=predicted,
                    latency_us=decision.latency_us,
                    routing_path=routing_path,
                    content_diagnostic=f"parse_failure={llm_review_trace.parse_failure}",
                )
            # Compare parsed_disposition (verb form) against the expected
            # disposition (past-tense form mapped via _DISPOSITION_TO_TRACE_VERB).
            expected_verb = _DISPOSITION_TO_TRACE_VERB.get(step.llm_disposition)
            if expected_verb is not None and llm_review_trace.parsed_disposition != expected_verb:
                status = "mismatch_disposition"
                return StepVerdict(
                    scenario_id=scenario_id,
                    step_index=step.step_index,
                    status=status,
                    expected=expected,
                    predicted=predicted,
                    latency_us=decision.latency_us,
                    routing_path=routing_path,
                    content_diagnostic=(
                        f"expected_disposition={expected_verb}"
                        f" parsed_disposition={llm_review_trace.parsed_disposition}"
                    ),
                )
        else:
            # PR205 review #2: no LLMReviewTrace means the engine emitted a
            # RefusalTrace instead. Per the LLMReviewTrace docstring, a JSON
            # parse failure on the specimen-review handler routes to a
            # STAGE_PRE_UNPARSEABLE refusal (not an LLMReviewTrace). For a step
            # the corpus annotated with llm_disposition, that's a content-gate
            # invalid_json. Operational refusals (skill/LLM unavailable, PHI)
            # fall through to the structural ladder — they aren't JSON failures.
            refusal_trace: RefusalTrace | None = next(
                (t for t in decision.decision_traces if isinstance(t, RefusalTrace)),
                None,
            )
            if (
                refusal_trace is not None
                and refusal_trace.refusal_reason == "STAGE_PRE_UNPARSEABLE"
            ):
                status = "invalid_json"
                return StepVerdict(
                    scenario_id=scenario_id,
                    step_index=step.step_index,
                    status=status,
                    expected=expected,
                    predicted=predicted,
                    latency_us=decision.latency_us,
                    routing_path=routing_path,
                    # PR211 review #5: `refusal_reason` and `parse_failure` are
                    # disjoint namespaces — STAGE_PRE_UNPARSEABLE is a refusal-
                    # stage label, not a parse-attempt discriminant
                    # (json_decode/schema_violation). Use the right key so
                    # operators don't conflate the two when triaging.
                    content_diagnostic=f"refusal_reason={refusal_trace.refusal_reason}",
                )
            # GH-285/GH-287: operational refusal on an llm_disposition-annotated step.
            # The handler returns STAGE_PRE_LLM_UNAVAILABLE when LLMClient raises,
            # STAGE_PRE_SKILL_UNAVAILABLE when SkillLoaderError is caught, and
            # STAGE_PRE_PHI_BOUNDARY when PHIBoundaryError fires.
            # Without this branch the structural check fires instead (state mismatch,
            # because the refusal keeps current_state while the fixture expects the
            # post-LLM state), burying the real failure cause.
            elif (
                refusal_trace is not None
                and refusal_trace.refusal_reason in _OPERATIONAL_REFUSAL_REASONS
            ):
                # See post-loop query gate for the outcome= omission rationale.
                _refusal_step_status = _REFUSAL_REASON_TO_STATUS[refusal_trace.refusal_reason]
                _diag_llm_review = (
                    f"refusal_reason={refusal_trace.refusal_reason} "
                    f"latency_us={decision.latency_us}"
                )
                if refusal_trace.underlying_error_type is not None:
                    _diag_llm_review += f" underlying={refusal_trace.underlying_error_type}"
                return StepVerdict(
                    scenario_id=scenario_id,
                    step_index=step.step_index,
                    status=_refusal_step_status,
                    expected=expected,
                    predicted=predicted,
                    latency_us=decision.latency_us,
                    routing_path=routing_path,
                    content_diagnostic=_diag_llm_review,
                )

    # --- Structural checks (original logic) ----------------------------------
    # Determine status — check state first, then rules, then flags, then
    # routing_path. The routing-path comparison fires only when state, rules,
    # and flags ALL match — i.e., it isolates the case where observable
    # outcomes agree but the harness took a different engine path than the
    # corpus annotation expected (PR #179 review M2 — catches the kind of
    # divergence M1's missing PreflightMissing arm could otherwise hide).
    # `expected_routing_path is None` means the fixture didn't annotate it;
    # treat as "don't compare" so older fixtures stay valid.
    if predicted.next_state != expected.next_state:
        status = "mismatch_state"
    elif (
        # GH-231 F-13: winner-first comparison. position 0 is the winning rule
        # (engine's applied_rule_id); positions 1+ are also_matched (order is
        # cosmetic so set-equality suffices there). An empty expected list must
        # still match an empty predicted list — the [:1] slice handles that.
        predicted.applied_rules[:1] != expected.applied_rules[:1]
        or set(predicted.applied_rules[1:]) != set(expected.applied_rules[1:])
    ):
        status = "mismatch_rules"
    elif set(predicted.flags) != set(expected.flags):
        status = "mismatch_flags"
    elif step.expected_routing_path is not None and routing_path != step.expected_routing_path:
        status = "mismatch_routing_path"
    else:
        status = "pass"

    return StepVerdict(
        scenario_id=scenario_id,
        step_index=step.step_index,
        status=status,
        expected=expected,
        predicted=predicted,
        latency_us=decision.latency_us,
        routing_path=routing_path,
    )


async def _replay_scenario_async(
    scenario: Scenario,
    rule_index: RuleIndex,
    *,
    harness: _ReplayHarness | None = None,
    re_raise_on_deterministic_error: bool = True,
) -> ScenarioVerdict:
    """Run one scenario through the engine and return a ScenarioVerdict.

    GH-324 Phase B Step 5 / GH-338: every step is submitted through the REAL
    production transport: POST /events → priority queue → _consume →
    dispatch_event → emit_receipt.  Does not call dispatch_event() or
    evaluate() directly.

    Per-step routing (GH-171): step routing (deterministic vs LLM-path) is
    decided inside the endpoint consumer.  The *routing_path* field on each
    StepVerdict is set here based on the per-step predicate so verdicts carry
    the correct routing attribution.

    If a step raises an unexpected error, the behaviour depends on the category:
    - Deterministic (``_DETERMINISTIC_CATEGORIES``): re-raise immediately so
      engine bugs are not hidden as error verdicts. Override with
      ``re_raise_on_deterministic_error=False`` (parity-replay path) to log the
      traceback and record the step as 'error' instead, so a single
      deterministic gap (e.g. an unmodelled flag in an external corpus) does
      not halt the entire sweep.
    - Otherwise (LLM-path categories like ``hallucination`` / ``unknown_input``,
      and out-of-bucket categories like ``llm_review``): log the traceback to
      stderr, record the step as 'error', and skip remaining steps. This
      prevents degenerate inputs from crashing the harness.

    Note: LLM-path categories are *in-bucket* for ``included_accuracy``
    (``_INCLUDED_CATEGORIES``) but *not* gated by re-raise here. The two roles
    are deliberately split between ``_DETERMINISTIC_CATEGORIES`` (re-raise gate)
    and ``_INCLUDED_CATEGORIES`` (accuracy bucket).
    """
    import time as _time

    order: Order | None = None
    current_state = "ACCESSIONING"
    flags: frozenset[str] = frozenset()
    step_verdicts: list[StepVerdict] = []

    # GH-194 Slice 5 / PR205 review #1: a single paired tracker for the
    # post-loop query content gate. Set ONLY after the step's verdict has been
    # successfully appended AND a QueryTrace is present. Pairing the index
    # and decision in one tuple makes desync structurally impossible if a
    # later step's dispatch succeeds but resolve_transition raises before
    # `step_verdicts.append(verdict)` runs.
    _last_query_gate_target: tuple[int, EngineDecision] | None = None

    for step in scenario.steps:
        event_data = step.event_data

        # Track routing_path across the try/except so the error branch can
        # attribute deterministic vs LLM-path errors distinctly. Without this,
        # a deterministic exception softened by re_raise_on_deterministic_error=False
        # would bin into errored_pre_routing in the composer's
        # step_routing_counts and trigger the GH-172 "model never called"
        # banner — even though the model was never going to be called for a
        # deterministic-category scenario in the first place.
        routing_path: Literal["deterministic", "llm"] | None = None

        try:
            # Step 1: extract the Order from event_data
            if order is None:
                order = _build_order(scenario.scenario_id, event_data)

            # GH-227: inject scenario-level user_role into event_data when non-None.
            # Copy-on-write: do not mutate the ScenarioStep's event_data Mapping.
            # Scenario-level wins: if the step's event_data already carries user_role,
            # the scenario-level value overrides it (scenario annotation is authoritative).
            if scenario.user_role is not None:
                event_data = dict(event_data) | {"user_role": scenario.user_role}

            event = Event(
                event_type=step.event_type,
                event_data=event_data,
                step_index=step.step_index,
            )

            ctx = SpecimenContext(
                order=order,
                current_state=current_state,
                flags=flags,
                event=event,
            )

            # GH-324 Phase B Step 5 / GH-338: endpoint path — the sole dispatch branch.
            # Submit every step through POST /events → queue → _consume → dispatch_event.
            # routing_path is determined by the per-step predicate below; it cannot be
            # read from the EventResponse (routing_path lives in EventDispatchContext
            # which is not serialised to the response body).
            assert harness is not None, (
                "_replay_scenario_async: harness must be provided (endpoint path is the sole path)"
            )
            routing_path = (
                "llm"
                if (
                    current_state == _LLM_REVIEW_PENDING_STATE
                    or step.event_type == _LLM_TYPED_EVENT
                )
                else "deterministic"
            )

            ctx_dict = ctx.model_dump(mode="json")
            body = {
                "ctx": ctx_dict,
                "session_id": scenario.scenario_id,
                "priority": "ROUTINE",
                # GH-324 Phase B: always include the key so EventRequest's
                # model_fields_set detects it as "explicitly provided".
                # str → forwarded verbatim; None → null → no anchor block.
                "prompt_timestamp": scenario.prompt_timestamp,
            }

            _post_start = _time.monotonic()
            response = await harness.client.post(
                "/events",
                json=body,
                headers=harness.make_auth_headers(),
            )
            _wall_clock_us = int((_time.monotonic() - _post_start) * 1_000_000)

            if response.status_code != 200:
                raise RuntimeError(
                    f"POST /events returned {response.status_code} "
                    f"for {scenario.scenario_id} step {step.step_index}: "
                    f"{response.text[:200]}"
                )

            resp_data = response.json()
            # GH-324/GH-333 PHI fix: the /events wire omits decision_traces and
            # primitive_traces (they carry verbatim clinical strings).
            # Reconstruct the FULL EngineDecision from the persisted receipt's
            # payload_json via the same connection the harness's ReceiptWriter
            # used — a fresh connect(":memory:") would be a different empty DB.
            # dispatch_event always emit_receipt()s before the response returns,
            # so a missing receipt is a real receipt-emission-invariant violation,
            # not an expected case — raise loudly rather than masking it.
            _receipt_id = resp_data["receipt_id"]
            _payload_json = harness.state.receipt_writer.fetch_payload_json(
                _receipt_id,
            )
            if _payload_json is not None:
                decision = EngineDecision.model_validate(json.loads(_payload_json))
            else:
                # Missing receipt is a receipt-emission-invariant violation.
                # dispatch_event always calls emit_receipt() before returning;
                # if fetch_payload_json() returns None the invariant was broken
                # by the caller (e.g. a test stub that skipped emit_receipt).
                raise RuntimeError(
                    f"replay: no persisted receipt for receipt_id={_receipt_id!r} "
                    f"({scenario.scenario_id} step {step.step_index}); "
                    "dispatch_event must call emit_receipt() before returning — "
                    "receipt-emission invariant violated"
                )

            _logger.debug(
                "replay endpoint step: scenario=%r step=%d routing=%s "
                "next_state=%r wall_clock_us=%d",
                scenario.scenario_id,
                step.step_index,
                routing_path,
                decision.next_state,
                _wall_clock_us,
            )

            # On the endpoint path, dispatch_event already resolved symbolic
            # transitions and applied runtime flag clearing. decision.next_state
            # is the final concrete state; no resolve_transition call needed.
            predicted_next_state = decision.next_state

            # Accumulate flags from this decision for _verdict_for_step.
            # On the endpoint path, routing.py has already applied apply_runtime_flag_clearing
            # with the symbolic next_state (before resolve_transition was called), and then
            # stamped the resolved concrete state onto decision.next_state.  So calling
            # apply_runtime_flag_clearing here would silently skip the clearing because
            # decision.next_state is now "RESULTING" (not the symbolic "RESOLVE_MISSING_INFO").
            # Instead, mirror the clearing using the stable rule ID: RES-002 + billing
            # info_type is the one case apply_runtime_flag_clearing handles.
            # NOTE: this 1:1 coupling assumes RES-002 is the SOLE rule emitting
            # RESOLVE_MISSING_INFO. If a second emitter is ever added, this rule-ID
            # check (and apply_runtime_flag_clearing) must be generalized together.
            flags = (flags - set(decision.flags_cleared)) | set(decision.flags_added)
            if decision.applied_rule_id == "RES-002" and event_data.get("info_type") == "billing":
                flags = flags - {"MISSING_INFO_PROCEED"}

        except Exception:  # noqa: BLE001
            # Re-raise gate is keyed on _DETERMINISTIC_CATEGORIES specifically —
            # do NOT substitute _INCLUDED_CATEGORIES here. LLM-path categories
            # are in-bucket for accuracy counting but their exceptions must stay
            # log-and-continue so degenerate inputs (e.g. SC-104's all-null Order)
            # don't crash the harness. See module docstring re-raise gate invariant.
            #
            # Note: asyncio.CancelledError is a BaseException (not Exception) and
            # intentionally bypasses this block — loop-wide cancellation should
            # propagate cleanly.
            if scenario.category in _DETERMINISTIC_CATEGORIES and re_raise_on_deterministic_error:
                raise

            # For non-deterministic categories (LLM-path or out-of-bucket),
            # log the traceback to stderr and record the step as 'error'.
            import traceback

            print(traceback.format_exc(), file=sys.stderr)

            expected = ExpectedOutput(
                next_state=step.expected_next_state,
                applied_rules=step.expected_applied_rules,
                flags=step.expected_flags,
            )
            predicted = PredictedOutput(
                next_state="<error>",
                applied_rules=(),
                flags=(),
            )
            step_verdicts.append(
                StepVerdict(
                    scenario_id=scenario.scenario_id,
                    step_index=step.step_index,
                    status="error",
                    expected=expected,
                    predicted=predicted,
                    # routing_path reflects the state at the moment of the
                    # exception: None if the failure happened before the
                    # engine call, "deterministic"/"llm" if it happened after
                    # routing was decided. The composer uses this to
                    # distinguish "orchestration refused" (None) from
                    # "engine bug under re_raise=False" (deterministic/llm).
                    routing_path=routing_path,
                )
            )
            # Skip remaining steps in this scenario
            break

        verdict = _verdict_for_step(
            scenario.scenario_id,
            step,
            decision,
            predicted_next_state,
            flags,
            routing_path,
            rule_index=rule_index,
        )
        step_verdicts.append(verdict)

        # GH-194 Slice 5 / PR205 review #1: pair the (index, decision) tracker
        # so a later step's dispatch-success-but-post-dispatch-raise cannot
        # leave the index pointing at one step while the decision advances to
        # another. Set only AFTER step_verdicts.append above —
        # if we never reach this line for step N, step N never wins the gate.
        if routing_path == "llm" and any(
            isinstance(t, QueryTrace)
            or (isinstance(t, RefusalTrace) and t.refusal_reason in _OPERATIONAL_REFUSAL_REASONS)
            for t in decision.decision_traces
        ):
            _last_query_gate_target = (len(step_verdicts) - 1, decision)

        # Thread forward using EXPECTED values so step failures don't cascade.
        # (Beck pattern from samantha-public harness: advance_order_state uses expected.)
        current_state = step.expected_next_state
        flags = frozenset(step.expected_flags)

    # GH-194 Slice 5: post-loop query content gate.
    # For query scenarios, apply content correctness checks to the last
    # LLM-routed step that produced a QueryTrace. This is post-loop (not
    # per-step) because only the final answer step owns content accountability —
    # intermediate LLM steps (e.g., routing hops) do not carry expected_order_ids.
    # StepVerdict is frozen — use dataclasses.replace to rewrite the verdict.
    #
    # Precedence (highest to lowest within the content gate):
    #   invalid_json > empty_response > mismatch_query_response
    if scenario.category == "query" and _last_query_gate_target is not None:
        _target_index, _target_decision = _last_query_gate_target
        last_verdict = step_verdicts[_target_index]
        # Only apply when the step is currently passing the structural gate —
        # if it's already failing structurally, leave the existing status.
        # PR205 Low #17: this intentionally inverts the per-step precedence
        # ladder (where hallucinations beat structural mismatches) into a
        # structural-mismatch-wins-over-content post-loop. Rationale: a
        # structural failure is already a more concrete signal than "the
        # content didn't match"; double-charging the same step as both
        # mismatch_state AND mismatch_query_response over-counts in
        # failure_counts. Keep failures attributed to their most-specific cause.
        if last_verdict.status == "pass":
            # GH-285/GH-287: check for operational refusals BEFORE the QueryTrace branch.
            # When LLMClient raises, SkillLoaderError is caught, or PHIBoundaryError fires,
            # the handler returns a RefusalTrace with the corresponding reason and no QueryTrace.
            # The structural checks pass (state/rules/flags match) so without this
            # branch the scenario silently passes — the fixture's expected
            # order_ids are never compared.
            _refusal_trace: RefusalTrace | None = next(
                (
                    t
                    for t in _target_decision.decision_traces
                    if isinstance(t, RefusalTrace)
                    and t.refusal_reason in _OPERATIONAL_REFUSAL_REASONS
                ),
                None,
            )
            if _refusal_trace is not None:
                # NOTE: query-gate diagnostic includes outcome= but the per-step
                # llm_review gate (in _verdict_for_step) omits it — intentional
                # asymmetry. The post-loop query gate is further from the
                # decision than the per-step path; surfacing outcome here gives
                # operators an extra hop of context. Keep both formats stable.
                _refusal_status = _REFUSAL_REASON_TO_STATUS[_refusal_trace.refusal_reason]
                _diag_parts = [
                    f"refusal_reason={_refusal_trace.refusal_reason}",
                    f"outcome={_target_decision.outcome}",
                    f"latency_us={_target_decision.latency_us}",
                ]
                if _refusal_trace.underlying_error_type is not None:
                    _diag_parts.append(f"underlying={_refusal_trace.underlying_error_type}")
                step_verdicts[_target_index] = dataclasses.replace(
                    last_verdict,
                    status=_refusal_status,
                    content_diagnostic=" ".join(_diag_parts),
                )
            else:
                query_trace: QueryTrace | None = next(
                    (t for t in _target_decision.decision_traces if isinstance(t, QueryTrace)),
                    None,
                )
                if query_trace is not None:
                    # PR205 review #8: narrow to the exact branch set so
                    # dataclasses.replace doesn't need a type: ignore.
                    # PR286 finding #5: "llm_unavailable" intentionally absent —
                    # the refusal arm (above the else: this lives in) sets
                    # StepVerdict.status directly, so this content_status
                    # local can never produce it. Including it here would
                    # let a future accidental assignment escape detection.
                    content_status: (
                        Literal[
                            "invalid_json",
                            "empty_response",
                            "mismatch_query_response",
                        ]
                        | None
                    ) = None
                    # GH-209: human-readable diagnostic populated alongside content_status.
                    _query_content_diagnostic: str | None = None
                    if query_trace.parse_failure is not None:
                        content_status = "invalid_json"
                        _query_content_diagnostic = f"parse_failure={query_trace.parse_failure}"
                    elif (
                        query_trace.parsed_order_ids is None
                        and query_trace.response_text_hash == _SHA256_EMPTY
                    ):
                        content_status = "empty_response"
                        # PR211 review #4: the branch fires *because* parse_failure
                        # is None, so the previous `parse_failure=None ...` key was
                        # uninformative. The informative field is the hash matching
                        # sha256('').
                        _query_content_diagnostic = (
                            f"response_text_hash={query_trace.response_text_hash} (empty)"
                        )
                    else:
                        # Determine expected order_ids from the scenario's accessor.
                        # Only apply the check when expected_query_content is present —
                        # None means the fixture doesn't annotate content, so pass.
                        #
                        # PR205 review #5: this uses SET-EQUALITY (per GH-194 § Task 3).
                        # Models returning correct IDs plus extras now fail —
                        # that's the truthfulness-fix design.
                        #
                        # PR205 review #6: step-level expected_output.order_ids
                        # override (per GH-194 § Task 3 last sentence) is OUT OF
                        # SCOPE for this PR — no corpus fixture uses it and
                        # ScenarioStep doesn't carry the field. Add an order_ids
                        # field to ScenarioStep + plumb it through to override the
                        # top-level value here when it lands.
                        #
                        # GH-220: branch on answer_type from the fixture.
                        # order_status fixtures carry a single subject_id in order_ids;
                        # the gate must verify (a) model used order_status answer_type
                        # and (b) model's order_ids matches the subject. This is
                        # semantically different from order_list set-equality.
                        _fixture_answer_type = scenario.expected_answer_type
                        expected_order_ids = scenario.expected_query_content
                        if _fixture_answer_type in {"no_orders", "uncertain"}:
                            # GH-231 F-6: no_orders/uncertain branch — check (a) model used the
                            # correct answer_type AND (b) model returned empty order_ids. Both
                            # conditions must hold; if the model returns order_list with non-empty
                            # ids against a no_orders fixture the gate must fire. This branch fires
                            # BEFORE the order_list/unannotated elif so the empty expected_order_ids
                            # path cannot accidentally fall through to set-equality (which would
                            # pass because the empty-set check was skipped by len > 0 guard).
                            if query_trace.parsed_answer_type != _fixture_answer_type:
                                content_status = "mismatch_query_response"
                                _query_content_diagnostic = (
                                    f"expected_answer_type={_fixture_answer_type}"
                                    f" parsed_answer_type={query_trace.parsed_answer_type!r}"
                                )
                            elif (
                                # Guard: parsed_order_ids is not None. Under
                                # QueryTrace._tri_state_invariant this is unreachable
                                # when parsed_answer_type is set (JSON-success requires
                                # both fields populated together). The explicit None-check
                                # defends against a future model_construct() bypass that
                                # would skip the validator and silently produce a
                                # parsed_order_ids=None record alongside the matched
                                # answer_type — mirroring the bypass-rationale comments
                                # on the order_status (L1226) and prioritized_list
                                # (L1200) branches. PR #238 review M2.
                                query_trace.parsed_order_ids is not None
                                and len(query_trace.parsed_order_ids) > 0
                            ):
                                content_status = "mismatch_query_response"
                                _parsed = _fmt_id_list(sorted(query_trace.parsed_order_ids))
                                _query_content_diagnostic = (
                                    f"expected_answer_type={_fixture_answer_type}"
                                    f" expected_order_ids=[]"
                                    f" parsed_order_ids={_parsed}"
                                )
                        elif _fixture_answer_type == "prioritized_list":
                            # GH-222: prioritized_list branch — sequence-equality
                            # (position-sensitive). The fixture's order_ids is an ordered
                            # list; the model must emit the same sequence in the same rank.
                            # Use expected_query_sequence (tuple) rather than
                            # expected_query_content (frozenset) to preserve order.
                            expected_sequence = scenario.expected_query_sequence
                            # PR224 Cluster D: empty-sequence early-exit. An empty
                            # prioritized_list fixture is degenerate — no meaningful
                            # sequence to validate. Pass-through aligns with the
                            # assertion path's early-exit on empty order_ids.
                            # tuple() is non-None, so the prior `if expected_sequence is not None`
                            # guard was insufficient — it entered the block and fired the
                            # parsed_answer_type check even for empty fixtures.
                            if expected_sequence is not None and len(expected_sequence) > 0:
                                if query_trace.parsed_answer_type != "prioritized_list":
                                    content_status = "mismatch_query_response"
                                    _query_content_diagnostic = (
                                        f"expected_answer_type=prioritized_list"
                                        f" parsed_answer_type={query_trace.parsed_answer_type!r}"
                                        f" expected_sequence={list(expected_sequence)!r}"
                                    )
                                elif (
                                    # Guard: parsed_order_ids is not None. Under
                                    # QueryTrace._tri_state_invariant this is unreachable
                                    # when parsed_answer_type is set (JSON-success requires
                                    # both fields populated together). The explicit None-check
                                    # defends against future model_construct() bypass that
                                    # would skip the validator and silently skip the sequence
                                    # comparison. PR224 Cluster D / GH-220 review #8 pattern.
                                    query_trace.parsed_order_ids is not None
                                    and tuple(query_trace.parsed_order_ids) != expected_sequence
                                ):
                                    content_status = "mismatch_query_response"
                                    _query_content_diagnostic = (
                                        f"expected_sequence={list(expected_sequence)!r}"
                                        f" parsed_sequence={list(query_trace.parsed_order_ids)!r}"
                                    )
                        elif (
                            _fixture_answer_type == "order_status"
                            and expected_order_ids is not None
                        ):
                            # order_status branch: validate (a) model used correct
                            # answer_type, then (b) model identified the right subject.
                            if query_trace.parsed_answer_type != "order_status":
                                content_status = "mismatch_query_response"
                                _query_content_diagnostic = (
                                    f"expected_answer_type=order_status"
                                    f" parsed_answer_type={query_trace.parsed_answer_type!r}"
                                    f" expected_subject={_fmt_id_list(sorted(expected_order_ids))}"
                                )
                            elif (
                                # Guard: parsed_order_ids is not None. Under
                                # QueryTrace._tri_state_invariant this is unreachable
                                # when parsed_answer_type is set (JSON-success requires
                                # both fields populated together). The explicit None-check
                                # defends against future model_construct() bypass that
                                # would skip the validator and silently skip the subject
                                # comparison. GH-220 fix-review #8.
                                query_trace.parsed_order_ids is not None
                                and set(query_trace.parsed_order_ids) != expected_order_ids
                            ):
                                content_status = "mismatch_query_response"
                                _expected_subject = sorted(expected_order_ids)
                                _parsed_subject = sorted(query_trace.parsed_order_ids)
                                _query_content_diagnostic = (
                                    f"expected_subject={_fmt_id_list(_expected_subject)}"
                                    f" parsed_subject={_fmt_id_list(_parsed_subject)}"
                                )
                        elif expected_order_ids is not None and len(expected_order_ids) > 0:
                            # order_list branch (or no answer_type annotation — None from the
                            # typed accessor means either absent or unrecognized, both handled
                            # by Scenario.expected_answer_type which already warns on unrecognized).
                            # GH-231 F-1: check answer_type before set-equality, mirroring the
                            # order_status branch above. An order_list fixture where the model
                            # returns order_status (correct IDs, wrong shape) must surface as
                            # mismatch_query_response rather than passing silently.
                            if (
                                _fixture_answer_type == "order_list"
                                and query_trace.parsed_answer_type != "order_list"
                            ):
                                content_status = "mismatch_query_response"
                                _sorted_expected_ids = sorted(expected_order_ids)
                                _query_content_diagnostic = (
                                    f"expected_answer_type=order_list"
                                    f" parsed_answer_type={query_trace.parsed_answer_type!r}"
                                    f" expected_order_ids={_fmt_id_list(_sorted_expected_ids)}"
                                )
                            elif (
                                query_trace.parsed_order_ids is not None
                                and set(query_trace.parsed_order_ids) != expected_order_ids
                            ):
                                # Set-equality check for order_list and unannotated fixtures.
                                content_status = "mismatch_query_response"
                                _sorted_expected = sorted(expected_order_ids)
                                _sorted_parsed = sorted(query_trace.parsed_order_ids)
                                _query_content_diagnostic = (
                                    f"expected_order_ids={_fmt_id_list(_sorted_expected)}"
                                    f" parsed_order_ids={_fmt_id_list(_sorted_parsed)}"
                                )

                    if content_status is not None:
                        # PR211 review #7: every branch that sets content_status
                        # must also set _query_content_diagnostic — the two travel
                        # together. A future fourth branch that forgets to populate
                        # the diagnostic would silently emit content_diagnostic=None
                        # for that gate's new status, recreating the GH-209 bug. Pin
                        # the contract here so the failure surfaces at gate-fire
                        # time, not via an operator-experience regression.
                        assert _query_content_diagnostic is not None, (
                            "GH-209 contract: content_status="
                            f"{content_status!r} set without _query_content_diagnostic"
                        )
                        step_verdicts[_target_index] = dataclasses.replace(
                            last_verdict,
                            status=content_status,
                            content_diagnostic=_query_content_diagnostic,
                        )

    scenario_status: Literal["pass", "fail"] = (
        "pass" if all(sv.status == "pass" for sv in step_verdicts) else "fail"
    )

    return ScenarioVerdict(
        scenario_id=scenario.scenario_id,
        category=scenario.category,
        status=scenario_status,
        step_verdicts=tuple(step_verdicts),
    )


async def _replay_all_async(
    scenarios: list[Scenario],
    rule_index: RuleIndex,
    *,
    re_raise_on_deterministic_error: bool = True,
    progress: bool = False,
    _harness_has_llm_path: bool = False,
    _harness_llm_client: LLMClient | None = None,
    _harness_receipts_db_path: Path | None = None,
) -> list[ScenarioVerdict]:
    """Run all scenarios sequentially inside a single event loop.

    Using a single asyncio.run() wrapper (rather than one per scenario) ensures
    the harness event loop is valid for the full corpus run. Multiple asyncio.run()
    calls (one per scenario) would create a new event loop per scenario; the
    asyncio.Lock in _ReplayHarness would be stale for subsequent scenarios
    (GH-121 Critical #3).

    GH-338: The endpoint harness (POST /events → queue → _consume → dispatch_event)
    is the sole dispatch path. A ``_ReplayHarness`` is always built here and shared
    across all scenario runs.

    When ``progress`` is True, emit one stdout line per completed scenario in the
    form ``[i/N] <scenario_id> <status> (<n.n>s)`` (elapsed seconds printed to
    one decimal place). ``N`` is the post-filter total — eligibility filters
    are applied by the caller before this function sees the list.
    """
    import time

    async def _run_scenarios(h: _ReplayHarness) -> list[ScenarioVerdict]:
        verdicts: list[ScenarioVerdict] = []
        total = len(scenarios)
        for i, scenario in enumerate(scenarios, start=1):
            started = time.monotonic()
            verdict = await _replay_scenario_async(
                scenario,
                rule_index,
                harness=h,
                re_raise_on_deterministic_error=re_raise_on_deterministic_error,
            )
            verdicts.append(verdict)
            if progress:
                elapsed = time.monotonic() - started
                print(
                    f"[{i}/{total}] {scenario.scenario_id} {verdict.status} ({elapsed:.1f}s)",
                    flush=True,
                )
        return verdicts

    h = _ReplayHarness(
        rule_index=rule_index,
        has_llm_path=_harness_has_llm_path,
        llm_client=_harness_llm_client,
        receipts_db_path=_harness_receipts_db_path,
        scenarios=list(scenarios),
    )
    async with h as harness:
        return await _run_scenarios(harness)


def _load_skiplist(directory: Path, override_path: Path | None = None) -> dict[str, str]:
    """Load skiplist JSON; return scenario_id → issue URL.

    By default reads `<directory>/.skiplist.json`. When *override_path* is
    set (GH-156 parity-replay), reads that file instead — pointing at a
    nonexistent path effectively disables the skiplist for parity runs
    that need to score against the as-published corpus rather than
    samantha_server's curated subset.

    Each entry's value must be a non-empty tracking issue URL. Raises
    ValueError when any entry has an empty or null URL. Skipped scenarios
    are excluded from BOTH numerator and denominator of accuracy ratios —
    they neither help nor hurt the gate. Each entry must point to a real
    tracking issue so the exclusion is auditable.
    """
    path = override_path if override_path is not None else directory / ".skiplist.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    raw = dict(data.get("skipped", {}))
    for scenario_id, url in raw.items():
        if not url:
            raise ValueError(
                f"Skiplist entry {scenario_id!r} has empty or null URL; "
                "each entry must point to a tracking issue."
            )
    return raw


def replay(
    directory: Path,
    *,
    rule_index: RuleIndex | None = None,
    _llm_client_override: LLMClient | None = None,
    receipts_db_path: Path | None = None,
    include_scenario_ids: set[str] | None = None,
    include_categories: set[str] | None = None,
    skiplist_path: Path | None = None,
    re_raise_on_deterministic_error: bool = True,
    progress: bool = False,
) -> AccuracyReport:
    """Replay all scenarios in *directory* through the rule engine.

    GH-338: every step is routed through the production endpoint path (POST
    /events → queue → _consume → dispatch_event → emit_receipt). The legacy
    direct-dispatch path (deps/tracer) has been removed.

    Returns an AccuracyReport with:
      - included_accuracy: pass rate for the broad bucket — deterministic
        categories (rule_coverage / multi_rule / accumulated_state) plus the
        re-admitted LLM-path categories (query / unknown_input / hallucination).
        See ``_INCLUDED_CATEGORIES``.
      - overall_accuracy: pass rate across all categories.
      - p99_latency_us: deterministic-bucket per-decision p99.
      - p99_latency_us_llm: LLM-path-bucket per-decision p99 (routing_path-keyed).
      - scenario_verdicts: per-scenario results.

    Pass *rule_index* to reuse an already-loaded index; otherwise loads from
    the default specs directory.

    Pass *_llm_client_override* to inject a stub LLM client for testing.
    When None and LLM-path scenarios are present, replay() calls
    _build_llm_client_for_replay() to build a real LLM client (the production-CLI
    path). Failures from _build_llm_client_for_replay() are propagated — no
    silent fallback to the deterministic engine.

    Tests that exercise LLM-path routing must pass a mock via _llm_client_override
    to avoid loading MLX in CI.

    Pass *re_raise_on_deterministic_error*=False (parity-replay path) to
    convert deterministic-category exceptions into ``status="error"``
    StepVerdicts instead of halting the sweep. Default True preserves the
    "deterministic exceptions are real engine bugs that must fail loud" CI
    contract; the parity CLI flips this to False so a single unmodelled flag
    in an external corpus does not collapse the entire diff to one finding.

    Scenarios listed in `<directory>/.skiplist.json` are excluded from BOTH
    numerator and denominator of the included/overall accuracy ratios AND
    from both p99 buckets. They still appear in `scenario_verdicts` with
    `status="fail"` so reports show the deferred work; each skiplist entry
    must point to a tracking issue.

    Pass *receipts_db_path* (GH-156, GH-306) to persist receipts to a SQLite
    file at that path instead of the default in-memory store. The path is
    forwarded to the ``_ReplayHarness`` which manages its own ``ReceiptWriter``.
    """
    if rule_index is None:
        rule_index = _get_default_rule_index()

    scenarios = load_scenarios(directory)

    # GH-156: filter post-load + before fan-out so overall_total reflects
    # the filtered population. Required by the parity CLI to score the
    # 33-scenario screening subset without copying fixtures into a temp dir.
    # TODO(GH-156): push include_scenario_ids into load_scenarios so that
    # non-matching JSON files are skipped before read_text() / json.loads().
    # Today the entire corpus is parsed for a 33-of-N filter; cost is
    # bounded while the corpus is small but grows linearly.
    if include_scenario_ids is not None:
        scenarios = [s for s in scenarios if s.scenario_id in include_scenario_ids]

    # GH-184 Slice 5: filter by category when include_categories is set.
    # When both include_scenario_ids and include_categories are set, BOTH
    # filters apply — the scenario must match both. Filter applied after
    # include_scenario_ids for consistency with the existing ordering.
    if include_categories is not None:
        scenarios = [s for s in scenarios if s.category in include_categories]

    skiplist = _load_skiplist(directory, override_path=skiplist_path)

    has_llm_path = any(s.category in _LLM_CLIENT_CATEGORIES for s in scenarios)

    # Resolve LLM client up-front so the harness can inject it into AppState.
    _harness_llm_client: LLMClient | None = None
    if has_llm_path:
        if _llm_client_override is not None:
            _harness_llm_client = _llm_client_override
        else:
            _harness_llm_client = _build_llm_client_for_replay()

    verdicts = asyncio.run(
        _replay_all_async(
            scenarios,
            rule_index,
            re_raise_on_deterministic_error=re_raise_on_deterministic_error,
            progress=progress,
            _harness_has_llm_path=has_llm_path,
            _harness_llm_client=_harness_llm_client,
            _harness_receipts_db_path=receipts_db_path,
        )
    )

    included_pass = sum(
        1
        for v in verdicts
        if v.category in _INCLUDED_CATEGORIES
        and v.scenario_id not in skiplist
        and v.status == "pass"
    )
    included_total = sum(
        1 for v in verdicts if v.category in _INCLUDED_CATEGORIES and v.scenario_id not in skiplist
    )
    overall_pass = sum(1 for v in verdicts if v.scenario_id not in skiplist and v.status == "pass")
    overall_total = sum(1 for v in verdicts if v.scenario_id not in skiplist)

    included_accuracy = included_pass / included_total if included_total > 0 else 1.0
    overall_accuracy = overall_pass / overall_total if overall_total > 0 else 1.0

    # Per-bucket p99 latency. Bucket membership is routing_path-keyed (not
    # category-keyed) per GH-121 Critical #2: only steps that actually called
    # dispatch_event() contribute to the LLM bucket; error steps (routing_path=None)
    # are excluded from both buckets.
    #
    # The two buckets are deliberately not merged (GH-90 do-not-merge invariant):
    # the deterministic anchor stays clean as the engine-perf canary.
    deterministic_latencies, llm_latencies = _collect_latencies(verdicts, skiplist)
    p99_latency_us = _p99(deterministic_latencies)
    p99_latency_us_llm = _p99(llm_latencies)

    return AccuracyReport(
        included_accuracy=included_accuracy,
        overall_accuracy=overall_accuracy,
        included_total=included_total,
        overall_total=overall_total,
        included_pass=included_pass,
        overall_pass=overall_pass,
        scenario_verdicts=tuple(verdicts),
        p99_latency_us=p99_latency_us,
        p99_latency_us_llm=p99_latency_us_llm,
        deterministic_latency_step_count=len(deterministic_latencies),
        llm_latency_step_count=len(llm_latencies),
    )


def replay_with_langfuse_export(
    directory: Path,
    *,
    langfuse_base_url: str,
    langfuse_public_key: str,
    langfuse_secret_key: str,
    release: str,
    include_categories: set[str] | None = None,
    progress: bool = False,
    receipts_db_path: Path | None = None,
    include_scenario_ids: set[str] | None = None,
    rule_index: RuleIndex | None = None,
    _llm_client_override: LLMClient | None = None,
    _id_exporter_override: InMemorySpanExporter | None = None,
) -> tuple[AccuracyReport, list[str]]:
    """Replay corpus through the endpoint path with OTLP export to Langfuse.

    GH-338: Unifies Langfuse export with the production endpoint path (POST
    /events → queue → _consume → dispatch_event). Replaces the legacy
    ``_replay_to_langfuse_core`` / ``replay_to_langfuse`` /
    ``_replay_to_langfuse_with_release`` chain for all new call sites.

    The OTel scaffolding (OTLP exporter, counting wrapper, Resource with
    ``langfuse.release``, BatchSpanProcessor, side-channel InMemorySpanExporter,
    global-provider save/install/restore via ``_otel_trace_module._TRACER_PROVIDER``)
    is lifted from ``_replay_to_langfuse_core``.  The crucial difference:
    the inner call is ``replay(directory, ...)`` with NO ``tracer=`` argument,
    so every step routes through the endpoint harness (``_use_endpoint = True``).
    events.py reads the global OTel provider to open the PARENT_SPAN_NAME span,
    so installing the OTLP-exporting provider as global is sufficient.

    Parameters
    ----------
    directory:
        Corpus directory (forwarded to ``replay()``).
    langfuse_base_url:
        Langfuse instance base URL.  Trailing slash is stripped.
    langfuse_public_key:
        Langfuse public key for Basic auth.
    langfuse_secret_key:
        Langfuse secret key for Basic auth.
    release:
        ``langfuse.release`` Resource attribute value.  Must be non-empty and
        non-whitespace (raises ``ValueError`` on blank).
    include_categories / progress / receipts_db_path / include_scenario_ids /
    rule_index / _llm_client_override:
        Forwarded verbatim to ``replay()``.
    _id_exporter_override:
        Test-only hook: inject an existing ``InMemorySpanExporter`` so tests can
        inspect finished spans after the call.
    """
    if not release.strip():
        raise ValueError(f"release must be a non-empty, non-whitespace string; got {release!r}")

    import base64
    import logging
    import uuid

    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter as _InMemorySpanExporter,
    )

    from samantha_server.observability.counters import CounterRegistry
    from samantha_server.observability.otel import make_counting_exporter

    log = logging.getLogger(__name__)

    endpoint = f"{langfuse_base_url.rstrip('/')}/api/public/otel/v1/traces"
    creds = f"{langfuse_public_key}:{langfuse_secret_key}"
    encoded = base64.b64encode(creds.encode("utf-8")).decode("ascii")
    auth_header = f"Basic {encoded}"

    otlp_exporter = OTLPSpanExporter(
        endpoint=endpoint,
        headers={"Authorization": auth_header},
    )

    counters = CounterRegistry()
    counting_exporter = make_counting_exporter(otlp_exporter, counters)

    run_id = uuid.uuid4().hex
    print(f"Langfuse: replay run id = {run_id}, release = {release!r}", file=sys.stderr)
    resource = Resource.create({"service.name": "samantha_server", "langfuse.release": release})

    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(counting_exporter))

    id_exporter = (
        _id_exporter_override if _id_exporter_override is not None else _InMemorySpanExporter()
    )
    provider.add_span_processor(SimpleSpanProcessor(id_exporter))

    # GH-208 global-provider save/install/restore (same pattern as _replay_to_langfuse_core).
    # Install as global so events.py's trace.get_tracer("samantha_server") picks up this provider.
    try:
        _prev_global_provider = _otel_trace_module._TRACER_PROVIDER
        _otel_trace_module._TRACER_PROVIDER = provider
        try:
            report = replay(
                directory,
                include_categories=include_categories,
                include_scenario_ids=include_scenario_ids,
                progress=progress,
                receipts_db_path=receipts_db_path,
                rule_index=rule_index,
                _llm_client_override=_llm_client_override,
            )
            if not provider.force_flush(timeout_millis=_FLUSH_TIMEOUT_MS):
                log.warning(
                    "replay_with_langfuse_export: force_flush timed out after %d ms; "
                    "buffered spans may not have reached %s",
                    _FLUSH_TIMEOUT_MS,
                    endpoint,
                )
        finally:
            _otel_trace_module._TRACER_PROVIDER = _prev_global_provider
    finally:
        try:
            provider.shutdown()
        except Exception as exc:  # noqa: BLE001
            log.warning("replay_with_langfuse_export: provider.shutdown() raised: %s", exc)

    trace_ids = [
        format(ctx.trace_id, "032x")
        for s in id_exporter.get_finished_spans()
        if (ctx := s.get_span_context()) is not None and ctx.trace_id != 0
    ]

    export_failures = counters.otel_export_failures.value

    log.info(
        "replay_with_langfuse_export: %d span(s), %d failure(s) (release=%s)",
        len(trace_ids),
        export_failures,
        release,
    )

    if export_failures > 0:
        print(
            f"Langfuse: WARNING — {export_failures} export failure(s) "
            f"(spans may not have reached {endpoint})",
            file=sys.stderr,
        )

    return report, trace_ids


def _print_report_and_get_exit_code(
    report: AccuracyReport,
    skiplist: dict[str, str],
    directory: Path,
) -> int:
    """Print a scenario replay report and return the appropriate exit code.

    GH-184 Slice 3: extracted from ``main()`` so the per-model loop and
    the single-model path share the same reporting logic.

    Returns 0 when all gates pass, 1 when any gate fails.
    """
    print("\nScenario Replay Report")
    print("======================")
    print(
        f"Included accuracy : {report.included_accuracy:.1%}"
        f"  ({report.included_pass}/{report.included_total})"
    )
    print(
        f"Overall accuracy  : {report.overall_accuracy:.1%}"
        f"  ({report.overall_pass}/{report.overall_total})"
    )

    # Warn about orphan skiplist entries: scenario is in the skiplist but passes.
    for v in report.scenario_verdicts:
        if v.scenario_id in skiplist and v.status == "pass":
            print(
                f"WARNING: orphan skiplist entry — {v.scenario_id!r} passes but is still skipped."
                f" Remove it from .skiplist.json. (tracked: {skiplist[v.scenario_id]})",
                file=sys.stderr,
            )

    if skiplist:
        skipped_verdicts = [v for v in report.scenario_verdicts if v.scenario_id in skiplist]
        print(f"\nSkipped (excluded from gate; tracked follow-ups): {len(skipped_verdicts)}")
        for v in skipped_verdicts:
            print(f"  [{v.category}] {v.scenario_id} → {skiplist[v.scenario_id]}")

    failures = [
        v for v in report.scenario_verdicts if v.scenario_id not in skiplist and v.status == "fail"
    ]
    if failures:
        print(f"\nFailed scenarios ({len(failures)}):")
        for v in failures:
            print(f"  [{v.category}] {v.scenario_id}")
            for sv in v.step_verdicts:
                if sv.status != "pass":
                    _step_line = (
                        f"    step {sv.step_index}: {sv.status}"
                        f" | expected_state={sv.expected.next_state}"
                        f" predicted_state={sv.predicted.next_state}"
                        f" | expected_rules={list(sv.expected.applied_rules)}"
                        f" predicted_rules={list(sv.predicted.applied_rules)}"
                        f" | expected_flags={list(sv.expected.flags)}"
                        f" predicted_flags={list(sv.predicted.flags)}"
                    )
                    if sv.content_diagnostic is not None:
                        _step_line += f" | content={sv.content_diagnostic}"
                    print(_step_line)
    else:
        print("\nAll scenarios passed.")

    # Evaluate both gates; accumulate failures so both are reported.
    exit_code = 0

    # LLM-path latency hard gate (GH-121 / Phase 3 Step 6).
    if report.llm_latency_step_count >= _LLM_ANCHOR_MIN_STEP_COUNT:
        if (
            report.p99_latency_us_llm is not None
            and report.p99_latency_us_llm >= _LLM_LATENCY_ANCHOR_US
        ):
            print(
                f"FAIL: LLM-path p99 latency {report.p99_latency_us_llm}us"
                f" >= anchor {_LLM_LATENCY_ANCHOR_US}us",
                file=sys.stderr,
            )
            exit_code = 1
    else:
        print(
            f"[INFO] LLM-path p99 gate deferred — "
            f"n={report.llm_latency_step_count} < floor={_LLM_ANCHOR_MIN_STEP_COUNT}"
        )

    threshold = 0.995
    if report.included_accuracy >= threshold:
        print(f"\nPASS: included_accuracy {report.included_accuracy:.4%} >= {threshold:.1%}")
    else:
        print(
            f"\nFAIL: included_accuracy {report.included_accuracy:.4%} < {threshold:.1%}",
            file=sys.stderr,
        )
        exit_code = 1

    return exit_code


def _aggregate_n_sweep_reports(
    reports: list[AccuracyReport],
) -> dict[str, tuple[int, int]]:
    """Aggregate per-fixture pass/total counts across N AccuracyReport sweeps.

    Args:
        reports: One AccuracyReport per sweep. The scenario-id set must be
            identical across all reports (corpus stability). Raises
            ValueError when sets differ.

    Returns:
        Mapping of scenario_id -> (passes, total_sweeps).
    """
    if not reports:
        return {}

    # Validate corpus stability: all reports must cover the same scenario ids.
    reference_ids = {v.scenario_id for v in reports[0].scenario_verdicts}
    for i, report in enumerate(reports[1:], start=1):
        current_ids = {v.scenario_id for v in report.scenario_verdicts}
        if current_ids != reference_ids:
            raise ValueError(
                f"Corpus stability mismatch between sweep 0 and sweep {i}: "
                f"scenario sets differ. "
                f"Added: {current_ids - reference_ids!r}, "
                f"Removed: {reference_ids - current_ids!r}"
            )

    totals: dict[str, int] = {sid: 0 for sid in reference_ids}
    passes: dict[str, int] = {sid: 0 for sid in reference_ids}

    for report in reports:
        for verdict in report.scenario_verdicts:
            totals[verdict.scenario_id] += 1
            if verdict.status == "pass":
                passes[verdict.scenario_id] += 1

    return {sid: (passes[sid], totals[sid]) for sid in sorted(reference_ids)}


def _print_n_sweep_summary(
    scenario_passes: dict[str, tuple[int, int]],
    n_sweeps: int,
    requested_n_sweeps: int | None = None,
    model_id: str | None = None,
) -> bool:
    """Print the N-sweep summary to stdout.

    Args:
        scenario_passes: Mapping of scenario_id -> (passes, total_sweeps)
            as returned by ``_aggregate_n_sweep_reports``.
        n_sweeps: Actual number of sweeps completed (used in header).
        requested_n_sweeps: Originally requested sweep count. When provided and
            less than n_sweeps, a WARNING line is emitted. Pass ``None`` to
            suppress the warning (e.g., when n_sweeps == requested_n_sweeps).
        model_id: When set (multi-model mode), a model header line is printed
            before the summary block so output is unambiguous.

    Returns:
        True if any fixture is flaky (passes < total across sweeps); False when
        every fixture passed every sweep. Callers should treat True as a signal
        to bump the exit code — a flaky corpus is an unstable baseline even if
        each individual sweep cleared the 99.5% accuracy gate.
    """
    print(f"\n{'=' * 60}")
    if model_id is not None:
        print(f"N-sweep summary for model: {model_id} (N={n_sweeps})")
    else:
        print(f"N-sweep summary (N={n_sweeps})")
    print(f"{'=' * 60}")

    if requested_n_sweeps is not None and n_sweeps < requested_n_sweeps:
        print(
            f"WARNING: only {n_sweeps}/{requested_n_sweeps} sweeps completed"
            + (f" for model {model_id}" if model_id is not None else "")
            + "; see per-sweep errors above"
        )

    if not scenario_passes:
        print("No scenarios to summarise.")
        return False

    total_fixtures = len(scenario_passes)
    stable_count = sum(1 for passes, total in scenario_passes.values() if passes == total)
    total_raw_passes = sum(passes for passes, _ in scenario_passes.values())
    total_raw_slots = sum(total for _, total in scenario_passes.values())

    stable_pct = 100.0 * stable_count / total_fixtures if total_fixtures else 0.0
    raw_pct = 100.0 * total_raw_passes / total_raw_slots if total_raw_slots else 0.0

    print(f"Stable accuracy:  {stable_count}/{total_fixtures} ({stable_pct:.2f}%)")
    print(f"Raw accuracy:     {total_raw_passes}/{total_raw_slots} ({raw_pct:.2f}%)")

    # Enumerate fixtures that did not pass every sweep.
    flaky = [
        (sid, passes, total) for sid, (passes, total) in scenario_passes.items() if passes < total
    ]
    if flaky:
        print("\nFlaky/failing fixtures:")
        for sid, passes, total in sorted(flaky, key=lambda t: (t[1], t[0])):
            print(f"  {sid}: {passes}/{total}")

    return bool(flaky)


def _positive_int(value: str) -> int:
    """argparse type validator: accept only integers >= 1."""
    try:
        ivalue = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not an integer") from None
    if ivalue < 1:
        raise argparse.ArgumentTypeError(f"--n-sweeps must be >= 1, got {ivalue}")
    return ivalue


def _receipts_db_path_type(value: str) -> Path:
    """argparse type validator: resolve --receipts-db-path and mkdir its parent.

    PR315 review #1+#8+#9: the file-backed receipts code path was reachable
    only from inside the sweep loop before. A non-existent parent dir or a
    permission-denied path raised a raw ``sqlite3.OperationalError`` from
    deep inside replay() and was swallowed by the per-model except guard
    (one traceback per (sweep, model) cell). Resolve + mkdir + writability
    are surfaced at parse time so a bad path fails fast and once.
    """
    path = Path(value).resolve()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise argparse.ArgumentTypeError(
            f"--receipts-db-path parent {path.parent} is not writable: {exc}"
        ) from exc
    return path


def _build_replay_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser for the replay CLI.

    Extracted from ``main()`` so tests can construct the parser directly
    without invoking the full main() body (GH-262 Slice 1).
    """
    parser = argparse.ArgumentParser(
        description="Replay scenarios through the rule engine (deterministic + LLM-path)."
    )
    parser.add_argument("directory", type=Path, help="Directory containing vendored scenarios")
    parser.add_argument(
        "--models",
        default=None,
        help=(
            "Comma-separated list of model IDs to sweep. When set, the corpus is "
            "replayed once per model (sequential); each model gets its own report. "
            "A combined cross-model summary is printed at the end. Exit code is "
            "non-zero if any model fails the gate. Overrides cfg.LLM_MODEL_PATH "
            "per iteration (scoped; restored after each run)."
        ),
    )
    parser.add_argument(
        "--include-category",
        dest="include_category",
        default=None,
        help=(
            "Comma-separated list of scenario categories to run "
            "(e.g. query,llm_review). When omitted, all categories run. "
            "Orthogonal to --models."
        ),
    )
    parser.add_argument(
        "--progress",
        action="store_true",
        help=(
            "Emit one stdout line per completed scenario in the form "
            "'[i/N] <scenario_id> <status> (<n.n>s)'. Useful for long-running "
            "live-LLM sweeps where the per-model summary would otherwise be "
            "the only output."
        ),
    )
    parser.add_argument(
        "--stamp-prompt",
        action="store_true",
        help=(
            "GH-196 / GH-363: force gen_ai.prompt stamping ON for this run by "
            "setting SAMANTHA_STAMP_PROMPT=1. Stamping is already default-on "
            "(GH-363), so this flag only matters as an override when the "
            "environment has it explicitly disabled (0/false/no/off). Applies "
            "to all model iterations in a --models sweep. Requires a self-hosted "
            "Langfuse instance; do not use with cloud Langfuse outside the "
            "lab-host topology."
        ),
    )
    parser.add_argument(
        "--n-sweeps",
        dest="n_sweeps",
        type=_positive_int,
        default=1,
        metavar="N",
        help=(
            "Number of full-corpus sweeps to run. Default 1. Recommended 5 for "
            "LLM-routed categories (model output at temperature=0.0 is not "
            "bit-deterministic on MLX; see GH-262). Each sweep produces a fresh "
            "Langfuse release_id. When combined with --models, each model gets "
            "its own N-sweep summary. Exit code is non-zero if any fixture is "
            "flaky (passes < N across sweeps) even if each individual sweep "
            "cleared the 99.5%% accuracy gate."
        ),
    )
    parser.add_argument(
        "--no-warm-up",
        dest="no_warm_up",
        action="store_true",
        help=(
            "GH-283: skip the per-(sweep, model) warm-up call. See "
            "_warm_up_model() docstring for the cold-load rationale."
        ),
    )
    parser.add_argument(
        "--receipts-db-path",
        dest="receipts_db_path",
        type=_receipts_db_path_type,
        default=None,
        metavar="PATH",
        help=(
            "Persist replay receipts to this SQLite file instead of the "
            "default in-memory store. Required for the receipts-evidence "
            "chart bundle (GH-311). Pointing at the configured production "
            "RECEIPTS_DB_PATH is refused (synthetic replay receipts must "
            "not pollute the prod audit trail). When combined with "
            "--models, each model's receipts go to a per-model file "
            "(``<stem>-<safe_model_id><suffix>``) so chart consumers can "
            "demux without relying on Langfuse trace ids. When omitted, "
            "receipts are held in memory and discarded per replay() call."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: python -m samantha_server.scenarios.replay <directory>

    Evaluates both the LLM-path latency gate and the accuracy gate; reports
    both failures before exiting. Returns combined non-zero exit when either
    gate fails so developers see all failures in a single run.
    """
    parser = _build_replay_parser()
    args = parser.parse_args(argv)

    # GH-196: --stamp-prompt sets SAMANTHA_STAMP_PROMPT before any LLM call so
    # that llm_complete_with_span / llm_complete_json_with_span stamp the prompt
    # text on gen_ai child spans. Propagated to all model iterations in the loop.
    #
    # PR207 review #5: snapshot the prior env state and restore it at the end
    # of main() so an in-process caller (test suite, orchestration layer)
    # that runs main(["--stamp-prompt", ...]) doesn't leak the env flag into
    # subsequent invocations in the same process. The try/finally below
    # wraps the rest of the function body.
    from samantha_server.observability.otel import STAMP_PROMPT_ENV

    _prev_stamp_prompt: str | None = None
    _set_stamp_prompt = bool(args.stamp_prompt)
    if _set_stamp_prompt:
        _prev_stamp_prompt = os.environ.get(STAMP_PROMPT_ENV)
        os.environ[STAMP_PROMPT_ENV] = "1"

    # Load `.env` before importing config so secrets / model paths are present
    # without an explicit `set -a; source .env; set +a` step. See _cli.py.
    from samantha_server._cli import load_dotenv_for_cli

    load_dotenv_for_cli()
    import samantha_server.config as cfg

    # PR315 review #7: refuse --receipts-db-path that aliases the production
    # store. Schema is idempotent, but writing synthetic replay receipts into
    # the prod audit trail would contaminate the signed chain consumers rely
    # on. Compare after resolve() so symlinks/relative paths can't sneak past.
    if args.receipts_db_path is not None:
        prod_receipts_path = Path(cfg.RECEIPTS_DB_PATH).resolve()
        if args.receipts_db_path == prod_receipts_path:
            print(
                f"ERROR: --receipts-db-path={args.receipts_db_path} points at "
                f"the production receipts store (cfg.RECEIPTS_DB_PATH). "
                f"Synthetic replay receipts must not be written to the prod "
                f"audit trail. Use a different path.",
                file=sys.stderr,
            )
            return 1

    # GH-184 Slice 3: resolve model list. When --models is set, split and
    # strip. When absent, treat as a single-element list containing the
    # config-default model so the downstream loop logic stays uniform.
    model_ids: list[str] = []
    if args.models:
        model_ids = [m.strip() for m in args.models.split(",") if m.strip()]
    if not model_ids:
        model_ids = [cfg.LLM_MODEL_NAME]

    multi_model = len(model_ids) > 1

    # GH-184 Slice 5: parse --include-category into a set. None means "all".
    include_categories: set[str] | None = None
    if args.include_category:
        include_categories = {c.strip() for c in args.include_category.split(",") if c.strip()}

    # GH-262: AccuracyReports from every sweep, partitioned by model_id.
    # This replaces the old flat all_sweep_reports list that mixed reports
    # from multiple models, producing wrong N denominators in multi-model mode.
    model_to_reports: dict[str, list[AccuracyReport]] = {mid: [] for mid in model_ids}

    skiplist = _load_skiplist(args.directory)
    combined_exit_code = 0

    for sweep_idx in range(args.n_sweeps):
        if args.n_sweeps > 1:
            print(f"\n[Sweep {sweep_idx + 1}/{args.n_sweeps}]")

        for model_id in model_ids:
            if multi_model:
                print(f"\n{'=' * 60}")
                print(f"Model: {model_id}")
                print(f"{'=' * 60}")

            # PR315 review #6: per-model receipts file when --receipts-db-path
            # is combined with --models. The replay code reuses the same
            # connection per replay() call but appends to the same file
            # across iterations of the multi-model loop, producing receipts
            # with no model_id discriminator column. Charts (GH-311) would
            # have to demux via Langfuse trace_id — only works when Langfuse
            # was enabled. Split foo.db → foo-<safe_model_id>.db so the
            # chart consumer can demux from the file path alone.
            safe_model_id = model_id.replace(":", "_").replace("/", "_")
            per_model_receipts_path: Path | None = None
            if args.receipts_db_path is not None:
                per_model_receipts_path = (
                    args.receipts_db_path.with_stem(f"{args.receipts_db_path.stem}-{safe_model_id}")
                    if multi_model
                    else args.receipts_db_path
                )

            # GH-283: warm up THIS (sweep, model) pair before the timed run.
            # Once-per-invocation placement would only defend sweep 1 model 1
            # — under LRU eviction on memory-constrained hosts, every later
            # (sweep, model) cell needs its own warm-up. Opt out with
            # --no-warm-up to measure cold-load deliberately.
            with _model_scope(model_id):
                # Warm-up runs INSIDE _model_scope so cfg is set when the LLM
                # client builds. SamanthaError (broken env) propagates out of
                # the with block and main() — fail loud. Other Exception is
                # logged-and-swallowed inside _warm_up_model() so the timed
                # sweep below still runs.
                if not args.no_warm_up:
                    _warm_up_model(model_id)

                try:
                    if cfg.LANGFUSE_ENABLED and cfg.LANGFUSE_PUBLIC_KEY and cfg.LANGFUSE_SECRET_KEY:
                        # GH-338: always route through the production endpoint path with OTLP
                        # export.  replay_with_langfuse_export installs the OTLP-exporting
                        # TracerProvider as global before calling replay() (no tracer= kwarg),
                        # so events.py's trace.get_tracer(...) picks it up transparently.
                        # Every (sweep, model) cell gets a fresh run_id so Langfuse can
                        # discriminate individual runs by the release label.
                        import uuid as _uuid

                        run_id = _uuid.uuid4().hex
                        release = f"{run_id}-{safe_model_id}"
                        report, trace_ids = replay_with_langfuse_export(
                            args.directory,
                            langfuse_base_url=cfg.LANGFUSE_BASE_URL,
                            langfuse_public_key=cfg.LANGFUSE_PUBLIC_KEY,
                            langfuse_secret_key=cfg.LANGFUSE_SECRET_KEY,
                            release=release,
                            include_categories=include_categories,
                            progress=args.progress,
                            receipts_db_path=per_model_receipts_path,
                        )
                        print(
                            f"Langfuse: {len(trace_ids)} span(s) exported (release={release})",
                            file=sys.stderr,
                        )
                    else:
                        report = replay(
                            args.directory,
                            include_categories=include_categories,
                            progress=args.progress,
                            receipts_db_path=per_model_receipts_path,
                        )
                except Exception:  # noqa: BLE001
                    # GH-184 fix-review M7: catch per-model exceptions so that a failure
                    # in model B does not discard model A's results. Mark the failed model
                    # in the combined exit code and continue to the next model.
                    import traceback

                    print(
                        f"\nERROR: replay failed for model {model_id!r}:\n{traceback.format_exc()}",
                        file=sys.stderr,
                    )
                    combined_exit_code = max(combined_exit_code, 1)
                    continue

            model_to_reports[model_id].append(report)
            model_exit_code = _print_report_and_get_exit_code(report, skiplist, args.directory)
            combined_exit_code = max(combined_exit_code, model_exit_code)

    # GH-262: N-sweep summary when more than one sweep was run.
    # Iterate per model so each summary has the correct N denominator.
    if args.n_sweeps > 1:
        for mid, reports in model_to_reports.items():
            if not reports:
                continue
            try:
                scenario_passes = _aggregate_n_sweep_reports(reports)
            except ValueError as exc:
                print(
                    f"WARNING: N-sweep aggregation failed for model {mid!r}: {exc}",
                    file=sys.stderr,
                )
                combined_exit_code = max(combined_exit_code, 1)
                continue
            actual_n = len(reports)
            if _print_n_sweep_summary(
                scenario_passes,
                n_sweeps=actual_n,
                requested_n_sweeps=args.n_sweeps if actual_n < args.n_sweeps else None,
                model_id=mid if multi_model else None,
            ):
                combined_exit_code = max(combined_exit_code, 1)

    # Cross-model summary when more than one model was run.
    # Derive one representative report per model from model_to_reports.
    # For multi-sweep runs we use the last completed sweep report per model;
    # for single-sweep runs this is equivalent to the old per_model_reports list.
    if multi_model:
        print(f"\n{'=' * 60}")
        print("Cross-model summary")
        print(f"{'=' * 60}")
        any_failed = False
        for mid, reports in model_to_reports.items():
            if not reports:
                # All sweeps for this model failed — mark as failed in summary.
                print(f"  FAIL [{mid}] no completed sweeps")
                any_failed = True
                continue
            # Use the last completed sweep's report for gate evaluation.
            report = reports[-1]
            gate_pass = report.included_accuracy >= 0.995
            status_str = "PASS" if gate_pass else "FAIL"
            if not gate_pass:
                any_failed = True
            print(
                f"  {status_str} [{mid}] "
                f"included_accuracy={report.included_accuracy:.4%} "
                f"({report.included_pass}/{report.included_total})"
            )
        if any_failed:
            print(
                "\nOverall: FAIL — one or more models below the 99.5% threshold.",
                file=sys.stderr,
            )
        else:
            print("\nOverall: PASS — all models at or above the 99.5% threshold.")

    try:
        return combined_exit_code
    finally:
        # PR207 review #5: restore the prior SAMANTHA_STAMP_PROMPT value so
        # an in-process caller that runs main(["--stamp-prompt", ...]) does
        # not leak the env flag into subsequent invocations.
        if _set_stamp_prompt:
            if _prev_stamp_prompt is None:
                os.environ.pop(STAMP_PROMPT_ENV, None)
            else:
                os.environ[STAMP_PROMPT_ENV] = _prev_stamp_prompt


if __name__ == "__main__":
    sys.exit(main())
