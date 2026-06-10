"""Shared test helpers for samantha_server.api tests."""

from __future__ import annotations

import asyncio
import pathlib
import sqlite3
import tempfile
from typing import Any
from unittest.mock import MagicMock

from samantha_server.api.receipt_writer import ReceiptWriterProtocol
from samantha_server.llm.client import LLMResponse
from samantha_server.receipts.signing import SignedReceipt


class SpyReceiptWriter(ReceiptWriterProtocol):
    """Test spy implementing ReceiptWriterProtocol; appends written receipts to a shared list.

    Replaces the three near-identical inline _SpyWriter classes that had been
    copy-pasted across tests/receipts/test_architectural.py,
    tests/scenarios/test_llm_review_replay.py, and tests/scenarios/test_query_replay.py
    after the router-shim deletion.
    """

    def __init__(self, written: list[SignedReceipt]) -> None:
        self._written = written

    def write_signed(self, receipt: SignedReceipt) -> None:
        self._written.append(receipt)

    def close(self) -> None:
        pass

    def is_open(self) -> bool:
        return True


def make_minimal_app_state():  # type: ignore[no-untyped-def]
    """Build an AppState with mocked dependencies for unit tests.

    Suitable for testing AppState field access and flag behavior without
    touching the file system or loading real indexes. The receipt writer
    uses an in-memory SQLite connection so ``is_open()`` returns True
    (required by ``/readyz``'s load-bearing checks).

    The receipt_audit_conn is a separate in-memory connection with
    query_only=1 that mirrors the production pattern from build_app_state.

    Tests that need a sentinel for the index fields (e.g., asserting
    ``/readyz`` 503s when an index is None) override the field on the
    returned object directly.
    """
    from samantha_server.api.lifespan import AppState
    from samantha_server.api.receipt_writer import ReceiptWriter
    from samantha_server.observability.cached_probe import make_langfuse_stub_probe
    from samantha_server.observability.counters import CounterRegistry
    from samantha_server.queue.priority import PriorityEventQueue
    from samantha_server.receipts.store import _SCHEMA_SQL

    mock_rule_index = MagicMock()
    mock_scenario_index = MagicMock()
    mock_skill_index = MagicMock()
    mock_llm_client = _make_mock_llm_client()

    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.executescript(_SCHEMA_SQL)
    conn.commit()
    receipt_writer = ReceiptWriter(conn)

    # Audit connection: separate in-memory DB with query_only=1.
    # check_same_thread=False is required because FastAPI handler threads
    # will use this connection after it is created in the test thread.
    # Uses the same schema so receipt read helpers can query it.
    audit_conn = sqlite3.connect(":memory:", check_same_thread=False)
    audit_conn.executescript(_SCHEMA_SQL)
    audit_conn.commit()
    audit_conn.execute("PRAGMA query_only=1")

    return AppState(
        rule_index=mock_rule_index,
        scenario_index=mock_scenario_index,
        skill_index=mock_skill_index,
        llm_client=mock_llm_client,
        receipt_writer=receipt_writer,
        receipt_write_lock=asyncio.Lock(),
        counters=CounterRegistry(),
        langfuse_probe=make_langfuse_stub_probe(),
        commit_sha="test-commit-sha",
        queue=PriorityEventQueue(maxsize=256),
        receipt_audit_conn=audit_conn,
    )


# Canned JSON response for tests that exercise handle_clinical_query in json mode.
# answer_type=no_orders keeps the fixture simple and avoids any order-ID assertions.
_CANNED_JSON_TEXT = '{"answer_type":"no_orders","order_ids":[],"reasoning":"","caveats":""}'


def _make_mock_llm_client(text: str = "Test response.") -> MagicMock:
    """Return a MagicMock with model_id + complete() and complete_json() stubs.

    complete() returns a stub LLMResponse with the given text (free_text mode).
    complete_json() returns a stub LLMResponse with a valid QueryResponseV1 JSON
    payload (json mode default). Tests that exercise mode-specific behavior should
    construct their own mock or override the return_value directly.
    """
    mock = MagicMock()
    mock.model_id = "test-model"
    mock.complete.return_value = LLMResponse(
        text=text,
        input_tokens=10,
        output_tokens=5,
        model_id="test-model",
        latency_us=1000,
    )
    mock.complete_json.return_value = LLMResponse(
        text=_CANNED_JSON_TEXT,
        input_tokens=10,
        output_tokens=5,
        model_id="test-model",
        latency_us=1000,
    )
    return mock


def make_consume_app_state(
    llm_client: Any | None = None,
    drift_monitor: Any | None = None,
) -> Any:
    """Build a fully-loaded AppState suitable for tests that exercise ``_consume``.

    Differs from ``make_minimal_app_state`` in three ways:
    1. Uses ``RuleIndex([])`` (empty but real) instead of ``MagicMock``.
    2. Uses ``discover()`` for the skill index so handlers can resolve skills.
    3. Uses an on-disk receipt-writer DB so the receipt path round-trips.

    Pass an ``llm_client`` mock to control LLM behaviour; defaults to a
    happy-path mock returning ``"Test response."``. Pass a
    ``drift_monitor`` to wire one in at construction time (the helper
    shares the AppState's ``counters`` with the monitor by reusing the
    monitor's existing counter binding).
    """
    from samantha_server.api.lifespan import AppState
    from samantha_server.api.receipt_writer import ReceiptWriter
    from samantha_server.observability.cached_probe import make_langfuse_stub_probe
    from samantha_server.observability.counters import CounterRegistry
    from samantha_server.queue.priority import PriorityEventQueue
    from samantha_server.receipts.store import _SCHEMA_SQL
    from samantha_server.rules.loader import RuleIndex
    from samantha_server.skills.loader import discover

    tmp = pathlib.Path(tempfile.mkdtemp())
    rw = ReceiptWriter.open(tmp / "r.db")

    audit_conn = sqlite3.connect(":memory:", check_same_thread=False)
    audit_conn.executescript(_SCHEMA_SQL)
    audit_conn.commit()
    audit_conn.execute("PRAGMA query_only=1")

    counters = CounterRegistry()

    return AppState(
        rule_index=RuleIndex([]),
        scenario_index={},
        skill_index=discover(),
        llm_client=llm_client if llm_client is not None else _make_mock_llm_client(),
        receipt_writer=rw,
        receipt_write_lock=asyncio.Lock(),
        counters=counters,
        langfuse_probe=make_langfuse_stub_probe(),
        commit_sha="test-sha",
        queue=PriorityEventQueue(maxsize=256),
        receipt_audit_conn=audit_conn,
        drift_monitor=drift_monitor,
    )


def make_clinical_query_ctx_dict(order_id: str = "TEST-001") -> dict[str, Any]:
    """Return a clinical_query SpecimenContext-compatible dict for consumer tests."""
    return {
        "order": {
            "order_id": order_id,
            "patient_name": None,
            "patient_sex": "F",
            "age": 40,
            "specimen_type": "biopsy",
            "anatomic_site": "breast",
            "fixative": "formalin",
            "fixation_time_hours": 24.0,
            "ordered_tests": ["ER"],
            "priority": "routine",
            "billing_info_present": True,
        },
        "current_state": "ACCESSIONING",
        "flags": [],
        "event": {
            "event_type": "clinical_query",
            "event_data": {"query": "test?"},
            "step_index": 0,
        },
    }
