"""Shared fixtures + helpers for tests/scenarios/.

M-16: apply receipts_test_isolation to scenario replay tests.

GH-141: re-export the multi-model sweep harness so any test in this
package that injects ``model_under_test`` automatically parametrizes
over the resolved list (CLI > env > config default).
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid as _uuid
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

# GH-141: ``model_under_test`` and ``pytest_generate_tests`` are
# re-exported so pytest discovers them on this conftest. The
# ``--models`` flag is registered via ``pytest_addoption`` in the
# rootdir conftest (``tests/conftest.py``) — only the topmost conftest
# can add CLI options.
from samantha_server.scenarios.sweep import (  # noqa: F401
    model_under_test,
    pytest_generate_tests,
)

_log = logging.getLogger(__name__)

# Bounded force_flush timeout. The SDK default is 30 s, which lets an
# unreachable Langfuse hang the run for half a minute with no signal.
# 10 s is enough to drain a typical batch and short enough that the
# caller gets quick feedback when the endpoint is wrong. Mirrors
# ``samantha_server/scenarios/replay.py``'s ``_FLUSH_TIMEOUT_MS``.
_FLUSH_TIMEOUT_MS = 10_000


@pytest.fixture(autouse=True)
def _scenarios_receipts_isolation(receipts_test_isolation: None) -> None:  # noqa: PT004
    """Auto-apply receipts_test_isolation to all scenario tests.

    M-16: prevents cross-test cache leakage for tests that exercise routes
    through the receipt-signing and store infrastructure.
    """


# GH-153: session-stable sweep_run_id so every fixture parametrization in a
# single ``pytest -m live_llm`` invocation shares one value — Langfuse can
# group "show me one sweep run" by filtering on ``samantha.sweep_run_id``.
_SWEEP_RUN_ID: str = f"sweep-{_uuid.uuid4().hex[:12]}"


@pytest.fixture(scope="session")
def sweep_run_id() -> str:
    """GH-153: stable sweep_run_id across all live_llm tests in one session."""
    return _SWEEP_RUN_ID


@contextmanager
def sweep_span_context(
    *,
    scenario_id: str,
    scenario_category: str,
    sweep_run_id: str,
) -> Iterator[object]:
    """GH-153 (PR #164 review M-1): shared outer-span helper for live_llm sweeps.

    Both ``test_query_replay_with_live_llm`` and
    ``test_specimen_review_with_live_llm`` open an outer span tagged with
    the G5 sweep attributes before calling ``dispatch_event``. This helper
    consolidates that block (deferred imports + the ``with
    tracer.start_as_current_span(...)`` context manager + the
    ``stamp_trace_attributes(span, TraceContext(...))`` call) so a future
    change lands in one place rather than two.

    Yields the outer sweep span so callers can attach additional asserts
    against it (e.g., span-nesting tests).
    """
    from opentelemetry import trace as _otel_trace

    from samantha_server.observability.otel import stamp_trace_attributes
    from samantha_server.observability.trace_context import TraceContext

    tracer = _otel_trace.get_tracer("samantha_server.tests")
    with tracer.start_as_current_span("samantha_server.scenario_sweep") as sweep_span:
        stamp_trace_attributes(
            sweep_span,
            TraceContext(
                scenario_id=scenario_id,
                scenario_category=scenario_category,
                sweep_run_id=sweep_run_id,
            ),
        )
        yield sweep_span


def make_dispatch_deps(
    llm: object,
    skills: object,
    *,
    session_id: str = "test-session",
) -> tuple[dict[str, Any], list[Any]]:
    """Build the keyword-argument dict for ``dispatch_event()`` and a receipt collector.

    Returns ``(deps, written)`` where ``deps`` can be splat into
    ``dispatch_event(ctx, **deps)`` and ``written`` is the shared
    receipt-collector list. Constructs in-memory deps (write_lock,
    counters, rule_index, receipt collector) so tests don't touch the
    real SQLite store.

    M-8 fix: previously duplicated across ``test_llm_review_replay.py``
    and ``test_query_replay.py`` with cosmetic deltas (parametrized
    vs. hardcoded session_id, different return-list type annotations).
    Consolidated here so the dispatch-deps shape has a single home.
    """
    from samantha_server.observability.counters import CounterRegistry
    from samantha_server.queue.priority import EventPriority
    from samantha_server.rules.loader import RuleIndex
    from tests.api.helpers import SpyReceiptWriter

    written: list[Any] = []
    deps: dict[str, Any] = {
        "session_id": session_id,
        "priority": EventPriority.ROUTINE,
        "queue_wait_us": 0,
        "receipt_writer": SpyReceiptWriter(written),
        "write_lock": asyncio.Lock(),
        "counters": CounterRegistry(),
        "llm_client": llm,
        "scenarios_index": {},
        "skills_index": skills,
        "rule_index": RuleIndex([]),
    }
    return deps, written


@pytest.fixture(scope="session")
def _live_otel_session_provider() -> object:
    """Session-scoped OTel TracerProvider exporting to Langfuse.

    Built once per session and installed as the global tracer provider.
    Required because `llm_complete_with_span` uses
    `trace.get_tracer(...)` against the global provider for its
    `gen_ai.completion` child spans; if the global provider is a no-op,
    Langfuse receives only the orphan parent and surfaces nothing.

    OTel's `set_tracer_provider` is set-once — re-installing per test
    silently no-ops and any teardown that calls `provider.shutdown()`
    leaves subsequent tests writing to a dead exporter. Session scope
    sidesteps both: the provider lives for the run, and the per-test
    `live_otel_provider` fixture force-flushes it so each test's spans
    land before the next one starts.

    Skip-vs-fail policy (M-4): no Langfuse credentials → skip cleanly
    (developer convenience). Partial credentials in CI (one of the two
    keys non-empty, the other missing) → fail loudly: that's a
    misconfiguration, not an opt-out, and a green "N skipped" line
    would hide it. Outside CI, partial credentials still skip but
    also emit a warning to surface the inconsistency.

    The exporter is wrapped in `make_counting_exporter` (M-6) to
    mirror production's defense-in-depth against Authorization-header
    leakage via `requests.PreparedRequest` repr in exception logs.
    """
    import base64

    import samantha_server.config as cfg

    pk = cfg.LANGFUSE_PUBLIC_KEY or ""
    sk = cfg.LANGFUSE_SECRET_KEY or ""
    base = cfg.LANGFUSE_BASE_URL or ""
    have_pk = bool(pk)
    have_sk = bool(sk)
    have_base = bool(base)

    fully_configured = have_pk and have_sk and have_base
    fully_unset = not (have_pk or have_sk)
    in_ci = bool(os.environ.get("CI"))

    if not fully_configured:
        partial = have_pk != have_sk  # exactly one of the two keys present
        if partial and in_ci:
            pytest.fail(
                "Langfuse credential misconfiguration in CI: exactly one of "
                "LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY is set. Either set "
                "both (to enable live_llm trace export) or unset both (to skip)."
            )
        if partial:
            warnings.warn(
                "Partial Langfuse credentials (one of public_key/secret_key set, "
                "the other missing). Skipping live OTel export setup; live_llm "
                "tests will not export to Langfuse.",
                stacklevel=2,
            )
        if not have_base and (have_pk or have_sk):
            warnings.warn(
                "LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY set but "
                "LANGFUSE_BASE_URL missing. Skipping live OTel export setup.",
                stacklevel=2,
            )
        if fully_unset:
            pytest.skip("Langfuse credentials not set; skipping live OTel export setup")
        else:
            pytest.skip("Langfuse credentials incomplete; skipping live OTel export setup")

    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    from samantha_server.observability.counters import CounterRegistry
    from samantha_server.observability.otel import make_counting_exporter

    endpoint = f"{base.rstrip('/')}/api/public/otel/v1/traces"
    creds = f"{pk}:{sk}"
    auth_header = "Basic " + base64.b64encode(creds.encode("utf-8")).decode("ascii")

    raw_exporter = OTLPSpanExporter(endpoint=endpoint, headers={"Authorization": auth_header})
    # M-6 fix: wrap in make_counting_exporter so a `requests.PreparedRequest`
    # exception (which embeds outbound headers in its repr) cannot surface
    # the Authorization header through `exc_info=True` log calls. Mirrors
    # samantha_server.observability.otel:182-192 production wiring.
    exporter = make_counting_exporter(raw_exporter, CounterRegistry())

    resource = Resource.create({"service.name": "samantha_server"})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    try:
        yield provider
    finally:
        # M-5 fix: check force_flush() return; warn on False so a silent
        # span loss at session end doesn't go unnoticed. Wrap in
        # try/finally so shutdown() always runs even if force_flush()
        # raises — without this, a flush-time exception leaks the
        # BatchSpanProcessor thread.
        try:
            flushed = provider.force_flush(timeout_millis=_FLUSH_TIMEOUT_MS)
            if not flushed:
                _log.warning(
                    "OTel session-end force_flush timed out after %d ms; "
                    "spans from final test(s) may not have reached Langfuse.",
                    _FLUSH_TIMEOUT_MS,
                )
        finally:
            provider.shutdown()


@pytest.fixture
def live_otel_provider(_live_otel_session_provider: object) -> object:
    """Per-test handle yielding the session OTel tracer + flushing on teardown.

    Force-flushes the session provider after each test so spans for
    that parametrization actually reach Langfuse before the next test
    starts (rather than queuing in the BatchSpanProcessor for the full
    session and only landing at session end, where they'd all carry
    near-identical wall-clock timestamps).

    Note on the skip cascade: when ``_live_otel_session_provider``
    skips on missing credentials, every test that requests
    ``live_otel_provider`` is skipped via dependency. That is the
    intended behaviour — a developer without Langfuse credentials
    should see ``N skipped`` for the live_llm sweep, not errors.
    """
    provider = _live_otel_session_provider
    try:
        yield provider.get_tracer("samantha_server")  # type: ignore[attr-defined]
    finally:
        # M-5 fix: warn on per-test flush timeout. Without this, a
        # quietly-timed-out flush silently loses the test's spans.
        flushed = provider.force_flush(timeout_millis=_FLUSH_TIMEOUT_MS)  # type: ignore[attr-defined]
        if not flushed:
            _log.warning(
                "OTel per-test force_flush timed out after %d ms; "
                "this test's spans may not have reached Langfuse.",
                _FLUSH_TIMEOUT_MS,
            )
