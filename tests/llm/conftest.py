"""Shared fixtures for tests/llm/.

M-07: consolidate _make_mock_llm_client into a single fixture used by
test_handlers.py.

M-16: apply receipts_test_isolation autouse to reset the write-connection
and signing-key caches between test runs.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _llm_receipts_isolation(receipts_test_isolation: None) -> None:  # noqa: PT004
    """Auto-apply receipts_test_isolation to all tests in tests/llm/.

    M-16: tests pass spy receipt_writers so they don't touch the real DB,
    but the cache-reset is still needed to prevent cross-test key leakage
    when RECEIPT_SIGNING_KEY is patched.
    """


@pytest.fixture
def mock_llm_client() -> object:
    """M-07: shared mock LLMClient with fixed canned response.

    Returns a MagicMock whose complete() returns LLMResponse(latency_us=1000).
    Tests that need different text or model_id should build their own mock.
    """
    from unittest.mock import MagicMock

    from samantha_server.llm.client import LLMResponse

    mock = MagicMock()
    mock.model_id = "test-model"
    mock.complete.return_value = LLMResponse(
        text="Canned response.",
        input_tokens=10,
        output_tokens=5,
        model_id="test-model",
        latency_us=1000,
    )
    return mock
