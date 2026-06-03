"""ReceiptWriter — owns a single SQLite connection for the API service lifetime.

The connection is opened with check_same_thread=False and serialized by the
asyncio.Lock in AppState.receipt_write_lock. All writes go through
asyncio.to_thread so the event loop is never blocked.

No LLM imports. Deterministic path only.
"""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path
from typing import Protocol, runtime_checkable

from samantha_server.receipts.signing import SignedReceipt
from samantha_server.receipts.store import fetch_by_receipt_id, init_store, insert


@runtime_checkable
class ReceiptWriterProtocol(Protocol):
    """Structural protocol for objects that can write signed receipts.

    Any class that implements write_signed(), close(), and is_open() satisfies
    this protocol regardless of inheritance. Use this as the parameter type
    for dispatch_event() so test-supplied spy adapters are accepted without
    type: ignore suppressions.
    """

    def write_signed(self, receipt: SignedReceipt) -> None: ...

    def close(self) -> None: ...

    def is_open(self) -> bool: ...

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

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    @classmethod
    def open(cls, db_path: Path) -> ReceiptWriter:
        """Open a new write connection to the receipts store at db_path.

        Initialises the store schema if the file doesn't exist, then opens
        a connection with check_same_thread=False for use from to_thread workers.
        """
        init_store(db_path)
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return cls(conn)

    def write_signed(self, receipt: SignedReceipt) -> None:
        """Insert *receipt* into the store using the owned connection.

        Called via asyncio.to_thread() from emit_receipt(); the caller holds
        AppState.receipt_write_lock for the duration of the to_thread call.
        """
        insert(self._conn, receipt)

    def close(self) -> None:
        """Close the underlying connection."""
        with contextlib.suppress(Exception):
            self._conn.close()
        self._conn = None  # type: ignore[assignment]

    def is_open(self) -> bool:
        """Return True if the underlying connection is alive.

        A no-op cheap probe used by ``/readyz`` for the load-bearing
        SQLite-connection check. Catches the case where ``close()`` was
        called or the connection raised on a prior write.
        """
        if self._conn is None:
            return False
        try:
            self._conn.execute("SELECT 1").fetchone()
        except Exception:
            return False
        return True

    def fetch_payload_json(self, receipt_id: str) -> str | None:
        """Return the persisted payload_json for *receipt_id*, or None if absent.

        Raises RuntimeError if the writer has been closed (``_conn`` is None)
        so callers get a clear error rather than an opaque AttributeError from
        a NoneType access.
        """
        if self._conn is None:
            raise RuntimeError("ReceiptWriter is closed")
        return fetch_by_receipt_id(self._conn, receipt_id)


__all__ = ["ReceiptWriter", "ReceiptWriterProtocol"]
