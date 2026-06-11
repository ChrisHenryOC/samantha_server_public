"""Read API for the receipts SQLite store.

No UPDATE or DELETE SQL appears in this module — append-only is enforced
both here (no such statements) and at the connection level (PRAGMA
query_only=1 on audit connections).

No LLM imports. Deterministic path only.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from samantha_server.engine.decision import EngineDecision
from samantha_server.errors import ReceiptPersistenceError
from samantha_server.receipts.signing import SignedReceipt, _is_utc, verify_signature

_logger = logging.getLogger(__name__)

# Re-export verify_signature so audit callers can import it from one place.
__all__ = [
    "count_total",
    "fetch_by_event_hash",
    "fetch_by_id",
    "fetch_by_order_id",
    "fetch_by_outcome",
    "fetch_by_query_text_hash",
    "fetch_by_rule_id",
    "fetch_in_window",
    "fetch_paginated",
    "verify_signature",
]


# ---------------------------------------------------------------------------
# 9-column projection shared by all audit SELECT queries.
#
# Column ordinals correspond to _row_to_receipt's positional expectations:
#   0: receipt_id        4: outcome          8: signed_at_utc
#   1: event_input_hash  5: signer_key_id
#   2: applied_rule_id   6: signature
#   3: next_state        7: payload_json
#
# NOTE: order_id (physically at schema position 3) is intentionally excluded.
# decision.order_id is rehydrated from payload_json; the column is a
# write-only search index here. A query that adds order_id to its SELECT
# list must not reuse these ordinals.
# Compile-time string literal — no runtime data is ever interpolated into
# this projection (all query values go through parameterized placeholders).
_RECEIPT_SELECT = (
    "receipt_id, event_input_hash, applied_rule_id, next_state, "
    "outcome, signer_key_id, signature, payload_json, signed_at_utc"
)


# ---------------------------------------------------------------------------
# Row → SignedReceipt rehydration
# ---------------------------------------------------------------------------


def _row_to_receipt(row: tuple[Any, ...]) -> SignedReceipt:
    """Rehydrate a receipts table row into a SignedReceipt.

    These ordinals reflect the explicit 9-column projection every audit.py
    SELECT uses, NOT the physical schema.sql column order. The
    ``order_id`` column (physically at schema position 3) is intentionally
    omitted from those SELECTs: ``decision.order_id`` is rehydrated from
    ``payload_json``, so the column is a write-only search index here. A new
    query that adds ``order_id`` to its SELECT list must not reuse these
    ordinals.
      0: receipt_id
      1: event_input_hash
      2: applied_rule_id
      3: next_state
      4: outcome
      5: signer_key_id
      6: signature (BLOB)
      7: payload_json
      8: signed_at_utc (ISO-8601 TEXT)
    """
    receipt_id = str(row[0])
    signer_key_id = str(row[5])
    signature = bytes(row[6])
    payload_json = str(row[7])
    signed_at_str = str(row[8])

    try:
        decision = EngineDecision.model_validate_json(payload_json)
        # Parse ISO-8601 timestamp. The store always writes UTC-aware ISO-8601
        # (signed_at_utc.isoformat() always emits +00:00 suffix). A naive
        # signed_at will fail SignedReceipt._enforce_utc validation below.
        signed_at = datetime.fromisoformat(signed_at_str)
        return SignedReceipt(
            receipt_id=receipt_id,
            decision=decision,
            signer_key_id=signer_key_id,
            signature=signature,
            signed_at_utc=signed_at,
        )
    except (ValidationError, json.JSONDecodeError, ValueError) as exc:
        raise ReceiptPersistenceError(cause=f"row {receipt_id} malformed: {exc}") from exc


# ---------------------------------------------------------------------------
# Query functions
# ---------------------------------------------------------------------------


def fetch_by_event_hash(
    conn: sqlite3.Connection,
    event_input_hash: str,
) -> list[SignedReceipt]:
    """Return all receipts whose event_input_hash matches *event_input_hash*.

    Parameters
    ----------
    conn:
        An open connection (audit or write).
    event_input_hash:
        The SHA-256 hex hash to match.

    Returns
    -------
    list[SignedReceipt]
        0 or more receipts, in insertion order.
    """
    cursor = conn.execute(
        f"SELECT {_RECEIPT_SELECT} FROM receipts WHERE event_input_hash = ?",
        (event_input_hash,),
    )
    return [_row_to_receipt(row) for row in cursor.fetchall()]


def fetch_by_rule_id(
    conn: sqlite3.Connection,
    rule_id: str | None,
) -> list[SignedReceipt]:
    """Return all receipts whose applied_rule_id matches *rule_id*.

    Passing ``None`` matches receipts with a NULL applied_rule_id
    (dispatch_empty, query, clarification, refusal cases).

    Parameters
    ----------
    conn:
        An open connection (audit or write).
    rule_id:
        The rule id to match, or None to match dispatch_empty / refusal receipts.

    Returns
    -------
    list[SignedReceipt]
        0 or more receipts, in insertion order.
    """
    if rule_id is None:
        cursor = conn.execute(
            f"SELECT {_RECEIPT_SELECT} FROM receipts WHERE applied_rule_id IS NULL",
        )
    else:
        cursor = conn.execute(
            f"SELECT {_RECEIPT_SELECT} FROM receipts WHERE applied_rule_id = ?",
            (rule_id,),
        )
    return [_row_to_receipt(row) for row in cursor.fetchall()]


def fetch_in_window(
    conn: sqlite3.Connection,
    start_utc: datetime,
    end_utc: datetime,
) -> list[SignedReceipt]:
    """Return receipts whose signed_at_utc falls in [start_utc, end_utc].

    Parameters
    ----------
    conn:
        An open connection (audit or write).
    start_utc:
        Inclusive lower bound; must be tz-aware UTC.
    end_utc:
        Inclusive upper bound; must be tz-aware UTC.

    Raises
    ------
    ValueError
        If either datetime is naive (missing tzinfo) or not UTC.
    """
    _validate_utc(start_utc, "start_utc")
    _validate_utc(end_utc, "end_utc")

    cursor = conn.execute(
        f"SELECT {_RECEIPT_SELECT} "
        "FROM receipts "
        "WHERE signed_at_utc >= ? AND signed_at_utc <= ? "
        "ORDER BY signed_at_utc ASC",
        (start_utc.isoformat(), end_utc.isoformat()),
    )
    return [_row_to_receipt(row) for row in cursor.fetchall()]


def fetch_by_query_text_hash(
    conn: sqlite3.Connection,
    query_text_hash: str,
) -> list[SignedReceipt]:
    """Return all query-response receipts whose QueryTrace.query_text_hash matches.

    Scans all receipts with outcome='query_response', rehydrates each, and
    filters by checking decision_traces[0].query_text_hash.

    Trade-off: O(N) scan over query_response rows. Acceptable for
    development-tool scale; Phase 3 may add a JSON-extracted index on the
    SQLite column for fast lookup. The outcome='query_response' pre-filter
    reduces the scan to the query subset only.

    Parameters
    ----------
    conn:
        An open connection (audit or write). Must have query_only=1 for
        production audit reads.
    query_text_hash:
        The sha256 hex hash of the canonical JSON of event.event_data to match.

    Returns
    -------
    list[SignedReceipt]
        0 or more receipts, in insertion order.
    """
    from samantha_server.engine.decision import QueryTrace

    # outcome='query_response' intentionally excludes refusal receipts: refusals
    # carry no recoverable response text (llm_failed is scoped to
    # LLMClientError; parse failures produce llm_failed=False with no
    # suggestions), so matching them by query_text_hash has no useful result.
    cursor = conn.execute(
        f"SELECT {_RECEIPT_SELECT} FROM receipts WHERE outcome = 'query_response'",
    )
    results = []
    # M-12a: iterate cursor directly instead of fetchall() for O(1) peak memory
    for row in cursor:
        # M-03: wrap rehydration in try/except so a single malformed row does
        # not abort the scan. A valid match later in the result set must still
        # be returned. Log + skip the bad row.
        try:
            receipt = _row_to_receipt(row)
        except ReceiptPersistenceError as exc:
            _logger.warning(
                "Skipping malformed receipt row in fetch_by_query_text_hash: %s",
                exc,
                exc_info=exc,
            )
            continue
        # QueryTrace is always the sole trace on a query_response receipt.
        traces = receipt.decision.decision_traces
        if (
            traces
            and isinstance(traces[0], QueryTrace)
            and traces[0].query_text_hash == query_text_hash
        ):
            results.append(receipt)
    return results


def fetch_by_outcome(
    conn: sqlite3.Connection,
    outcome: str,
) -> list[SignedReceipt]:
    """Return all receipts whose outcome field exactly matches *outcome*.

    Exact string match only — wildcard patterns (e.g., ``"refused_*"``) are
    NOT supported. Callers that need prefix filtering should fetch all receipts
    and filter post-fetch. This keeps the SQL parameterized and safe.

    C-03: mirrors fetch_by_query_text_hash M-03: a single malformed row
    does not abort the scan. Each row is wrapped in try/except; malformed
    rows are logged as WARNING and skipped; remaining valid rows are returned.

    Parameters
    ----------
    conn:
        An open connection (audit or write).
    outcome:
        The exact outcome string to match (e.g., ``"refused_skill_unavailable"``).

    Returns
    -------
    list[SignedReceipt]
        0 or more receipts, in insertion order.
    """
    cursor = conn.execute(
        f"SELECT {_RECEIPT_SELECT} FROM receipts WHERE outcome = ?",
        (outcome,),
    )
    results: list[SignedReceipt] = []
    for row in cursor:
        try:
            receipt = _row_to_receipt(row)
        except ReceiptPersistenceError as exc:
            _logger.warning(
                "Skipping malformed receipt row in fetch_by_outcome: %s",
                exc,
                exc_info=exc,
            )
            continue
        results.append(receipt)
    return results


def fetch_by_order_id(
    conn: sqlite3.Connection,
    order_id: str,
) -> list[SignedReceipt]:
    """Return all receipts whose order_id column matches *order_id*.

    Only exact-match receipts are returned; receipts with a NULL order_id
    are never returned by this function. Mirrors the M-03 / C-03 pattern
    from fetch_by_outcome: malformed rows are logged as WARNING and skipped
    so a single bad row does not abort the scan.

    Parameters
    ----------
    conn:
        An open connection (audit or write).
    order_id:
        The synthetic LIS order identifier to match.

    Returns
    -------
    list[SignedReceipt]
        0 or more receipts, in insertion order.
    """
    cursor = conn.execute(
        f"SELECT {_RECEIPT_SELECT} FROM receipts WHERE order_id = ?",
        (order_id,),
    )
    results: list[SignedReceipt] = []
    for row in cursor:
        try:
            receipt = _row_to_receipt(row)
        except ReceiptPersistenceError as exc:
            _logger.warning(
                "Skipping malformed receipt row in fetch_by_order_id: %s",
                exc,
                exc_info=exc,
            )
            continue
        results.append(receipt)
    return results


def fetch_by_id(conn: sqlite3.Connection, receipt_id: str) -> SignedReceipt | None:
    """Return the receipt with the given id, or None if not present.

    Parameters
    ----------
    conn:
        An open connection (audit or write).
    receipt_id:
        The ULID receipt identifier to look up.

    Returns
    -------
    SignedReceipt | None
        The matching receipt, or None if no receipt has that id.
    """
    cursor = conn.execute(
        f"SELECT {_RECEIPT_SELECT} FROM receipts WHERE receipt_id = ? LIMIT 1",
        (receipt_id,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return _row_to_receipt(row)


def fetch_paginated(
    conn: sqlite3.Connection,
    *,
    offset: int,
    limit: int,
) -> list[SignedReceipt]:
    """Return a page of receipts ordered by (signed_at_utc DESC, receipt_id DESC).

    The secondary sort on receipt_id is the stable tie-break for receipts
    emitted in the same second. signed_at_utc is an ISO-8601 string with a
    +00:00 suffix, so lexicographic order equals chronological order.

    Parameters
    ----------
    conn:
        An open connection (audit or write).
    offset:
        Number of rows to skip (0-based).
    limit:
        Maximum number of rows to return.

    Returns
    -------
    list[SignedReceipt]
        0 or more receipts, newest first.
    """
    cursor = conn.execute(
        f"SELECT {_RECEIPT_SELECT} "
        "FROM receipts "
        "ORDER BY signed_at_utc DESC, receipt_id DESC "
        "LIMIT ? OFFSET ?",
        (limit, offset),
    )
    results: list[SignedReceipt] = []
    for row in cursor.fetchall():
        try:
            results.append(_row_to_receipt(row))
        except ReceiptPersistenceError as exc:
            _logger.warning(
                "Skipping malformed receipt row in fetch_paginated: %s",
                exc,
                exc_info=exc,
            )
    return results


def count_total(conn: sqlite3.Connection) -> int:
    """Return the total number of receipts in the store.

    Parameters
    ----------
    conn:
        An open connection (audit or write).

    Returns
    -------
    int
        The row count of the receipts table (0 for an empty store).
    """
    cursor = conn.execute("SELECT COUNT(*) FROM receipts")
    row = cursor.fetchone()
    return int(row[0])


def _validate_utc(dt: datetime, param_name: str) -> None:
    """Raise ValueError if *dt* is naive or not UTC."""
    if not _is_utc(dt):
        raise ValueError(
            f"{param_name} must be a tz-aware UTC datetime; got {dt!r}. "
            "Use datetime.now(tz=timezone.utc) or datetime(..., tzinfo=timezone.utc)."
        )
