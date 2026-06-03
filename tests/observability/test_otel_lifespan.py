"""Tests for OTel SDK setup, lifespan wiring, and export-failure counter."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


def test_appstate_has_tracer_provider_field() -> None:
    """AppState declares a tracer_provider field for shutdown ownership."""
    from samantha_server.api.lifespan import AppState

    fields = AppState.__dataclass_fields__
    assert "tracer_provider" in fields


def test_make_minimal_app_state_provides_default_tracer_provider() -> None:
    """make_minimal_app_state defaults tracer_provider to None for unit tests."""
    from tests.api.helpers import make_minimal_app_state

    state = make_minimal_app_state()
    assert state.tracer_provider is None


def test_configure_otel_with_test_exporter_attaches_to_existing_provider(
    _otel_global_exporter: InMemorySpanExporter,
) -> None:
    """configure_otel with test_exporter attaches a SimpleSpanProcessor to the existing global.

    The session conftest installs an SDK TracerProvider once. This test
    verifies that calling configure_otel with a fresh test_exporter
    correctly attaches a new processor to that provider (rather than
    refusing or trying to install a second one). Forward-looking
    safeguard against the M16 processor-accumulation concern: the
    add_span_processor call here is bounded to the test that needs an
    exporter wire-up.
    """
    from samantha_server.observability.counters import CounterRegistry
    from samantha_server.observability.otel import configure_otel

    counters = CounterRegistry()
    exporter = InMemorySpanExporter()
    existing_global = trace.get_tracer_provider()

    tracer, provider, returned_exporter = configure_otel(counters, test_exporter=exporter)

    assert tracer is not None
    assert provider is existing_global  # Reuse, not replace.
    assert returned_exporter is exporter


def test_configure_otel_reuses_existing_sdk_provider(
    _otel_global_exporter: InMemorySpanExporter,
) -> None:
    """configure_otel detects an already-installed SDK TracerProvider and reuses it."""
    from samantha_server.observability.counters import CounterRegistry
    from samantha_server.observability.otel import configure_otel

    counters = CounterRegistry()
    existing_global = trace.get_tracer_provider()

    tracer, provider, in_memory = configure_otel(counters)

    # The conftest already installed an SDK provider — configure_otel must reuse it.
    assert provider is existing_global
    assert tracer is not None
    assert in_memory is None


def test_export_failure_increments_counter_on_raise() -> None:
    """OTel export raise → counters.otel_export_failures increments."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    from samantha_server.observability.counters import CounterRegistry
    from samantha_server.observability.otel import _CountingSpanExporter

    class _FailingExporter:
        def export(self, spans: Any) -> Any:
            raise RuntimeError("simulated OTLP failure")

        def shutdown(self) -> None:
            pass

        def force_flush(self, timeout_millis: int = 30000) -> bool:
            return True

    counters = CounterRegistry()
    counting = _CountingSpanExporter(_FailingExporter(), counters)  # type: ignore[arg-type]

    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(counting))
    tracer = provider.get_tracer("test")

    assert counters.otel_export_failures.value == 0

    with tracer.start_as_current_span("doomed"):
        pass

    # SimpleSpanProcessor.on_end calls export synchronously; the failing
    # exporter raises during on_end, the counting wrapper catches it.
    assert counters.otel_export_failures.value >= 1


def test_export_failure_increments_counter_on_non_raising_failure_result() -> None:
    """L22: a SpanExportResult.FAILURE return (no raise) also bumps the counter."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExportResult

    from samantha_server.observability.counters import CounterRegistry
    from samantha_server.observability.otel import _CountingSpanExporter

    class _SoftFailingExporter:
        """Returns FAILURE without raising — exercises the second counter-increment branch."""

        def export(self, spans: Any) -> SpanExportResult:
            return SpanExportResult.FAILURE

        def shutdown(self) -> None:
            pass

        def force_flush(self, timeout_millis: int = 30000) -> bool:
            return True

    counters = CounterRegistry()
    counting = _CountingSpanExporter(_SoftFailingExporter(), counters)  # type: ignore[arg-type]

    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(counting))
    tracer = provider.get_tracer("test")

    assert counters.otel_export_failures.value == 0
    with tracer.start_as_current_span("soft-fail"):
        pass
    assert counters.otel_export_failures.value >= 1


def test_lifespan_calls_configure_otel_and_stores_provider() -> None:
    """The lifespan calls configure_otel and stores the provider on AppState."""
    import asyncio

    from samantha_server.api.app import lifespan
    from tests.api.helpers import make_minimal_app_state

    stub_state = make_minimal_app_state()

    class _StubApp:
        class _State:
            engine: object = None

        state = _State()

    captured_provider: list[Any] = []

    async def run_lifespan() -> None:
        with patch("samantha_server.api.lifespan.build_app_state", return_value=stub_state):
            async with lifespan(_StubApp()):  # type: ignore[arg-type]
                captured_provider.append(stub_state.tracer_provider)

    asyncio.run(run_lifespan())

    assert captured_provider[0] is not None
    assert isinstance(captured_provider[0], TracerProvider)


def test_aclose_flushes_tracer_provider() -> None:
    """AppState.aclose flushes the tracer provider (shutdown is skipped — see lifespan.py)."""
    import asyncio

    from tests.api.helpers import make_minimal_app_state

    state = make_minimal_app_state()
    mock_provider = MagicMock(spec=TracerProvider)
    mock_provider.force_flush.return_value = True
    state.tracer_provider = mock_provider

    asyncio.run(state.aclose())

    mock_provider.force_flush.assert_called_once()


def test_aclose_increments_counter_when_force_flush_returns_false() -> None:
    """A force_flush that returns False bumps otel_export_failures."""
    import asyncio

    from tests.api.helpers import make_minimal_app_state

    state = make_minimal_app_state()
    mock_provider = MagicMock(spec=TracerProvider)
    mock_provider.force_flush.return_value = False
    state.tracer_provider = mock_provider

    assert state.counters.otel_export_failures.value == 0
    asyncio.run(state.aclose())
    assert state.counters.otel_export_failures.value == 1
