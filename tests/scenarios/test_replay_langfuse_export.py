"""Tests for GH-338 Slice A: replay_with_langfuse_export().

replay_with_langfuse_export() configures OTLP export and calls replay()
on the ENDPOINT path (no tracer= kwarg), so spans flow through
events.py's own parent span (POST /events → queue → _consume).

Tests do not make live network calls. The OTLPSpanExporter constructor
is monkeypatched to capture the endpoint+headers it was built with.
The _id_exporter_override kwarg is used to inspect finished spans.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

_OTLP_PATCH_TARGET = "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter"
_BASE_URL = "http://localhost:3000"
_PUBLIC_KEY = "pk-test-export"
_SECRET_KEY = "sk-test-export"
_RELEASE = "abc123-test_model"
_ENDPOINT = f"{_BASE_URL}/api/public/otel/v1/traces"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_deterministic_scenario(tmp_path: Path) -> Path:
    """Write a single-step rule_coverage scenario to tmp_path."""
    scenario = {
        "scenario_id": "SC-LFE-01",
        "category": "rule_coverage",
        "description": "Langfuse export test",
        "events": [
            {
                "step": 1,
                "event_type": "order_received",
                "event_data": {
                    "patient_name": "TEST, LFE",
                    "age": 45,
                    "sex": "F",
                    "specimen_type": "biopsy",
                    "anatomic_site": "breast",
                    "fixative": "formalin",
                    "fixation_time_hours": 24.0,
                    "ordered_tests": ["Breast IHC Panel"],
                    "priority": "routine",
                    "billing_info_present": True,
                },
                "expected_output": {
                    "next_state": "ACCEPTED",
                    "applied_rules": ["ACC-008"],
                    "flags": [],
                    "routing_path": "deterministic",
                },
            }
        ],
    }
    subdir = tmp_path / "rule_coverage"
    subdir.mkdir(parents=True, exist_ok=True)
    path = subdir / "sc_lfe_01.json"
    path.write_text(json.dumps(scenario, indent=2))
    return tmp_path


def _make_fake_otlp_class(captured: dict[str, Any]) -> type:
    """Build a fake OTLPSpanExporter that records constructor kwargs."""
    from opentelemetry.sdk.trace.export import SpanExportResult

    class _FakeOTLPExporter:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

        def export(self, spans: object) -> SpanExportResult:
            return SpanExportResult.SUCCESS

        def shutdown(self) -> None:
            pass

        def force_flush(self, timeout_millis: int = 30_000) -> bool:
            return True

    return _FakeOTLPExporter


def _make_failing_otlp_class() -> type:
    """Build a fake OTLPSpanExporter whose export() always returns FAILURE."""
    from opentelemetry.sdk.trace.export import SpanExportResult

    class _FailingOTLPExporter:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def export(self, spans: object) -> SpanExportResult:
            return SpanExportResult.FAILURE

        def shutdown(self) -> None:
            pass

        def force_flush(self, timeout_millis: int = 30_000) -> bool:
            return True

    return _FailingOTLPExporter


_UNUSED: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Slice A Tests
# ---------------------------------------------------------------------------


def test_replay_with_langfuse_export_exists() -> None:
    """replay_with_langfuse_export is importable from samantha_server.scenarios.replay."""
    from samantha_server.scenarios.replay import replay_with_langfuse_export  # noqa: F401


def test_replay_with_langfuse_export_returns_report_and_trace_ids(
    tmp_path: Path,
) -> None:
    """replay_with_langfuse_export returns (AccuracyReport, list[str])."""
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from samantha_server.scenarios.replay import AccuracyReport, replay_with_langfuse_export

    corpus_dir = _write_deterministic_scenario(tmp_path)
    id_exporter = InMemorySpanExporter()

    with patch(_OTLP_PATCH_TARGET, _make_fake_otlp_class(_UNUSED)):
        report, trace_ids = replay_with_langfuse_export(
            corpus_dir,
            langfuse_base_url=_BASE_URL,
            langfuse_public_key=_PUBLIC_KEY,
            langfuse_secret_key=_SECRET_KEY,
            release=_RELEASE,
            _id_exporter_override=id_exporter,
        )

    assert isinstance(report, AccuracyReport)
    assert isinstance(trace_ids, list)
    assert len(trace_ids) >= 1, "Expected at least one trace_id for single-step corpus"
    for tid in trace_ids:
        assert isinstance(tid, str)
        assert len(tid) == 32, f"trace_id {tid!r} is not 32 hex chars"
        assert all(c in "0123456789abcdef" for c in tid)


def test_replay_with_langfuse_export_spans_come_from_events_endpoint(
    tmp_path: Path,
) -> None:
    """Finished spans include at least one PARENT_SPAN_NAME span (events.py produced it).

    The endpoint path (POST /events) produces 'samantha_server.event' spans.
    This asserts the endpoint is being used (not the legacy direct-dispatch path).
    """
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from samantha_server.observability.otel import PARENT_SPAN_NAME
    from samantha_server.scenarios.replay import replay_with_langfuse_export

    corpus_dir = _write_deterministic_scenario(tmp_path)
    id_exporter = InMemorySpanExporter()

    with patch(_OTLP_PATCH_TARGET, _make_fake_otlp_class(_UNUSED)):
        replay_with_langfuse_export(
            corpus_dir,
            langfuse_base_url=_BASE_URL,
            langfuse_public_key=_PUBLIC_KEY,
            langfuse_secret_key=_SECRET_KEY,
            release=_RELEASE,
            _id_exporter_override=id_exporter,
        )

    spans = id_exporter.get_finished_spans()
    parent_spans = [s for s in spans if s.name == PARENT_SPAN_NAME]
    assert len(parent_spans) >= 1, (
        f"Expected at least one {PARENT_SPAN_NAME!r} span from the endpoint path; "
        f"got span names: {[s.name for s in spans]}"
    )


def test_replay_with_langfuse_export_resource_carries_release(
    tmp_path: Path,
) -> None:
    """Spans carry langfuse.release == release on their Resource."""
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from samantha_server.scenarios.replay import replay_with_langfuse_export

    corpus_dir = _write_deterministic_scenario(tmp_path)
    id_exporter = InMemorySpanExporter()

    with patch(_OTLP_PATCH_TARGET, _make_fake_otlp_class(_UNUSED)):
        replay_with_langfuse_export(
            corpus_dir,
            langfuse_base_url=_BASE_URL,
            langfuse_public_key=_PUBLIC_KEY,
            langfuse_secret_key=_SECRET_KEY,
            release=_RELEASE,
            _id_exporter_override=id_exporter,
        )

    spans = id_exporter.get_finished_spans()
    assert spans, "Expected at least one finished span"
    for span in spans:
        attrs = dict(span.resource.attributes)
        assert "langfuse.release" in attrs, (
            f"Expected 'langfuse.release' in resource.attributes; got {attrs!r}"
        )
        assert attrs["langfuse.release"] == _RELEASE, (
            f"Expected langfuse.release={_RELEASE!r}; got {attrs['langfuse.release']!r}"
        )


def test_replay_with_langfuse_export_rejects_empty_release(tmp_path: Path) -> None:
    """replay_with_langfuse_export raises ValueError for blank release strings."""
    from samantha_server.scenarios.replay import replay_with_langfuse_export

    corpus_dir = _write_deterministic_scenario(tmp_path)

    for bad_release in ("", "   ", "\t\n"):
        with pytest.raises(ValueError, match="non-empty, non-whitespace"):
            replay_with_langfuse_export(
                corpus_dir,
                langfuse_base_url=_BASE_URL,
                langfuse_public_key=_PUBLIC_KEY,
                langfuse_secret_key=_SECRET_KEY,
                release=bad_release,
            )


def test_replay_with_langfuse_export_otlp_endpoint_correct(tmp_path: Path) -> None:
    """replay_with_langfuse_export passes correct traces endpoint to OTLPSpanExporter."""
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from samantha_server.scenarios.replay import replay_with_langfuse_export

    corpus_dir = _write_deterministic_scenario(tmp_path)
    captured: dict[str, Any] = {}

    with patch(_OTLP_PATCH_TARGET, _make_fake_otlp_class(captured)):
        replay_with_langfuse_export(
            corpus_dir,
            langfuse_base_url=_BASE_URL,
            langfuse_public_key=_PUBLIC_KEY,
            langfuse_secret_key=_SECRET_KEY,
            release=_RELEASE,
            _id_exporter_override=InMemorySpanExporter(),
        )

    assert captured.get("endpoint") == _ENDPOINT, (
        f"Expected endpoint={_ENDPOINT!r}, got {captured.get('endpoint')!r}"
    )


def test_replay_with_langfuse_export_normalizes_trailing_slash(tmp_path: Path) -> None:
    """Trailing slash on langfuse_base_url is stripped."""
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from samantha_server.scenarios.replay import replay_with_langfuse_export

    corpus_dir = _write_deterministic_scenario(tmp_path)
    captured: dict[str, Any] = {}

    with patch(_OTLP_PATCH_TARGET, _make_fake_otlp_class(captured)):
        replay_with_langfuse_export(
            corpus_dir,
            langfuse_base_url=f"{_BASE_URL}/",
            langfuse_public_key=_PUBLIC_KEY,
            langfuse_secret_key=_SECRET_KEY,
            release=_RELEASE,
            _id_exporter_override=InMemorySpanExporter(),
        )

    assert captured.get("endpoint") == _ENDPOINT


def test_replay_with_langfuse_export_basic_auth_header(tmp_path: Path) -> None:
    """replay_with_langfuse_export passes Basic auth in Authorization header."""
    import base64

    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from samantha_server.scenarios.replay import replay_with_langfuse_export

    corpus_dir = _write_deterministic_scenario(tmp_path)
    captured: dict[str, Any] = {}

    with patch(_OTLP_PATCH_TARGET, _make_fake_otlp_class(captured)):
        replay_with_langfuse_export(
            corpus_dir,
            langfuse_base_url=_BASE_URL,
            langfuse_public_key=_PUBLIC_KEY,
            langfuse_secret_key=_SECRET_KEY,
            release=_RELEASE,
            _id_exporter_override=InMemorySpanExporter(),
        )

    expected_creds = base64.b64encode(f"{_PUBLIC_KEY}:{_SECRET_KEY}".encode()).decode("ascii")
    expected_auth = f"Basic {expected_creds}"
    headers = captured.get("headers", {})
    assert "Authorization" in headers
    assert headers["Authorization"] == expected_auth


def test_replay_with_langfuse_export_stderr_breadcrumb(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """replay_with_langfuse_export prints a run-id + release breadcrumb to stderr."""
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from samantha_server.scenarios.replay import replay_with_langfuse_export

    corpus_dir = _write_deterministic_scenario(tmp_path)

    with patch(_OTLP_PATCH_TARGET, _make_fake_otlp_class(_UNUSED)):
        replay_with_langfuse_export(
            corpus_dir,
            langfuse_base_url=_BASE_URL,
            langfuse_public_key=_PUBLIC_KEY,
            langfuse_secret_key=_SECRET_KEY,
            release=_RELEASE,
            _id_exporter_override=InMemorySpanExporter(),
        )

    stderr = capsys.readouterr().err
    assert "Langfuse: replay run id = " in stderr, (
        f"Expected run id breadcrumb in stderr; got:\n{stderr!r}"
    )
    assert f", release = {_RELEASE!r}" in stderr, (
        f"Expected ', release = {_RELEASE!r}' in breadcrumb; got:\n{stderr!r}"
    )


def test_replay_with_langfuse_export_force_flushes_provider(tmp_path: Path) -> None:
    """replay_with_langfuse_export calls TracerProvider.force_flush with a bounded timeout."""
    from opentelemetry.sdk.trace import TracerProvider

    from samantha_server.scenarios.replay import replay_with_langfuse_export

    corpus_dir = _write_deterministic_scenario(tmp_path)
    captured: dict[str, Any] = {}
    real_force_flush = TracerProvider.force_flush

    def spy_force_flush(self: TracerProvider, timeout_millis: int = 30_000) -> bool:
        captured["called"] = True
        captured["timeout_millis"] = timeout_millis
        return real_force_flush(self, timeout_millis)

    with (
        patch(_OTLP_PATCH_TARGET, _make_fake_otlp_class(_UNUSED)),
        patch.object(TracerProvider, "force_flush", spy_force_flush),
    ):
        replay_with_langfuse_export(
            corpus_dir,
            langfuse_base_url=_BASE_URL,
            langfuse_public_key=_PUBLIC_KEY,
            langfuse_secret_key=_SECRET_KEY,
            release=_RELEASE,
        )

    assert captured.get("called") is True
    assert isinstance(captured.get("timeout_millis"), int)
    assert 0 < captured["timeout_millis"] < 30_000


def test_replay_with_langfuse_export_export_failure_stderr_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Export failures surface to stderr as a WARNING."""
    from samantha_server.scenarios.replay import replay_with_langfuse_export

    corpus_dir = _write_deterministic_scenario(tmp_path)

    with patch(_OTLP_PATCH_TARGET, _make_failing_otlp_class()):
        replay_with_langfuse_export(
            corpus_dir,
            langfuse_base_url=_BASE_URL,
            langfuse_public_key=_PUBLIC_KEY,
            langfuse_secret_key=_SECRET_KEY,
            release=_RELEASE,
        )

    stderr = capsys.readouterr().err
    assert "Langfuse: WARNING" in stderr, (
        f"Expected export-failure WARNING on stderr; got:\n{stderr!r}"
    )
    assert "export failure(s)" in stderr
    assert re.search(r"\d+ export failure\(s\)", stderr)


def test_replay_with_langfuse_export_log_info_line(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """replay_with_langfuse_export emits a log.info line with span count and release."""
    from samantha_server.scenarios.replay import replay_with_langfuse_export

    corpus_dir = _write_deterministic_scenario(tmp_path)

    with (
        patch(_OTLP_PATCH_TARGET, _make_fake_otlp_class(_UNUSED)),
        caplog.at_level(logging.INFO, logger="samantha_server.scenarios.replay"),
    ):
        replay_with_langfuse_export(
            corpus_dir,
            langfuse_base_url=_BASE_URL,
            langfuse_public_key=_PUBLIC_KEY,
            langfuse_secret_key=_SECRET_KEY,
            release=_RELEASE,
        )

    info_records = [
        r
        for r in caplog.records
        if r.levelno == logging.INFO and "replay_with_langfuse_export:" in r.getMessage()
    ]
    assert info_records, (
        f"Expected INFO record with 'replay_with_langfuse_export:' prefix; "
        f"got records: {[r.getMessage() for r in caplog.records]!r}"
    )
    msg = info_records[0].getMessage()
    assert "span(s)" in msg
    assert f"release={_RELEASE}" in msg
