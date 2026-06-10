"""Orchestrator-side receipt emission chokepoint.

emit_receipt() is the single async entry point for signing and persisting
EngineDecision receipts in the FastAPI service. Step 4 migrates the
router-shim's _finalize() into this function.

Architecture:
- Acquires write_lock before calling asyncio.to_thread (serializes SQLite writes).
- Calls asyncio.to_thread(receipt_writer.write_signed, ...) to avoid blocking
  the event loop on SQLite I/O.
- Sign + write both run inside the same try/except so signing-side and
  write-side exceptions both increment counters.receipt_signing_failures.
- Re-raises after counter increment.

No callers in Step 1. Step 4 migrates the router-shim's _finalize into this.

No LLM imports. Deterministic path only.
"""

from __future__ import annotations

import asyncio

from samantha_server.api.receipt_writer import ReceiptWriterProtocol
from samantha_server.engine.decision import EngineDecision
from samantha_server.observability.counters import CounterRegistry
from samantha_server.receipts.signing import SignedReceipt, sign_decision


async def emit_receipt(
    decision: EngineDecision,
    session_id: str | None,
    *,
    receipt_writer: ReceiptWriterProtocol,
    write_lock: asyncio.Lock,
    counters: CounterRegistry,
) -> SignedReceipt:
    """Sign *decision* and persist it to the receipt store.

    This is the orchestrator-side chokepoint. Step 4 migrates the
    router-shim's _finalize() into this function.

    If *session_id* is provided and *decision* already carries a different
    non-None *session_id*, raises ``ValueError`` rather than silently
    overwriting. The orchestrator's caller is
    expected to pass either ``None`` (engine produced no session_id) or
    the same value the decision already carries.

    Parameters
    ----------
    decision:
        The EngineDecision to sign and persist.
    session_id:
        Optional session identifier; stamped onto the decision before
        signing. Must equal ``decision.session_id`` if both are non-None.
    receipt_writer:
        A ReceiptWriterProtocol implementation (e.g., ReceiptWriter).
    write_lock:
        asyncio.Lock serializing writes from concurrent coroutines.
    counters:
        CounterRegistry; receipt_signing_failures is incremented on any
        signing- or write-side exception.

    Returns
    -------
    SignedReceipt
        The signed receipt that was persisted.

    Raises
    ------
    ValueError
        If *session_id* is provided and conflicts with
        ``decision.session_id``. Not counted as a signing failure.
    Exception
        Any exception from sign_decision or receipt_writer.write_signed,
        after incrementing counters.receipt_signing_failures.
    """
    import samantha_server.config as cfg

    if (
        session_id is not None
        and decision.session_id is not None
        and decision.session_id != session_id
    ):
        raise ValueError(
            f"session_id mismatch: kwarg={session_id!r} but "
            f"decision.session_id={decision.session_id!r}"
        )

    async with write_lock:
        try:
            stamped = (
                decision.model_copy(update={"session_id": session_id})
                if session_id is not None
                else decision
            )
            signed = sign_decision(
                stamped,
                key_id=cfg.RECEIPT_SIGNING_KEY_ID,
                signing_key=cfg.RECEIPT_SIGNING_KEY,
            )
            await asyncio.to_thread(receipt_writer.write_signed, signed)
        except Exception:
            counters.receipt_signing_failures.increment()
            raise
    return signed


__all__ = ["emit_receipt"]
