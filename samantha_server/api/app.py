"""FastAPI application factory for samantha_server.

create_app() builds the production FastAPI instance with:
- Lifespan-managed engine initialization (loads indexes, opens SQLite).
  The lifespan's ``finally`` clause sets ``is_draining=True`` before
  releasing resources — this is the production drain trigger. (
  C3 fix-review: a separate SIGTERM signal handler doesn't survive
  Uvicorn's own handler installation; lifespan-finally is the path
  Uvicorn always invokes on graceful shutdown.)
- Request ID middleware (assigns UUID4 per request, echoes in response header).
- JSON-formatted access logger (PHI-safe: drops query strings + bodies).
- Uncaught exception handler (500 with error=internal + request_id, no traceback).
- /healthz, /readyz, /version diagnostic endpoints.
- Swagger UI, ReDoc, **and** /openapi.json all disabled (H5
  fix-review applied the plan's spike-fallback floor: openapi_url=None
  until Step 5 ships the health:read RBAC gate). A lab dev who
  wants the schema can run create_app().openapi() against a checkout.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, MutableMapping
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from samantha_server.api.health import register_health_routes

if TYPE_CHECKING:
    from samantha_server.api.lifespan import AppState

_log = logging.getLogger(__name__)

# ASGI type aliases for middleware callables.
_ASGIScope = MutableMapping[str, Any]
_ASGIReceive = Callable[[], Awaitable[MutableMapping[str, Any]]]
_ASGISend = Callable[[MutableMapping[str, Any]], Awaitable[None]]
_ASGIApp = Callable[[_ASGIScope, _ASGIReceive, _ASGISend], Awaitable[None]]


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


async def _consume(state: AppState) -> None:
    """Priority-queue consumer task.

    Dequeues _QueuePayload items, calls dispatch_event, and resolves the
    payload's future with (SignedReceipt, EngineDecision, EventDispatchContext).
    On dispatch failure, sets the exception on the future and continues.

    Decision G3: no task.cancel() calls here. STAT preemption is
    queue-ordering, not mid-evaluate cancellation.

    One bad event must not poison the consumer loop — any exception from
    dispatch_event is forwarded to the payload's future and the loop
    continues.

    Shutdown contract: asyncio.CancelledError is handled explicitly. Any
    in-flight payload's future is resolved with ShutdownError before
    re-raising so the awaiting request handler gets a deterministic error
    rather than hanging indefinitely.
    """
    import contextvars
    import time

    import pydantic
    from opentelemetry import context as otel_context
    from opentelemetry.context import Context

    from samantha_server.api.events import _QueuePayload
    from samantha_server.api.routing import dispatch_event
    from samantha_server.errors import ShutdownError
    from samantha_server.models.context import SpecimenContext

    while True:
        # Track the current in-flight payload so the CancelledError handler
        # can resolve its future on shutdown without a hang.
        current_payload: _QueuePayload | None = None
        # ``otel_context.attach`` returns a token that ``detach``
        # consumes; without the detach the context leaks into the next
        # loop iteration and unrelated events become children of a
        # stale span (Step 8).
        otel_token: contextvars.Token[Context] | None = None
        try:
            queue_item = await state.queue.get()
            dequeue_ns = time.monotonic_ns()
            queue_wait_us = (dequeue_ns - queue_item.enqueue_monotonic_ns) // 1000

            current_payload = queue_item.item

            if queue_item.otel_context is not None:
                otel_token = otel_context.attach(queue_item.otel_context)

            # Inject user_role from the payload into event_data so
            # handle_clinical_query can extract it. Copy-on-write: do not
            # mutate the immutable Mapping from the queue item. Hard key
            # access (no .get fallbacks) — ctx_dict is always a
            # SpecimenContext.model_dump() shape; missing keys signal schema
            # drift and must fail loudly rather than build a synthetic dict.
            # post-injection size delta is ≤30 bytes; M-13 64KB ceiling is unaffected.
            ctx_dict = current_payload.ctx_dict
            if current_payload.user_role is not None:
                ctx_dict = dict(ctx_dict)
                ctx_dict["event"] = dict(ctx_dict["event"])
                ctx_dict["event"]["event_data"] = dict(ctx_dict["event"]["event_data"]) | {
                    "user_role": current_payload.user_role,
                }
            ctx = SpecimenContext.model_validate(ctx_dict)
            decision, dispatch_ctx, receipt = await dispatch_event(
                ctx,
                session_id=current_payload.session_id,
                priority=current_payload.priority,
                queue_wait_us=max(0, queue_wait_us),
                receipt_writer=state.receipt_writer,
                write_lock=state.receipt_write_lock,
                counters=state.counters,
                llm_client=state.llm_client,
                scenarios_index=state.scenario_index,
                skills_index=state.skill_index,
                rule_index=state.rule_index,
                prompt_timestamp=current_payload.prompt_timestamp,
            )
            # INVARIANT: dispatch_event() has already called emit_receipt() (the
            # receipt is persisted) BEFORE we resolve the future here. The replay
            # harness depends on this ordering — it reads the persisted receipt by
            # id immediately after its POST returns. Do NOT reorder set_result()
            # ahead of receipt persistence: the harness would then read None and
            # silently fall back to empty traces, disabling the content gate.
            if not current_payload.future.done():
                current_payload.future.set_result((receipt, decision, dispatch_ctx))
            # Record routing_path for the drift-alarm rolling buffer.
            # Sourced from EventDispatchContext (orchestrator-owned),
            # never EngineDecision. Wrapped in its own narrow try/except
            # so an observability bug never converts a successful
            # dispatch into a set_exception() on the caller's future
            # (the future is already resolved above).
            if state.drift_monitor is not None:
                try:
                    state.drift_monitor.record(
                        routing_path=dispatch_ctx.routing_path,
                        monotonic_ns=time.monotonic_ns(),
                    )
                except Exception:
                    _log.exception("drift_monitor.record failed; dispatch already resolved")
        except asyncio.CancelledError:
            # Shutdown path: resolve any in-flight payload's future so the
            # request handler receives ShutdownError instead of hanging.
            if current_payload is not None and not current_payload.future.done():
                current_payload.future.set_exception(ShutdownError())
            raise
        except pydantic.ValidationError as exc:
            # Log PHI-safely: Pydantic ValidationError tracebacks embed
            # input_value=... repr which can contain PHI. Log only error
            # count and locations — no exc_info, no str(exc).
            if current_payload is not None:
                _log.warning(
                    "Consumer ctx validation failed session_id=%s: %d error(s) at %s",
                    current_payload.session_id,
                    exc.error_count(),
                    [str(e["loc"]) for e in exc.errors()],
                )
                if not current_payload.future.done():
                    current_payload.future.set_exception(exc)
        except Exception as exc:
            if current_payload is not None:
                _log.warning(
                    "Consumer dispatch failed session_id=%s: %s",
                    current_payload.session_id,
                    exc,
                    exc_info=True,
                )
                if not current_payload.future.done():
                    current_payload.future.set_exception(exc)
        finally:
            # The token is opaque — only the original attach knows how
            # to undo itself, so detach must run in finally regardless
            # of which arm fired.
            if otel_token is not None:
                otel_context.detach(otel_token)


async def _drift_loop(state: AppState) -> None:
    """Periodic drift-alarm task.

    Wakes every ``check_interval_sec`` and asks the monitor to compute
    the rolling-window LLM-call rate. If over threshold, the monitor
    fires the webhook (gated on ``webhook_url`` being set; failures
    increment ``counters.drift_webhook_failures`` per G21 — no retry).

    Cancelled cleanly on lifespan shutdown via ``aclose``. Any
    non-cancellation exception is logged, ``drift_loop_errors`` is
    incremented, and the loop continues — a bug in ``check_and_fire``
    must not poison the lifespan, but the counter ensures a sustained
    crash loop is visible on ``/readyz`` rather than silently disabling
    the alarm.
    """
    assert state.drift_monitor is not None  # guarded by caller

    interval_sec = state.drift_monitor.check_interval_sec
    while True:
        try:
            await asyncio.sleep(interval_sec)
            await state.drift_monitor.check_and_fire(now_ns=time.monotonic_ns())
        except asyncio.CancelledError:
            raise
        except Exception:
            state.counters.drift_loop_errors.increment()
            _log.exception("drift monitor iteration failed; loop continues")


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Async lifespan context: builds AppState synchronously, then serves.

    Decision (G1): build_app_state() is synchronous. The async wrapper is
    required by FastAPI; the body is sequential for fail-loud discipline.

    Drain trigger: on shutdown (Uvicorn's SIGTERM handling
    invokes lifespan.shutdown), the ``finally`` clause flips
    ``is_draining=True`` *before* releasing resources, so any in-flight
    or last-arriving /readyz call sees 503 immediately.

    Consumer task (Step 2): a long-lived asyncio.Task draining the
    priority queue. Started here after build_app_state() returns (so it
    runs in the same event loop as the FastAPI server). state.aclose()
    cancels and awaits the task on shutdown.
    """
    from samantha_server.api.lifespan import build_app_state
    from samantha_server.observability.otel import configure_otel

    state = build_app_state()

    # The provider is stored on AppState so ``aclose`` can flush it.
    # ``configure_otel`` reuses an already-installed SDK provider (the
    # test-conftest pattern); production always installs a fresh one.
    _tracer, provider, _exporter = configure_otel(state.counters)
    state.tracer_provider = provider

    app.state.engine = state

    # Start the consumer task. The type annotation avoids a circular import
    # at module scope — AppState is only needed inside the coroutine.
    state.consumer_task = asyncio.create_task(_consume(state), name="priority-queue-consumer")

    # Drift-alarm task. Always started when a monitor is present;
    # webhook firing is gated inside check_and_fire on webhook_url=None
    # so the rolling buffer's rate is always available to /readyz.
    if state.drift_monitor is not None:
        state.drift_task = asyncio.create_task(_drift_loop(state), name="drift-alarm-monitor")

    try:
        yield
    finally:
        state.is_draining = True
        # state.aclose() handles consumer-task cancel + receipt_writer
        # close. Calling it here (and not duplicating the cancel logic)
        # makes aclose() the single source of truth for shutdown order
        # and ensures M3's broadened-suppress contract applies uniformly.
        await state.aclose()


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class RequestIDMiddleware:
    """ASGI middleware that assigns a UUID4 request_id to every inbound request.

    Ignores any inbound X-Request-ID header (prevents ID spoofing).
    Echoes the assigned value as X-Request-ID on the response.
    Stores the id in request.state.request_id for handlers to access.
    """

    def __init__(self, app: _ASGIApp) -> None:
        self.app = app

    async def __call__(
        self,
        scope: _ASGIScope,
        receive: _ASGIReceive,
        send: _ASGISend,
    ) -> None:
        if scope["type"] == "http":
            scope.setdefault("state", {})
            request_id = str(uuid.uuid4())
            # Store on scope so Request can expose it via request.state
            scope["state"]["request_id"] = request_id

            async def send_with_header(message: MutableMapping[str, Any]) -> None:
                if message["type"] == "http.response.start":
                    headers = list(message.get("headers", []))
                    headers.append((b"x-request-id", request_id.encode()))
                    message = {**message, "headers": headers}
                await send(message)

            await self.app(scope, receive, send_with_header)
        else:
            await self.app(scope, receive, send)


# ---------------------------------------------------------------------------
# Access logging
# ---------------------------------------------------------------------------


class _AccessLogger:
    """JSON-formatted access log. PHI-safe: no query strings, no bodies."""

    def __init__(self, app: _ASGIApp) -> None:
        self.app = app

    async def __call__(
        self,
        scope: _ASGIScope,
        receive: _ASGIReceive,
        send: _ASGISend,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = time.monotonic()
        method = scope.get("method", "")
        # Path only — no query string (PHI boundary)
        path = scope.get("path", "")
        request_id = scope.get("state", {}).get("request_id", "")

        status_code = [200]

        async def send_capture(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.start":
                status_code[0] = message.get("status", 200)
            await send(message)

        await self.app(scope, receive, send_capture)

        latency_ms = round((time.monotonic() - start) * 1000, 1)
        _log.info(
            json.dumps(
                {
                    "ts": time.time(),
                    "method": method,
                    "path": path,
                    "status": status_code[0],
                    "latency_ms": latency_ms,
                    "request_id": request_id,
                }
            )
        )


# ---------------------------------------------------------------------------
# Body-cap middleware
# ---------------------------------------------------------------------------


class _BodyCapMiddleware:
    """ASGI middleware that enforces a maximum request body size for POST requests.

    Inspects Content-Length and Transfer-Encoding headers before any route
    handler or pydantic parser sees the request. Short-circuits with:
      - 411 if Transfer-Encoding: chunked is present (no Content-Length).
      - 413 if Content-Length > max_request_body_bytes.

    The 413 / 411 responses carry X-Request-ID (from RequestIDMiddleware's
    scope state) and a structured JSON body. Neither response echoes any
    portion of the request body — PHI boundary enforced structurally.

    Design reference: Step 4 / G22 (phase-3-implementation.md).
    """

    def __init__(self, app: _ASGIApp, *, max_request_body_bytes: int) -> None:
        self.app = app
        self._max = max_request_body_bytes

    async def __call__(
        self,
        scope: _ASGIScope,
        receive: _ASGIReceive,
        send: _ASGISend,
    ) -> None:
        if scope["type"] != "http" or scope.get("method", "") != "POST":
            await self.app(scope, receive, send)
            return

        request_id = scope.get("state", {}).get("request_id", str(uuid.uuid4()))

        headers: dict[bytes, bytes] = {k.lower(): v for k, v in scope.get("headers", [])}

        # 411 — Transfer-Encoding: chunked (rejected unconditionally).
        # POC concession: streaming-with-counting is the production-hardening
        # path; no v0 client legitimately needs chunked encoding.
        if b"transfer-encoding" in headers:
            te_value = headers[b"transfer-encoding"].decode("latin-1").lower()
            if "chunked" in te_value:
                await _send_json_response(
                    send,
                    status=411,
                    body={"error": "length_required", "request_id": request_id},
                    extra_headers=[(b"x-request-id", request_id.encode())],
                )
                return

        # Content-Length validation.
        if b"content-length" in headers:
            try:
                content_length = int(headers[b"content-length"])
            except ValueError:
                content_length = 0
            # 400 — negative Content-Length is invalid per RFC 7230.
            if content_length < 0:
                await _send_json_response(
                    send,
                    status=400,
                    body={"error": "invalid_content_length", "request_id": request_id},
                    extra_headers=[(b"x-request-id", request_id.encode())],
                )
                return
            # 413 — Content-Length exceeds cap.
            if content_length > self._max:
                await _send_json_response(
                    send,
                    status=413,
                    body={
                        "error": "payload_too_large",
                        "request_id": request_id,
                        "max_request_body_bytes": self._max,
                    },
                    extra_headers=[(b"x-request-id", request_id.encode())],
                )
                return

        await self.app(scope, receive, send)


async def _send_json_response(
    send: _ASGISend,
    *,
    status: int,
    body: dict[str, object],
    extra_headers: list[tuple[bytes, bytes]] | None = None,
) -> None:
    """Send a minimal JSON HTTP response through the ASGI send callable."""
    body_bytes = json.dumps(body).encode()
    headers: list[tuple[bytes, bytes]] = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body_bytes)).encode()),
    ]
    if extra_headers:
        headers.extend(extra_headers)
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body_bytes, "more_body": False})


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    """Build the samantha_server FastAPI application.

    Returns a fully-configured FastAPI instance ready for Uvicorn. In tests,
    the lifespan context is bypassed by setting app.state.engine directly.
    """
    import samantha_server

    version = getattr(samantha_server, "__version__", "0.1.0")

    app = FastAPI(
        lifespan=lifespan,
        title="samantha_server",
        version=version,
        docs_url=None,
        redoc_url=None,
        # openapi_url=None is the plan's
        # spike-fallback floor. Step 5 will set this back to
        # "/openapi.json" once the health:read RBAC gate exists.
        openapi_url=None,
    )
    app.debug = False

    # Middleware ordering.
    # Starlette's add_middleware is LIFO: the *last* call becomes the
    # *outermost* wrapper. Outside-in order desired:
    #   RequestIDMiddleware → _BodyCapMiddleware → _AccessLogger → app
    # Registration order (inner → outer):
    #   1. _AccessLogger (innermost)
    #   2. _BodyCapMiddleware (reads request_id from scope set by RequestIDMiddleware)
    #   3. RequestIDMiddleware (outermost; stamps request_id before inner stack)
    import samantha_server.config as _cfg

    app.add_middleware(_AccessLogger)
    app.add_middleware(_BodyCapMiddleware, max_request_body_bytes=_cfg.MAX_REQUEST_BODY_BYTES)
    app.add_middleware(RequestIDMiddleware)

    # Uncaught-exception handler.
    @app.exception_handler(Exception)
    async def _uncaught_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        _log.exception("Uncaught exception request_id=%s", request_id)
        return JSONResponse(
            {"error": "internal", "request_id": request_id},
            status_code=500,
        )

    # Register RBAC exception handlers (must be before events + health routes).
    # 401/403 from require_capability() are rendered via these
    # handlers to produce the exact {"error": "unauthorized"} / {"error": "forbidden"}
    # bodies — not FastAPI's default {"detail": ...} wrapper.
    from samantha_server.api.rbac import register_rbac_exception_handlers

    register_rbac_exception_handlers(app)

    # Register health routes.
    register_health_routes(app)

    # Register events route + PHI-safe 422 handler.
    from samantha_server.api.events import register_events_routes

    register_events_routes(app)

    # Register receipt read endpoints (Step 5.5).
    from samantha_server.api.receipts import register_receipts_routes

    register_receipts_routes(app)

    return app


__all__ = ["create_app"]
