"""Tests for the real Langfuse probe."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _build_mock_client(*, get_return: Any = None, get_side_effect: Any = None) -> MagicMock:
    """Build a MagicMock that satisfies httpx.AsyncClient's ``async with`` contract."""
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    if get_side_effect is not None:
        mock_client.get = AsyncMock(side_effect=get_side_effect)
    else:
        mock_client.get = AsyncMock(return_value=get_return)
    return mock_client


def test_langfuse_probe_reports_reachable_on_200() -> None:
    """A 200 from /api/public/health → reachable: True."""
    from samantha_server.observability.cached_probe import _langfuse_probe

    response = MagicMock()
    response.status_code = 200
    mock_client = _build_mock_client(get_return=response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = asyncio.run(_langfuse_probe("http://localhost:3000"))

    assert result == {"reachable": True}
    mock_client.get.assert_awaited_once_with("http://localhost:3000/api/public/health")


def test_langfuse_probe_reports_unreachable_on_non_200() -> None:
    """A non-200 status → reachable: False with the http_<code> reason."""
    from samantha_server.observability.cached_probe import _langfuse_probe

    response = MagicMock()
    response.status_code = 503
    mock_client = _build_mock_client(get_return=response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = asyncio.run(_langfuse_probe("http://localhost:3000"))

    assert result == {"reachable": False, "reason": "http_503"}


def test_langfuse_probe_handles_connect_error() -> None:
    """Connection refused / DNS failure → reachable: False, reason carries the type."""
    import httpx

    from samantha_server.observability.cached_probe import _langfuse_probe

    mock_client = _build_mock_client(get_side_effect=httpx.ConnectError("refused"))

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = asyncio.run(_langfuse_probe("http://localhost:3000"))

    assert result["reachable"] is False
    assert "ConnectError" in result["reason"]


def test_langfuse_probe_handles_invalid_url() -> None:
    """L12: httpx.InvalidURL escapes RequestError; the probe must catch it too."""
    import httpx

    from samantha_server.observability.cached_probe import _langfuse_probe

    mock_client = _build_mock_client(get_side_effect=httpx.InvalidURL("malformed"))

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = asyncio.run(_langfuse_probe("http://localhost:3000"))

    assert result["reachable"] is False
    assert "InvalidURL" in result["reason"]


def test_langfuse_probe_handles_aenter_oserror() -> None:
    """M9: a transport-init OSError (e.g., TLS layer) is caught, not propagated."""
    from samantha_server.observability.cached_probe import _langfuse_probe

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(side_effect=OSError("transport init failed"))
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = asyncio.run(_langfuse_probe("http://localhost:3000"))

    assert result["reachable"] is False
    assert "OSError" in result["reason"]


def test_make_langfuse_probe_caches() -> None:
    """The cached probe runs the underlying probe at most once per TTL window."""
    from samantha_server.observability.cached_probe import make_langfuse_probe

    response = MagicMock()
    response.status_code = 200
    mock_client = _build_mock_client(get_return=response)

    async def _drive() -> tuple[dict[str, Any], dict[str, Any]]:
        probe = make_langfuse_probe(base_url="http://localhost:3000", ttl_sec=60.0)
        a = await probe.current()
        b = await probe.current()
        return a, b

    with patch("httpx.AsyncClient", return_value=mock_client):
        result_a, result_b = asyncio.run(_drive())

    assert result_a == {"reachable": True}
    assert result_b == {"reachable": True}
    # Cached: get called once, not twice.
    assert mock_client.get.await_count == 1


def test_make_langfuse_probe_signature() -> None:
    """Importable + returns a CachedProbe."""
    from samantha_server.observability.cached_probe import CachedProbe, make_langfuse_probe

    probe = make_langfuse_probe(base_url="http://localhost:3000", ttl_sec=7.0)
    assert isinstance(probe, CachedProbe)


def test_langfuse_probe_strips_trailing_slash_from_base_url() -> None:
    """``http://x:3000/`` is normalized so we don't double-slash the endpoint."""
    from samantha_server.observability.cached_probe import _langfuse_probe

    response = MagicMock()
    response.status_code = 200
    mock_client = _build_mock_client(get_return=response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        asyncio.run(_langfuse_probe("http://localhost:3000/"))

    mock_client.get.assert_awaited_once_with("http://localhost:3000/api/public/health")


@pytest.fixture(autouse=True)
def _allow_real_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """The conftest no-network guard would trip even on mocked httpx clients.

    Setting ALLOW_REAL_NETWORK=1 disables the guard for this test
    module — safe because every test patches ``httpx.AsyncClient``,
    so no real socket is opened.
    """
    monkeypatch.setenv("ALLOW_REAL_NETWORK", "1")
    return None
