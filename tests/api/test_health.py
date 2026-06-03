"""Tests for samantha_server.api.health — /healthz, /readyz, /version endpoints."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.api.helpers import make_minimal_app_state

_VERSION_TEST_KEY = bytes.fromhex("cafebabe" + "deadbeef" * 6 + "cafebabe")


def _make_health_read_token() -> str:
    """Build a valid health:read token for test use."""
    from samantha_server.api.rbac import _sign_token

    now = int(time.time())
    return _sign_token("health:read", now, now + 3600, hmac_key=_VERSION_TEST_KEY)


def _make_test_app() -> FastAPI:
    """Build a test FastAPI app with health routes and a healthy AppState.

    GH-119: registers RBAC exception handlers so /version 401/403 responses
    render correctly. Tests for /version must supply a valid health:read
    Bearer token.
    """

    from samantha_server.api.health import register_health_routes
    from samantha_server.api.rbac import register_rbac_exception_handlers

    app = FastAPI()
    app.state.engine = make_minimal_app_state()
    register_rbac_exception_handlers(app)
    register_health_routes(app)

    # Patch _get_rbac_hmac_key globally so tests using _make_test_app get
    # the test key rather than the conftest sentinel. This is app-local and
    # does not affect other test files.
    # NOTE: patching is done by the individual test helpers rather than here
    # because pytest-level patching during app construction is fragile.
    return app


def _version_get(app: FastAPI) -> Any:
    """GET /version with a valid health:read token, with RBAC key patched.

    GH-119: /version now requires health:read. Use this helper instead of
    calling client.get("/version") directly to avoid 401 failures.
    """
    token = _make_health_read_token()
    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_VERSION_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=True)
        return client.get("/version", headers={"Authorization": f"Bearer {token}"})


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    """Pin LANGFUSE_ENABLED=false and clear DRIFT_ALARM_WEBHOOK_URL for deterministic /readyz.

    Pinning "false" (rather than deleting) is load-bearing: cfg now defaults
    LANGFUSE_ENABLED to true, so a deleted var lets config's first import in a
    test resolve to true (keys are present from .env), which then leaks via
    monkeypatch.setattr save/restore into sibling tests.
    """
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    monkeypatch.delenv("DRIFT_ALARM_WEBHOOK_URL", raising=False)
    yield monkeypatch


# ---------------------------------------------------------------------------
# /healthz
# ---------------------------------------------------------------------------


def test_healthz_returns_200() -> None:
    client = TestClient(_make_test_app(), raise_server_exceptions=True)
    response = client.get("/healthz")
    assert response.status_code == 200


def test_healthz_head_returns_200() -> None:
    client = TestClient(_make_test_app())
    response = client.head("/healthz")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# /readyz — happy path
# ---------------------------------------------------------------------------


def test_readyz_returns_200_when_healthy(clean_env: pytest.MonkeyPatch) -> None:
    client = TestClient(_make_test_app())
    response = client.get("/readyz")
    assert response.status_code == 200


def test_readyz_head_returns_200(clean_env: pytest.MonkeyPatch) -> None:
    """PR #131 test-cov L-01: HEAD /readyz coverage symmetric to /healthz."""
    client = TestClient(_make_test_app())
    response = client.head("/readyz")
    assert response.status_code == 200


def test_readyz_body_has_status_ok(clean_env: pytest.MonkeyPatch) -> None:
    client = TestClient(_make_test_app())
    response = client.get("/readyz")
    assert response.json()["status"] == "ok"


def test_readyz_body_always_has_components_block(clean_env: pytest.MonkeyPatch) -> None:
    client = TestClient(_make_test_app())
    data = client.get("/readyz").json()
    assert "components" in data
    assert "langfuse" in data["components"]
    assert "drift_webhook" in data["components"]


def test_readyz_components_langfuse_shape(clean_env: pytest.MonkeyPatch) -> None:
    client = TestClient(_make_test_app())
    langfuse = client.get("/readyz").json()["components"]["langfuse"]
    assert "enabled" in langfuse
    assert "reachable" in langfuse
    assert "last_probe_age_sec" in langfuse


def test_readyz_components_drift_webhook_shape(clean_env: pytest.MonkeyPatch) -> None:
    """PR #131 test-cov L-02: drift_webhook component shape symmetric to langfuse."""
    client = TestClient(_make_test_app())
    drift = client.get("/readyz").json()["components"]["drift_webhook"]
    assert "configured" in drift
    assert "last_failure_age_sec" in drift


def test_readyz_omits_counters_when_all_zero(clean_env: pytest.MonkeyPatch) -> None:
    client = TestClient(_make_test_app())
    assert "counters" not in client.get("/readyz").json()


def test_readyz_includes_counters_when_nonzero(clean_env: pytest.MonkeyPatch) -> None:
    app = _make_test_app()
    app.state.engine.counters.receipt_signing_failures.increment()
    client = TestClient(app)
    data = client.get("/readyz").json()
    assert "counters" in data
    assert data["counters"]["receipt_signing_failures"] == 1


def test_readyz_503_when_draining(clean_env: pytest.MonkeyPatch) -> None:
    app = _make_test_app()
    app.state.engine.is_draining = True
    client = TestClient(app, raise_server_exceptions=True)
    response = client.get("/readyz")
    assert response.status_code == 503


# ---------------------------------------------------------------------------
# /readyz — two-tier readiness model (PR #131 H3)
# ---------------------------------------------------------------------------


def test_readyz_503_when_receipt_writer_closed(clean_env: pytest.MonkeyPatch) -> None:
    """503 when the SQLite receipt-store connection is closed (load-bearing dep)."""
    app = _make_test_app()
    app.state.engine.receipt_writer.close()
    client = TestClient(app, raise_server_exceptions=True)
    response = client.get("/readyz")
    assert response.status_code == 503
    body = response.json()
    assert "receipt_writer_sqlite" in body["failed_components"]


def test_readyz_503_when_rule_index_missing(clean_env: pytest.MonkeyPatch) -> None:
    """503 when the rule_index is None (engine indices are load-bearing)."""
    app = _make_test_app()
    app.state.engine.rule_index = None
    client = TestClient(app, raise_server_exceptions=True)
    response = client.get("/readyz")
    assert response.status_code == 503
    assert "rule_index" in response.json()["failed_components"]


def test_readyz_components_langfuse_enabled_reflects_cfg(
    clean_env: pytest.MonkeyPatch,
) -> None:
    """components.langfuse.enabled is read from cfg.LANGFUSE_ENABLED (PR #145 L15).

    Previously this read live from os.environ, which let /readyz drift
    from the lifespan-bound probe selection. Standardised on cfg now.
    """
    import samantha_server.config as cfg

    clean_env.setattr(cfg, "LANGFUSE_ENABLED", True)
    client = TestClient(_make_test_app())
    langfuse = client.get("/readyz").json()["components"]["langfuse"]
    assert langfuse["enabled"] is True


def test_readyz_components_langfuse_disabled_when_cfg_false(
    clean_env: pytest.MonkeyPatch,
) -> None:
    """components.langfuse.enabled is False when cfg.LANGFUSE_ENABLED is False."""
    import samantha_server.config as cfg

    clean_env.setattr(cfg, "LANGFUSE_ENABLED", False)
    client = TestClient(_make_test_app())
    langfuse = client.get("/readyz").json()["components"]["langfuse"]
    assert langfuse["enabled"] is False


def test_readyz_components_drift_webhook_configured_reflects_env(
    clean_env: pytest.MonkeyPatch,
) -> None:
    """components.drift_webhook.configured is True when DRIFT_ALARM_WEBHOOK_URL is set."""
    clean_env.setenv("DRIFT_ALARM_WEBHOOK_URL", "http://lab.example/drift")
    client = TestClient(_make_test_app())
    drift = client.get("/readyz").json()["components"]["drift_webhook"]
    assert drift["configured"] is True


def test_readyz_invokes_cached_probe(clean_env: pytest.MonkeyPatch) -> None:
    """/readyz calls CachedProbe.current() so the TTL/single-flight cache populates.

    PR #131 C2 regression: previously /readyz read ``_cached`` directly
    and never invoked ``current()`` — the probe never ran. This test
    asserts ``current()`` is awaited at least once per /readyz call.
    """
    from unittest.mock import patch

    app = _make_test_app()
    client = TestClient(app)

    with patch.object(
        app.state.engine.langfuse_probe,
        "current",
        wraps=app.state.engine.langfuse_probe.current,
    ) as spy:
        client.get("/readyz")
        assert spy.await_count == 1


def test_readyz_flood_invokes_probe_at_most_once_per_ttl(
    clean_env: pytest.MonkeyPatch,
) -> None:
    """100 sequential /readyz calls produce ≤ 1 outbound probe call.

    PR #131 M9 + plan Step 1 done-when. The cache amortizes the
    underlying probe work across the TTL window.
    """
    from samantha_server.observability.cached_probe import CachedProbe

    app = _make_test_app()

    call_count = {"n": 0}

    async def counting_probe() -> dict[str, object]:
        call_count["n"] += 1
        return {"reachable": False, "reason": "stub"}

    app.state.engine.langfuse_probe = CachedProbe(counting_probe, ttl_sec=60.0)

    client = TestClient(app)
    for _ in range(100):
        client.get("/readyz")

    assert call_count["n"] == 1


# ---------------------------------------------------------------------------
# /version
# ---------------------------------------------------------------------------


def test_version_returns_200(clean_env: pytest.MonkeyPatch) -> None:
    assert _version_get(_make_test_app()).status_code == 200


def test_version_body_has_commit(clean_env: pytest.MonkeyPatch) -> None:
    data = _version_get(_make_test_app()).json()
    assert "commit" in data


def test_version_returns_cached_commit_sha(clean_env: pytest.MonkeyPatch) -> None:
    """/version reads commit_sha from AppState — does not fork a subprocess.

    PR #131 M3 regression: previously each /version call ran ``git
    rev-parse HEAD`` (10–50 ms fork). The SHA is now computed once in
    build_app_state and cached; this test asserts the value comes from
    state, not from a fresh subprocess.
    """
    app = _make_test_app()
    app.state.engine.commit_sha = "deadbeef-cached-sha"
    response = _version_get(app)
    assert response.json()["commit"] == "deadbeef-cached-sha"


def test_version_body_has_config(clean_env: pytest.MonkeyPatch) -> None:
    assert "config" in _version_get(_make_test_app()).json()


def test_version_config_redacts_secret_values(clean_env: pytest.MonkeyPatch) -> None:
    """Any KEY/SALT/SECRET/PASSWORD-named variable is redacted."""
    config_snap = _version_get(_make_test_app()).json()["config"]
    for key, val in config_snap.items():
        upper = key.upper()
        if any(p in upper for p in ("KEY", "SALT", "SECRET", "PASSWORD")):
            assert val == "<redacted>", f"Secret config value {key!r} was not redacted: {val!r}"


def test_version_config_redacts_path_values(clean_env: pytest.MonkeyPatch) -> None:
    """RECEIPTS_DB_PATH and any *_PATH-named variable is redacted (PR #131 H2).

    Filesystem paths leak the OS username and directory layout — the
    plan's PHI-boundary discipline applies the same way.
    """
    config_snap = _version_get(_make_test_app()).json()["config"]
    assert config_snap["RECEIPTS_DB_PATH"] == "<redacted>"


def test_version_config_redacts_path_shaped_string_values(
    clean_env: pytest.MonkeyPatch,
) -> None:
    """Variables whose VALUES contain a path separator are redacted (PR #131 L6).

    Catches LLM_MODEL_NAME defaulting to LLM_MODEL_PATH when the operator
    hasn't set MODEL_NAME explicitly and MODEL_PATH points at a local
    filesystem location.
    """
    import samantha_server.config as cfg

    original = cfg.LLM_MODEL_NAME
    try:
        cfg.LLM_MODEL_NAME = "/Users/alice/models/llama-3b"  # path-shaped
        config_snap = _version_get(_make_test_app()).json()["config"]
        assert config_snap["LLM_MODEL_NAME"] == "<redacted>"
    finally:
        cfg.LLM_MODEL_NAME = original


def test_version_config_does_not_redact_numeric_token_ttls(
    clean_env: pytest.MonkeyPatch,
) -> None:
    """RBAC_TOKEN_TTL_SEC etc. should NOT be redacted just because of "TOKEN" (PR #131 L3).

    Step 5 will introduce these vars; ensure the redaction policy doesn't
    incorrectly hide their integer values when they land. Today the
    snapshot has no TOKEN-named entry, but the assertion below guards
    against a future regression by directly invoking ``_is_secret``.
    """
    from samantha_server.api.health import _is_secret

    assert _is_secret("RBAC_TOKEN_TTL_SEC") is False
    assert _is_secret("DISPATCH_TOKEN_TTL_SEC") is False


def test_get_commit_sha_falls_back_logs_unexpected(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unexpected exceptions in get_commit_sha are logged, not silently absorbed.

    PR #131 M5 regression: previously the bare `except Exception: pass`
    masked any non-FileNotFoundError. Now the broad fallback logs.
    """
    from unittest.mock import patch

    from samantha_server.api.health import get_commit_sha

    with (
        patch("samantha_server.api.health.subprocess.run", side_effect=MemoryError),
        caplog.at_level(logging.ERROR, logger="samantha_server.api.health"),
    ):
        sha = get_commit_sha()

    assert sha == "unknown"
    assert any("Unexpected error" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# /readyz — Step 2: queue load-bearing check (GH-116)
# ---------------------------------------------------------------------------


def test_readyz_503_when_queue_missing(clean_env: pytest.MonkeyPatch) -> None:
    """503 when AppState.queue is None (defensive: should never happen after lifespan).

    The plan's two-tier readiness model names 'queue' as a load-bearing dep
    for Step 2. If queue is None (e.g., a partial-lifespan crash left it
    uninitialized), /readyz surfaces it immediately rather than allowing the
    consumer-side to fail silently.
    """
    app = _make_test_app()
    app.state.engine.queue = None  # type: ignore[assignment]
    client = TestClient(app, raise_server_exceptions=True)
    response = client.get("/readyz")
    assert response.status_code == 503
    assert "queue" in response.json()["failed_components"]
