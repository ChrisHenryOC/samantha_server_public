"""Lifespan-integration tests for the Langfuse wiring (GH-124).

Covers:

- M10: ``build_app_state`` selects the correct probe type based on
  ``cfg.LANGFUSE_ENABLED`` (real probe vs stub).
- M11: the ordering invariant — ``OTEL_EXPORTER_OTLP_HEADERS`` is
  populated by the time ``build_app_state`` returns, i.e., before
  any subsequent ``configure_otel`` would construct the OTLP
  exporter.

Both go through the production ``build_app_state`` factory rather
than calling helpers in isolation, so a future refactor that
reorders or drops the steps would be caught.
"""

from __future__ import annotations

import asyncio
import base64
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _build_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    enabled: bool,
    public_key: str = "pk",
    secret_key: str = "sk",
) -> object:
    """Run ``build_app_state`` with a stubbed LLM client and a temp DB."""
    monkeypatch.setenv("RECEIPTS_DB_PATH", str(tmp_path / "r.db"))

    import samantha_server.config as cfg

    monkeypatch.setattr(cfg, "RECEIPTS_DB_PATH", str(tmp_path / "r.db"))
    monkeypatch.setattr(cfg, "LANGFUSE_ENABLED", enabled)
    monkeypatch.setattr(cfg, "LANGFUSE_PUBLIC_KEY", public_key)
    monkeypatch.setattr(cfg, "LANGFUSE_SECRET_KEY", secret_key)

    mock_llm = MagicMock()
    mock_llm.model_id = "test-model"

    from samantha_server.api.lifespan import build_app_state

    return build_app_state(_llm_client_override=mock_llm)


def test_build_app_state_uses_stub_probe_when_langfuse_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """M10: ENABLED=false → state.langfuse_probe is the stub probe.

    The stub returns ``{"reachable": False, "reason": "not_configured"}``
    without making a network call.
    """
    state = _build_state(monkeypatch, tmp_path, enabled=False)

    result = asyncio.run(state.langfuse_probe.current())  # type: ignore[attr-defined]

    assert result == {"reachable": False, "reason": "not_configured"}


def test_build_app_state_uses_real_probe_when_langfuse_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """M10: ENABLED=true → state.langfuse_probe is the real probe.

    The real probe makes an outbound call (mocked here). Distinguishing
    feature vs the stub: it does NOT report ``"not_configured"``; it
    reports ``"reachable": True`` (or a connect-error reason) depending
    on the mocked transport.
    """
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    monkeypatch.setenv("ALLOW_REAL_NETWORK", "1")

    response = MagicMock()
    response.status_code = 200
    mock_client = MagicMock()
    mock_client.__aenter__ = _async_value(mock_client)
    mock_client.__aexit__ = _async_value(False)
    mock_client.get = _async_value(response)

    from unittest.mock import patch

    with patch("httpx.AsyncClient", return_value=mock_client):
        state = _build_state(monkeypatch, tmp_path, enabled=True)
        result = asyncio.run(state.langfuse_probe.current())  # type: ignore[attr-defined]

    assert result == {"reachable": True}


def test_build_app_state_seeds_otlp_headers_before_returning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """M11: OTEL_EXPORTER_OTLP_HEADERS is populated by the time build_app_state returns.

    A future refactor that moves ``_seed_langfuse_otlp_headers`` after
    the probe construction (or drops the call entirely) would leave
    the env var unset by the time ``configure_otel`` constructs the
    OTLP exporter — silently dropping the auth header.
    """
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_HEADERS", raising=False)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")

    _build_state(monkeypatch, tmp_path, enabled=True, public_key="pk-x", secret_key="sk-x")

    encoded = base64.b64encode(b"pk-x:sk-x").decode("ascii")
    expected = f"Authorization=Basic {encoded}"
    assert os.environ["OTEL_EXPORTER_OTLP_HEADERS"] == expected


def test_build_app_state_disabled_does_not_seed_otlp_headers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """M11 (negative): ENABLED=false → no OTEL_EXPORTER_OTLP_HEADERS write."""
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_HEADERS", raising=False)

    _build_state(monkeypatch, tmp_path, enabled=False)

    assert "OTEL_EXPORTER_OTLP_HEADERS" not in os.environ


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _async_value(value: object) -> object:
    """Return an AsyncMock that resolves to ``value`` (or returns it from __aexit__)."""
    from unittest.mock import AsyncMock

    return AsyncMock(return_value=value)
