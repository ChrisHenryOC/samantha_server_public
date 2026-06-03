"""Tests for GH-321 Phase 2: finish_reason plumbing in OMLXClient.

S2.2 — complete() populates LLMResponse.finish_reason from choices[0].finish_reason.
S2.3 — complete_json() populates LLMResponse.finish_reason from choices[0].finish_reason.
"""

from __future__ import annotations

import httpx
import pytest

_CANNED_MODELS_RESPONSE = {
    "data": [{"id": "test-model", "object": "model"}],
    "object": "list",
}


# ---------------------------------------------------------------------------
# S2.2 — complete() finish_reason plumbing
# ---------------------------------------------------------------------------


def _make_completions_transport(
    finish_reason: str | None, *, include_key: bool = True
) -> httpx.MockTransport:
    """Return a transport where complete() response has the given finish_reason.

    When include_key=False, the finish_reason key is entirely absent from the
    choices[0] dict (simulates a legacy backend that doesn't emit the field).
    """
    choice: dict = {"text": "hi"}
    if include_key:
        choice["finish_reason"] = finish_reason

    body = {
        "choices": [choice],
        "usage": {"prompt_tokens": 3, "completion_tokens": 1},
    }

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=_CANNED_MODELS_RESPONSE)
        return httpx.Response(200, json=body)

    return httpx.MockTransport(_handler)


@pytest.mark.parametrize(
    "finish_reason,expected",
    [
        ("length", "length"),
        ("stop", "stop"),
        ("content_filter", "content_filter"),
    ],
)
def test_complete_populates_finish_reason(finish_reason: str, expected: str) -> None:
    """complete() LLMResponse.finish_reason matches choices[0].finish_reason from the wire."""
    from samantha_server.llm.omlx_client import OMLXClient

    client = OMLXClient(_transport=_make_completions_transport(finish_reason=finish_reason))
    result = client.complete("ping")
    assert result.finish_reason == expected


def test_complete_finish_reason_none_when_field_absent() -> None:
    """complete() LLMResponse.finish_reason is None when choices[0] lacks finish_reason key."""
    from samantha_server.llm.omlx_client import OMLXClient

    client = OMLXClient(
        _transport=_make_completions_transport(finish_reason=None, include_key=False)
    )
    result = client.complete("ping")
    assert result.finish_reason is None


def test_complete_finish_reason_none_when_field_is_none() -> None:
    """complete() LLMResponse.finish_reason is None when choices[0].finish_reason is null."""
    from samantha_server.llm.omlx_client import OMLXClient

    client = OMLXClient(
        _transport=_make_completions_transport(finish_reason=None, include_key=True)
    )
    result = client.complete("ping")
    assert result.finish_reason is None


# ---------------------------------------------------------------------------
# S2.3 — complete_json() finish_reason plumbing
# ---------------------------------------------------------------------------


def _make_chat_transport_with_finish(
    finish_reason: str | None, *, include_key: bool = True
) -> httpx.MockTransport:
    """Return a transport where complete_json() response has the given finish_reason.

    finish_reason is at choices[0].finish_reason (OpenAI chat-completions shape),
    NOT under choices[0].message.
    """
    choice: dict = {"message": {"content": "{}"}}
    if include_key:
        choice["finish_reason"] = finish_reason

    body = {
        "choices": [choice],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
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


@pytest.mark.parametrize(
    "finish_reason,expected",
    [
        ("length", "length"),
        ("stop", "stop"),
        ("content_filter", "content_filter"),
    ],
)
def test_complete_json_populates_finish_reason(finish_reason: str, expected: str) -> None:
    """complete_json() LLMResponse.finish_reason matches choices[0].finish_reason from the wire."""
    from samantha_server.llm.omlx_client import OMLXClient

    client = OMLXClient(_transport=_make_chat_transport_with_finish(finish_reason=finish_reason))
    result = client.complete_json(_TEST_MESSAGES, schema=_TEST_SCHEMA, schema_name="t")
    assert result.finish_reason == expected


def test_complete_json_finish_reason_none_when_field_absent() -> None:
    """complete_json() LLMResponse.finish_reason is None when choices[0] lacks finish_reason key."""
    from samantha_server.llm.omlx_client import OMLXClient

    client = OMLXClient(
        _transport=_make_chat_transport_with_finish(finish_reason=None, include_key=False)
    )
    result = client.complete_json(_TEST_MESSAGES, schema=_TEST_SCHEMA, schema_name="t")
    assert result.finish_reason is None


def test_complete_json_finish_reason_none_when_field_is_none() -> None:
    """complete_json() LLMResponse.finish_reason is None when choices[0].finish_reason is null."""
    from samantha_server.llm.omlx_client import OMLXClient

    client = OMLXClient(
        _transport=_make_chat_transport_with_finish(finish_reason=None, include_key=True)
    )
    result = client.complete_json(_TEST_MESSAGES, schema=_TEST_SCHEMA, schema_name="t")
    assert result.finish_reason is None
