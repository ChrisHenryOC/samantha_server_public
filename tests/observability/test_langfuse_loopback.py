"""Loopback smoke test for the Langfuse health endpoint.

Defends the dual-stack landmine where ``localhost`` resolves to ``::1``
while the docker-compose binds IPv4-only. The OTel exporter and the
/readyz probe both connect via the configured ``LANGFUSE_BASE_URL``;
if name resolution and bind interface disagree, every outbound call
fails silently and the operator only finds out from a missing
``trace_url`` field on the next decision.

Excluded from CI (no Langfuse in the CI environment) via the
``langfuse`` marker. Run as a release-checklist item after
``docker compose up`` and before the orchestrator goes live:

    uv run pytest -m langfuse

The test fails closed: if the loopback path is broken or the server
isn't running, the smoke test fails. The deployment doc names this
test as a manual gate; the test itself is the automation.
"""

from __future__ import annotations

import asyncio
import os

import pytest

pytestmark = pytest.mark.langfuse


def test_langfuse_health_endpoint_responds_200() -> None:
    """``GET {LANGFUSE_BASE_URL}/api/public/health`` returns 200 on the deploy target."""
    base_url = os.environ.get("LANGFUSE_BASE_URL", "http://localhost:3000")

    from samantha_server.observability.cached_probe import _langfuse_probe

    result = asyncio.run(_langfuse_probe(base_url))
    assert result.get("reachable") is True, (
        f"Langfuse health probe at {base_url} failed: {result!r}. "
        "Common cause: dual-stack DNS resolves localhost → ::1 while "
        "docker-compose binds IPv4-only on 127.0.0.1. Verify with: "
        "  docker compose ps     # confirm running\n"
        f"  curl -v {base_url}/api/public/health"
    )
