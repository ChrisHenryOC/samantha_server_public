"""Test fixtures for OTel observability tests.

Installs a process-global ``TracerProvider`` once (OTel's
``set_tracer_provider`` is one-shot per process via
``_TRACER_PROVIDER_SET_ONCE``) with an ``InMemorySpanExporter`` so
tests can read recorded spans without standing up a collector.

The ``otel_exporter`` fixture clears the buffer at the start of each
test that needs it, then yields the same exporter. This mirrors the
production wiring: production code calls
``trace.get_tracer("samantha_server")`` and the global SDK provider
is whatever was installed first — the lifespan's ``configure_otel``
detects an already-installed SDK provider and reuses it (see
``samantha_server/observability/otel.py::configure_otel``).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from samantha_server.observability.counters import CounterRegistry
from samantha_server.observability.otel import configure_otel

_GLOBAL_EXPORTER: InMemorySpanExporter | None = None


@pytest.fixture(scope="session")
def _otel_global_exporter() -> InMemorySpanExporter:
    """Install the global TracerProvider once per process; return its exporter.

    Routes through ``configure_otel`` so the same code path that
    production uses also wires the test exporter — including the
    ``add_span_processor`` branch when another test (e.g.,
    ``test_lifespan.py``) has already installed a provider.
    """
    global _GLOBAL_EXPORTER
    if _GLOBAL_EXPORTER is None:
        exporter = InMemorySpanExporter()
        configure_otel(CounterRegistry(), test_exporter=exporter)
        _GLOBAL_EXPORTER = exporter
    return _GLOBAL_EXPORTER


@pytest.fixture
def otel_exporter(_otel_global_exporter: InMemorySpanExporter) -> Iterator[InMemorySpanExporter]:
    """Clear the in-memory exporter, yield it, then clear again on teardown."""
    _otel_global_exporter.clear()
    yield _otel_global_exporter
    _otel_global_exporter.clear()
