"""Tests for _BodyCapMiddleware ASGI middleware — Slice 5."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_minimal_test_app(max_bytes: int = 100) -> FastAPI:
    """Build a minimal FastAPI app with the body-cap middleware wired."""
    from samantha_server.api.app import RequestIDMiddleware, _BodyCapMiddleware

    app = FastAPI()

    @app.post("/test")
    async def _stub() -> dict[str, str]:
        return {"ok": "true"}

    @app.get("/test")
    async def _stub_get() -> dict[str, str]:
        return {"ok": "true"}

    # Register middleware LIFO: inner first, outer last.
    app.add_middleware(_BodyCapMiddleware, max_request_body_bytes=max_bytes)
    app.add_middleware(RequestIDMiddleware)

    return app


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_post_at_content_length_limit_passes_through() -> None:
    """POST with Content-Length exactly at max → 200 from the stub route."""
    max_bytes = 100
    app = _make_minimal_test_app(max_bytes)
    client = TestClient(app, raise_server_exceptions=False)

    body = b"x" * max_bytes
    resp = client.post(
        "/test",
        content=body,
        headers={"Content-Length": str(max_bytes), "Content-Type": "text/plain"},
    )
    assert resp.status_code == 200


def test_post_over_content_length_limit_returns_413() -> None:
    """POST with Content-Length > max → 413."""
    max_bytes = 100
    app = _make_minimal_test_app(max_bytes)
    client = TestClient(app, raise_server_exceptions=False)

    body = b"x" * (max_bytes + 1)
    resp = client.post(
        "/test",
        content=body,
        headers={"Content-Length": str(max_bytes + 1), "Content-Type": "text/plain"},
    )
    assert resp.status_code == 413


def test_413_carries_x_request_id_header() -> None:
    """413 response carries X-Request-ID header."""
    max_bytes = 100
    app = _make_minimal_test_app(max_bytes)
    client = TestClient(app, raise_server_exceptions=False)

    body = b"x" * (max_bytes + 1)
    resp = client.post(
        "/test",
        content=body,
        headers={"Content-Length": str(max_bytes + 1)},
    )
    assert resp.status_code == 413
    assert "x-request-id" in {k.lower() for k in resp.headers}


def test_413_body_does_not_contain_rejected_payload() -> None:
    """413 body must not echo any portion of the rejected request payload."""
    max_bytes = 10
    app = _make_minimal_test_app(max_bytes)
    client = TestClient(app, raise_server_exceptions=False)

    secret_payload = b"SECRET_CLINICAL_DATA_ABCDEF"
    resp = client.post(
        "/test",
        content=secret_payload,
        headers={"Content-Length": str(len(secret_payload))},
    )
    assert resp.status_code == 413
    # The rejected payload must not appear in the response body
    assert b"SECRET_CLINICAL_DATA_ABCDEF" not in resp.content
    assert b"SECRET" not in resp.content


def test_413_body_contains_error_and_max_bytes() -> None:
    """413 body is structured JSON with 'error' and 'max_request_body_bytes' keys."""
    max_bytes = 50
    app = _make_minimal_test_app(max_bytes)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.post(
        "/test",
        content=b"x" * 51,
        headers={"Content-Length": "51"},
    )
    assert resp.status_code == 413
    body = resp.json()
    assert body["error"] == "payload_too_large"
    assert body["max_request_body_bytes"] == max_bytes
    assert "request_id" in body


def test_post_with_chunked_transfer_encoding_returns_411() -> None:
    """POST with Transfer-Encoding: chunked and no Content-Length → 411."""
    max_bytes = 1024
    app = _make_minimal_test_app(max_bytes)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.post(
        "/test",
        content=b"some data",
        headers={"Transfer-Encoding": "chunked"},
    )
    assert resp.status_code == 411


def test_411_body_has_length_required_error() -> None:
    """411 body has error='length_required'."""
    max_bytes = 1024
    app = _make_minimal_test_app(max_bytes)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.post(
        "/test",
        content=b"some data",
        headers={"Transfer-Encoding": "chunked"},
    )
    assert resp.status_code == 411
    body = resp.json()
    assert body["error"] == "length_required"
    assert "request_id" in body


def test_post_with_no_content_length_no_chunked_passes_through() -> None:
    """POST with neither Content-Length nor Transfer-Encoding → passes through (zero-byte)."""
    max_bytes = 100
    app = _make_minimal_test_app(max_bytes)
    client = TestClient(app, raise_server_exceptions=False)

    # No Content-Length or Transfer-Encoding → treated as zero-byte body
    resp = client.post(
        "/test",
        headers={"Content-Type": "application/json"},
    )
    # Should not be 413 or 411 (the route itself handles the empty body)
    assert resp.status_code not in (411, 413)


def test_negative_content_length_returns_400() -> None:
    """POST with negative Content-Length → 400 Bad Request.

    Issue #21: negative Content-Length was silently treated as 0 (pass-through).
    Any value < 0 is invalid per RFC 7230 and must be rejected explicitly.
    """
    max_bytes = 100
    app = _make_minimal_test_app(max_bytes)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.post(
        "/test",
        content=b"some data",
        headers={"Content-Length": "-1"},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"] == "invalid_content_length"
    assert "request_id" in body


def test_non_integer_content_length_passes_through() -> None:
    """POST with non-integer Content-Length → middleware passes through (treated as 0 bytes)."""
    max_bytes = 100
    app = _make_minimal_test_app(max_bytes)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.post(
        "/test",
        content=b"hello",
        headers={"Content-Length": "not-a-number", "Content-Type": "text/plain"},
    )
    # Non-integer is treated as zero → passes through (not 413 or 400)
    assert resp.status_code not in (400, 413)


def test_chunked_plus_content_length_returns_411() -> None:
    """POST with both Transfer-Encoding: chunked and Content-Length → 411.

    The chunked check takes priority; presence of Content-Length is irrelevant.
    """
    max_bytes = 100
    app = _make_minimal_test_app(max_bytes)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.post(
        "/test",
        content=b"some data",
        headers={"Transfer-Encoding": "chunked", "Content-Length": "9"},
    )
    assert resp.status_code == 411


def test_get_request_middleware_ignores() -> None:
    """GET requests bypass the body-cap middleware entirely."""
    max_bytes = 100
    app = _make_minimal_test_app(max_bytes)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.get("/test")
    assert resp.status_code == 200
