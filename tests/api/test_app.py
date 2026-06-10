"""Tests for samantha_server.api.app — FastAPI app factory."""

from __future__ import annotations

from tests.api.helpers import make_minimal_app_state


def test_create_app_returns_fastapi_instance() -> None:
    """create_app() returns a FastAPI instance."""
    from fastapi import FastAPI

    from samantha_server.api.app import create_app

    app = create_app()
    assert isinstance(app, FastAPI)


def test_create_app_has_correct_title() -> None:
    """create_app() sets title to 'samantha_server'."""
    from samantha_server.api.app import create_app

    app = create_app()
    assert app.title == "samantha_server"


def test_create_app_disables_swagger_ui() -> None:
    """create_app() disables Swagger UI (docs_url=None)."""
    from samantha_server.api.app import create_app

    app = create_app()
    # docs_url=None means Swagger UI is disabled
    assert app.docs_url is None


def test_create_app_disables_redoc() -> None:
    """create_app() disables ReDoc (redoc_url=None)."""
    from samantha_server.api.app import create_app

    app = create_app()
    assert app.redoc_url is None


def test_create_app_disables_openapi_url() -> None:
    """create_app ships with openapi_url=None (H5 spike-fallback floor).

    Step 5 will swap to ``/openapi.json`` once the health:read
    RBAC gate exists. Until then, leaving the schema unauthenticated is
    a reconnaissance surface (endpoint paths, future PHI-shaped pydantic
    field names), so the floor is "no schema at all".
    """
    from samantha_server.api.app import create_app

    app = create_app()
    assert app.openapi_url is None


def test_create_app_debug_is_false() -> None:
    """create_app() sets app.debug = False."""
    from samantha_server.api.app import create_app

    app = create_app()
    assert app.debug is False


def test_app_healthz_route_registered() -> None:
    """create_app() registers the /healthz route."""
    from samantha_server.api.app import create_app

    app = create_app()
    paths = [route.path for route in app.routes]  # type: ignore[attr-defined]
    assert "/healthz" in paths


def test_app_readyz_route_registered() -> None:
    """create_app() registers the /readyz route."""
    from samantha_server.api.app import create_app

    app = create_app()
    paths = [route.path for route in app.routes]  # type: ignore[attr-defined]
    assert "/readyz" in paths


def test_app_version_route_registered() -> None:
    """create_app() registers the /version route."""
    from samantha_server.api.app import create_app

    app = create_app()
    paths = [route.path for route in app.routes]  # type: ignore[attr-defined]
    assert "/version" in paths


def test_request_id_middleware_assigns_uuid() -> None:
    """The request_id middleware assigns a valid UUID4 to every request.

     test-cov L4: assert UUID4 *shape* (not just non-empty length)
    so the test fails if the middleware ever emits e.g. a sequence
    counter or empty string.
    """
    import uuid as uuid_module

    from fastapi.testclient import TestClient

    from samantha_server.api.app import create_app

    app = create_app()
    app.state.engine = make_minimal_app_state()

    client = TestClient(app)
    response = client.get("/healthz")
    assert "X-Request-ID" in response.headers
    parsed = uuid_module.UUID(response.headers["X-Request-ID"])
    assert parsed.version == 4


def test_request_id_middleware_ignores_inbound_header() -> None:
    """The request_id middleware ignores inbound X-Request-ID headers.

    The replacement is a fresh UUID4 — not just "not equal to inbound";
     test-cov L4 strengthens this assertion.
    """
    import uuid as uuid_module

    from fastapi.testclient import TestClient

    from samantha_server.api.app import create_app

    app = create_app()
    app.state.engine = make_minimal_app_state()

    client = TestClient(app)
    response = client.get("/healthz", headers={"X-Request-ID": "my-custom-id"})
    assert response.headers["X-Request-ID"] != "my-custom-id"
    parsed = uuid_module.UUID(response.headers["X-Request-ID"])
    assert parsed.version == 4


def test_uncaught_exception_handler_returns_500() -> None:
    """Uncaught exceptions return 500 with error=internal and no traceback."""
    from fastapi.testclient import TestClient

    from samantha_server.api.app import create_app

    app = create_app()
    app.state.engine = make_minimal_app_state()

    # Add a route that raises an uncaught exception
    @app.get("/explode")
    async def explode() -> None:
        raise RuntimeError("intentional test explosion")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/explode")
    assert response.status_code == 500
    data = response.json()
    assert data["error"] == "internal"
    assert "request_id" in data
    # No traceback in the body
    assert "RuntimeError" not in response.text
    assert "Traceback" not in response.text


def test_app_receipts_by_id_route_registered() -> None:
    """create_app() registers the GET /receipts/{receipt_id} route."""
    from samantha_server.api.app import create_app

    app = create_app()
    paths = [route.path for route in app.routes]  # type: ignore[attr-defined]
    assert "/receipts/{receipt_id}" in paths


def test_app_receipts_list_route_registered() -> None:
    """create_app() registers the GET /receipts route."""
    from samantha_server.api.app import create_app

    app = create_app()
    paths = [route.path for route in app.routes]  # type: ignore[attr-defined]
    assert "/receipts" in paths
