"""Tests for OMLXClient — loopback HTTP client to an oMLX server.

Unit tests use httpx.MockTransport to avoid real network calls.
Live-server tests are decorated @pytest.mark.local_omlx so that CI's
addopts filter excludes them. Run locally with:
    uv run pytest tests/llm/test_omlx_client.py -m local_omlx

These tests do NOT require any optional dependency group beyond the
default install (httpx is in the main dependency group).
"""

from __future__ import annotations

import json

import httpx
import pytest

from samantha_server.errors import (
    LLMInferenceError,
    LLMModelLoadError,
    LLMTimeoutError,
)
from samantha_server.llm.client import LLMResponse

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CANNED_COMPLETIONS_RESPONSE = {
    "choices": [{"text": "hi"}],
    "usage": {"prompt_tokens": 3, "completion_tokens": 1},
}

# Canned /v1/models response (probe target after fix #1)
_CANNED_MODELS_RESPONSE = {
    "data": [{"id": "test-model", "object": "model"}],
    "object": "list",
}


def _make_transport_with_status(status_code: int) -> httpx.MockTransport:
    """Return a MockTransport that always responds with *status_code*."""

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"error": "service unavailable"})

    return httpx.MockTransport(_handler)


def _make_ok_transport(body: dict[str, object] | None = None) -> httpx.MockTransport:
    """Return a MockTransport that responds 200 with *body* on any request.

    For probes (GET /v1/models) it returns *body* or _CANNED_MODELS_RESPONSE.
    For complete() calls (POST /v1/completions) it returns *body* or
    _CANNED_COMPLETIONS_RESPONSE.  When *body* is provided it is used for all
    requests; when omitted, the handler routes by method.
    """
    if body is not None:
        fixed = body

        def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=fixed)

    else:

        def _handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(200, json=_CANNED_MODELS_RESPONSE)
            return httpx.Response(200, json=_CANNED_COMPLETIONS_RESPONSE)

    return httpx.MockTransport(_handler)


def _make_probe_ok_complete_transport(
    complete_body: dict[str, object],
) -> httpx.MockTransport:
    """Probe succeeds (GET /v1/models → 200 + models JSON); complete uses *complete_body*."""

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=_CANNED_MODELS_RESPONSE)
        return httpx.Response(200, json=complete_body)

    return httpx.MockTransport(_handler)


# ---------------------------------------------------------------------------
# Slice 3 — construction probe
# ---------------------------------------------------------------------------


def test_omlx_client_construction_503_raises_model_load_error() -> None:
    """OMLXClient with a transport that returns 503 raises LLMModelLoadError."""
    from samantha_server.llm.omlx_client import OMLXClient

    with pytest.raises(LLMModelLoadError) as exc_info:
        OMLXClient(_transport=_make_transport_with_status(503))

    assert exc_info.value.model_path  # non-empty


def test_omlx_client_construction_model_path_equals_base_url() -> None:
    """LLMModelLoadError.model_path equals the configured base URL."""
    from samantha_server import config
    from samantha_server.llm.omlx_client import OMLXClient

    with pytest.raises(LLMModelLoadError) as exc_info:
        OMLXClient(_transport=_make_transport_with_status(503))

    assert exc_info.value.model_path == config.LLM_OMLX_BASE_URL


def test_omlx_client_construction_error_message_non_empty() -> None:
    """LLMModelLoadError message from a failed probe is non-empty."""
    from samantha_server.llm.omlx_client import OMLXClient

    with pytest.raises(LLMModelLoadError) as exc_info:
        OMLXClient(_transport=_make_transport_with_status(503))

    assert len(str(exc_info.value)) > 0


def test_omlx_client_construction_ok_succeeds() -> None:
    """OMLXClient with a transport returning 200 constructs without error."""
    from samantha_server.llm.omlx_client import OMLXClient

    client = OMLXClient(_transport=_make_ok_transport())
    assert isinstance(client, OMLXClient)


# ---------------------------------------------------------------------------
# Slice 4 — complete() happy path
# ---------------------------------------------------------------------------


def test_omlx_complete_returns_llm_response() -> None:
    """complete() returns an LLMResponse on a canned 200 response."""
    from samantha_server.llm.omlx_client import OMLXClient

    client = OMLXClient(_transport=_make_ok_transport())
    result = client.complete("ping")

    assert isinstance(result, LLMResponse)


def test_omlx_complete_text() -> None:
    """complete() LLMResponse.text matches choices[0].text from the response."""
    from samantha_server.llm.omlx_client import OMLXClient

    client = OMLXClient(_transport=_make_ok_transport())
    result = client.complete("ping")

    assert result.text == "hi"


def test_omlx_complete_token_counts() -> None:
    """complete() LLMResponse token counts match usage from the response."""
    from samantha_server.llm.omlx_client import OMLXClient

    client = OMLXClient(_transport=_make_ok_transport())
    result = client.complete("ping")

    assert result.input_tokens == 3
    assert result.output_tokens == 1


def test_omlx_complete_model_id() -> None:
    """complete() LLMResponse.model_id equals config.LLM_MODEL_NAME."""
    from samantha_server import config
    from samantha_server.llm.omlx_client import OMLXClient

    client = OMLXClient(_transport=_make_ok_transport())
    result = client.complete("ping")

    assert result.model_id == config.LLM_MODEL_NAME


def test_omlx_complete_latency_us_non_negative() -> None:
    """complete() LLMResponse.latency_us is >= 0."""
    from samantha_server.llm.omlx_client import OMLXClient

    client = OMLXClient(_transport=_make_ok_transport())
    result = client.complete("ping")

    assert result.latency_us >= 0


def test_omlx_complete_model_id_matches_client_model_id() -> None:
    """LLMResponse.model_id matches client.model_id (Protocol invariant)."""
    from samantha_server.llm.omlx_client import OMLXClient

    client = OMLXClient(_transport=_make_ok_transport())
    result = client.complete("ping")

    assert result.model_id == client.model_id


def test_omlx_complete_request_body_shape() -> None:
    """complete() sends a request with model, prompt, max_tokens, temperature, stream=False."""
    captured: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=_CANNED_MODELS_RESPONSE)
        return httpx.Response(200, json=_CANNED_COMPLETIONS_RESPONSE)

    from samantha_server import config
    from samantha_server.llm.omlx_client import OMLXClient

    client = OMLXClient(_transport=httpx.MockTransport(_handler))
    # The constructor sends a probe request (GET /v1/models); capture only the
    # inference request (POST /v1/completions).
    captured.clear()
    client.complete("ping", max_tokens=16, temperature=0.5)

    assert len(captured) == 1
    body = json.loads(captured[0].content)
    assert body["model"] == config.LLM_MODEL_NAME
    assert body["prompt"] == "ping"
    assert body["max_tokens"] == 16
    assert body["temperature"] == 0.5
    assert body["stream"] is False


def test_omlx_complete_auth_header_sent_when_token_set() -> None:
    """When LLM_OMLX_AUTH_TOKEN is set, Authorization: Bearer ... header is sent on complete()."""
    captured: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=_CANNED_MODELS_RESPONSE)
        return httpx.Response(200, json=_CANNED_COMPLETIONS_RESPONSE)

    from samantha_server.llm.omlx_client import OMLXClient

    client = OMLXClient(
        _transport=httpx.MockTransport(_handler),
        auth_token="test-bearer-token",
    )
    # The constructor sends a probe request; capture the inference request separately.
    captured.clear()
    client.complete("ping")

    assert len(captured) == 1
    assert captured[0].headers.get("Authorization") == "Bearer test-bearer-token"


def test_omlx_complete_no_auth_header_when_token_not_set() -> None:
    """When no auth token is set, no Authorization header is sent."""
    captured: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=_CANNED_MODELS_RESPONSE)
        return httpx.Response(200, json=_CANNED_COMPLETIONS_RESPONSE)

    from samantha_server.llm.omlx_client import OMLXClient

    client = OMLXClient(
        _transport=httpx.MockTransport(_handler),
        auth_token=None,
    )
    # The constructor sends a probe request; capture the inference request separately.
    captured.clear()
    client.complete("ping")

    assert len(captured) == 1
    assert "Authorization" not in captured[0].headers


# ---------------------------------------------------------------------------
# Slice 5 — complete() error paths
# ---------------------------------------------------------------------------


def test_omlx_complete_timeout_raises_llm_timeout_error() -> None:
    """httpx.TimeoutException from transport during complete() raises LLMTimeoutError.

    The probe (GET /v1/models) succeeds; the inference POST raises TimeoutException.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            # Probe succeeds.
            return httpx.Response(200, json=_CANNED_MODELS_RESPONSE)
        # Inference request — raise timeout.
        raise httpx.TimeoutException("timed out", request=request)

    from samantha_server.llm.omlx_client import OMLXClient

    client = OMLXClient(_transport=httpx.MockTransport(_handler))

    with pytest.raises(LLMTimeoutError) as exc_info:
        client.complete("ping")

    assert exc_info.value.model_id == client.model_id


def test_omlx_complete_timeout_error_has_timeout_us() -> None:
    """LLMTimeoutError.timeout_us is set from the configured timeout."""

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=_CANNED_MODELS_RESPONSE)
        raise httpx.TimeoutException("timed out", request=request)

    from samantha_server import config
    from samantha_server.llm.omlx_client import OMLXClient

    client = OMLXClient(_transport=httpx.MockTransport(_handler))

    with pytest.raises(LLMTimeoutError) as exc_info:
        client.complete("ping")

    expected_us = int(config.LLM_OMLX_TIMEOUT_S * 1_000_000)
    assert exc_info.value.timeout_us == expected_us


def test_omlx_complete_non_2xx_raises_llm_inference_error() -> None:
    """Non-2xx response from complete() raises LLMInferenceError.

    The client probes successfully (200 /v1/models), then complete() returns 500.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=_CANNED_MODELS_RESPONSE)
        return httpx.Response(500, json={"error": "internal server error"})

    from samantha_server.llm.omlx_client import OMLXClient

    client = OMLXClient(_transport=httpx.MockTransport(_handler))

    with pytest.raises(LLMInferenceError) as exc_info:
        client.complete("ping")

    assert exc_info.value.model_id == client.model_id


def test_omlx_complete_malformed_json_raises_llm_inference_error() -> None:
    """Malformed JSON in complete() response raises LLMInferenceError."""

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=_CANNED_MODELS_RESPONSE)
        return httpx.Response(200, content=b"not-json", headers={"Content-Type": "text/plain"})

    from samantha_server.llm.omlx_client import OMLXClient

    client = OMLXClient(_transport=httpx.MockTransport(_handler))

    with pytest.raises(LLMInferenceError) as exc_info:
        client.complete("ping")

    assert exc_info.value.model_id == client.model_id


def test_omlx_complete_missing_choices_raises_llm_inference_error() -> None:
    """Response with empty choices[] in complete() raises LLMInferenceError."""

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=_CANNED_MODELS_RESPONSE)
        body = {"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 0}}
        return httpx.Response(200, json=body)

    from samantha_server.llm.omlx_client import OMLXClient

    client = OMLXClient(_transport=httpx.MockTransport(_handler))

    with pytest.raises(LLMInferenceError) as exc_info:
        client.complete("ping")

    assert exc_info.value.model_id == client.model_id


def test_omlx_complete_null_text_raises_llm_inference_error() -> None:
    """Response with choices[0].text == null raises LLMInferenceError.

    Same shape as the chat-completions null-content case (test_omlx_complete_json.py):
    null text propagates to LLMResponse(text=...) outside the try block and
    surfaces as Pydantic ValidationError — outside the LLMClientError hierarchy,
    so the handler's refusal catch misses it. Affects the SAMANTHA_LLM_OUTPUT_MODE
    =free_text rollback path.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=_CANNED_MODELS_RESPONSE)
        body = {
            "choices": [{"text": None}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 0},
        }
        return httpx.Response(200, json=body)

    from samantha_server.llm.omlx_client import OMLXClient

    client = OMLXClient(_transport=httpx.MockTransport(_handler))

    with pytest.raises(LLMInferenceError) as exc_info:
        client.complete("ping")

    assert exc_info.value.model_id == client.model_id


# ---------------------------------------------------------------------------
# Fix #1 — Probe uses GET /v1/models; validates response shape
# ---------------------------------------------------------------------------


def test_probe_uses_get_v1_models() -> None:
    """Construction probe sends GET /v1/models, not POST /v1/completions."""
    captured: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_CANNED_MODELS_RESPONSE)

    from samantha_server.llm.omlx_client import OMLXClient

    OMLXClient(_transport=httpx.MockTransport(_handler))

    probe_reqs = [r for r in captured if r.method == "GET"]
    assert len(probe_reqs) == 1
    assert "/v1/models" in str(probe_reqs[0].url)


def test_probe_empty_body_raises_model_load_error() -> None:
    """Probe that receives 200 with empty body {} raises LLMModelLoadError (no data key)."""

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    from samantha_server.llm.omlx_client import OMLXClient

    with pytest.raises(LLMModelLoadError):
        OMLXClient(_transport=httpx.MockTransport(_handler))


def test_probe_200_without_data_key_raises_model_load_error() -> None:
    """Probe that receives 200 with non-models JSON raises LLMModelLoadError."""

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": "ok"})

    from samantha_server.llm.omlx_client import OMLXClient

    with pytest.raises(LLMModelLoadError):
        OMLXClient(_transport=httpx.MockTransport(_handler))


@pytest.mark.parametrize("status_code", [401, 404, 503])
def test_probe_non_2xx_raises_model_load_error(status_code: int) -> None:
    """Non-2xx probe response raises LLMModelLoadError (parametrized over 401/404/503)."""

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"error": "unavailable"})

    from samantha_server.llm.omlx_client import OMLXClient

    with pytest.raises(LLMModelLoadError):
        OMLXClient(_transport=httpx.MockTransport(_handler))


# ---------------------------------------------------------------------------
# Fix #3 — Absent usage key raises LLMInferenceError
# ---------------------------------------------------------------------------


def test_complete_missing_usage_key_raises_inference_error() -> None:
    """complete() response without 'usage' key raises LLMInferenceError."""

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=_CANNED_MODELS_RESPONSE)
        # No 'usage' key
        return httpx.Response(200, json={"choices": [{"text": "hi"}]})

    from samantha_server.llm.omlx_client import OMLXClient

    client = OMLXClient(_transport=httpx.MockTransport(_handler))
    with pytest.raises(LLMInferenceError) as exc_info:
        client.complete("ping")

    assert exc_info.value.model_id == client.model_id


def test_complete_missing_prompt_tokens_raises_inference_error() -> None:
    """complete() response with usage missing prompt_tokens raises LLMInferenceError."""

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=_CANNED_MODELS_RESPONSE)
        return httpx.Response(
            200,
            json={"choices": [{"text": "hi"}], "usage": {"completion_tokens": 1}},
        )

    from samantha_server.llm.omlx_client import OMLXClient

    client = OMLXClient(_transport=httpx.MockTransport(_handler))
    with pytest.raises(LLMInferenceError):
        client.complete("ping")


# ---------------------------------------------------------------------------
# Fix #4 — choices key absent entirely raises LLMInferenceError
# ---------------------------------------------------------------------------


def test_complete_choices_key_absent_raises_inference_error() -> None:
    """Response with no 'choices' key (not empty, entirely absent) raises LLMInferenceError."""

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=_CANNED_MODELS_RESPONSE)
        # No 'choices' key at all
        return httpx.Response(
            200,
            json={"usage": {"prompt_tokens": 3, "completion_tokens": 1}},
        )

    from samantha_server.llm.omlx_client import OMLXClient

    client = OMLXClient(_transport=httpx.MockTransport(_handler))
    with pytest.raises(LLMInferenceError) as exc_info:
        client.complete("ping")

    assert exc_info.value.model_id == client.model_id


# ---------------------------------------------------------------------------
# Fix #6 — Negative token counts raise LLMInferenceError (not ValidationError)
# ---------------------------------------------------------------------------


def test_complete_negative_prompt_tokens_raises_inference_error() -> None:
    """complete() with usage.prompt_tokens=-1 raises LLMInferenceError, not ValidationError."""

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=_CANNED_MODELS_RESPONSE)
        return httpx.Response(
            200,
            json={
                "choices": [{"text": "hi"}],
                "usage": {"prompt_tokens": -1, "completion_tokens": 1},
            },
        )

    from samantha_server.llm.omlx_client import OMLXClient

    client = OMLXClient(_transport=httpx.MockTransport(_handler))
    with pytest.raises(LLMInferenceError) as exc_info:
        client.complete("ping")

    assert exc_info.value.model_id == client.model_id


# ---------------------------------------------------------------------------
# Fix #7 — Auth token does not leak into error cause
# ---------------------------------------------------------------------------


def test_probe_transport_error_cause_does_not_contain_auth_token() -> None:
    """When an httpx exception is raised during probe, LLMModelLoadError.cause does NOT
    contain the auth token (CWE-312)."""

    def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    from samantha_server.llm.omlx_client import OMLXClient

    secret_token = "super-secret-bearer-xyz"
    with pytest.raises(LLMModelLoadError) as exc_info:
        OMLXClient(
            _transport=httpx.MockTransport(_handler),
            auth_token=secret_token,
        )

    assert secret_token not in exc_info.value.cause
    assert secret_token not in str(exc_info.value)


# ---------------------------------------------------------------------------
# Fix #9 — Phase-specific httpx.Timeout shape
# ---------------------------------------------------------------------------


def test_client_uses_structured_timeout() -> None:
    """OMLXClient._client.timeout uses separate connect/read/write/pool values."""
    from samantha_server.llm.omlx_client import OMLXClient

    client = OMLXClient(_transport=_make_ok_transport())
    t = client._client.timeout  # type: ignore[attr-defined]

    # connect and pool should be short (5 s); read should match configured timeout
    assert t.connect == 5.0
    assert t.pool == 5.0
    # read matches the configured inference timeout
    from samantha_server import config

    assert t.read == config.LLM_OMLX_TIMEOUT_S


# ---------------------------------------------------------------------------
# Fix #10 — LLMTimeoutError.__cause__ is preserved (from exc, not from None)
# ---------------------------------------------------------------------------


def test_complete_timeout_error_preserves_cause() -> None:
    """LLMTimeoutError raised by complete() has __cause__ set to the original httpx exception."""

    original_exc = httpx.ReadTimeout("read timed out", request=None)  # type: ignore[arg-type]

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=_CANNED_MODELS_RESPONSE)
        raise original_exc

    from samantha_server.llm.omlx_client import OMLXClient

    client = OMLXClient(_transport=httpx.MockTransport(_handler))
    with pytest.raises(LLMTimeoutError) as exc_info:
        client.complete("ping")

    assert exc_info.value.__cause__ is original_exc


# ---------------------------------------------------------------------------
# Fix #13 — Auth header on probe request
# ---------------------------------------------------------------------------


def test_probe_auth_header_sent_when_token_set() -> None:
    """When auth_token is set, the probe (GET /v1/models) carries Authorization: Bearer."""
    captured: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=_CANNED_MODELS_RESPONSE)
        return httpx.Response(200, json=_CANNED_COMPLETIONS_RESPONSE)

    from samantha_server.llm.omlx_client import OMLXClient

    OMLXClient(_transport=httpx.MockTransport(_handler), auth_token="probe-token")

    probe_reqs = [r for r in captured if r.method == "GET"]
    assert len(probe_reqs) == 1
    assert probe_reqs[0].headers.get("Authorization") == "Bearer probe-token"


# ---------------------------------------------------------------------------
# Slice 7 — live server (excluded by default addopts; requires local oMLX)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# GH-229 — Request-timing instrumentation (S1: complete() success path)
# ---------------------------------------------------------------------------


def test_complete_success_emits_timing_log_with_all_phase_keys(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """complete() success path emits a structured INFO log with all timing phase keys.

    The log must contain pre_send_us, server_elapsed_us, response_read_us,
    total_us, outcome=success, and the completions path.
    """
    import logging

    from samantha_server.llm.omlx_client import OMLXClient

    client = OMLXClient(_transport=_make_ok_transport())
    with caplog.at_level(logging.INFO, logger="samantha_server.llm.omlx_client"):
        client.complete("ping")

    timing_records = [r for r in caplog.records if "pre_send_us" in r.getMessage()]
    assert len(timing_records) == 1, "Expected exactly one timing log record"
    msg = timing_records[0].getMessage()
    assert "server_elapsed_us" in msg
    assert "response_read_us" in msg
    assert "total_us" in msg
    assert "outcome=success" in msg
    assert timing_records[0].levelno == logging.INFO
    # L5: pre_send_us must be a positive integer on the success path (not zero,
    # not the "never" sentinel) — guards against a regression to a constant.
    import re

    pre_send_match = re.search(r"pre_send_us=(\S+)", msg)
    assert pre_send_match is not None
    pre_send_val = pre_send_match.group(1)
    assert pre_send_val != "never", "pre_send_us must not be 'never' on success path"
    assert int(pre_send_val) > 0, f"pre_send_us must be a positive integer; got {pre_send_val!r}"


# ---------------------------------------------------------------------------
# GH-229 — Request-timing instrumentation (S2: complete() timeout path)
# ---------------------------------------------------------------------------


def test_complete_timeout_emits_warning_timing_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """complete() timeout path emits a WARNING log with outcome=timeout and timing fields.

    The log must include pre_send_us (or "never"), server_elapsed_us (or "never"),
    response_read_us (or "n/a"), total_us, and outcome=timeout.
    In the MockTransport case the request hook fires before the transport raises,
    so pre_send_us will be a concrete integer (not "never").
    """
    import logging

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=_CANNED_MODELS_RESPONSE)
        raise httpx.TimeoutException("timed out", request=request)

    from samantha_server.llm.omlx_client import OMLXClient

    client = OMLXClient(_transport=httpx.MockTransport(_handler))
    with (
        caplog.at_level(logging.WARNING, logger="samantha_server.llm.omlx_client"),
        pytest.raises(LLMTimeoutError),
    ):
        client.complete("ping")

    timing_records = [r for r in caplog.records if "pre_send_us" in r.getMessage()]
    assert len(timing_records) == 1, "Expected exactly one timing log record"
    msg = timing_records[0].getMessage()
    assert "server_elapsed_us" in msg
    assert "response_read_us" in msg
    assert "total_us" in msg
    assert "outcome=timeout" in msg
    assert timing_records[0].levelno == logging.WARNING


# ---------------------------------------------------------------------------
# PR #244 S6 — M4: pre_send_us=never sentinel branch
# ---------------------------------------------------------------------------


def test_complete_timeout_pre_send_us_never_when_request_hook_does_not_fire(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When the request hook does not fire, the timing log emits pre_send_us=never.

    This is the H1 hypothesis: a connection-pool exhaustion or similar failure
    prevents the request from being dispatched — the hook never writes
    request_dispatched_ns, so _log_timing emits the "never" sentinel.

    We simulate this by removing the request event hook from the inference client
    so _on_request is never called, then triggering a timeout.
    """
    import logging

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=_CANNED_MODELS_RESPONSE)
        raise httpx.TimeoutException("timed out", request=request)

    from samantha_server.llm.omlx_client import OMLXClient

    client = OMLXClient(_transport=httpx.MockTransport(_handler))
    # Remove the request event hook so request_dispatched_ns is never written.
    client._client.event_hooks["request"] = []  # type: ignore[attr-defined]

    with (
        caplog.at_level(logging.WARNING, logger="samantha_server.llm.omlx_client"),
        pytest.raises(LLMTimeoutError),
    ):
        client.complete("ping")

    timing_records = [r for r in caplog.records if "pre_send_us" in r.getMessage()]
    assert len(timing_records) == 1
    assert "pre_send_us=never" in timing_records[0].getMessage()


# ---------------------------------------------------------------------------
# PR #244 S3 — C1: Transport-error path emits diagnostic timing log
# ---------------------------------------------------------------------------


def test_complete_transport_error_raises_llm_inference_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """httpx.ConnectError during complete() raises LLMInferenceError AND emits
    a WARNING timing log with outcome=transport_error.

    Pre-fix: the bare except-Exception arm skips _log_timing entirely, so
    no timing log is emitted — defeating the diagnostic purpose of GH-229.
    """
    import logging

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=_CANNED_MODELS_RESPONSE)
        raise httpx.ConnectError("connection refused", request=request)

    from samantha_server.llm.omlx_client import OMLXClient

    client = OMLXClient(_transport=httpx.MockTransport(_handler))
    with (
        caplog.at_level(logging.WARNING, logger="samantha_server.llm.omlx_client"),
        pytest.raises(LLMInferenceError),
    ):
        client.complete("ping")

    timing_records = [r for r in caplog.records if "outcome=transport_error" in r.getMessage()]
    assert len(timing_records) == 1, "Expected exactly one transport_error timing log record"
    msg = timing_records[0].getMessage()
    assert "pre_send_us=" in msg
    assert "total_us=" in msg
    assert timing_records[0].levelno == logging.WARNING


# ---------------------------------------------------------------------------
# PR #244 L6 — ContextVar leakage: second call sees a fresh dict
# ---------------------------------------------------------------------------


def test_complete_called_twice_hooks_see_independent_timing_dicts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Two sequential complete() calls each produce their own timing log.

    Validates that the finally: _REQUEST_TIMING.reset(token) in _timed_post
    correctly isolates each call's ContextVar state. A future refactor that
    drops the reset() call would cause the second call's hooks to see the
    first call's dict — producing duplicate or stale timestamps.
    """
    import logging

    from samantha_server.llm.omlx_client import OMLXClient

    client = OMLXClient(_transport=_make_ok_transport())
    with caplog.at_level(logging.INFO, logger="samantha_server.llm.omlx_client"):
        client.complete("first")
        client.complete("second")

    timing_records = [r for r in caplog.records if "outcome=success" in r.getMessage()]
    assert len(timing_records) == 2, "Expected exactly two timing log records (one per call)"


# ---------------------------------------------------------------------------
# PR #244 S1 — H1: Hook exception safety
# ---------------------------------------------------------------------------


def test_on_request_hook_swallows_exception_and_logs_warning(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_on_request does not raise when its internals fail; it logs a WARNING instead.

    httpx propagates exceptions raised inside event hooks. If _on_request
    raises, the exception exits self._client.post() as RuntimeError, hits the
    bare except-Exception arm, and becomes LLMInferenceError — defeating the
    diagnostic purpose of the PR. The try/except inside the hook prevents this.

    The test seeds the ContextVar with a dict subclass whose __setitem__ raises,
    so the assignment `timing["request_dispatched_ns"] = ...` triggers the error
    path inside _on_request without patching any globals used by complete() itself.
    """
    import logging
    from contextvars import copy_context

    from samantha_server.llm import omlx_client

    class _ExplodingDict(dict):  # type: ignore[type-arg]
        def __setitem__(self, key: object, value: object) -> None:
            raise RuntimeError("instrumentation bug")

    fake_request = httpx.Request("POST", "http://localhost/v1/completions")

    def _run_hook_in_context() -> None:
        # Seed the ContextVar with an exploding dict so the hook body raises.
        omlx_client._REQUEST_TIMING.set(_ExplodingDict())  # type: ignore[arg-type]
        with caplog.at_level(logging.WARNING, logger="samantha_server.llm.omlx_client"):
            # Must not raise:
            omlx_client._on_request(fake_request)

    copy_context().run(_run_hook_in_context)

    warning_msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("omlx request-timing hook failed" in m for m in warning_msgs)


def test_on_response_hook_swallows_exception_and_logs_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """_on_response does not raise when its internals fail; it logs a WARNING instead.

    Same safety net as _on_request: httpx propagates hook exceptions, so
    a bug in _on_response would surface as LLMInferenceError. The try/except
    inside the hook prevents this.
    """
    import logging
    from contextvars import copy_context

    from samantha_server.llm import omlx_client

    class _ExplodingDict(dict):  # type: ignore[type-arg]
        def __setitem__(self, key: object, value: object) -> None:
            raise RuntimeError("instrumentation bug")

    fake_response = httpx.Response(200)

    def _run_hook_in_context() -> None:
        omlx_client._REQUEST_TIMING.set(_ExplodingDict())  # type: ignore[arg-type]
        with caplog.at_level(logging.WARNING, logger="samantha_server.llm.omlx_client"):
            # Must not raise:
            omlx_client._on_response(fake_response)

    copy_context().run(_run_hook_in_context)

    warning_msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("omlx request-timing hook failed" in m for m in warning_msgs)


@pytest.mark.local_omlx
def test_live_omlx_complete() -> None:
    """Live smoke test: OMLXClient.complete() against a locally running oMLX server.

    Requires oMLX to be running at the configured LLM_OMLX_BASE_URL.
    Run with: uv run pytest tests/llm/test_omlx_client.py -m local_omlx
    """
    from samantha_server.llm.omlx_client import OMLXClient

    client = OMLXClient()  # no transport = real network (loopback)
    result = client.complete("Say 'hello'", max_tokens=16)

    assert isinstance(result, LLMResponse)
    assert len(result.text) > 0
    assert result.input_tokens > 0
    assert result.output_tokens > 0
    assert result.model_id == client.model_id
    assert result.latency_us >= 0


# ---------------------------------------------------------------------------
# GH-229 — disable HTTP keepalive on the inference client
# ---------------------------------------------------------------------------


def test_omlx_inference_client_disables_keepalive_gh229(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OMLXClient must disable HTTP keepalive on the inference client (GH-229).

    Stale keepalive connections caused 120s read timeouts in LR-001 after long
    idle gaps from the preceding query batch. Isolated-category sweep (no
    queries before LR-001) shows LR-001 5/5 in ~1.9s; full corpus (27 queries
    before LR-001) reproduces the hang with `pre_send_us=103
    server_elapsed_us=never total_us=120003347` — request hit the wire but no
    response ever came back. The pool returned a connection oMLX had
    half-closed during the idle gap; the write succeeded at TCP level then
    blocked for the full 120s read timeout.

    Fix: pass `httpx.Limits(max_keepalive_connections=0)` to the inference
    client so every request gets a fresh connection. The probe client is
    short-lived (context-managed, init-only) and unaffected.
    """
    from samantha_server.llm import omlx_client as omlx_mod
    from samantha_server.llm.omlx_client import OMLXClient

    captured: list[dict[str, object]] = []
    real_client_cls = httpx.Client

    def _capturing_client(**kwargs: object) -> httpx.Client:
        captured.append(dict(kwargs))
        return real_client_cls(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(omlx_mod.httpx, "Client", _capturing_client)

    OMLXClient(_transport=_make_ok_transport())

    # First construction is the inference client (omlx_client.py line 233);
    # second is the probe inside _probe() (line 260). Only the inference
    # client must disable keepalive — the probe is one-shot.
    #
    # Refactor-resilience note: this assertion is positioned at the Client
    # kwarg level. If a future PR moves keepalive config to
    # `HTTPTransport(limits=...)` (Client receives transport, not limits),
    # this assertion would need to split into a Client-kwarg check AND a
    # transport-attr check. The companion behavioral test below
    # (test_omlx_inference_client_pool_has_keepalive_disabled_gh229) goes
    # through the real pool and is the canonical regression guard.
    assert len(captured) >= 1, "OMLXClient must construct at least one httpx.Client"
    inference_kwargs = captured[0]
    limits = inference_kwargs.get("limits")
    assert isinstance(limits, httpx.Limits), (
        "Inference client must pass an explicit httpx.Limits (GH-229 keepalive fix)"
    )
    assert limits.max_keepalive_connections == 0, (
        "Inference client must disable keepalive (max_keepalive_connections=0) "
        "so every request gets a fresh connection — prevents stale-connection "
        "hangs after long idle gaps (GH-229)."
    )


def test_omlx_inference_client_pool_has_keepalive_disabled_gh229(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Behavioral regression guard: the real httpcore ConnectionPool has
    max_keepalive_connections=0 (GH-229).

    Why this test exists in addition to the kwarg-capture test above:
    `httpx.Client._init_transport()` silently drops the `limits=` kwarg
    when a `_transport` override is supplied — it returns the override
    directly without constructing an HTTPTransport(limits=...). The
    kwarg-capture test (which passes `_transport=_make_ok_transport()`)
    therefore verifies only that the kwarg is present at the call site,
    not that the pool is actually configured. A regression that moves
    `limits=` inside an `if _transport is None:` guard, or applies it to
    the wrong client object, would pass the kwarg test but break the
    runtime fix.

    This test constructs OMLXClient WITHOUT a `_transport` override (so
    httpx builds a real HTTPTransport wrapping a real httpcore
    ConnectionPool), monkeypatches `_probe` to skip the server contact,
    and asserts on the pool directly. The `_pool._max_keepalive_connections`
    attribute is httpcore-private — the fragility is intentional: we want
    a real-pool assertion, and there is no public httpx/httpcore API to
    read this value. If the attribute name changes in a future httpcore
    version, this test fails loudly and the new attribute path replaces
    the old.
    """
    from samantha_server.llm.omlx_client import OMLXClient

    monkeypatch.setattr(OMLXClient, "_probe", lambda self, **_kw: None)

    client = OMLXClient()
    transport = client._client._transport
    # Inference client must wrap a real HTTPTransport (not MockTransport).
    assert isinstance(transport, httpx.HTTPTransport)
    pool = transport._pool
    assert pool._max_keepalive_connections == 0, (
        "httpcore ConnectionPool must have max_keepalive_connections=0 "
        "so every request opens a fresh connection (GH-229). A non-zero "
        "value here means the runtime fix is silently inactive even if "
        "the constructor kwarg test passes."
    )
