"""Tests for the trace_url field in POST /events response.

When LANGFUSE_ENABLED=false (test/CI default) the response carries
``trace_url: null``. When enabled, it carries
``{LANGFUSE_BASE_URL}/trace/{trace_id_hex}``.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

# Reuse the test app builder + dispatch helpers from the events-endpoint test.
from tests.api.test_events_endpoint import (
    _EVENTS_TEST_KEY,
    _auto_enqueue,
    _make_auth_headers,
    _make_event_request_body,
    _make_test_app,
)


@pytest.fixture
def _real_tracer_provider() -> Iterator[None]:
    """Install an SDK TracerProvider so spans get real (non-zero) trace IDs.

    Without this, ``trace.get_tracer("samantha_server")`` returns the no-op
    tracer and ``span.get_span_context().trace_id`` is 0 — the previous
    `trace_url` test asserted hex-only on the resulting ``"0" * 32``,
    which passed vacuously (review M7 / workflow M1). We use
    ``configure_otel`` so the same code path that production uses also
    wires the test exporter (and respects the reuse-existing-provider
    semantics if a session-level provider is already installed).
    """
    from samantha_server.observability.counters import CounterRegistry
    from samantha_server.observability.otel import configure_otel

    existing = trace.get_tracer_provider()
    if isinstance(existing, TracerProvider):
        # An SDK provider is already installed (probably by the
        # observability conftest); just use it.
        yield
        return

    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(InMemorySpanExporter()))
    configure_otel(CounterRegistry())  # no-op when the global is already set
    trace.set_tracer_provider(provider)
    yield


def test_trace_url_is_null_when_langfuse_disabled() -> None:
    """LANGFUSE_ENABLED=false → response.trace_url is null."""
    app, _state = _make_test_app()

    import samantha_server.config as cfg

    with (
        patch.object(cfg, "LANGFUSE_ENABLED", False),
        patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_EVENTS_TEST_KEY),
        patch("samantha_server.api.events.enqueue_or_reject", side_effect=_auto_enqueue),
    ):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/events", json=_make_event_request_body(), headers=_make_auth_headers())

    assert resp.status_code == 200
    assert resp.json()["trace_url"] is None


def test_trace_url_uses_langfuse_base_url_when_enabled(
    _real_tracer_provider: None,
) -> None:
    """LANGFUSE_ENABLED=true → trace_url is {BASE_URL}/trace/{trace_id_hex}."""
    app, _state = _make_test_app()

    # Loopback validation rejects non-loopback hosts; pin to a valid one.
    base_url = "http://localhost:9999"

    import samantha_server.config as cfg

    with (
        patch.object(cfg, "LANGFUSE_ENABLED", True),
        patch.object(cfg, "LANGFUSE_BASE_URL", base_url),
        patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_EVENTS_TEST_KEY),
        patch("samantha_server.api.events.enqueue_or_reject", side_effect=_auto_enqueue),
    ):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/events", json=_make_event_request_body(), headers=_make_auth_headers())

    assert resp.status_code == 200
    trace_url = resp.json()["trace_url"]
    assert trace_url is not None
    assert trace_url.startswith(f"{base_url}/trace/")
    # The trace_id segment is 32 hex chars (128-bit OTel trace ID).
    suffix = trace_url.removeprefix(f"{base_url}/trace/")
    assert len(suffix) == 32
    assert all(c in "0123456789abcdef" for c in suffix)
    # M7: the suffix is a real OTel-generated id, not the no-op
    # all-zeros placeholder. The previous hex-only check passed vacuously
    # on "0" * 32 because _make_test_app didn't install a TracerProvider.
    assert int(suffix, 16) != 0, (
        "trace_url suffix is the no-op INVALID_SPAN id (all zeros) — "
        "no real TracerProvider installed for this test"
    )
