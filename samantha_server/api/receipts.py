"""GET /receipts/{receipt_id} and GET /receipts pagination endpoints.

Phase 3 Step 5.5 — GH-120.

Both routes require the ``receipts:read`` RBAC capability. The 404 for an
unknown receipt_id is returned only after RBAC verification succeeds — an
unauthenticated request returns 401 regardless of whether the id exists,
preventing the endpoint from acting as an existence oracle.

No LLM imports. Deterministic path only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from samantha_server.api.rbac import require_capability
from samantha_server.receipts import audit
from samantha_server.receipts.signing import SignedReceipt

if TYPE_CHECKING:
    from fastapi import FastAPI

    from samantha_server.api.lifespan import AppState

# Upper bound on the offset query parameter (exclusive); values above this
# return 400 invalid_pagination. Chosen to be safely within SQLite's OFFSET
# range while preventing trivially-large cursor walks.
_MAX_OFFSET = 10_000_000


class ReceiptListResponse(BaseModel):
    """Typed response envelope for GET /receipts.

    Provides mypy + OpenAPI a typed surface. Receipt dicts are
    PHI-redacted via _receipt_to_json_dict before being placed in
    ``receipts``.
    """

    receipts: list[dict[str, Any]]
    offset: int
    limit: int
    total: int


def _receipt_to_json_dict(receipt: SignedReceipt) -> dict[str, Any]:
    """Serialize a SignedReceipt as a PHI-redacted JSON dict.

    Excludes EngineDecision.decision_traces and EngineDecision.primitive_traces
    from the wire shape — these can carry verbatim clinical strings via
    CanonicalizationTrace.raw_value and PrimitiveTrace.actual. The receipt
    store retains them in full for in-process auditing; HTTP consumers
    receive only the PHI-safe surface fields.

    ``signature`` is hex-encoded (Ed25519 detached, 64 bytes) per the
    Phase-2 schema contract. ``signed_at_utc`` is rendered as ISO-8601.
    """
    raw = receipt.model_dump(
        mode="python",
        exclude={"decision": {"decision_traces", "primitive_traces"}},
    )
    raw["signature"] = receipt.signature.hex()
    raw["signed_at_utc"] = receipt.signed_at_utc.isoformat()
    return raw


def register_receipts_routes(app: FastAPI) -> None:
    """Register GET /receipts/{receipt_id} and GET /receipts on *app*.

    Both routes depend on ``require_capability("receipts:read")``, which
    raises _RBACUnauthorized (→ 401) or _RBACForbidden (→ 403) before the
    handler body runs. The 404 path is therefore only reachable by an
    authenticated caller.
    """

    @app.get(
        "/receipts/{receipt_id}",
        dependencies=[Depends(require_capability("receipts:read"))],
    )
    async def get_receipt_by_id(receipt_id: str, request: Request) -> JSONResponse:
        """Return a single receipt by id.

        Responses
        ---------
        200: SignedReceipt JSON (Phase-2 shape).
        401: missing/invalid/expired token.
        403: valid token with wrong capability.
        404: no receipt with that id (only reachable after RBAC succeeds).
        """
        state: AppState = request.app.state.engine
        receipt = audit.fetch_by_id(state.receipt_audit_conn, receipt_id)
        if receipt is None:
            return JSONResponse({"error": "receipt_not_found"}, status_code=404)
        return JSONResponse(_receipt_to_json_dict(receipt), status_code=200)

    @app.get(
        "/receipts",
        dependencies=[Depends(require_capability("receipts:read"))],
    )
    async def list_receipts(
        request: Request,
        offset: int = 0,
        limit: int = 50,
    ) -> JSONResponse:
        """Return a paginated list of receipts, newest first.

        Parameters
        ----------
        offset:
            Number of rows to skip (default 0). Must be >= 0 and <= _MAX_OFFSET.
        limit:
            Maximum number of rows to return (default 50, max 200). Must be >= 1.

        Pagination validation is hand-rolled rather than via FastAPI
        Query(ge=0, le=200) because the spec mandates 400 invalid_pagination,
        not 422 type-validation; the static body shape is part of the API
        contract.

        Consistency note: fetch_paginated and count_total are wrapped in a
        single SQLite transaction (BEGIN DEFERRED via ``with conn:``) so that
        a concurrent WAL writer cannot produce ``len(receipts) + offset > total``
        inconsistency within a single response. The audit connection has
        query_only=1, so the transaction is read-only and composes safely.

        Responses
        ---------
        200: ReceiptListResponse JSON {receipts: [...], offset: int, limit: int, total: int}.
        400: offset < 0, offset > _MAX_OFFSET, limit < 1, or limit > 200.
        401: missing/invalid/expired token.
        403: valid token with wrong capability.
        422: non-integer offset or limit (FastAPI/pydantic default).
        """
        if offset < 0 or offset > _MAX_OFFSET or limit < 1 or limit > 200:
            return JSONResponse({"error": "invalid_pagination"}, status_code=400)

        state: AppState = request.app.state.engine
        conn = state.receipt_audit_conn
        # Wrap both reads in a single transaction to close the TOCTOU window
        # between fetch_paginated and count_total (concurrent WAL writer).
        with conn:
            receipts = audit.fetch_paginated(conn, offset=offset, limit=limit)
            total = audit.count_total(conn)

        body = ReceiptListResponse(
            receipts=[_receipt_to_json_dict(r) for r in receipts],
            offset=offset,
            limit=limit,
            total=total,
        )
        return JSONResponse(body.model_dump(), status_code=200)


__all__ = ["ReceiptListResponse", "register_receipts_routes"]
