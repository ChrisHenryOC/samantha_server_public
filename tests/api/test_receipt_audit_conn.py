"""Tests for AppState.receipt_audit_conn field."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path


def test_app_state_has_receipt_audit_conn_field() -> None:
    """AppState dataclass has a receipt_audit_conn field."""
    from samantha_server.api.lifespan import AppState

    fields = AppState.__dataclass_fields__  # type: ignore[union-attr]
    assert "receipt_audit_conn" in fields


def test_make_minimal_app_state_has_audit_conn() -> None:
    """make_minimal_app_state provides a receipt_audit_conn."""
    from tests.api.helpers import make_minimal_app_state

    state = make_minimal_app_state()
    assert state.receipt_audit_conn is not None
    assert isinstance(state.receipt_audit_conn, sqlite3.Connection)


def test_audit_conn_is_query_only() -> None:
    """receipt_audit_conn has query_only=1 (writes raise OperationalError)."""
    import pytest

    from tests.api.helpers import make_minimal_app_state

    state = make_minimal_app_state()
    conn = state.receipt_audit_conn
    # query_only=1 means INSERT/UPDATE/DELETE raise OperationalError
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("CREATE TABLE IF NOT EXISTS _audit_test (x TEXT)")


def test_build_app_state_opens_receipt_audit_conn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[name-defined]  # noqa: F821
    receipts_test_isolation: None,
) -> None:
    """build_app_state sets receipt_audit_conn on the returned AppState."""
    from unittest.mock import MagicMock

    import samantha_server.config as cfg

    monkeypatch.setattr(cfg, "RECEIPTS_DB_PATH", str(tmp_path / "receipts.db"))
    monkeypatch.setattr(cfg, "WEB_CONCURRENCY", 1)

    from samantha_server.api.lifespan import build_app_state

    mock_llm = MagicMock()
    mock_llm.model_id = "test-model"
    state = build_app_state(_llm_client_override=mock_llm)

    assert state.receipt_audit_conn is not None
    assert isinstance(state.receipt_audit_conn, sqlite3.Connection)

    asyncio.run(state.aclose())


def test_aclose_closes_receipt_audit_conn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[name-defined]  # noqa: F821
    receipts_test_isolation: None,
) -> None:
    """aclose() closes receipt_audit_conn."""
    from unittest.mock import MagicMock

    import pytest

    import samantha_server.config as cfg

    monkeypatch.setattr(cfg, "RECEIPTS_DB_PATH", str(tmp_path / "receipts.db"))
    monkeypatch.setattr(cfg, "WEB_CONCURRENCY", 1)

    from samantha_server.api.lifespan import build_app_state

    mock_llm = MagicMock()
    mock_llm.model_id = "test-model"
    state = build_app_state(_llm_client_override=mock_llm)

    asyncio.run(state.aclose())

    # After close, executing a query on the connection raises ProgrammingError
    with pytest.raises(Exception):  # noqa: B017 — ProgrammingError is what sqlite3 raises
        state.receipt_audit_conn.execute("SELECT 1")
