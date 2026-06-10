"""AppState dataclass and build_app_state factory for the FastAPI service.

AppState owns all long-lived dependencies constructed at startup. The
build_app_state() factory performs eager validation and raises
MisconfiguredEnvironmentError for any misconfiguration — the process must
not start with a broken config.

Decision (G1): build_app_state() is synchronous. The lifespan wrapper in
app.py is async for FastAPI compatibility, but the body is sequential.

Decision (G2): WEB_CONCURRENCY > 1 is a hard-fail. See Phase 3 design doc
§ Step 1 for rationale (session-state correctness invariant).
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from opentelemetry.sdk.trace import TracerProvider

from samantha_server.api.receipt_writer import ReceiptWriter
from samantha_server.errors import MisconfiguredEnvironmentError
from samantha_server.llm.client import LLMClient
from samantha_server.observability.cached_probe import (
    CachedProbe,
    make_langfuse_probe,
    make_langfuse_stub_probe,
)
from samantha_server.observability.counters import CounterRegistry
from samantha_server.observability.drift import DriftMonitor
from samantha_server.queue.priority import PriorityEventQueue
from samantha_server.rules.loader import RuleIndex
from samantha_server.scenarios.loader import Scenario
from samantha_server.skills.loader import SkillSpec

_log = logging.getLogger(__name__)

# Type aliases for Step-1 index shapes.
# ScenarioIndex and SkillIndex are plain Mappings at this point; named here
# so the AppState field types are readable and Steps 2/5/8/9/12 can widen them.
ScenarioIndex = Mapping[str, Scenario]
SkillIndex = Mapping[str, SkillSpec]


async def _cancel_and_await_task(
    task: asyncio.Task[None],
    *,
    label: str,
) -> None:
    """Cancel a single asyncio.Task and await it, logging appropriately.

    If the task already exited with an exception before shutdown, logs the
    exception at ERROR level. If the task is still running,
    cancels it and awaits it; non-CancelledError exceptions from the drain
    are logged at WARNING level and suppressed.

    ``cancelled()`` must be checked before ``.exception()`` because the
    latter raises CancelledError on a cancelled task.
    """
    if task.done():
        if not task.cancelled():
            exc = task.exception()
            if exc is not None:
                _log.error(
                    "%s exited with exception before shutdown",
                    label,
                    exc_info=exc,
                )
    else:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            _log.exception("%s raised during shutdown drain", label)


@dataclass
class AppState:
    """All long-lived dependencies for the samantha_server FastAPI service.

    Constructed once by build_app_state() at startup. Steps 8/9/12 will
    extend this with tracer and drift_monitor fields respectively.

    Fields
    ------
    rule_index:
        Indexed RuleSpec objects, loaded from specs/ at startup.
    scenario_index:
        Mapping of scenario_id → Scenario loaded at startup.
    skill_index:
        Mapping of skill_name → SkillSpec loaded at startup.
    llm_client:
        Initialized LLM client implementation.
    receipt_writer:
        Owns the shared SQLite write connection (check_same_thread=False).
        Must satisfy ReceiptWriterProtocol.
    receipt_write_lock:
        asyncio.Lock serializing writes to receipt_writer from to_thread workers.
    counters:
        Process-lifetime monotonic counters for silent-loss observability.
    langfuse_probe:
        Cached readiness probe for the Langfuse exporter (Step 9 wires real probe).
    queue:
        Bounded priority event queue (Step 2). Constructed from cfg.QUEUE_BOUND
        by build_app_state(). Step 4 replaces the consumer body with dispatch_event.
    receipt_audit_conn:
        Read-only (query_only=1) SQLite connection for receipt read endpoints
        (Step 5.5). Structurally prevents accidental writes from the read path.
        Closed by aclose().
    consumer_task:
        Long-lived asyncio.Task that drains the queue. None until lifespan
        starts it in app.py. Cancelled and awaited in the lifespan finally clause.
    is_draining:
        Set to True by the SIGTERM handler. Step 4 consumers check this flag
        and return 503 during the drain window.
    """

    rule_index: RuleIndex
    scenario_index: ScenarioIndex
    skill_index: SkillIndex
    llm_client: LLMClient
    receipt_writer: ReceiptWriter
    receipt_write_lock: asyncio.Lock
    counters: CounterRegistry
    langfuse_probe: CachedProbe
    commit_sha: str
    queue: PriorityEventQueue
    receipt_audit_conn: sqlite3.Connection
    consumer_task: asyncio.Task[None] | None = field(default=None)
    is_draining: bool = field(default=False)
    # ``None`` means OTel was not configured — the unit-test path that
    # builds AppState directly via ``make_minimal_app_state``.
    tracer_provider: TracerProvider | None = field(default=None)
    # Drift monitor + its lifespan task. None on the unit-test path
    # that builds AppState directly via the test helpers.
    drift_monitor: DriftMonitor | None = field(default=None)
    drift_task: asyncio.Task[None] | None = field(default=None)

    async def aclose(self) -> None:
        """Release long-lived resources on shutdown.

        Cancels and awaits consumer_task, then closes the SQLite write
        connection owned by receipt_writer. The receipt-writer close runs
        unconditionally: a non-CancelledError raised by the
        consumer (Step 4 dispatch_event panic) is logged but does not
        abort the close path. SQLite leaks are worse than a noisy log.

        Shutdown drain: after cancelling the consumer task, drains any
        payloads still in the queue and resolves their futures with
        ShutdownError so in-flight POST /events requests return promptly
        rather than hanging until their timeout expires.
        """
        from samantha_server.api.events import _QueuePayload
        from samantha_server.errors import ShutdownError

        if self.consumer_task is not None:
            await _cancel_and_await_task(self.consumer_task, label="consumer task")

        # Cancel + await the drift-alarm task using the same pattern.
        if self.drift_task is not None:
            await _cancel_and_await_task(self.drift_task, label="drift task")

        # Drain any payloads still in the queue after the consumer exits.
        # These items were enqueued but never dequeued — resolve their futures
        # with ShutdownError so POST /events callers get a deterministic error
        # rather than hanging until EVENT_DISPATCH_TIMEOUT_SEC fires.
        pending_count = self.queue.qsize()
        if pending_count > 0:
            _log.info("Draining %d queued payload(s) on shutdown", pending_count)
        drained = 0
        for _ in range(pending_count):
            try:
                # Use the underlying asyncio.PriorityQueue.get_nowait() so we
                # don't block. The loop iterates at most qsize() times (live
                # items only — tombstones don't count in qsize()).
                raw_item = self.queue._queue.get_nowait()
                payload: _QueuePayload = raw_item.item
                if not payload.future.done():
                    payload.future.set_exception(ShutdownError())
                drained += 1
            except Exception:
                break
        if drained > 0:
            _log.info("Resolved %d pending future(s) with ShutdownError", drained)

        # Wrap each close in contextlib.suppress(Exception) so that a failure
        # in receipt_writer.close() never prevents receipt_audit_conn.close()
        # from running. SQLite connection leaks are worse than a noisy log
        #.
        import contextlib

        with contextlib.suppress(Exception):
            self.receipt_writer.close()
        with contextlib.suppress(Exception):
            self.receipt_audit_conn.close()

        # Step 8: Flush in-flight spans on shutdown. Provider
        # shutdown is intentionally skipped — the global TracerProvider
        # may be shared (test isolation; OTel's set_tracer_provider is
        # one-shot per process), and a Python exit handler reclaims the
        # batch processors anyway. Both the False-return and
        # raised-exception paths bump ``counters.otel_export_failures``
        # so /readyz surfaces the silent loss; the exception is logged
        # but does not abort the rest of aclose. Using try/except
        # rather than contextlib.suppress here is load-bearing — the
        # suppress form would skip the counter increment when
        # force_flush raises (the increment would never run).
        if self.tracer_provider is not None:
            try:
                if not bool(self.tracer_provider.force_flush(30_000)):
                    self.counters.otel_export_failures.increment()
            except Exception as exc:
                self.counters.otel_export_failures.increment()
                _log.warning(
                    "OTel provider force_flush raised during aclose: %s: %s",
                    type(exc).__name__,
                    exc,
                )


def build_app_state(
    *,
    _llm_client_override: LLMClient | None = None,
) -> AppState:
    """Construct every long-lived dependency; raise MisconfiguredEnvironmentError on misconfig.

    This is the startup-fatal validation path. Any failure here produces a
    non-zero process exit before /readyz ever serves a request.

    Validation performed:
    - WEB_CONCURRENCY must be 1 (G2: single-worker v0 invariant).
    - QUEUE_BOUND must be > 0 (already enforced at config import).
    - Phase-2 secrets (RECEIPT_SIGNING_KEY, PHI_HASH_SALT) validated by config.py.

    Parameters
    ----------
    _llm_client_override:
        Internal test-only override for the LLM client. Not part of the public
        API; tests monkeypatch this to avoid loading MLX model weights.
    """
    # Lazy import to avoid triggering config sentinel at collection time.
    import samantha_server.config as cfg

    # Step 9: Seed OTEL_EXPORTER_OTLP_HEADERS *before* configure_otel
    # constructs the OTLP exporter. The exporter reads the env var at
    # construction time, so the ordering is load-bearing.
    _seed_langfuse_otlp_headers()

    # G2: hard-fail if operator accidentally sets WEB_CONCURRENCY > 1.
    # No escape hatch ships in v0; multi-worker support is the only legitimate override.
    if cfg.WEB_CONCURRENCY > 1:
        raise MisconfiguredEnvironmentError(
            f"WEB_CONCURRENCY={cfg.WEB_CONCURRENCY} is not supported in v0. "
            "samantha_server v0 must run as a single Uvicorn worker "
            "(--workers 1 / WEB_CONCURRENCY=1) for session-state correctness. "
            "See the multi-worker migration plan."
        )

    # Load engine indexes.
    rule_index = _load_rule_index()
    scenario_index = _load_scenario_index()
    skill_index = _load_skill_index()

    # Initialize the LLM client.
    llm_client = _llm_client_override if _llm_client_override is not None else _build_llm_client()

    # Open the receipt store. Uses a shared connection with check_same_thread=False
    # so to_thread workers can write without opening new connections.
    db_path = Path(cfg.RECEIPTS_DB_PATH)
    receipt_writer = ReceiptWriter.open(db_path)

    # Open a separate read-only (query_only=1) connection for the receipt read
    # endpoints (Step 5.5). Structural separation prevents accidental writes
    # from the read path.
    from samantha_server.receipts.store import _open_audit_conn

    receipt_audit_conn = _open_audit_conn(db_path)

    # Build the Langfuse probe. When LANGFUSE_ENABLED=false (the
    # test/CI default) the stub probe reports ``not_configured`` —
    # /readyz surfaces this as degraded JSON without making an outbound
    # call. When enabled, the real probe hits
    # ``{LANGFUSE_BASE_URL}/api/public/health`` with 5s TTL caching so a
    # /readyz flood doesn't amplify outbound traffic.
    if cfg.LANGFUSE_ENABLED:
        langfuse_probe = make_langfuse_probe(
            base_url=cfg.LANGFUSE_BASE_URL,
            ttl_sec=float(cfg.READYZ_LANGFUSE_PROBE_TTL_SEC),
            timeout_sec=cfg.READYZ_LANGFUSE_PROBE_TIMEOUT_SEC,
        )
    else:
        langfuse_probe = make_langfuse_stub_probe(ttl_sec=float(cfg.READYZ_LANGFUSE_PROBE_TTL_SEC))

    # Compute commit SHA once at startup; /version reads from AppState
    # rather than forking a subprocess per request.
    from samantha_server.api.health import get_commit_sha

    # Build the priority event queue (Step 2). consumer_task is left None
    # here; app.py's lifespan starts the task after build_app_state returns.
    queue = PriorityEventQueue(maxsize=cfg.QUEUE_BOUND)

    counters = CounterRegistry()

    # Build the drift monitor. It always exists (the rolling deque
    # keeps recording even when ``DRIFT_ALARM_WEBHOOK_URL`` is unset);
    # the webhook itself is gated on the URL inside ``check_and_fire``.
    drift_monitor = DriftMonitor(
        webhook_url=cfg.DRIFT_ALARM_WEBHOOK_URL,
        threshold=cfg.DRIFT_ALARM_THRESHOLD,
        window_sec=cfg.DRIFT_ALARM_WINDOW_SEC,
        check_interval_sec=cfg.DRIFT_ALARM_CHECK_INTERVAL_SEC,
        deque_maxlen=cfg.DRIFT_DEQUE_MAXLEN,
        counters=counters,
    )

    return AppState(
        rule_index=rule_index,
        scenario_index=scenario_index,
        skill_index=skill_index,
        llm_client=llm_client,
        receipt_writer=receipt_writer,
        receipt_write_lock=asyncio.Lock(),
        counters=counters,
        langfuse_probe=langfuse_probe,
        commit_sha=get_commit_sha(),
        queue=queue,
        receipt_audit_conn=receipt_audit_conn,
        drift_monitor=drift_monitor,
    )


# ---------------------------------------------------------------------------
# Private loader helpers
# ---------------------------------------------------------------------------


def _load_rule_index() -> RuleIndex:
    """Load and index rules from the default specs directory."""
    from samantha_server.rules.loader import RuleIndex, load_rule_specs

    specs_dir = Path(__file__).resolve().parents[1] / "rules" / "specs"
    if not specs_dir.exists():
        # No specs directory yet — return empty index.
        return RuleIndex([])
    return RuleIndex(load_rule_specs(specs_dir))


def _load_scenario_index() -> ScenarioIndex:
    """Load and index scenarios from the configured scenarios directory.

    Path comes from the ``SCENARIOS_DIR`` env var (read here rather than
    via ``config.py`` to keep the orchestrator boot path lazy; M2
    notes path-traversal validation as a follow-up). Defaults to the
    in-repo ``samantha_server/scenarios/`` package directory, which
    carries no built-in corpus.

    Returns an empty index when:
      - the directory does not exist, OR
      - the directory exists but is genuinely empty.

    Any other failure (malformed files, schema-version mismatch, future
    ``ScenarioCorpusError`` variants) propagates and crashes the lifespan,
    per the plan's "build_app_state raises loudly on misconfig" invariant
.
    """
    import os

    from samantha_server.scenarios.index import build_scenario_index
    from samantha_server.scenarios.loader import load_scenarios

    default_dir = Path(__file__).resolve().parents[2] / "scenarios"
    scenarios_dir = Path(os.environ.get("SCENARIOS_DIR", str(default_dir)))

    # Minimal path-traversal denylist. The bare-env-var read
    # bypasses config.py validators; a hostile or accidental
    # ``SCENARIOS_DIR=/etc`` would otherwise walk a system directory.
    # This isn't a full allowlist (POC concession — the comprehensive
    # version would route through an `_optional_path_under` helper);
    # rejecting the obvious system roots is enough to defang the attack.
    resolved = scenarios_dir.resolve()
    for forbidden in (Path("/etc"), Path("/proc"), Path("/sys"), Path("/dev")):
        if resolved.is_relative_to(forbidden):
            raise MisconfiguredEnvironmentError(
                f"SCENARIOS_DIR resolves under a system directory ({forbidden}); "
                "refusing to load. Point SCENARIOS_DIR at a corpus directory "
                "under your home or the repo. (Raw value omitted per G18.)"
            )

    if not scenarios_dir.exists():
        return {}

    # Empty-directory case: no files to load. Test explicitly rather than
    # catching a non-empty-malformed_files ScenarioCorpusError, which
    # silently absorbed schema-version errors.
    if not any(scenarios_dir.iterdir()):
        return {}

    scenarios = load_scenarios(scenarios_dir)
    return build_scenario_index(scenarios)


def _load_skill_index() -> SkillIndex:
    """Load and index skills from the default specs directory.

    discover() returns a dict[skill_name, SkillSpec] keyed by skill name.
    """
    from samantha_server.skills.loader import discover

    return discover()


def _build_llm_client() -> LLMClient:
    """Build the LLM client from config.

    PHI invariant: the literal clinical-query text
    travels in the LLM prompt unredacted under the local-LLM +
    self-hosted-Langfuse topology. Every provider listed below MUST be
    loopback-constrained (in-process MLX or a localhost-only server such
    as oMLX, with config-level URL validation in samantha_server.config).
    Adding a cloud provider (OpenAI, Anthropic, Bedrock, etc.) to this
    acceptlist would silently transit query text in cleartext over a
    third-party boundary and is NOT permitted under the current PHI policy.
    """
    import samantha_server.config as cfg
    from samantha_server.llm.mlx_client import MLXClient
    from samantha_server.llm.omlx_client import OMLXClient

    if cfg.LLM_PROVIDER == "mlx":
        # MLXClient accepts model_path only; model_name is stored in config for audit logs.
        return MLXClient(model_path=cfg.LLM_MODEL_PATH)

    if cfg.LLM_PROVIDER == "omlx":
        # OMLXClient probes the loopback oMLX server at construction.
        return OMLXClient()

    raise MisconfiguredEnvironmentError(
        f"Unknown LLM_PROVIDER={cfg.LLM_PROVIDER!r}. Supported: 'mlx', 'omlx'."
    )


def _seed_langfuse_otlp_headers() -> None:
    """When LANGFUSE_ENABLED=true, write Basic auth into OTEL_EXPORTER_OTLP_HEADERS.

    The OTLP HTTP exporter reads ``OTEL_EXPORTER_OTLP_HEADERS`` at
    construction time; this helper must run before
    ``configure_otel`` so the constructed exporter sees the
    Authorization header.

    G18: the error message names the missing variable but does not
    echo any partial-key value.

    Merging: ``OTEL_EXPORTER_OTLP_HEADERS`` is comma-separated
    ``key=value`` pairs (per the OTel spec). If the operator already
    set non-Authorization headers (e.g., a tracing tag), we preserve
    them and only inject / replace the ``Authorization`` entry. The
    previous overwrite-everything form would silently clobber
    operator-set headers.
    """
    import samantha_server.config as cfg

    if not cfg.LANGFUSE_ENABLED:
        return

    if not cfg.LANGFUSE_PUBLIC_KEY:
        raise MisconfiguredEnvironmentError(
            "LANGFUSE_ENABLED=true requires a non-empty LANGFUSE_PUBLIC_KEY."
        )
    if not cfg.LANGFUSE_SECRET_KEY:
        raise MisconfiguredEnvironmentError(
            "LANGFUSE_ENABLED=true requires a non-empty LANGFUSE_SECRET_KEY."
        )

    creds = f"{cfg.LANGFUSE_PUBLIC_KEY}:{cfg.LANGFUSE_SECRET_KEY}"
    encoded = base64.b64encode(creds.encode("utf-8")).decode("ascii")
    auth_header = f"Authorization=Basic {encoded}"

    existing = os.environ.get("OTEL_EXPORTER_OTLP_HEADERS", "")
    preserved = [
        pair.strip()
        for pair in existing.split(",")
        if pair.strip() and not pair.strip().lower().startswith("authorization=")
    ]
    os.environ["OTEL_EXPORTER_OTLP_HEADERS"] = ",".join([*preserved, auth_header])


__all__ = ["AppState", "ScenarioIndex", "SkillIndex", "build_app_state"]
