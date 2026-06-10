"""Tests for samantha_server.api.lifespan — AppState and build_app_state."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock


def _make_mock_llm_client() -> MagicMock:
    """Return a mock LLMClient."""
    client = MagicMock()
    client.model_id = "test-model"
    return client


def test_app_state_has_required_fields() -> None:
    """AppState dataclass has the required Step-1 fields."""
    from samantha_server.api.lifespan import AppState

    fields = AppState.__dataclass_fields__  # type: ignore[union-attr]
    assert "rule_index" in fields
    assert "scenario_index" in fields
    assert "skill_index" in fields
    assert "llm_client" in fields
    assert "receipt_writer" in fields
    assert "receipt_write_lock" in fields
    assert "counters" in fields
    assert "langfuse_probe" in fields
    assert "is_draining" in fields


def test_app_state_aclose_is_callable() -> None:
    """AppState has an async aclose() method."""
    from samantha_server.api.lifespan import AppState

    assert hasattr(AppState, "aclose")
    import inspect

    assert inspect.iscoroutinefunction(AppState.aclose)


def test_app_state_initial_draining_flag_is_false() -> None:
    """AppState.is_draining defaults to False."""

    from tests.api.helpers import make_minimal_app_state

    state = make_minimal_app_state()
    assert state.is_draining is False


def test_aclose_actually_closes_sqlite_connection() -> None:
    """aclose() releases the SQLite write connection.

     test-cov L-03 regression: previously the test only checked
    aclose was a coroutine, not that it actually closed the connection.
    This test asserts a write attempt after aclose raises (or
    is_open() returns False).
    """
    import asyncio

    from tests.api.helpers import make_minimal_app_state

    state = make_minimal_app_state()
    assert state.receipt_writer.is_open() is True
    asyncio.run(state.aclose())
    assert state.receipt_writer.is_open() is False


def test_build_app_state_raises_on_web_concurrency_gt_1(
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[name-defined]  # noqa: F821
    tmp_path: Path,
) -> None:
    """build_app_state raises MisconfiguredEnvironmentError when WEB_CONCURRENCY > 1."""
    import pytest

    import samantha_server.config as cfg

    monkeypatch.setattr(cfg, "WEB_CONCURRENCY", 2)

    from samantha_server.errors import MisconfiguredEnvironmentError

    with pytest.raises(MisconfiguredEnvironmentError, match="WEB_CONCURRENCY"):
        from samantha_server.api.lifespan import build_app_state

        build_app_state()


def test_build_app_state_returns_app_state_on_valid_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[name-defined]  # noqa: F821
    receipts_test_isolation: None,
) -> None:
    """build_app_state returns an AppState instance with valid config."""
    from unittest.mock import MagicMock

    monkeypatch.setenv("RECEIPTS_DB_PATH", str(tmp_path / "receipts.db"))
    monkeypatch.setenv("WEB_CONCURRENCY", "1")

    import samantha_server.config as cfg

    monkeypatch.setattr(cfg, "RECEIPTS_DB_PATH", str(tmp_path / "receipts.db"))
    monkeypatch.setattr(cfg, "WEB_CONCURRENCY", 1)

    from samantha_server.api.lifespan import AppState, build_app_state

    mock_llm = MagicMock()
    mock_llm.model_id = "test-model"
    state = build_app_state(_llm_client_override=mock_llm)
    assert isinstance(state, AppState)

    # Cleanup
    asyncio.run(state.aclose())


def test_build_app_state_receipt_writer_is_not_none(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[name-defined]  # noqa: F821
    receipts_test_isolation: None,
) -> None:
    """build_app_state creates a receipt_writer."""
    from unittest.mock import MagicMock

    monkeypatch.setenv("RECEIPTS_DB_PATH", str(tmp_path / "receipts.db"))

    import samantha_server.config as cfg

    monkeypatch.setattr(cfg, "RECEIPTS_DB_PATH", str(tmp_path / "receipts.db"))
    monkeypatch.setattr(cfg, "WEB_CONCURRENCY", 1)

    from samantha_server.api.lifespan import build_app_state

    mock_llm = MagicMock()
    mock_llm.model_id = "test-model"
    state = build_app_state(_llm_client_override=mock_llm)
    assert state.receipt_writer is not None
    asyncio.run(state.aclose())


def test_build_app_state_counters_start_at_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[name-defined]  # noqa: F821
    receipts_test_isolation: None,
) -> None:
    """build_app_state CounterRegistry starts with all zeros."""
    from unittest.mock import MagicMock

    monkeypatch.setenv("RECEIPTS_DB_PATH", str(tmp_path / "receipts.db"))

    import samantha_server.config as cfg

    monkeypatch.setattr(cfg, "RECEIPTS_DB_PATH", str(tmp_path / "receipts.db"))
    monkeypatch.setattr(cfg, "WEB_CONCURRENCY", 1)

    from samantha_server.api.lifespan import build_app_state

    mock_llm = MagicMock()
    mock_llm.model_id = "test-model"
    state = build_app_state(_llm_client_override=mock_llm)
    snap = state.counters.snapshot()
    assert snap["receipt_signing_failures"] == 0
    assert snap["otel_export_failures"] == 0
    asyncio.run(state.aclose())


# ---------------------------------------------------------------------------
# Step 2: queue and consumer_task fields on AppState
# ---------------------------------------------------------------------------


def test_app_state_has_queue_field() -> None:
    """AppState has a 'queue' field (Step 2 extension)."""
    from samantha_server.api.lifespan import AppState

    fields = AppState.__dataclass_fields__  # type: ignore[union-attr]
    assert "queue" in fields


def test_app_state_has_consumer_task_field() -> None:
    """AppState has a 'consumer_task' field defaulting to None."""
    from samantha_server.api.lifespan import AppState

    fields = AppState.__dataclass_fields__  # type: ignore[union-attr]
    assert "consumer_task" in fields


def test_app_state_consumer_task_default_is_none() -> None:
    """AppState.consumer_task defaults to None before lifespan starts it."""
    from tests.api.helpers import make_minimal_app_state

    state = make_minimal_app_state()
    assert state.consumer_task is None


def test_app_state_queue_is_priority_event_queue() -> None:
    """AppState.queue is a PriorityEventQueue instance after build_app_state."""
    from samantha_server.queue.priority import PriorityEventQueue
    from tests.api.helpers import make_minimal_app_state

    state = make_minimal_app_state()
    assert isinstance(state.queue, PriorityEventQueue)


def test_build_app_state_constructs_queue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[name-defined]  # noqa: F821
    receipts_test_isolation: None,
) -> None:
    """build_app_state creates a PriorityEventQueue from cfg.QUEUE_BOUND."""
    from unittest.mock import MagicMock

    monkeypatch.setenv("RECEIPTS_DB_PATH", str(tmp_path / "receipts.db"))

    import samantha_server.config as cfg

    monkeypatch.setattr(cfg, "RECEIPTS_DB_PATH", str(tmp_path / "receipts.db"))
    monkeypatch.setattr(cfg, "WEB_CONCURRENCY", 1)

    from samantha_server.api.lifespan import build_app_state
    from samantha_server.queue.priority import PriorityEventQueue

    mock_llm = MagicMock()
    mock_llm.model_id = "test-model"
    state = build_app_state(_llm_client_override=mock_llm)
    assert isinstance(state.queue, PriorityEventQueue)
    asyncio.run(state.aclose())


def test_build_app_state_queue_maxsize_matches_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[name-defined]  # noqa: F821
    receipts_test_isolation: None,
) -> None:
    """Queue maxsize matches cfg.QUEUE_BOUND, not a default-factory hardcode.

     M2 regression: AppState.queue previously had
    ``default_factory=lambda: PriorityEventQueue(maxsize=256)``, so any
    construction path that bypassed build_app_state silently used 256
    regardless of config. Removing the default_factory makes queue a
    required constructor argument; this test verifies the configured
    bound flows through to the underlying asyncio.PriorityQueue.
    """
    from unittest.mock import MagicMock

    monkeypatch.setenv("RECEIPTS_DB_PATH", str(tmp_path / "receipts.db"))

    import samantha_server.config as cfg

    monkeypatch.setattr(cfg, "RECEIPTS_DB_PATH", str(tmp_path / "receipts.db"))
    monkeypatch.setattr(cfg, "WEB_CONCURRENCY", 1)
    monkeypatch.setattr(cfg, "QUEUE_BOUND", 17)

    from samantha_server.api.lifespan import build_app_state

    mock_llm = MagicMock()
    mock_llm.model_id = "test-model"
    state = build_app_state(_llm_client_override=mock_llm)
    assert state.queue._queue.maxsize == 17
    asyncio.run(state.aclose())


# ---------------------------------------------------------------------------
# Step 2: consumer task lifecycle in the FastAPI lifespan
# ---------------------------------------------------------------------------


def test_consumer_task_started_in_lifespan() -> None:
    """Lifespan startup creates a non-None consumer_task that is not done.

    Uses the same patch pattern as test_shutdown_drain to avoid loading
    real LLM model weights during startup.
    """
    from unittest.mock import patch

    from samantha_server.api.app import lifespan
    from tests.api.helpers import make_minimal_app_state

    stub_state = make_minimal_app_state()
    captured: dict[str, object] = {}

    class _StubApp:
        class _State:
            engine: object = None

        state = _State()

    stub_app = _StubApp()

    async def run_lifespan() -> None:
        with patch(
            "samantha_server.api.lifespan.build_app_state",
            return_value=stub_state,
        ):
            async with lifespan(stub_app):  # type: ignore[arg-type]
                captured["task"] = stub_state.consumer_task
                captured["task_done"] = (
                    stub_state.consumer_task.done()
                    if stub_state.consumer_task is not None
                    else None
                )

    asyncio.run(run_lifespan())

    assert captured["task"] is not None
    assert captured["task_done"] is False


def test_consumer_task_cancelled_at_shutdown() -> None:
    """Lifespan shutdown cancels and awaits the consumer task; no orphan tasks.

    Uses the same patch pattern as test_shutdown_drain to avoid loading
    real LLM model weights during startup.
    """
    from unittest.mock import patch

    from samantha_server.api.app import lifespan
    from tests.api.helpers import make_minimal_app_state

    stub_state = make_minimal_app_state()
    task_ref: list[asyncio.Task[None]] = []

    class _StubApp:
        class _State:
            engine: object = None

        state = _State()

    stub_app = _StubApp()

    async def run_lifespan() -> None:
        with patch(
            "samantha_server.api.lifespan.build_app_state",
            return_value=stub_state,
        ):
            async with lifespan(stub_app):  # type: ignore[arg-type]
                if stub_state.consumer_task is not None:
                    task_ref.append(stub_state.consumer_task)

    asyncio.run(run_lifespan())

    assert len(task_ref) == 1
    # Assert the cause is cancellation, not normal completion.
    # `.done()` alone passes if the task simply returned (which a future
    # _consume bug could do).
    assert task_ref[0].done()
    assert task_ref[0].cancelled()


def test_aclose_idempotent_on_already_cancelled_consumer() -> None:
    """aclose() does not raise when consumer_task was actually cancelled.

     L9: previously this test used a normally-completed `noop`
    coroutine — the test name said "already cancelled" but the fixture
    didn't exercise that scenario. Now we cancel the task explicitly
    before calling aclose so the path matches the name.
    """
    from tests.api.helpers import make_minimal_app_state

    async def run() -> None:
        state = make_minimal_app_state()

        async def slow() -> None:
            await asyncio.sleep(60)

        import contextlib

        loop = asyncio.get_event_loop()
        task: asyncio.Task[None] = loop.create_task(slow())
        await asyncio.sleep(0)  # let slow() reach the await
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        assert task.cancelled()

        state.consumer_task = task
        # aclose() must not raise even though task is already cancelled.
        await state.aclose()

    asyncio.run(run())


def test_aclose_logs_dead_consumer_task_exception(
    caplog: pytest.LogCaptureFixture,  # type: ignore[name-defined]  # noqa: F821
) -> None:
    """aclose logs a dead-task exception (regression for L7)."""
    import logging

    from tests.api.helpers import make_minimal_app_state

    async def run() -> None:
        state = make_minimal_app_state()

        async def explode() -> None:
            raise RuntimeError("dispatch boom")

        import contextlib

        loop = asyncio.get_event_loop()
        task: asyncio.Task[None] = loop.create_task(explode())
        with contextlib.suppress(RuntimeError):
            await task
        state.consumer_task = task

        with caplog.at_level(logging.ERROR, logger="samantha_server.api.lifespan"):
            await state.aclose()

    asyncio.run(run())
    assert any("consumer task exited" in rec.message for rec in caplog.records)


def test_aclose_closes_audit_conn_even_when_writer_close_raises() -> None:
    """aclose() closes receipt_audit_conn even if receipt_writer.close() raises.

    Regression for review finding #4: the original aclose had no
    guard between writer.close() and audit_conn.close(). If the writer raised,
    the audit_conn would leak. The fix wraps each close in suppress(Exception).
    """
    import sqlite3
    from unittest.mock import MagicMock

    from tests.api.helpers import make_minimal_app_state

    async def run() -> None:
        state = make_minimal_app_state()

        # Make receipt_writer.close() raise.
        state.receipt_writer.close = MagicMock(side_effect=RuntimeError("writer exploded"))  # type: ignore[method-assign]

        # Patch sqlite3.Connection.close on the specific audit_conn instance to
        # spy on whether it was called. sqlite3.Connection.close is a read-only
        # slot, so we patch the method on the class via patch.object on the
        # module, but the cleanest approach here is to replace the audit_conn
        # with a MagicMock so we can assert close() was called.
        mock_audit_conn = MagicMock(spec=sqlite3.Connection)
        state.receipt_audit_conn = mock_audit_conn  # type: ignore[assignment]

        # aclose() must not propagate the exception from writer.close().
        await state.aclose()

        mock_audit_conn.close.assert_called_once()


# ---------------------------------------------------------------------------
# Slice 6: _build_llm_client provider switch
# ---------------------------------------------------------------------------


def test_build_llm_client_omlx_returns_omlx_client() -> None:
    """_build_llm_client() with LLM_PROVIDER='omlx' returns an OMLXClient.

    The OMLXClient probe is intercepted via a stub transport so no real
    oMLX server is needed.
    """
    import unittest.mock

    import httpx

    import samantha_server.config as cfg
    from samantha_server.llm.omlx_client import OMLXClient

    canned_models_response = {
        "data": [{"id": "test-model", "object": "model"}],
        "object": "list",
    }
    canned_completions_response = {
        "choices": [{"text": "hi"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=canned_models_response)
        return httpx.Response(200, json=canned_completions_response)

    original_provider = cfg.LLM_PROVIDER
    cfg.LLM_PROVIDER = "omlx"
    try:
        # Patch OMLXClient in the lifespan module to inject a stub transport.
        original_omlx_init = OMLXClient.__init__

        def _stub_init(self: OMLXClient, **kwargs: object) -> None:
            original_omlx_init(self, _transport=httpx.MockTransport(_handler))  # type: ignore[arg-type]

        with unittest.mock.patch.object(OMLXClient, "__init__", _stub_init):
            from samantha_server.api.lifespan import _build_llm_client

            client = _build_llm_client()

        assert isinstance(client, OMLXClient)
    finally:
        cfg.LLM_PROVIDER = original_provider


def test_build_llm_client_mlx_dispatches_to_mlx_constructor() -> None:
    """_build_llm_client() with LLM_PROVIDER='mlx' attempts to build an MLXClient.

    The test accepts either a successful MLXClient (if mlx-lm is installed)
    or a LLMModelLoadError (if mlx-lm is absent on CI). Both outcomes confirm
    the switch dispatched to the MLX constructor, not the oMLX one.
    """

    import samantha_server.config as cfg
    from samantha_server.errors import LLMModelLoadError

    original_provider = cfg.LLM_PROVIDER
    cfg.LLM_PROVIDER = "mlx"
    try:
        from samantha_server.api.lifespan import _build_llm_client
        from samantha_server.llm.mlx_client import MLXClient

        try:
            client = _build_llm_client()
            assert isinstance(client, MLXClient)
        except LLMModelLoadError:
            # mlx-lm not installed; the constructor was reached.
            pass
    finally:
        cfg.LLM_PROVIDER = original_provider


def test_build_llm_client_unknown_provider_raises_misconfigured_error() -> None:
    """_build_llm_client() with an unknown LLM_PROVIDER raises MisconfiguredEnvironmentError
    whose message mentions both 'mlx' and 'omlx'.
    """
    import pytest

    import samantha_server.config as cfg
    from samantha_server.errors import MisconfiguredEnvironmentError

    original_provider = cfg.LLM_PROVIDER
    cfg.LLM_PROVIDER = "bogus"
    try:
        from samantha_server.api.lifespan import _build_llm_client

        with pytest.raises(MisconfiguredEnvironmentError) as exc_info:
            _build_llm_client()

        msg = str(exc_info.value)
        assert "mlx" in msg
        assert "omlx" in msg
    finally:
        cfg.LLM_PROVIDER = original_provider
