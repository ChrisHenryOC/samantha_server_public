"""Tests for OMLXClient.complete_json via /v1/chat/completions (GH-192 Slice 5).

All tests use httpx.MockTransport — no live server required.
"""

from __future__ import annotations

import json

import httpx
import pytest

from samantha_server.errors import LLMInferenceError, LLMTimeoutError
from samantha_server.llm.client import LLMResponse

# ---------------------------------------------------------------------------
# Shared helpers (mirror pattern from test_omlx_client.py)
# ---------------------------------------------------------------------------

_CANNED_MODELS_RESPONSE = {
    "data": [{"id": "test-model", "object": "model"}],
    "object": "list",
}

_CANNED_CHAT_RESPONSE = {
    "choices": [{"message": {"content": '{"answer_type":"no_orders","order_ids":[]}'}}],
    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
}


def _make_probe_ok_chat_transport(
    chat_body: dict | None = None,
    *,
    status_code: int = 200,
    raise_exc: Exception | None = None,
) -> httpx.MockTransport:
    """Probe succeeds (GET /v1/models → 200); chat completions uses chat_body.

    If raise_exc is set, it is raised on POST (inference) requests.
    If status_code != 200, the POST returns that status with an error body.
    """
    body = chat_body if chat_body is not None else _CANNED_CHAT_RESPONSE

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=_CANNED_MODELS_RESPONSE)
        # POST — inference request
        if raise_exc is not None:
            raise raise_exc
        if status_code != 200:
            return httpx.Response(status_code, json={"error": "server error"})
        return httpx.Response(200, json=body)

    return httpx.MockTransport(_handler)


_TEST_SCHEMA = {
    "type": "object",
    "properties": {"answer_type": {"type": "string"}},
    "additionalProperties": False,
    "required": ["answer_type"],
}
_TEST_MESSAGES = [{"role": "user", "content": "Which orders are ready?"}]


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_complete_json_returns_llm_response() -> None:
    """complete_json() returns an LLMResponse on a canned 200 chat response."""
    from samantha_server.llm.omlx_client import OMLXClient

    client = OMLXClient(_transport=_make_probe_ok_chat_transport())
    result = client.complete_json(
        _TEST_MESSAGES,
        schema=_TEST_SCHEMA,
        schema_name="test_schema",
    )
    assert isinstance(result, LLMResponse)


def test_complete_json_text_equals_message_content() -> None:
    """complete_json() .text equals choices[0].message.content."""
    from samantha_server.llm.omlx_client import OMLXClient

    client = OMLXClient(_transport=_make_probe_ok_chat_transport())
    result = client.complete_json(
        _TEST_MESSAGES,
        schema=_TEST_SCHEMA,
        schema_name="test_schema",
    )
    assert result.text == '{"answer_type":"no_orders","order_ids":[]}'


def test_complete_json_token_counts() -> None:
    """complete_json() token counts match usage from the response."""
    from samantha_server.llm.omlx_client import OMLXClient

    client = OMLXClient(_transport=_make_probe_ok_chat_transport())
    result = client.complete_json(
        _TEST_MESSAGES,
        schema=_TEST_SCHEMA,
        schema_name="test_schema",
    )
    assert result.input_tokens == 10
    assert result.output_tokens == 5


def test_complete_json_latency_us_non_negative() -> None:
    """complete_json() .latency_us is >= 0."""
    from samantha_server.llm.omlx_client import OMLXClient

    client = OMLXClient(_transport=_make_probe_ok_chat_transport())
    result = client.complete_json(
        _TEST_MESSAGES,
        schema=_TEST_SCHEMA,
        schema_name="test_schema",
    )
    assert result.latency_us >= 0


def test_complete_json_uses_chat_completions_endpoint() -> None:
    """complete_json() sends POST to /v1/chat/completions (not /v1/completions)."""
    captured: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=_CANNED_MODELS_RESPONSE)
        return httpx.Response(200, json=_CANNED_CHAT_RESPONSE)

    from samantha_server.llm.omlx_client import OMLXClient

    client = OMLXClient(_transport=httpx.MockTransport(_handler))
    captured.clear()
    client.complete_json(_TEST_MESSAGES, schema=_TEST_SCHEMA, schema_name="test")

    post_reqs = [r for r in captured if r.method == "POST"]
    assert len(post_reqs) == 1
    assert "/v1/chat/completions" in str(post_reqs[0].url)


def test_complete_json_request_body_contains_response_format() -> None:
    """complete_json() request body includes response_format with json_schema."""
    captured: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=_CANNED_MODELS_RESPONSE)
        return httpx.Response(200, json=_CANNED_CHAT_RESPONSE)

    from samantha_server.llm.omlx_client import OMLXClient

    client = OMLXClient(_transport=httpx.MockTransport(_handler))
    captured.clear()
    client.complete_json(_TEST_MESSAGES, schema=_TEST_SCHEMA, schema_name="test_schema")

    post_reqs = [r for r in captured if r.method == "POST"]
    body = json.loads(post_reqs[0].content)
    assert "response_format" in body
    rf = body["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["name"] == "test_schema"
    assert rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["schema"] == _TEST_SCHEMA


def test_complete_json_request_body_contains_messages() -> None:
    """complete_json() request body includes the messages array."""
    captured: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=_CANNED_MODELS_RESPONSE)
        return httpx.Response(200, json=_CANNED_CHAT_RESPONSE)

    from samantha_server.llm.omlx_client import OMLXClient

    client = OMLXClient(_transport=httpx.MockTransport(_handler))
    captured.clear()
    client.complete_json(_TEST_MESSAGES, schema=_TEST_SCHEMA, schema_name="test")

    post_reqs = [r for r in captured if r.method == "POST"]
    body = json.loads(post_reqs[0].content)
    assert body["messages"] == _TEST_MESSAGES


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_complete_json_timeout_raises_llm_timeout_error() -> None:
    """TimeoutException from transport during complete_json() raises LLMTimeoutError."""
    from samantha_server.llm.omlx_client import OMLXClient

    exc = httpx.TimeoutException("timed out", request=None)  # type: ignore[arg-type]
    client = OMLXClient(_transport=_make_probe_ok_chat_transport(raise_exc=exc))

    with pytest.raises(LLMTimeoutError) as exc_info:
        client.complete_json(_TEST_MESSAGES, schema=_TEST_SCHEMA, schema_name="test")

    assert exc_info.value.model_id == client.model_id


def test_complete_json_http_500_raises_llm_inference_error() -> None:
    """HTTP 500 from complete_json() raises LLMInferenceError."""
    from samantha_server.llm.omlx_client import OMLXClient

    client = OMLXClient(_transport=_make_probe_ok_chat_transport(status_code=500))

    with pytest.raises(LLMInferenceError) as exc_info:
        client.complete_json(_TEST_MESSAGES, schema=_TEST_SCHEMA, schema_name="test")

    assert exc_info.value.model_id == client.model_id


def test_complete_json_missing_usage_raises_llm_inference_error() -> None:
    """Response without 'usage' key raises LLMInferenceError."""
    from samantha_server.llm.omlx_client import OMLXClient

    body_no_usage = {
        "choices": [{"message": {"content": '{"answer_type":"no_orders"}'}}],
        # no "usage" key
    }
    client = OMLXClient(_transport=_make_probe_ok_chat_transport(chat_body=body_no_usage))

    with pytest.raises(LLMInferenceError) as exc_info:
        client.complete_json(_TEST_MESSAGES, schema=_TEST_SCHEMA, schema_name="test")

    assert exc_info.value.model_id == client.model_id


def test_complete_json_missing_message_content_raises_llm_inference_error() -> None:
    """Response missing choices[0].message.content raises LLMInferenceError."""
    from samantha_server.llm.omlx_client import OMLXClient

    body_no_content = {
        "choices": [{"message": {}}],  # no "content"
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
    client = OMLXClient(_transport=_make_probe_ok_chat_transport(chat_body=body_no_content))

    with pytest.raises(LLMInferenceError) as exc_info:
        client.complete_json(_TEST_MESSAGES, schema=_TEST_SCHEMA, schema_name="test")

    assert exc_info.value.model_id == client.model_id


def test_complete_json_null_message_content_raises_llm_inference_error() -> None:
    """Response with choices[0].message.content == null raises LLMInferenceError.

    oMLX can emit content=null on content-filter stops. Without an explicit
    guard the None propagates to _sha256_hex(None).encode() and crashes as
    AttributeError — outside the LLMClientError hierarchy, so the handler's
    refusal-decision catch misses it and the request 500s.
    """
    from samantha_server.llm.omlx_client import OMLXClient

    body_null_content = {
        "choices": [{"message": {"content": None}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 0},
    }
    client = OMLXClient(_transport=_make_probe_ok_chat_transport(chat_body=body_null_content))

    with pytest.raises(LLMInferenceError) as exc_info:
        client.complete_json(_TEST_MESSAGES, schema=_TEST_SCHEMA, schema_name="test")

    assert exc_info.value.model_id == client.model_id


# ---------------------------------------------------------------------------
# GH-229 — Request-timing instrumentation (S3: complete_json() both paths)
# ---------------------------------------------------------------------------


def test_complete_json_success_emits_timing_log_with_all_phase_keys(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """complete_json() success path emits a structured INFO log with all timing phase keys.

    The log must contain pre_send_us, server_elapsed_us, response_read_us,
    total_us, outcome=success, and the chat completions path.
    """
    import logging

    from samantha_server.llm.omlx_client import OMLXClient

    client = OMLXClient(_transport=_make_probe_ok_chat_transport())
    with caplog.at_level(logging.INFO, logger="samantha_server.llm.omlx_client"):
        client.complete_json(_TEST_MESSAGES, schema=_TEST_SCHEMA, schema_name="test")

    timing_records = [r for r in caplog.records if "pre_send_us" in r.getMessage()]
    assert len(timing_records) == 1, "Expected exactly one timing log record"
    msg = timing_records[0].getMessage()
    assert "server_elapsed_us" in msg
    assert "response_read_us" in msg
    assert "total_us" in msg
    assert "outcome=success" in msg
    assert timing_records[0].levelno == logging.INFO


def test_complete_json_transport_error_raises_llm_inference_error_and_emits_timing_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """httpx.ConnectError during complete_json() raises LLMInferenceError AND emits
    a WARNING timing log with outcome=transport_error.

    Pre-fix: the bare except-Exception arm skips _log_timing entirely.
    """
    import logging

    exc = httpx.ConnectError("connection refused", request=None)  # type: ignore[arg-type]
    from samantha_server.llm.omlx_client import OMLXClient

    client = OMLXClient(_transport=_make_probe_ok_chat_transport(raise_exc=exc))
    with (
        caplog.at_level(logging.WARNING, logger="samantha_server.llm.omlx_client"),
        pytest.raises(LLMInferenceError),
    ):
        client.complete_json(_TEST_MESSAGES, schema=_TEST_SCHEMA, schema_name="test")

    timing_records = [r for r in caplog.records if "outcome=transport_error" in r.getMessage()]
    assert len(timing_records) == 1, "Expected exactly one transport_error timing log record"
    msg = timing_records[0].getMessage()
    assert "pre_send_us=" in msg
    assert "total_us=" in msg
    assert timing_records[0].levelno == logging.WARNING


def test_complete_json_timeout_emits_warning_timing_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """complete_json() timeout path emits a WARNING log with outcome=timeout and timing fields."""
    import logging

    exc = httpx.TimeoutException("timed out", request=None)  # type: ignore[arg-type]
    from samantha_server.llm.omlx_client import OMLXClient

    client = OMLXClient(_transport=_make_probe_ok_chat_transport(raise_exc=exc))
    with (
        caplog.at_level(logging.WARNING, logger="samantha_server.llm.omlx_client"),
        pytest.raises(LLMTimeoutError),
    ):
        client.complete_json(_TEST_MESSAGES, schema=_TEST_SCHEMA, schema_name="test")

    timing_records = [r for r in caplog.records if "pre_send_us" in r.getMessage()]
    assert len(timing_records) == 1, "Expected exactly one timing log record"
    msg = timing_records[0].getMessage()
    assert "server_elapsed_us" in msg
    assert "response_read_us" in msg
    assert "total_us" in msg
    assert "outcome=timeout" in msg
    assert timing_records[0].levelno == logging.WARNING


# ---------------------------------------------------------------------------
# GH-271: chat_template_kwargs plumbing through complete_json
# ---------------------------------------------------------------------------


def _captured_body_transport() -> tuple[list[dict], httpx.MockTransport]:
    """Return (captured-bodies-list, transport) — body of each POST appended on call."""
    captured: list[dict] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=_CANNED_MODELS_RESPONSE)
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=_CANNED_CHAT_RESPONSE)

    return captured, httpx.MockTransport(_handler)


def test_complete_json_omits_chat_template_kwargs_when_client_has_none() -> None:
    """Default-off: a client with no chat_template_kwargs sends no field in the body.

    Preserves the pre-GH-271 request shape for the in-flight Gemma baseline
    and any model not explicitly mapped in chat_template_config.
    """
    from samantha_server.llm.omlx_client import OMLXClient

    captured, transport = _captured_body_transport()
    client = OMLXClient(_transport=transport)
    client.complete_json(_TEST_MESSAGES, schema=_TEST_SCHEMA, schema_name="test_schema")

    assert len(captured) == 1
    assert "chat_template_kwargs" not in captured[0], (
        f"client with no kwargs must not send the field; body keys={list(captured[0].keys())}"
    )


def test_complete_json_includes_chat_template_kwargs_when_client_has_them() -> None:
    """When the client carries chat_template_kwargs, they're included verbatim in the body.

    GH-271: Qwen3.5/3.6 need `enable_thinking=False` here to suppress the
    `<think>` reasoning tokens that would otherwise bleed into the JSON
    output. This test verifies the field reaches the wire.
    """
    from samantha_server.llm.omlx_client import OMLXClient

    captured, transport = _captured_body_transport()
    client = OMLXClient(
        _transport=transport,
        chat_template_kwargs={"enable_thinking": False},
    )
    client.complete_json(_TEST_MESSAGES, schema=_TEST_SCHEMA, schema_name="test_schema")

    assert len(captured) == 1
    assert captured[0].get("chat_template_kwargs") == {"enable_thinking": False}


def test_complete_omits_chat_template_kwargs_when_client_has_none() -> None:
    """Same default-off invariant for the legacy /v1/completions endpoint."""
    from samantha_server.llm.omlx_client import OMLXClient

    captured: list[dict] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=_CANNED_MODELS_RESPONSE)
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [{"text": "ok"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 1},
            },
        )

    client = OMLXClient(_transport=httpx.MockTransport(_handler))
    client.complete("hello")

    assert len(captured) == 1
    assert "chat_template_kwargs" not in captured[0]


def test_complete_includes_chat_template_kwargs_when_client_has_them() -> None:
    """The legacy /v1/completions endpoint also forwards chat_template_kwargs.

    Both endpoints route through the same chat template at the oMLX server
    layer; the kwarg surface should be symmetric across the client's two
    completion methods.
    """
    from samantha_server.llm.omlx_client import OMLXClient

    captured: list[dict] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=_CANNED_MODELS_RESPONSE)
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [{"text": "ok"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 1},
            },
        )

    client = OMLXClient(
        _transport=httpx.MockTransport(_handler),
        chat_template_kwargs={"enable_thinking": False},
    )
    client.complete("hello")

    assert len(captured) == 1
    assert captured[0].get("chat_template_kwargs") == {"enable_thinking": False}


# ---------------------------------------------------------------------------
# GH-271: auto-lookup from chat_template_config based on configured model
#
# These tests exercise OMLXClient constructor behavior (the auto-lookup path
# at __init__) AND its wire-format effect (whether the resulting kwargs show
# up in the request body). They live here rather than in a dedicated
# constructor-only test file because the wire-format assertion via
# `_captured_body_transport` is the load-bearing check — without it the
# auto-lookup might run but silently drop kwargs at body construction.
# PR #272 review #4 considered moving them; co-location with wire-format
# assertions kept them here.
# ---------------------------------------------------------------------------


def test_omlx_client_auto_looks_up_kwargs_from_chat_template_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When no explicit chat_template_kwargs is passed, the client falls back to
    the per-model config lookup keyed on config.LLM_MODEL_NAME.

    Ensures that simply pointing the runtime at Qwen3.5-27B (via env) is
    sufficient — operators don't need a separate config flip to enable
    enable_thinking=False.
    """
    import samantha_server.config as cfg

    monkeypatch.setattr(cfg, "LLM_MODEL_NAME", "mlx-community/Qwen3.5-27B-4bit")  # nosec: model-id

    from samantha_server.llm.omlx_client import OMLXClient

    captured, transport = _captured_body_transport()
    client = OMLXClient(_transport=transport)  # no explicit kwargs
    client.complete_json(_TEST_MESSAGES, schema=_TEST_SCHEMA, schema_name="test")

    assert len(captured) == 1
    assert captured[0].get("chat_template_kwargs") == {"enable_thinking": False}, (
        "OMLXClient should auto-derive chat_template_kwargs from chat_template_config "
        "based on the configured model name"
    )


def test_omlx_client_auto_lookup_returns_no_kwargs_for_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default-off invariant: the in-flight Gemma baseline gets no chat_template_kwargs.

    Catches a regression where the auto-lookup accidentally always-stamps.
    """
    import samantha_server.config as cfg

    monkeypatch.setattr(cfg, "LLM_MODEL_NAME", "gemma-4-26B-A4B-it-MLX-4bit")

    from samantha_server.llm.omlx_client import OMLXClient

    captured, transport = _captured_body_transport()
    client = OMLXClient(_transport=transport)
    client.complete_json(_TEST_MESSAGES, schema=_TEST_SCHEMA, schema_name="test")

    assert len(captured) == 1
    assert "chat_template_kwargs" not in captured[0]


def test_explicit_chat_template_kwargs_override_auto_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit constructor kwarg wins over the auto-lookup.

    Pin the precedence rule so a future caller can short-circuit the
    config table for tests or one-off experiments.
    """
    import samantha_server.config as cfg

    monkeypatch.setattr(cfg, "LLM_MODEL_NAME", "mlx-community/Qwen3.5-27B-4bit")  # nosec: model-id

    from samantha_server.llm.omlx_client import OMLXClient

    captured, transport = _captured_body_transport()
    client = OMLXClient(
        _transport=transport,
        chat_template_kwargs={"enable_thinking": True, "override": "test"},
    )
    client.complete_json(_TEST_MESSAGES, schema=_TEST_SCHEMA, schema_name="test")

    assert len(captured) == 1
    assert captured[0].get("chat_template_kwargs") == {
        "enable_thinking": True,
        "override": "test",
    }, "explicit constructor kwargs must take precedence over the auto-lookup table"


def test_explicit_empty_dict_is_treated_as_no_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PR #272 review #1: `chat_template_kwargs={}` must NOT stamp the field on the wire.

    Pre-fix, the `is not None` guard accepted `{}` as a non-default value
    and emitted `"chat_template_kwargs": {}` in every request body. That
    diverges from the `None` path's behavior in a way oMLX may silently
    accept (no override) or silently reject (empty-object validation),
    with no diagnostic pointer. Tighten the guard so `{}` behaves
    identically to `None`: no field emitted, no auto-lookup performed.

    Auto-lookup is intentionally suppressed here too — passing `{}`
    explicitly is the documented "I want no kwargs even if a table
    entry would set some" escape hatch.
    """
    import samantha_server.config as cfg

    monkeypatch.setattr(cfg, "LLM_MODEL_NAME", "mlx-community/Qwen3.5-27B-4bit")  # nosec: model-id

    from samantha_server.llm.omlx_client import OMLXClient

    captured, transport = _captured_body_transport()
    client = OMLXClient(_transport=transport, chat_template_kwargs={})
    client.complete_json(_TEST_MESSAGES, schema=_TEST_SCHEMA, schema_name="test")

    assert len(captured) == 1
    assert "chat_template_kwargs" not in captured[0], (
        "chat_template_kwargs={} must be treated as 'no override' — the field is "
        f"omitted from the wire, identical to the None path. body keys={list(captured[0].keys())}"
    )
