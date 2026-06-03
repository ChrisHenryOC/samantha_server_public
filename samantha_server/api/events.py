"""POST /events endpoint — Phase 3 Step 4.

Accepts an event submission, enqueues it on the priority queue, and
returns a signed receipt + serialized EngineDecision.

Request lifecycle:
1. FastAPI parses EventRequest (422 on schema failure, PHI scrubbed).
2. _BodyCapMiddleware enforces Content-Length cap (upstream).
3. Build _QueuePayload with an asyncio.Future.
4. enqueue_or_reject — 503 if queue full.
5. Await the future with a per-request timeout (504 on timeout).
6. Return EventResponse with receipt_id, decision, trace_url=null.

PHI boundary: RequestValidationError handler strips the body from
error.detail before returning 422.

Receipt contract: the consumer's dispatch_event emits exactly one
receipt per event. Back-pressure rejection (step 4 above) is a
documented no-receipt path.
"""

from __future__ import annotations

import asyncio
import datetime
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any

import pydantic
from fastapi import APIRouter, Depends, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

if TYPE_CHECKING:
    from fastapi import FastAPI

from samantha_server.api.backpressure import (
    BackpressureRejection,
    enqueue_or_reject,
    to_503_response,
)
from samantha_server.api.event_context import EventDispatchContext
from samantha_server.api.rbac import require_capability
from samantha_server.engine.decision import EngineDecision
from samantha_server.models.context import SpecimenContext
from samantha_server.models.roles import UserRole
from samantha_server.queue.priority import EventPriority
from samantha_server.receipts.signing import SignedReceipt

router = APIRouter()

# Module-level dispatch timeout override for tests.
# None = read from cfg.EVENT_DISPATCH_TIMEOUT_SEC at request time (lazy).
# Tests patch this directly to a small float to speed up timeout assertions.
_EVENT_DISPATCH_TIMEOUT_SEC: float | None = None


def _get_dispatch_timeout() -> float:
    """Return the effective dispatch timeout in seconds.

    Reads cfg.EVENT_DISPATCH_TIMEOUT_SEC lazily so this module can be
    imported at test-collection time without triggering the config sentinel.
    Tests can override by setting _EVENT_DISPATCH_TIMEOUT_SEC at module scope.
    """
    if _EVENT_DISPATCH_TIMEOUT_SEC is not None:
        return _EVENT_DISPATCH_TIMEOUT_SEC
    import samantha_server.config as cfg

    return float(cfg.EVENT_DISPATCH_TIMEOUT_SEC)


def register_events_routes(app: FastAPI) -> None:
    """Register the events router and PHI-safe 422 exception handler on *app*.

    Called by create_app() and test factories. Registers:
    - POST /events route handler (requires events:submit RBAC capability).
    - RequestValidationError handler that strips body content from 422 detail.

    Side effect: this function installs an app-wide RequestValidationError
    exception handler on the FastAPI instance. If the host app already has
    one, this call overrides it silently (FastAPI's add_exception_handler
    replaces the existing registration for the same exception type). Callers
    that need a custom 422 handler must register it *after* this call.

    GH-119 Step 5: RBAC exception handlers must be registered before calling
    this function (via register_rbac_exception_handlers) for 401/403 responses
    to be rendered correctly.
    """

    app.include_router(router)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


def _parse_event_priority(v: Any) -> EventPriority:
    """Pydantic BeforeValidator: coerce string → EventPriority enum member.

    PHI boundary: error messages must NOT echo the submitted value — the
    priority string itself is not PHI, but the msg field is forwarded
    verbatim by the 422 handler. Use a controlled-vocabulary message.
    """
    if isinstance(v, EventPriority):
        return v
    if isinstance(v, str):
        try:
            return EventPriority[v]
        except KeyError:
            # Do NOT include v in the message — msg is forwarded verbatim in 422.
            raise ValueError("priority must be one of: STAT, ROUTINE, BACKGROUND") from None
    raise ValueError(f"priority must be a string, got {type(v).__name__!r}")


def _validate_ctx(v: Any) -> Any:
    """Pydantic BeforeValidator: eagerly validate ctx as a SpecimenContext.

    Moves SpecimenContext validation to the request boundary so schema errors
    return 422 (PHI-scrubbed) rather than 500 from inside the consumer.
    """
    if isinstance(v, dict):
        try:
            SpecimenContext.model_validate(v)
        except pydantic.ValidationError as exc:
            # Re-raise as a plain ValueError so Pydantic captures it as a
            # field-level validation error (converted to 422 by the handler).
            raise ValueError(
                f"ctx failed SpecimenContext validation: {exc.error_count()} error(s)"
            ) from None
    return v


class EventRequest(BaseModel):
    """POST /events request body."""

    ctx: Annotated[dict[str, Any], pydantic.BeforeValidator(_validate_ctx)]
    # min_length=1 prevents empty-string token-binding; pattern rejects '|'
    # which is the HMAC separator in _make_token (dispatcher.py) — a pipe in
    # session_id could enable separator-confusion attacks if the verifier ever
    # re-parses the concatenated input.
    session_id: Annotated[str, pydantic.Field(min_length=1, pattern=r"^[^|]+$")]
    priority: Annotated[EventPriority, pydantic.BeforeValidator(_parse_event_priority)]
    # GH-227: user role of the requesting user. Not PHI; Pydantic validates
    # against the Literal type and returns 422 on invalid values.
    user_role: UserRole | None = None
    # GH-324 Phase B: replay callers send this field (string or null) to
    # override the now() default. Production callers omit the field entirely,
    # so model_fields_set distinguishes "not provided" from "provided as null".
    prompt_timestamp: str | None = None


class EventResponse(BaseModel):
    """POST /events 200 response body."""

    receipt_id: str
    decision: dict[str, Any]
    trace_url: str | None = None


# ---------------------------------------------------------------------------
# Queue payload dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _QueuePayload:
    """Bundle enqueued with the priority queue item.

    The consumer dequeues this, calls dispatch_event, and resolves the
    future with the result or sets an exception on failure.

    Immutability contract: frozen=True + Mapping views prevent producer/
    consumer from mutating shared dict state after enqueue. The producer
    (post_events) owns ctx_dict construction; ownership transfers to the
    consumer (dispatch_event) on dequeue. Neither party should mutate
    ctx_dict after construction — frozen + Mapping enforces this at the
    type level.

    PHI boundary: this dataclass must NOT be stored on
    BackpressureRejection — the rejection exception carries only queue
    metadata (see backpressure.py).
    """

    future: asyncio.Future[tuple[SignedReceipt, EngineDecision, EventDispatchContext]]
    ctx_dict: Mapping[str, Any]
    session_id: str
    priority: EventPriority
    # GH-233: ISO-8601 "now" anchor captured at request entry. The consumer
    # forwards this into dispatch_event so clinical_query prompts emit a
    # <prompt_timestamp> block on the live path (mirrors the replay harness).
    prompt_timestamp: str | None = None
    # GH-227: user role of the requesting user. Not PHI; passed through
    # verbatim from EventRequest.user_role. None when not supplied.
    user_role: UserRole | None = None


# ---------------------------------------------------------------------------
# Exception handler: scrub PHI from 422 bodies
# ---------------------------------------------------------------------------


async def _validation_error_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """Return 422 with error locations but no body/input content.

    FastAPI's default 422 handler echoes the submitted request body in
    `detail[*].input`. This handler strips those fields to enforce the
    PHI boundary.

    Constraint for Pydantic validator authors: validator error messages
    (the `msg` field passed through here) must NOT include the
    repr() of any input value — msg is forwarded verbatim. Use
    controlled-vocabulary messages like "invalid format" rather than
    f"got {value!r}".
    """
    scrubbed_errors = []
    for error in exc.errors():
        scrubbed_errors.append(
            {
                "type": error.get("type", ""),
                "loc": error.get("loc", ()),
                "msg": error.get("msg", ""),
            }
        )
    return JSONResponse(
        status_code=422,
        content={"detail": scrubbed_errors},
    )


# ---------------------------------------------------------------------------
# Route handler
# ---------------------------------------------------------------------------


@router.post("/events", response_model=EventResponse)
async def post_events(
    request_body: EventRequest,
    request: Request,
    _rbac: None = Depends(require_capability("events:submit")),
) -> JSONResponse:
    """Submit an event for dispatch.

    Validates the request, enqueues a _QueuePayload, waits for the
    consumer to call dispatch_event, and returns the result.

    503 if queue is full (back-pressure, no receipt emitted).
    422 if request body is invalid (PHI scrubbed from error detail).
    504 if the consumer does not resolve the dispatch future within
        EVENT_DISPATCH_TIMEOUT_SEC seconds.

    Step 8 (GH-123): wraps the dispatch in a ``samantha_server.event``
    parent span. The span context is snapshotted onto the queue item
    so the consumer task (running in a different asyncio task) attaches
    it before invoking the LLM handlers — without that, gen_ai child
    spans become orphan traces.
    """
    from opentelemetry import context as otel_context
    from opentelemetry import trace

    from samantha_server.observability.otel import (
        ENVIRONMENT_PRODUCTION,
        PARENT_SPAN_NAME,
        stamp_trace_attributes,
    )
    from samantha_server.observability.trace_context import TraceContext

    state = request.app.state.engine
    request_id = getattr(request.state, "request_id", None)

    # Priority was validated and coerced by the Pydantic BeforeValidator.
    priority: EventPriority = request_body.priority

    tracer = trace.get_tracer("samantha_server")

    with tracer.start_as_current_span(PARENT_SPAN_NAME) as span:
        # Pre-dispatch: stamp engine-internal samantha.* keys and environment.
        # GH-183: canonical dashboard surface is langfuse.trace.metadata.*
        # via stamp_trace_attributes(TraceContext(environment=ENVIRONMENT_PRODUCTION)).
        stamp_trace_attributes(
            span,
            TraceContext(
                session_id=request_body.session_id,
                priority=priority.name,
                environment=ENVIRONMENT_PRODUCTION,
            ),
        )

        # Capture the ambient context for the consumer task. Must be
        # taken *inside* the span so the snapshot points at this span.
        captured_otel_ctx = otel_context.get_current()

        # Build the future the consumer will resolve.
        loop = asyncio.get_running_loop()
        future: asyncio.Future[tuple[SignedReceipt, EngineDecision, EventDispatchContext]] = (
            loop.create_future()
        )

        # GH-233 / GH-324 Phase B: capture a temporal anchor for clinical_query prompts.
        # Production callers omit the field (not in model_fields_set) → stamp now().
        # Replay callers always include the key: a string is forwarded verbatim;
        # null (None) suppresses the anchor so build_query_prompt emits no block.
        if "prompt_timestamp" in request_body.model_fields_set:
            prompt_timestamp = request_body.prompt_timestamp
        else:
            prompt_timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

        payload = _QueuePayload(
            future=future,
            ctx_dict=request_body.ctx,
            session_id=request_body.session_id,
            priority=priority,
            prompt_timestamp=prompt_timestamp,
            user_role=request_body.user_role,
        )

        import samantha_server.config as _cfg

        try:
            await enqueue_or_reject(
                state.queue,
                item=payload,
                priority=priority,
                counters=state.counters,
                retry_after_sec=_cfg.QUEUE_BACKPRESSURE_RETRY_AFTER_SEC,
                otel_context=captured_otel_ctx,
            )
        except BackpressureRejection as rejection:
            # Stamp the span ERROR explicitly: BackpressureRejection is
            # caught inside the with-block, so the span exits via a
            # normal return rather than via exception propagation —
            # without an explicit set_status the span shows as UNSET,
            # masking 503s in dashboards.
            span.set_status(trace.StatusCode.ERROR, "backpressure_rejection")
            span.record_exception(rejection)
            return to_503_response(rejection)

        # Wait for the consumer to dispatch the event, with a configurable timeout.
        timeout_sec = _get_dispatch_timeout()
        try:
            receipt, decision, _dispatch_ctx = await asyncio.wait_for(future, timeout=timeout_sec)
        except TimeoutError as exc:
            # Same rationale as the 503 arm: explicit ERROR status so
            # dashboards distinguish a successful dispatch from a
            # silently-timed-out one.
            span.set_status(trace.StatusCode.ERROR, "dispatch_timeout")
            span.record_exception(exc)
            return JSONResponse(
                status_code=504,
                content={
                    "error": "dispatch_timeout",
                    "request_id": request_id,
                    "timeout_sec": int(timeout_sec),
                },
            )

        # Post-dispatch: stamp remaining fields from EngineDecision and
        # EventDispatchContext. applied_rule_id is omitted when None
        # (OTel attributes do not accept None; absence is the signal).
        stamp_trace_attributes(
            span,
            TraceContext(
                event_input_hash=decision.event_input_hash,
                routing_path=_dispatch_ctx.routing_path,
                next_state=decision.next_state,
                outcome=decision.outcome,
                latency_us=decision.latency_us,
                receipt_id=receipt.receipt_id,
                applied_rule_id=decision.applied_rule_id,
                environment=ENVIRONMENT_PRODUCTION,
                order_id=decision.order_id,
            ),
        )

        # Step 9 (GH-124): trace_url is null when LANGFUSE_ENABLED=false
        # (the test / CI default). Otherwise, build the Langfuse
        # trace-deeplink. Clients should not depend on this field for
        # correctness — the receipt is the contract; the trace is
        # observability.
        trace_url: str | None = None
        if _cfg.LANGFUSE_ENABLED:
            trace_id_hex = format(span.get_span_context().trace_id, "032x")
            trace_url = f"{_cfg.LANGFUSE_BASE_URL}/trace/{trace_id_hex}"

        return JSONResponse(
            status_code=200,
            content=EventResponse(
                receipt_id=receipt.receipt_id,
                # mode="json" so JSON-hostile values (e.g. InEnum.trace() stores
                # its allowed set as a frozenset) are converted to JSON-native
                # types before JSONResponse runs json.dumps. The LLM path returns
                # primitive_traces={}, so this only bites once a real rule fires
                # through the endpoint (GH-324).
                #
                # exclude decision_traces + primitive_traces: these carry verbatim
                # clinical strings (PrimitiveTrace.actual = event field values,
                # CanonicalizationTrace.raw_value), so they must not reach the wire.
                # Mirrors receipts.py::_receipt_to_json_dict — the persisted receipt
                # retains them for in-process auditing; HTTP consumers get only the
                # PHI-safe surface fields.
                decision=decision.model_dump(
                    mode="json", exclude={"decision_traces", "primitive_traces"}
                ),
                trace_url=trace_url,
            ).model_dump(),
        )


__all__ = ["EventRequest", "EventResponse", "_QueuePayload", "router"]
