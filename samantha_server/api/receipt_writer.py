"""ReceiptWriter — owns a single SQLite connection for the API service lifetime.

The connection is opened with check_same_thread=False and serialized by the
asyncio.Lock in AppState.receipt_write_lock. All writes go through
asyncio.to_thread so the event loop is never blocked.

No LLM imports. Deterministic path only.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Protocol, runtime_checkable

from samantha_server.errors import ReceiptPersistenceError
from samantha_server.receipts.signing import SignedReceipt
from samantha_server.receipts.store import _open_write_conn, fetch_by_receipt_id, init_store, insert

_log = logging.getLogger(__name__)


@runtime_checkable
class ReceiptWriterProtocol(Protocol):
    """Structural protocol for objects that can write signed receipts.

    Any class that implements write_signed(), close(), and is_open() satisfies
    this protocol regardless of inheritance. Use this as the parameter type
    for dispatch_event() so test-supplied spy adapters are accepted without
    type: ignore suppressions.
    """

    def write_signed(self, receipt: SignedReceipt) -> None: ...

    def close(self) -> None:
        """Close the writer. Must be idempotent and must not raise.

        aclose() may reach close() from more than one shutdown path; a second
        call must be a silent no-op, and failures are logged, not raised.
        """
        ...

    def is_open(self) -> bool:
        """Return True if the writer is open and ready to accept writes.

        This is a liveness FLAG, not a deep connection probe. The flag is set
        True at construction and flipped to False by close() or on any
        write_signed() failure. A dead-but-never-written connection therefore
        reads True until the first write fails (issue-preferred
        design). /readyz relies on this flag to avoid blocking the event loop.
        """
        ...

    def fetch_payload_json(self, receipt_id: str) -> str | None: ...


class ReceiptWriter:
    """Thin wrapper around a shared SQLite write connection for signed receipts.

    Owns the connection lifetime; callers serialize writes via an external
    asyncio.Lock (AppState.receipt_write_lock) and call write_signed() from
    within asyncio.to_thread().

    Parameters
    ----------
    conn:
        An open SQLite connection. Must have been opened with
        check_same_thread=False if it will be used from worker threads.
    """

    _conn: sqlite3.Connection | None
    _is_open_flag: bool

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._is_open_flag = True

    @classmethod
    def open(cls, db_path: Path) -> ReceiptWriter:
        """Open a new write connection to the receipts store at db_path.

        Initialises the store schema if the file doesn't exist, then opens
        a connection via store._open_write_conn with check_same_thread=False
        for use from to_thread workers (single connection constructor).
        """
        init_store(db_path)
        conn = _open_write_conn(db_path, check_same_thread=False)
        return cls(conn)

    def write_signed(self, receipt: SignedReceipt) -> None:
        """Insert *receipt* into the store using the owned connection.

        Called via asyncio.to_thread() from emit_receipt(); the caller holds
        AppState.receipt_write_lock for the duration of the to_thread call.

        On insert failure the liveness flag is flipped to False and the failure
        is logged at WARNING with exc_info so a corrupted-DB error is
        distinguishable from a clean close (Item 3 comment).

        Raises
        ------
        ReceiptPersistenceError
            If the writer has been closed or if the insert fails.
        """
        if self._conn is None:
            raise ReceiptPersistenceError(cause="writer is closed")
        try:
            insert(self._conn, receipt)
        except Exception:
            self._is_open_flag = False
            _log.warning(
                "ReceiptWriter.write_signed() failed; liveness flag set to False",
                exc_info=True,
            )
            raise

    def close(self) -> None:
        """Close the underlying connection.

        Suppression semantics preserved: close() never raises so callers
        (aclose()) are not disrupted by a WAL-checkpoint failure at shutdown.
        Failure is logged at WARNING with exc_info so it is diagnosable
        (recent receipts may not be durably flushed if close raises).
        Idempotent: a second call on an already-closed writer is a no-op.
        Sets the liveness flag to False.
        """
        if self._conn is None:
            return
        self._is_open_flag = False
        try:
            self._conn.close()
        except Exception:
            _log.warning(
                "ReceiptWriter.close() failed; recent receipts may not be durably flushed",
                exc_info=True,
            )
        self._conn = None

    def is_open(self) -> bool:
        """Return True if the writer is open and ready to accept writes.

        Item 3: returns a liveness flag (no DB call) rather than
        issuing a synchronous SELECT 1 on the event loop. The flag is set
        True in __init__, and flipped to False by close() or on any
        write_signed() failure, so /readyz never blocks the event loop.
        """
        return self._is_open_flag

    def fetch_payload_json(self, receipt_id: str) -> str | None:
        """Return the persisted payload_json for *receipt_id*, or None if absent.

        Raises ReceiptPersistenceError if the writer has been closed so callers
        get a typed error from the project hierarchy rather than a bare RuntimeError
        or AttributeError from a NoneType access.
        """
        if self._conn is None:
            raise ReceiptPersistenceError(cause="writer is closed")
        return fetch_by_receipt_id(self._conn, receipt_id)


__all__ = ["ReceiptWriter", "ReceiptWriterProtocol"]
