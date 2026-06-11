"""SQLite WAL-mode store for signed receipts.

Append-only by convention: only INSERT operations are exposed through
the public API. The audit-side connection opens with PRAGMA query_only=1
so any UPDATE/DELETE attempted through it raises sqlite3.OperationalError.

No LLM imports. Deterministic path only.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from samantha_server.errors import ReceiptPersistenceError
from samantha_server.receipts.signing import SignedReceipt, _canonical_signing_payload

_SCHEMA_SQL = (Path(__file__).resolve().parent / "schema.sql").read_text()


# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------


def _validate_store_path(path: Path, *, allowed_base: Path | None = None) -> None:
    """Reject '..' traversal and paths outside the configured receipts directory.

    The check is intentionally conservative: any path whose string
    representation contains '..' (before or after resolution) is
    rejected. The store is a local developer-machine file; there is no
    reason to use relative traversal in a legitimate path.

    M-01/M-02: when *allowed_base* is provided, the resolved path must be a
    descendant of that directory. This anchors the check against the configured
    receipts directory (Path(config.RECEIPTS_DB_PATH).resolve().parent) rather
    than the supplied path's own parent — the prior self-check was a tautology
    for non-symlink paths and missed absolute paths outside any configured dir.
    """
    # Reject explicit '..' components anywhere in the original path
    if ".." in path.parts:
        raise ValueError(f"receipts store path must not contain '..': {path}")
    # Resolve symlinks and check ancestry
    resolved = path.resolve()
    base = allowed_base if allowed_base is not None else path.parent.resolve()
    try:
        resolved.relative_to(base)
    except ValueError:
        raise ValueError(
            f"receipts store path {path!r} resolves outside the allowed directory "
            f"({base}): {resolved}"
        ) from None


# ---------------------------------------------------------------------------
# Connection factories
# ---------------------------------------------------------------------------


def _open_write_conn(path: Path, *, check_same_thread: bool = True) -> sqlite3.Connection:
    """Open a write connection to the receipts store.

    The connection has NORMAL synchronous mode (safe for WAL) and
    foreign-keys enabled. The caller is responsible for committing
    and closing.

    Parameters
    ----------
    path:
        Full path to the SQLite file.
    check_same_thread:
        Passed through to sqlite3.connect. Pass False when the connection
        will be used from worker threads (e.g., ReceiptWriter.open).
    """
    conn = sqlite3.connect(str(path), check_same_thread=check_same_thread)
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _open_audit_conn(path: Path) -> sqlite3.Connection:
    """Open a read-only (query_only) connection to the receipts store.

    PRAGMA query_only=1 causes SQLite to reject any DML that modifies
    the database (UPDATE, DELETE, INSERT, CREATE, DROP).

    check_same_thread=False is required because FastAPI's TestClient runs
    the ASGI app in a thread different from the lifespan startup thread;
    this flag allows the audit conn opened at startup to be reused on
    request-handler threads. Production handlers are async (single-thread
    on the event loop), but TestClient compatibility is the load-bearing
    reason. SQLite in WAL mode is safe to read from a single connection
    across threads when no writes are issued — query_only=1 enforces that
    structurally.
    """
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.execute("PRAGMA query_only=1")
    return conn


# ---------------------------------------------------------------------------
# Idempotent ALTER TABLE migration helper
# ---------------------------------------------------------------------------


def _migrate_add_order_id_column(conn: sqlite3.Connection) -> None:
    """Idempotently add the order_id column to a pre-existing receipts table.

    Must run BEFORE the schema's ``CREATE INDEX idx_receipts_order_id``, which
    references the column. Two OperationalError cases are expected and swallowed:

    - ``"duplicate column name"``: already-migrated or fresh DB (schema.sql
      includes the column on CREATE TABLE, so the fresh path sees the table
      after executescript runs — this ALTER is a no-op in that case).
    - ``"no such table"``: fresh DB where the receipts table does not exist yet;
      executescript will create it with the column already present.

    Any other OperationalError (e.g. a locked DB) propagates with its true cause
    rather than being masked. Called by both init_schema and init_store.
    """
    try:
        conn.execute("ALTER TABLE receipts ADD COLUMN order_id TEXT")
    except sqlite3.OperationalError as exc:
        if not any(msg in str(exc) for msg in ("duplicate column name", "no such table")):
            raise


# ---------------------------------------------------------------------------
# Store initialisation
# ---------------------------------------------------------------------------


def init_schema(conn: sqlite3.Connection) -> None:
    """Apply the receipts schema to *conn* (idempotent).

    Public helper for callers that already hold an open connection
    (replay harness, parity CLI). Avoids reaching into ``_SCHEMA_SQL``
    and duplicating the foreign-keys / executescript / commit idiom.

    Applies an idempotent ALTER TABLE migration for pre-existing
    databases that lack the order_id column. Fresh databases already have
    the column from schema.sql; the OperationalError for duplicate column
    name is swallowed.
    """
    conn.execute("PRAGMA foreign_keys=ON")
    # Migrate pre-existing DBs; must precede executescript so
    # schema.sql's idx_receipts_order_id can reference the column.
    _migrate_add_order_id_column(conn)
    conn.executescript(_SCHEMA_SQL)
    conn.commit()


def init_store(path: Path) -> None:
    """Initialise the receipts SQLite store at *path*.

    Creates the file if it doesn't exist, sets permissions to 0o600,
    enables WAL journaling, and applies the schema (idempotent via
    CREATE TABLE IF NOT EXISTS).

    Parameters
    ----------
    path:
        Full path to the SQLite file. Must not contain '..'.

    Raises
    ------
    ValueError
        If path contains '..' or resolves outside its parent.
    ReceiptPersistenceError
        If the file cannot be created or the schema cannot be applied.
    """
    # M-01/M-02: anchor validation against the configured receipts directory.
    # Import lazily to avoid triggering the sentinel check at collection time.
    import samantha_server.config as cfg

    allowed_base = Path(cfg.RECEIPTS_DB_PATH).resolve().parent
    _validate_store_path(path, allowed_base=allowed_base)

    try:
        # Create parent dirs if needed
        path.parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(str(path))
        try:
            # G4: SQLite WAL — append-only, crash-safe; readers never block writers
            conn.execute("PRAGMA journal_mode=WAL")
            # Migrate pre-existing DBs; must precede executescript so
            # schema.sql's idx_receipts_order_id can reference the column.
            _migrate_add_order_id_column(conn)
            # Apply schema (idempotent)
            conn.executescript(_SCHEMA_SQL)
            conn.commit()
        finally:
            conn.close()

        # G15: 0o600 — owner read/write only; no group or world access
        path.chmod(0o600)

    except (sqlite3.OperationalError, sqlite3.DatabaseError, OSError) as exc:
        raise ReceiptPersistenceError(cause=f"Failed to initialise receipts store: {exc}") from exc


# ---------------------------------------------------------------------------
# Insert
# ---------------------------------------------------------------------------


def insert(conn: sqlite3.Connection, receipt: SignedReceipt) -> None:
    """Insert *receipt* into the receipts table via *conn*.

    The caller must commit the connection after calling this function.

    Parameters
    ----------
    conn:
        An open write connection (from _open_write_conn).
    receipt:
        The SignedReceipt to persist.

    Raises
    ------
    ReceiptPersistenceError
        If the insert fails (duplicate receipt_id, DB error, etc.).
    """
    # Reuse carried payload bytes when available (set by sign_decision);
    # fall back to recomputing for rehydrated receipts that have no carry.
    _carry = receipt.canonical_payload_bytes()
    payload_json = (_carry if _carry else _canonical_signing_payload(receipt.decision)).decode()
    try:
        conn.execute(
            """
            INSERT INTO receipts (
                receipt_id, event_input_hash, applied_rule_id, order_id,
                next_state, outcome, signer_key_id, signature,
                payload_json, signed_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                receipt.receipt_id,
                receipt.decision.event_input_hash,
                receipt.decision.applied_rule_id,
                receipt.decision.order_id,
                receipt.decision.next_state,
                receipt.decision.outcome,
                receipt.signer_key_id,
                receipt.signature,
                payload_json,
                receipt.signed_at_utc.isoformat(),
            ),
        )
        conn.commit()
    except (sqlite3.DatabaseError, OSError) as exc:
        raise ReceiptPersistenceError(cause=f"Failed to insert receipt: {exc}") from exc


# ---------------------------------------------------------------------------
# Read helper — used by the replay harness to recover the full EngineDecision
# (including decision_traces / primitive_traces) from the persisted receipt,
# bypassing the PHI-stripped /events HTTP response.
# ---------------------------------------------------------------------------


def fetch_by_receipt_id(conn: sqlite3.Connection, receipt_id: str) -> str | None:
    """Return the persisted payload_json string for *receipt_id*, or None if absent.

    Parameters
    ----------
    conn:
        An open connection to the receipts store (any access mode).
    receipt_id:
        The ULID string identifying the receipt row to look up.

    Returns
    -------
    str | None
        The raw ``payload_json`` column value, or ``None`` when no row exists
        for *receipt_id*.
    """
    row = conn.execute(
        "SELECT payload_json FROM receipts WHERE receipt_id = ?",
        (receipt_id,),
    ).fetchone()
    return row[0] if row is not None else None


__all__ = [
    "_migrate_add_order_id_column",
    "fetch_by_receipt_id",
    "init_schema",
    "init_store",
    "insert",
]
