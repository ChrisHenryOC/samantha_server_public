"""Tests for the access log shape — JSON fields, PHI safety, query-string dropping."""

from __future__ import annotations

import json
import logging
import uuid as uuid_module

import pytest

from tests.api.helpers import make_minimal_app_state


def test_access_log_has_required_fields(caplog: pytest.LogCaptureFixture) -> None:
    """Access log contains ts, method, path, status, latency_ms, request_id."""
    from fastapi.testclient import TestClient

    from samantha_server.api.app import create_app

    app = create_app()
    app.state.engine = make_minimal_app_state()

    with caplog.at_level(logging.INFO, logger="samantha_server.api.app"):
        client = TestClient(app)
        client.get("/healthz")

    json_records = []
    for record in caplog.records:
        try:
            data = json.loads(record.message)
        except (json.JSONDecodeError, AttributeError):
            continue
        if "method" in data and "path" in data:
            json_records.append(data)

    assert json_records, "No JSON access log records found"
    record = json_records[0]
    assert "ts" in record
    assert "method" in record
    assert "path" in record
    assert "status" in record
    assert "latency_ms" in record
    assert "request_id" in record


def test_access_log_request_id_matches_response_header(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The logged request_id is a valid UUID4 matching the X-Request-ID response header.

    PR #131 H1 regression: when the middleware order was inverted,
    every log line emitted ``request_id=""``. This assertion would have
    caught it (the existing key-presence check did not).
    """
    from fastapi.testclient import TestClient

    from samantha_server.api.app import create_app

    app = create_app()
    app.state.engine = make_minimal_app_state()

    with caplog.at_level(logging.INFO, logger="samantha_server.api.app"):
        client = TestClient(app)
        response = client.get("/healthz")

    header_id = response.headers["X-Request-ID"]
    parsed = uuid_module.UUID(header_id)
    assert parsed.version == 4

    json_records = []
    for record in caplog.records:
        try:
            data = json.loads(record.message)
        except (json.JSONDecodeError, AttributeError):
            continue
        if "request_id" in data:
            json_records.append(data)

    assert json_records, "No JSON access log records found"
    assert json_records[0]["request_id"] == header_id, (
        "Access log request_id must match X-Request-ID response header — "
        "if these diverge, middleware order has regressed (PR #131 H1)."
    )


def test_access_log_drops_query_string(caplog: pytest.LogCaptureFixture) -> None:
    """Access log path does not include query string (PHI boundary)."""
    from fastapi.testclient import TestClient

    from samantha_server.api.app import create_app

    app = create_app()
    app.state.engine = make_minimal_app_state()

    with caplog.at_level(logging.INFO, logger="samantha_server.api.app"):
        client = TestClient(app)
        client.get("/healthz?patient_name=JohnDoe&dob=19800101")

    for record in caplog.records:
        assert "patient_name" not in record.message, (
            "PHI boundary violation: query string appeared in access log"
        )
        assert "JohnDoe" not in record.message, (
            "PHI boundary violation: query param value appeared in access log"
        )
