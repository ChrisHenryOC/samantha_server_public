"""Tests for the updated _consume consumer task — Slice 7."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any
from unittest.mock import patch

from samantha_server.api.events import EventDispatchContext, _QueuePayload
from samantha_server.engine.decision import EngineDecision
from samantha_server.queue.priority import EventPriority
from samantha_server.receipts.signing import SignedReceipt
from tests.api.helpers import (
    _make_mock_llm_client,
    make_clinical_query_ctx_dict,
    make_consume_app_state,
)


def _make_app_state_for_consume(llm_text: str = "Test response.") -> Any:
    """Backwards-compatible wrapper that lets the legacy tests pick the LLM-text reply."""
    return make_consume_app_state(_make_mock_llm_client(llm_text))


def _make_clinical_query_ctx_dict() -> dict[str, Any]:
    return make_clinical_query_ctx_dict(order_id="CONSUME-001")


def test_consume_resolves_future_with_receipt_and_decision() -> None:
    """_consume resolves the payload's future with (receipt, decision, ctx)."""
    from samantha_server.api.app import _consume

    async def run() -> None:
        state = _make_app_state_for_consume()

        future: asyncio.Future[tuple[SignedReceipt, EngineDecision, EventDispatchContext]] = (
            asyncio.get_event_loop().create_future()
        )

        payload = _QueuePayload(
            future=future,
            ctx_dict=_make_clinical_query_ctx_dict(),
            session_id="consume-test-1",
            priority=EventPriority.ROUTINE,
        )

        await state.queue.put(payload, EventPriority.ROUTINE)

        async def run_once() -> None:
            await _consume(state)

        task = asyncio.create_task(run_once())

        receipt, decision, dispatch_ctx = await asyncio.wait_for(future, timeout=5.0)

        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        assert isinstance(receipt, SignedReceipt)
        assert isinstance(decision, EngineDecision)
        assert isinstance(dispatch_ctx, EventDispatchContext)

    asyncio.run(run())


def test_consume_stamps_session_id_on_decision() -> None:
    """Consumer stamps session_id on the resolved decision."""
    from samantha_server.api.app import _consume

    async def run() -> None:
        state = _make_app_state_for_consume()

        future: asyncio.Future[Any] = asyncio.get_event_loop().create_future()

        payload = _QueuePayload(
            future=future,
            ctx_dict=_make_clinical_query_ctx_dict(),
            session_id="my-session-42",
            priority=EventPriority.ROUTINE,
        )

        await state.queue.put(payload, EventPriority.ROUTINE)

        task = asyncio.create_task(_consume(state))
        receipt, decision, _ = await asyncio.wait_for(future, timeout=5.0)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        assert decision.session_id == "my-session-42"

    asyncio.run(run())


def test_consume_continues_after_dispatch_exception() -> None:
    """Consumer loop continues after a failed dispatch (bad ctx_dict)."""
    from samantha_server.api.app import _consume

    async def run() -> None:
        state = _make_app_state_for_consume()

        # First item: invalid ctx_dict — dispatch will fail.
        bad_future: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        bad_payload = _QueuePayload(
            future=bad_future,
            ctx_dict={"bad": "data", "not_a_context": True},
            session_id="bad-sess",
            priority=EventPriority.ROUTINE,
        )

        # Second item: valid ctx_dict.
        good_future: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        good_payload = _QueuePayload(
            future=good_future,
            ctx_dict=_make_clinical_query_ctx_dict(),
            session_id="good-sess",
            priority=EventPriority.ROUTINE,
        )

        await state.queue.put(bad_payload, EventPriority.ROUTINE)
        await state.queue.put(good_payload, EventPriority.ROUTINE)

        task = asyncio.create_task(_consume(state))

        _, good_result, _ = await asyncio.wait_for(good_future, timeout=5.0)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        assert bad_future.done()
        assert bad_future.exception() is not None
        assert isinstance(good_result, EngineDecision)

    asyncio.run(run())


def test_consume_cancelled_error_resolves_inflight_future() -> None:
    """When the consumer task is cancelled, any in-flight payload's future gets resolved.

    Issue #7/#23: CancelledError was escaping the except-Exception block, leaving
    the dequeued payload's future unresolved (hang scenario on shutdown).
    The fix: handle CancelledError explicitly and resolve the future before re-raising.
    """
    from samantha_server.api.app import _consume

    async def run() -> None:
        state = _make_app_state_for_consume()

        future: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        payload = _QueuePayload(
            future=future,
            ctx_dict=_make_clinical_query_ctx_dict(),
            session_id="shutdown-test",
            priority=EventPriority.ROUTINE,
        )

        await state.queue.put(payload, EventPriority.ROUTINE)

        # Start consumer
        task = asyncio.create_task(_consume(state))

        # Give consumer a moment to dequeue
        await asyncio.sleep(0.05)

        # Cancel the consumer task (simulates shutdown)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        # The future must be resolved (done), not hanging
        assert future.done(), "Future must be resolved after consumer cancellation"

    asyncio.run(run())


def test_consume_queue_wait_us_positive() -> None:
    """Consumer computes a non-negative queue_wait_us."""
    from samantha_server.api.app import _consume

    async def run() -> None:
        state = _make_app_state_for_consume()

        future: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        payload = _QueuePayload(
            future=future,
            ctx_dict=_make_clinical_query_ctx_dict(),
            session_id="sess",
            priority=EventPriority.ROUTINE,
        )

        await state.queue.put(payload, EventPriority.ROUTINE)

        task = asyncio.create_task(_consume(state))
        _, _, dispatch_ctx = await asyncio.wait_for(future, timeout=5.0)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        assert dispatch_ctx.queue_wait_us >= 0

    asyncio.run(run())


def test_consume_detaches_otel_context_when_dispatch_raises() -> None:
    """M18 regression: when dispatch_event raises with a non-None otel token,

    the consumer's finally-block must call ``otel_context.detach``.
    Without that, the parent-span context leaks into the next loop
    iteration and subsequent (unrelated) events become children of a
    stale span — the entire motivation for the finally-block.
    """
    from opentelemetry import context as otel_context
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider

    from samantha_server.api.app import _consume
    from samantha_server.observability.otel import PARENT_SPAN_NAME

    # Detach calls observed during the consumer loop.
    detach_calls: list[Any] = []
    real_detach = otel_context.detach

    def _spying_detach(token: Any) -> None:
        detach_calls.append(token)
        return real_detach(token)

    async def run() -> None:
        state = _make_app_state_for_consume()
        # Force dispatch to raise: a corrupt ctx_dict triggers a
        # ValidationError inside _consume's body — exercises the
        # exception path while the otel token is non-None.
        bad_ctx = {"corrupt": True}

        # Need an SDK provider for the captured context to be meaningful.
        # If the test conftest already installed one, reuse; otherwise install.
        existing = trace.get_tracer_provider()
        if not isinstance(existing, TracerProvider):
            trace.set_tracer_provider(TracerProvider())

        tracer = trace.get_tracer("samantha_server")
        with tracer.start_as_current_span(PARENT_SPAN_NAME):
            captured_ctx = otel_context.get_current()

            future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
            payload = _QueuePayload(
                future=future,
                ctx_dict=bad_ctx,
                session_id="sess",
                priority=EventPriority.ROUTINE,
            )
            await state.queue.put(payload, EventPriority.ROUTINE, otel_context=captured_ctx)

            with patch.object(otel_context, "detach", side_effect=_spying_detach):
                task = asyncio.create_task(_consume(state))
                # The dispatch raises; future receives the exception.
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(future, timeout=5.0)
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        # The finally-block detached the otel token even though dispatch raised.
        assert detach_calls, "detach not called after consumer raised — context leaked"

    asyncio.run(run())


def test_consume_forwards_prompt_timestamp_to_dispatch_event() -> None:
    """GH-233 / PR #242 fix-review: _consume forwards _QueuePayload.prompt_timestamp
    into dispatch_event as a kwarg. Regression guard for the production-path
    omission flagged as Critical.
    """
    from samantha_server.api.app import _consume

    captured: dict[str, Any] = {}

    async def _fake_dispatch(*args: Any, **kwargs: Any) -> Any:
        captured["prompt_timestamp"] = kwargs.get("prompt_timestamp")
        # Build minimal-shape return tuple matching dispatch_event signature.
        decision = EngineDecision(
            applied_rule_id=None,
            next_state="ACCESSIONING",
            flags_added=(),
            flags_cleared=(),
            outcome="query_response",
            also_matched=(),
            dispatched_rule_ids=(),
            event_input_hash="a" * 64,
            primitive_traces={},
            latency_us=0,
            session_id=kwargs["session_id"],
        )
        import samantha_server.config as cfg
        from samantha_server.receipts.signing import sign_decision

        receipt = sign_decision(
            decision,
            key_id=cfg.RECEIPT_SIGNING_KEY_ID,
            signing_key=cfg.RECEIPT_SIGNING_KEY,
        )
        ctx = EventDispatchContext(
            session_id=kwargs["session_id"],
            priority=kwargs["priority"],
            routing_path="llm",
            queue_wait_us=kwargs["queue_wait_us"],
        )
        return decision, ctx, receipt

    async def run() -> None:
        state = _make_app_state_for_consume()
        future: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        payload = _QueuePayload(
            future=future,
            ctx_dict=_make_clinical_query_ctx_dict(),
            session_id="ts-test-1",
            priority=EventPriority.ROUTINE,
            prompt_timestamp="2025-04-15T12:34:56Z",
        )
        await state.queue.put(payload, EventPriority.ROUTINE)

        with patch("samantha_server.api.routing.dispatch_event", side_effect=_fake_dispatch):
            task = asyncio.create_task(_consume(state))
            await asyncio.wait_for(future, timeout=5.0)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        assert captured["prompt_timestamp"] == "2025-04-15T12:34:56Z"

    asyncio.run(run())


def test_consume_forwards_user_role_to_dispatch_event() -> None:
    """S5 PR243 review H3: _consume forwards _QueuePayload.user_role into
    event_data["user_role"] via the copy-on-write rewrite in app.py.
    Regression guard for the production-path injection bridge.
    """
    from samantha_server.api.app import _consume

    captured: dict[str, Any] = {}

    async def _fake_dispatch(*args: Any, **kwargs: Any) -> Any:
        ctx = args[0]
        captured["user_role"] = ctx.event.event_data.get("user_role")
        decision = EngineDecision(
            applied_rule_id=None,
            next_state="ACCESSIONING",
            flags_added=(),
            flags_cleared=(),
            outcome="query_response",
            also_matched=(),
            dispatched_rule_ids=(),
            event_input_hash="a" * 64,
            primitive_traces={},
            latency_us=0,
            session_id=kwargs["session_id"],
        )
        import samantha_server.config as cfg
        from samantha_server.receipts.signing import sign_decision

        receipt = sign_decision(
            decision,
            key_id=cfg.RECEIPT_SIGNING_KEY_ID,
            signing_key=cfg.RECEIPT_SIGNING_KEY,
        )
        ctx_out = EventDispatchContext(
            session_id=kwargs["session_id"],
            priority=kwargs["priority"],
            routing_path="llm",
            queue_wait_us=kwargs["queue_wait_us"],
        )
        return decision, ctx_out, receipt

    async def run() -> None:
        state = _make_app_state_for_consume()
        future: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        payload = _QueuePayload(
            future=future,
            ctx_dict=_make_clinical_query_ctx_dict(),
            session_id="role-test-1",
            priority=EventPriority.ROUTINE,
            user_role="pathologist",
        )
        await state.queue.put(payload, EventPriority.ROUTINE)

        with patch("samantha_server.api.routing.dispatch_event", side_effect=_fake_dispatch):
            task = asyncio.create_task(_consume(state))
            await asyncio.wait_for(future, timeout=5.0)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        assert captured["user_role"] == "pathologist"

    asyncio.run(run())


def test_consume_forwards_user_role_none_when_absent() -> None:
    """S5 PR243 review H3: _consume with user_role=None leaves event_data unchanged.
    user_role key must not be injected when payload carries no role.
    """
    from samantha_server.api.app import _consume

    captured: dict[str, Any] = {}

    async def _fake_dispatch(*args: Any, **kwargs: Any) -> Any:
        ctx = args[0]
        captured["has_user_role_key"] = "user_role" in dict(ctx.event.event_data)
        decision = EngineDecision(
            applied_rule_id=None,
            next_state="ACCESSIONING",
            flags_added=(),
            flags_cleared=(),
            outcome="query_response",
            also_matched=(),
            dispatched_rule_ids=(),
            event_input_hash="a" * 64,
            primitive_traces={},
            latency_us=0,
            session_id=kwargs["session_id"],
        )
        import samantha_server.config as cfg
        from samantha_server.receipts.signing import sign_decision

        receipt = sign_decision(
            decision,
            key_id=cfg.RECEIPT_SIGNING_KEY_ID,
            signing_key=cfg.RECEIPT_SIGNING_KEY,
        )
        ctx_out = EventDispatchContext(
            session_id=kwargs["session_id"],
            priority=kwargs["priority"],
            routing_path="llm",
            queue_wait_us=kwargs["queue_wait_us"],
        )
        return decision, ctx_out, receipt

    async def run() -> None:
        state = _make_app_state_for_consume()
        future: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        payload = _QueuePayload(
            future=future,
            ctx_dict=_make_clinical_query_ctx_dict(),
            session_id="role-test-2",
            priority=EventPriority.ROUTINE,
            # user_role defaults to None
        )
        await state.queue.put(payload, EventPriority.ROUTINE)

        with patch("samantha_server.api.routing.dispatch_event", side_effect=_fake_dispatch):
            task = asyncio.create_task(_consume(state))
            await asyncio.wait_for(future, timeout=5.0)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        assert not captured["has_user_role_key"], (
            "user_role must not be injected into event_data when payload.user_role is None"
        )
