"""Tests for GH-321: WARNING log when oMLX output reaches max_tokens.

S1.1 — complete() emits WARNING when output_tokens == max_tokens.
S1.2 — complete_json() emits WARNING when output_tokens == max_tokens.
"""

from __future__ import annotations

import logging

import httpx
import pytest

from samantha_server.llm.client import LLMResponse  # noqa: F401

_CANNED_MODELS_RESPONSE = {
    "data": [{"id": "test-model", "object": "model"}],
    "object": "list",
}


# ---------------------------------------------------------------------------
# S1.1 — complete() WARNING on max_tokens hit
# ---------------------------------------------------------------------------


def _make_complete_transport(completion_tokens: int, max_tokens: int) -> httpx.MockTransport:
    """Transport for complete(): probe succeeds, completions returns given token count."""
    body = {
        "choices": [{"text": "x"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": completion_tokens},
    }

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=_CANNED_MODELS_RESPONSE)
        return httpx.Response(200, json=body)

    return httpx.MockTransport(_handler)


def test_complete_warns_when_output_tokens_equals_max_tokens(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """complete() emits WARNING with 'LLM output reached max_tokens=N' when
    output_tokens == max_tokens (the request limit)."""
    from samantha_server.llm.omlx_client import OMLXClient

    max_tok = 16
    client = OMLXClient(
        _transport=_make_complete_transport(completion_tokens=max_tok, max_tokens=max_tok)
    )
    with caplog.at_level(logging.WARNING, logger="samantha_server.llm.omlx_client"):
        client.complete("ping", max_tokens=max_tok)

    warning_msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any(f"LLM output reached max_tokens={max_tok}" in m for m in warning_msgs), (
        f"Expected WARNING containing 'LLM output reached max_tokens={max_tok}'; "
        f"got: {warning_msgs}"
    )


def test_complete_no_warning_when_output_tokens_below_max_tokens(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """complete() does NOT emit max_tokens WARNING when output_tokens < max_tokens."""
    from samantha_server.llm.omlx_client import OMLXClient

    max_tok = 16
    client = OMLXClient(
        _transport=_make_complete_transport(completion_tokens=5, max_tokens=max_tok)
    )
    with caplog.at_level(logging.WARNING, logger="samantha_server.llm.omlx_client"):
        client.complete("ping", max_tokens=max_tok)

    warning_msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert not any("LLM output reached max_tokens" in m for m in warning_msgs), (
        f"Unexpected WARNING about max_tokens; got: {warning_msgs}"
    )


def test_complete_warns_when_output_tokens_exceeds_max_tokens(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """complete() emits WARNING on overflow (output_tokens > max_tokens — review #3).

    The production guard uses `>=` (not `==`) so that any backend reporting
    completion_tokens slightly above max_tokens — e.g., due to a counting
    discrepancy at the EOS boundary — is still surfaced. This test pins
    the overflow branch.
    """
    from samantha_server.llm.omlx_client import OMLXClient

    max_tok = 16
    client = OMLXClient(
        _transport=_make_complete_transport(completion_tokens=max_tok + 1, max_tokens=max_tok)
    )
    with caplog.at_level(logging.WARNING, logger="samantha_server.llm.omlx_client"):
        client.complete("ping", max_tokens=max_tok)

    warning_msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any(f"LLM output reached max_tokens={max_tok}" in m for m in warning_msgs), (
        f"Expected WARNING on overflow; got: {warning_msgs}"
    )


def test_complete_no_warning_at_tight_boundary_below_cap(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """complete() does NOT emit WARNING at output_tokens == max_tokens - 1 (off-by-one guard)."""
    from samantha_server.llm.omlx_client import OMLXClient

    max_tok = 16
    client = OMLXClient(
        _transport=_make_complete_transport(completion_tokens=max_tok - 1, max_tokens=max_tok)
    )
    with caplog.at_level(logging.WARNING, logger="samantha_server.llm.omlx_client"):
        client.complete("ping", max_tokens=max_tok)

    warning_msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert not any("LLM output reached max_tokens" in m for m in warning_msgs), (
        f"Unexpected WARNING just below cap; got: {warning_msgs}"
    )


# ---------------------------------------------------------------------------
# S1.2 — complete_json() WARNING on max_tokens hit
# ---------------------------------------------------------------------------


def _make_chat_transport(completion_tokens: int) -> httpx.MockTransport:
    """Transport for complete_json(): probe succeeds, chat completions returns given token count."""
    body = {
        "choices": [{"message": {"content": "{}"}, "finish_reason": "length"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": completion_tokens},
    }

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=_CANNED_MODELS_RESPONSE)
        return httpx.Response(200, json=body)

    return httpx.MockTransport(_handler)


_TEST_SCHEMA = {
    "type": "object",
    "properties": {"answer_type": {"type": "string"}},
    "additionalProperties": False,
    "required": ["answer_type"],
}
_TEST_MESSAGES = [{"role": "user", "content": "Which orders are ready?"}]


def test_complete_json_warns_when_output_tokens_equals_max_tokens(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """complete_json() emits WARNING with 'LLM output reached max_tokens=N' when
    output_tokens == max_tokens (the request limit)."""
    from samantha_server.llm.omlx_client import OMLXClient

    max_tok = 32
    client = OMLXClient(_transport=_make_chat_transport(completion_tokens=max_tok))
    with caplog.at_level(logging.WARNING, logger="samantha_server.llm.omlx_client"):
        client.complete_json(
            _TEST_MESSAGES,
            schema=_TEST_SCHEMA,
            schema_name="test_schema",
            max_tokens=max_tok,
        )

    warning_msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any(f"LLM output reached max_tokens={max_tok}" in m for m in warning_msgs), (
        f"Expected WARNING containing 'LLM output reached max_tokens={max_tok}'; "
        f"got: {warning_msgs}"
    )


def test_complete_json_no_warning_when_output_tokens_below_max_tokens(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """complete_json() does NOT emit max_tokens WARNING when output_tokens < max_tokens."""
    from samantha_server.llm.omlx_client import OMLXClient

    max_tok = 32
    client = OMLXClient(_transport=_make_chat_transport(completion_tokens=5))
    with caplog.at_level(logging.WARNING, logger="samantha_server.llm.omlx_client"):
        client.complete_json(
            _TEST_MESSAGES,
            schema=_TEST_SCHEMA,
            schema_name="test_schema",
            max_tokens=max_tok,
        )

    warning_msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert not any("LLM output reached max_tokens" in m for m in warning_msgs), (
        f"Unexpected WARNING about max_tokens; got: {warning_msgs}"
    )


def test_complete_json_warns_when_output_tokens_exceeds_max_tokens(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """complete_json() emits WARNING on overflow (output_tokens > max_tokens — review #3)."""
    from samantha_server.llm.omlx_client import OMLXClient

    max_tok = 32
    client = OMLXClient(_transport=_make_chat_transport(completion_tokens=max_tok + 1))
    with caplog.at_level(logging.WARNING, logger="samantha_server.llm.omlx_client"):
        client.complete_json(
            _TEST_MESSAGES,
            schema=_TEST_SCHEMA,
            schema_name="test_schema",
            max_tokens=max_tok,
        )

    warning_msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any(f"LLM output reached max_tokens={max_tok}" in m for m in warning_msgs), (
        f"Expected WARNING on overflow; got: {warning_msgs}"
    )
