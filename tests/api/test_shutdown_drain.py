"""Tests for graceful shutdown — lifespan drain trigger.

PR #131 C3 fix-review: an explicit ``signal.signal(SIGTERM, ...)``
handler is overwritten by Uvicorn at startup, so the drain flag never
flipped in production. The fix moves the flag into the lifespan's
``finally`` clause — which Uvicorn invokes on its own SIGTERM handling.
The tests below exercise the lifespan-shutdown path directly via
TestClient and assert that ``is_draining`` becomes True before resources
are released.
"""

from __future__ import annotations

from tests.api.helpers import make_minimal_app_state


def test_is_draining_defaults_to_false() -> None:
    """AppState.is_draining starts as False."""
    state = make_minimal_app_state()
    assert state.is_draining is False


def test_is_draining_can_be_set_true() -> None:
    """AppState.is_draining can be flipped at runtime."""
    state = make_minimal_app_state()
    state.is_draining = True
    assert state.is_draining is True


def test_lifespan_finally_sets_is_draining_true() -> None:
    """Lifespan shutdown flips is_draining=True before aclose runs.

    PR #131 C3 regression: the previous SIGTERM-handler approach never
    fired under Uvicorn (Uvicorn replaced the handler). Wiring drain to
    lifespan-finally ensures the flag flips on every controlled shutdown
    regardless of how it was triggered.

    We stub ``build_app_state`` so the lifespan doesn't try to load real
    LLM weights; the assertion is about the lifespan-finally drain hook,
    not about lifespan boot.
    """
    import asyncio
    from unittest.mock import patch

    from samantha_server.api.app import lifespan

    stub_state = make_minimal_app_state()

    class _StubApp:
        class _State:
            engine: object = None

        state = _State()

    stub_app = _StubApp()
    captured: dict[str, object] = {}

    async def run_lifespan() -> None:
        with patch(
            "samantha_server.api.lifespan.build_app_state",
            return_value=stub_state,
        ):
            async with lifespan(stub_app):  # type: ignore[arg-type]
                captured["pre_shutdown_draining"] = stub_state.is_draining

    asyncio.run(run_lifespan())

    assert captured["pre_shutdown_draining"] is False
    assert stub_state.is_draining is True


def test_readyz_returns_503_when_draining() -> None:
    """GET /readyz returns 503 when is_draining is True."""
    from fastapi.testclient import TestClient

    from samantha_server.api.app import create_app

    app = create_app()
    state = make_minimal_app_state()
    state.is_draining = True
    app.state.engine = state

    client = TestClient(app, raise_server_exceptions=True)
    response = client.get("/readyz")
    assert response.status_code == 503
