"""Tests for samantha_server.api.receipt_emission — async emit_receipt function."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_mock_decision(session_id: str | None = None) -> MagicMock:
    """Return a minimal mock EngineDecision with a controllable session_id."""
    decision = MagicMock()
    decision.applied_rule_id = "test-rule"
    decision.next_state = "NEXT_STATE"
    decision.outcome = "ADVANCE"
    decision.session_id = session_id
    decision.model_copy.return_value = decision
    return decision


def _make_stub_receipt() -> MagicMock:
    """Return a minimal mock SignedReceipt."""
    receipt = MagicMock()
    receipt.receipt_id = "01ABCDEF0123456789012345"
    return receipt


def test_emit_receipt_acquires_lock() -> None:
    """emit_receipt holds the write_lock during the to_thread write call."""

    async def run() -> None:
        from samantha_server.api.receipt_emission import emit_receipt
        from samantha_server.api.receipt_writer import ReceiptWriter
        from samantha_server.observability.counters import CounterRegistry

        lock = asyncio.Lock()
        mock_writer = MagicMock(spec=ReceiptWriter)
        counters = CounterRegistry()
        stub_receipt = _make_stub_receipt()
        observed_lock_state: list[bool] = []

        async def fake_to_thread(*_args, **_kwargs):  # type: ignore[no-untyped-def]
            observed_lock_state.append(lock.locked())
            return stub_receipt

        with (
            patch(
                "samantha_server.api.receipt_emission.asyncio.to_thread",
                side_effect=fake_to_thread,
            ),
            patch(
                "samantha_server.api.receipt_emission.sign_decision",
                return_value=stub_receipt,
            ),
        ):
            await emit_receipt(
                _make_mock_decision(),
                "session-123",
                receipt_writer=mock_writer,
                write_lock=lock,
                counters=counters,
            )

        assert observed_lock_state == [True]
        assert not lock.locked()

    asyncio.run(run())


def test_emit_receipt_uses_to_thread() -> None:
    """emit_receipt calls asyncio.to_thread for the blocking write — regression guard."""

    async def run() -> None:
        from samantha_server.api.receipt_emission import emit_receipt
        from samantha_server.api.receipt_writer import ReceiptWriter
        from samantha_server.observability.counters import CounterRegistry

        lock = asyncio.Lock()
        mock_writer = MagicMock(spec=ReceiptWriter)
        counters = CounterRegistry()
        stub_receipt = _make_stub_receipt()

        with (
            patch(
                "samantha_server.api.receipt_emission.asyncio.to_thread",
                new_callable=AsyncMock,
            ) as mock_to_thread,
            patch(
                "samantha_server.api.receipt_emission.sign_decision",
                return_value=stub_receipt,
            ),
        ):
            mock_to_thread.return_value = stub_receipt
            result = await emit_receipt(
                _make_mock_decision(),
                "session-123",
                receipt_writer=mock_writer,
                write_lock=lock,
                counters=counters,
            )

        mock_to_thread.assert_called_once()
        assert result == stub_receipt

    asyncio.run(run())


def test_emit_receipt_increments_failure_counter_on_write_exception() -> None:
    """receipt_signing_failures increments when to_thread (write) raises."""

    async def run() -> None:
        from samantha_server.api.receipt_emission import emit_receipt
        from samantha_server.api.receipt_writer import ReceiptWriter
        from samantha_server.observability.counters import CounterRegistry

        lock = asyncio.Lock()
        mock_writer = MagicMock(spec=ReceiptWriter)
        counters = CounterRegistry()
        stub_receipt = _make_stub_receipt()

        with (
            patch(
                "samantha_server.api.receipt_emission.asyncio.to_thread",
                new_callable=AsyncMock,
            ) as mock_to_thread,
            patch(
                "samantha_server.api.receipt_emission.sign_decision",
                return_value=stub_receipt,
            ),
        ):
            mock_to_thread.side_effect = RuntimeError("write failed")
            with pytest.raises(RuntimeError, match="write failed"):
                await emit_receipt(
                    _make_mock_decision(),
                    "session-123",
                    receipt_writer=mock_writer,
                    write_lock=lock,
                    counters=counters,
                )

        assert counters.receipt_signing_failures.value == 1

    asyncio.run(run())


def test_emit_receipt_increments_failure_counter_on_sign_exception() -> None:
    """receipt_signing_failures increments when sign_decision (sign) raises.

    Regression for C1: previously _sign_receipt ran outside the
    try/except, so signing-side failures bypassed the counter.
    """

    async def run() -> None:
        from samantha_server.api.receipt_emission import emit_receipt
        from samantha_server.api.receipt_writer import ReceiptWriter
        from samantha_server.observability.counters import CounterRegistry

        lock = asyncio.Lock()
        mock_writer = MagicMock(spec=ReceiptWriter)
        counters = CounterRegistry()

        with (
            patch(
                "samantha_server.api.receipt_emission.sign_decision",
                side_effect=RuntimeError("nacl exploded"),
            ),
            pytest.raises(RuntimeError, match="nacl exploded"),
        ):
            await emit_receipt(
                _make_mock_decision(),
                "session-123",
                receipt_writer=mock_writer,
                write_lock=lock,
                counters=counters,
            )

        assert counters.receipt_signing_failures.value == 1

    asyncio.run(run())


def test_emit_receipt_reraises_exception() -> None:
    """emit_receipt re-raises the original exception type after incrementing counter."""

    async def run() -> None:
        from samantha_server.api.receipt_emission import emit_receipt
        from samantha_server.api.receipt_writer import ReceiptWriter
        from samantha_server.observability.counters import CounterRegistry

        lock = asyncio.Lock()
        mock_writer = MagicMock(spec=ReceiptWriter)
        counters = CounterRegistry()
        stub_receipt = _make_stub_receipt()

        class SpecialError(Exception):
            pass

        with (
            patch(
                "samantha_server.api.receipt_emission.asyncio.to_thread",
                new_callable=AsyncMock,
            ) as mock_to_thread,
            patch(
                "samantha_server.api.receipt_emission.sign_decision",
                return_value=stub_receipt,
            ),
        ):
            mock_to_thread.side_effect = SpecialError("specific failure")
            with pytest.raises(SpecialError):
                await emit_receipt(
                    _make_mock_decision(),
                    "session-123",
                    receipt_writer=mock_writer,
                    write_lock=lock,
                    counters=counters,
                )

    asyncio.run(run())


def test_emit_receipt_rejects_session_id_mismatch() -> None:
    """ValueError when session_id kwarg conflicts with decision.session_id; no counter increment.

    Regression for H4: previously the wrapper silently overwrote.
    """

    async def run() -> None:
        from samantha_server.api.receipt_emission import emit_receipt
        from samantha_server.api.receipt_writer import ReceiptWriter
        from samantha_server.observability.counters import CounterRegistry

        lock = asyncio.Lock()
        mock_writer = MagicMock(spec=ReceiptWriter)
        counters = CounterRegistry()

        decision = _make_mock_decision(session_id="session-AAA")

        with pytest.raises(ValueError, match="session_id mismatch"):
            await emit_receipt(
                decision,
                "session-BBB",
                receipt_writer=mock_writer,
                write_lock=lock,
                counters=counters,
            )

        # Mismatch is a programming error, not a signing failure.
        assert counters.receipt_signing_failures.value == 0

    asyncio.run(run())


def test_emit_receipt_accepts_matching_session_id() -> None:
    """Same session_id on both sides is accepted (idempotent stamp)."""

    async def run() -> None:
        from samantha_server.api.receipt_emission import emit_receipt
        from samantha_server.api.receipt_writer import ReceiptWriter
        from samantha_server.observability.counters import CounterRegistry

        lock = asyncio.Lock()
        mock_writer = MagicMock(spec=ReceiptWriter)
        counters = CounterRegistry()
        stub_receipt = _make_stub_receipt()

        decision = _make_mock_decision(session_id="session-MATCH")

        with (
            patch(
                "samantha_server.api.receipt_emission.asyncio.to_thread",
                new_callable=AsyncMock,
                return_value=stub_receipt,
            ),
            patch(
                "samantha_server.api.receipt_emission.sign_decision",
                return_value=stub_receipt,
            ),
        ):
            result = await emit_receipt(
                decision,
                "session-MATCH",
                receipt_writer=mock_writer,
                write_lock=lock,
                counters=counters,
            )

        assert result == stub_receipt

    asyncio.run(run())
