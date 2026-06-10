"""oMLX LLMClient implementation.

Talks to a same-host oMLX server over loopback HTTP using the
OpenAI-compatible /v1/completions endpoint. The oMLX server must be
running before OMLXClient is constructed — the constructor probes the
server and raises LLMModelLoadError if the probe fails.

Why oMLX instead of in-process mlx-lm?  mlx-lm's generate() discards
the KV cache between calls.  For a 32B model with a stable skill-doc
prefix, that costs 5–15 s of wasted prefill per call.  oMLX has a
tiered paged KV cache; loopback HTTP to a same-host oMLX process reuses
prefix work across calls.  See docs/plans/phase-2-amendment-omlx.md.

LLM-tier live tests are decorated @pytest.mark.local_omlx so CI's
addopts filter (-m 'not local_omlx') excludes them.

Request-timing diagnostic surface
------------------------------------------
Every complete() and complete_json() call emits one structured log line
at INFO (success) or WARNING (timeout). Grep for "omlx request timing"
to find them. The line shape is::

    omlx request timing path=<path> outcome=<success|timeout>
    pre_send_us=<int|never> server_elapsed_us=<int|never>
    response_read_us=<int|n/a> total_us=<int>

Phase meanings:

- pre_send_us       — microseconds from method entry until httpx fired
                      the request event hook (i.e., the request was
                      dispatched on the wire). Value is "never" if the
                      hook did not fire — indicating the hang occurred
                      before httpx could send anything (e.g., connection
                      pool exhaustion, H1 hypothesis).
- server_elapsed_us — microseconds from request dispatch until the
                      response-headers hook fired (first byte from the
                      server). Value is "never" on timeout before any
                      response was received (H2/H4 hypotheses).
- response_read_us  — microseconds from response headers until the
                      post() call returned (body read). Value is "n/a"
                      when no response arrived.
- total_us          — microseconds from method entry to method exit
                      (includes all phases including any exception path).

After the keepalive disable: an `outcome=timeout` with `pre_send_us`
low and `server_elapsed_us=never` no longer indicates H1 (stale keepalive
socket) — that hypothesis is structurally eliminated because every
request opens a fresh connection. Future occurrences of that shape are
H2 (oMLX content-specific stall) or H4 (oMLX queue/scheduling) candidates.

Prompt and response content are never included (PHI boundary).
"""

from __future__ import annotations

import logging
import time
from contextvars import ContextVar
from typing import Any, Final, Literal

import httpx

from samantha_server import config
from samantha_server.errors import (
    LLMInferenceError,
    LLMModelLoadError,
    LLMTimeoutError,
)
from samantha_server.llm.chat_template_config import chat_template_kwargs_for
from samantha_server.llm.client import ChatMessage, LLMResponse

_logger = logging.getLogger(__name__)

# Path probed at construction time: GET /v1/models verifies the server is
# reachable and the models list is available (OpenAI shape).
_PROBE_PATH: Final = "/v1/models"
_COMPLETIONS_PATH: Final = "/v1/completions"
_CHAT_COMPLETIONS_PATH: Final = "/v1/chat/completions"

# Sentinel for "caller did not supply auth_token; use config default".
# object() is unforgeable by callers (unlike the string "UNSET").
_UNSET: Final = object()

# Short timeout for the construction probe — loopback connection-refused
# is OS-immediate, so 10 s is generous.
_PROBE_TIMEOUT: Final = httpx.Timeout(10.0)

# ContextVar seeded by complete()/complete_json() so event hooks can write
# per-call timestamps without coupling to a specific client method.
# Safe under asyncio.to_thread (uses contextvars.copy_context()).
# Unsafe under raw ThreadPoolExecutor.submit() — hooks would see None because
# the seeded timing dict is invisible across threads without explicit
# copy_context propagation.
_REQUEST_TIMING: ContextVar[dict[str, int] | None] = ContextVar("_REQUEST_TIMING", default=None)


def _on_request(request: httpx.Request) -> None:
    """httpx request event hook — fires just before the request is sent on the wire."""
    try:
        timing = _REQUEST_TIMING.get()
        if timing is not None:
            timing["request_dispatched_ns"] = time.perf_counter_ns()
    except Exception as exc:
        _logger.warning("omlx request-timing hook failed: %s", type(exc).__name__)


def _on_response(response: httpx.Response) -> None:
    """httpx response event hook — fires when response headers are received."""
    try:
        timing = _REQUEST_TIMING.get()
        if timing is not None:
            timing["response_received_ns"] = time.perf_counter_ns()
    except Exception as exc:
        _logger.warning("omlx request-timing hook failed: %s", type(exc).__name__)


def _log_timing(
    timing: dict[str, int],
    *,
    outcome: Literal["success", "timeout", "transport_error"],
    path: str,
) -> None:
    """Emit ONE structured log line with per-phase timing deltas.

    Phases:
    - pre_send_us:       time from method entry to request dispatched on wire
                         (or "never" if hook did not fire — pool/connect hang)
    - server_elapsed_us: time from dispatch to first response byte
                         (or "never" if no response arrived)
    - response_read_us:  time from first response byte to method exit
                         (or "n/a" if no response arrived)
    - total_us:          total time from method entry to method exit

    Log level: INFO on success, WARNING on timeout. Prompt/response content
    is never included (PHI boundary).
    """
    start = timing["request_start_ns"]
    complete_ns = timing["request_complete_ns"]
    dispatched = timing.get("request_dispatched_ns")
    received = timing.get("response_received_ns")

    if dispatched is not None:
        pre_send_us: int | str = (dispatched - start) // 1_000
    else:
        pre_send_us = "never"

    if dispatched is not None and received is not None:
        server_elapsed_us: int | str = (received - dispatched) // 1_000
    else:
        server_elapsed_us = "never"

    if received is not None:
        response_read_us: int | str = (complete_ns - received) // 1_000
    else:
        response_read_us = "n/a"

    total_us = (complete_ns - start) // 1_000

    _fmt = (
        "omlx request timing path=%s outcome=%s "
        "pre_send_us=%s server_elapsed_us=%s "
        "response_read_us=%s total_us=%s"
    )
    if outcome == "success":
        _logger.info(
            _fmt, path, outcome, pre_send_us, server_elapsed_us, response_read_us, total_us
        )
    else:
        _logger.warning(
            _fmt, path, outcome, pre_send_us, server_elapsed_us, response_read_us, total_us
        )


class OMLXClient:
    """Concrete LLMClient against a same-host oMLX server via loopback HTTP.

    Probes the server at construction; subsequent complete() calls send
    HTTP POST requests to the /v1/completions endpoint. The model_id is
    stable for the client's lifetime (per the LLMClient.model_id invariant).

    Parameters
    ----------
    base_url:
        Base URL for the oMLX server (default: config.LLM_OMLX_BASE_URL).
        Must be a loopback URL — enforced at config import.
    auth_token:
        Optional bearer token (default: config.LLM_OMLX_AUTH_TOKEN).
        Pass None explicitly to disable auth regardless of config.
    _transport:
        httpx transport for dependency injection in tests.  Production code
        omits this argument and uses the default httpx transport.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        auth_token: str | None | object = _UNSET,
        chat_template_kwargs: dict[str, Any] | None = None,
        _transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Probe the oMLX server; raise LLMModelLoadError on failure.

        ``chat_template_kwargs``: optional dict forwarded verbatim to every
        completion request body. Used to pass model-specific chat-template
        arguments (e.g., ``{"enable_thinking": False}`` for Qwen3.5/3.6
        to suppress `<think>` reasoning tokens that would otherwise bleed
        into structured JSON output). When ``None``, the field is omitted
        from request bodies entirely — preserves the request shape for
        models that don't need it.
        """
        self._base_url: str = base_url or config.LLM_OMLX_BASE_URL
        # _UNSET sentinel: fall back to config. Explicit None means "no token".
        if auth_token is _UNSET:
            self._auth_token: str | None = config.LLM_OMLX_AUTH_TOKEN
        else:
            # auth_token is str | None here (sentinel branch already handled)
            self._auth_token = auth_token if auth_token else None  # type: ignore[assignment]
        self._timeout_s: float = config.LLM_OMLX_TIMEOUT_S
        self._model_id: str = config.LLM_MODEL_NAME

        # Explicit constructor kwarg wins over the per-model auto-lookup.
        # When unset, fall back to chat_template_config so simply pointing
        # the runtime at a model in the table (e.g., via env LLM_MODEL_NAME)
        # is sufficient to enable model-specific kwargs like enable_thinking.
        if chat_template_kwargs is not None:
            self._chat_template_kwargs: dict[str, Any] | None = chat_template_kwargs
        else:
            self._chat_template_kwargs = chat_template_kwargs_for(self._model_id)

        # Pre-build headers once — auth is stable for the client's lifetime.
        self._headers: dict[str, str] = {}
        if self._auth_token:
            self._headers["Authorization"] = f"Bearer {self._auth_token}"

        # Inference client: phase-specific timeout (connect 5 s, read = config).
        # Event hooks record per-call timestamps into _REQUEST_TIMING so we can
        # distinguish "never dispatched" (pool hang) from "no response" (server hang).
        #
        # max_keepalive_connections=0 forces a fresh TCP connection per
        # request so a stale (server half-closed) keepalive socket can never be
        # handed back from the pool. Diagnostic chain and acceptance evidence
        # live in test_omlx_inference_client_*_gh229.
        self._client: httpx.Client = httpx.Client(
            base_url=self._base_url,
            timeout=httpx.Timeout(
                connect=5.0,
                read=self._timeout_s,
                write=10.0,
                pool=5.0,
            ),
            limits=httpx.Limits(max_keepalive_connections=0),
            transport=_transport,
            event_hooks={"request": [_on_request], "response": [_on_response]},
        )

        # Construction probe — verify the server responds with valid model list.
        self._probe(_transport=_transport)

    def _probe(self, *, _transport: httpx.BaseTransport | None) -> None:
        """Send GET /v1/models to verify the oMLX server is reachable and ready.

        Uses a short dedicated timeout (10 s) independent of the inference
        timeout. Validates that the response is 2xx with a JSON body containing
        a ``data`` key (OpenAI /v1/models shape).

        Raises LLMModelLoadError on any failure (transport error, non-2xx,
        missing ``data`` key).
        """
        try:
            # Use a short-timeout client for the probe to avoid blocking startup.
            with httpx.Client(
                base_url=self._base_url,
                timeout=_PROBE_TIMEOUT,
                transport=_transport,
            ) as probe_client:
                response = probe_client.get(
                    _PROBE_PATH,
                    headers=self._headers,
                )
        except Exception as exc:
            # Strip the exception message to prevent bearer-token leakage (CWE-312):
            # httpx may embed the originating Request (with headers) in __str__.
            _logger.debug("oMLX probe transport error", exc_info=exc)
            raise LLMModelLoadError(
                model_path=self._base_url,
                cause=f"oMLX server probe failed: {type(exc).__name__}",
            ) from exc

        if not response.is_success:
            raise LLMModelLoadError(
                model_path=self._base_url,
                cause=(
                    f"oMLX server probe returned HTTP {response.status_code}; "
                    f"ensure the server is running at the configured base URL."
                ),
            )

        # Validate OpenAI /v1/models shape: must have a "data" key.
        try:
            body = response.json()
        except Exception:
            raise LLMModelLoadError(
                model_path=self._base_url,
                cause="oMLX probe response is not valid JSON; model server not ready.",
            ) from None

        if "data" not in body:
            raise LLMModelLoadError(
                model_path=self._base_url,
                cause=(
                    "oMLX probe response missing 'data' key; "
                    "model server not ready or returned unexpected shape."
                ),
            )

    @property
    def model_id(self) -> str:
        """Constant model identifier this client returns in every LLMResponse."""
        return self._model_id

    def _timed_post(self, path: str, body: dict[str, Any]) -> httpx.Response:
        """Issue a POST and emit a structured timing log.

        Handles ContextVar lifecycle, perf_counter sampling, and log emission
        for success, timeout, and transport-error outcomes. Re-raises the
        exception types unchanged so callers can map them to their preferred
        typed exceptions.
        """
        timing: dict[str, int] = {"request_start_ns": time.perf_counter_ns()}
        token = _REQUEST_TIMING.set(timing)
        try:
            response = self._client.post(path, json=body, headers=self._headers)
            timing["request_complete_ns"] = time.perf_counter_ns()
            _log_timing(timing, outcome="success", path=path)
            return response
        except httpx.TimeoutException:
            timing["request_complete_ns"] = time.perf_counter_ns()
            _log_timing(timing, outcome="timeout", path=path)
            raise
        except Exception:
            timing["request_complete_ns"] = time.perf_counter_ns()
            _log_timing(timing, outcome="transport_error", path=path)
            raise
        finally:
            _REQUEST_TIMING.reset(token)

    def complete(
        self,
        prompt: str,
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        """Send *prompt* to the oMLX /v1/completions endpoint.

        Raises:
            LLMTimeoutError: on httpx.TimeoutException.
            LLMInferenceError: on non-2xx response or malformed payload.
        """
        max_tok = max_tokens if max_tokens is not None else config.LLM_MAX_TOKENS
        temp = temperature if temperature is not None else config.LLM_TEMPERATURE

        body: dict[str, Any] = {
            "model": self._model_id,
            "prompt": prompt,
            "max_tokens": max_tok,
            "temperature": temp,
            "stream": False,
        }
        # Truthy check (not `is not None`): an explicit empty dict from the
        # caller means "no override" — treat it identically to None and
        # omit the field.
        if self._chat_template_kwargs:
            body["chat_template_kwargs"] = self._chat_template_kwargs

        _start_ns = time.perf_counter_ns()
        try:
            response = self._timed_post(_COMPLETIONS_PATH, body)
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(
                model_id=self._model_id,
                timeout_us=int(self._timeout_s * 1_000_000),
            ) from exc
        except Exception as exc:
            # Strip exc message to prevent bearer-token leakage (CWE-312).
            _logger.debug("oMLX inference transport error", exc_info=exc)
            raise LLMInferenceError(
                model_id=self._model_id,
                cause=f"HTTP request failed: {type(exc).__name__}",
            ) from exc
        # elapsed_us is measured after the POST returns so it captures the full
        # round-trip. The start is recorded before _timed_post so the value
        # aligns with total_us in the timing log (both use the same epoch).
        elapsed_us = (time.perf_counter_ns() - _start_ns) // 1_000

        if not response.is_success:
            raise LLMInferenceError(
                model_id=self._model_id,
                cause=f"oMLX returned HTTP {response.status_code}",
            )

        try:
            data = response.json()
            raw_text = data["choices"][0]["text"]
            # oMLX can emit text=null on content-filter stops; without this guard the
            # None reaches LLMResponse(text=...) outside the try block and raises
            # Pydantic ValidationError — outside the LLMClientError hierarchy.
            if raw_text is None:
                raise LLMInferenceError(
                    model_id=self._model_id,
                    cause="oMLX response choices[0].text is null (content-filter stop?)",
                )
            text: str = raw_text
            # Read finish_reason with .get() — legacy backends may omit it.
            finish_reason: str | None = data["choices"][0].get("finish_reason")
            # Explicit usage validation — silent zero counts would corrupt audit
            # receipts with no observable signal (fix #3).
            if "usage" not in data:
                raise LLMInferenceError(
                    model_id=self._model_id,
                    cause="oMLX response missing usage",
                )
            usage: dict[str, Any] = data["usage"]
            if "prompt_tokens" not in usage or "completion_tokens" not in usage:
                raise LLMInferenceError(
                    model_id=self._model_id,
                    cause="oMLX response missing usage",
                )
            input_tokens: int = int(usage["prompt_tokens"])
            output_tokens: int = int(usage["completion_tokens"])
        except LLMInferenceError:
            raise
        except Exception as exc:
            raise LLMInferenceError(
                model_id=self._model_id,
                cause=f"Malformed oMLX response: {type(exc).__name__}: {exc}",
            ) from exc

        # Pre-validate token counts before Pydantic sees them (fix #6).
        if input_tokens < 0 or output_tokens < 0:
            raise LLMInferenceError(
                model_id=self._model_id,
                cause="oMLX returned invalid token counts",
            )

        # Warn when the output hit the token budget — the response may
        # be silently truncated. Use >= (not ==) as a defensive guard: spec-
        # compliant OpenAI-style backends report `completion_tokens <= max_tokens`,
        # but downgrading to `>=` avoids silently missing any future backend or
        # off-by-one counting variation at the EOS boundary.
        if output_tokens >= max_tok:
            _logger.warning("LLM output reached max_tokens=%d; response may be truncated", max_tok)

        return LLMResponse(
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model_id=self._model_id,
            latency_us=elapsed_us,
            # LLMResponse's before-mode field_validator coerces unknown wire
            # values to None at runtime; mypy can't see across that boundary,
            # so the assignment from the wire-typed `str | None` here is fine.
            finish_reason=finish_reason,  # type: ignore[arg-type]
        )

    def complete_json(
        self,
        messages: list[ChatMessage],
        *,
        schema: dict[str, Any],
        schema_name: str,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        """Send messages to /v1/chat/completions with JSON schema enforcement.

        Uses the oMLX response_format={"type":"json_schema","strict":true}
        extension (confirmed working probe).

        The legacy /v1/completions endpoint silently ignores response_format;
        only the chat completions endpoint honors it.

        Raises:
            LLMTimeoutError: on httpx.TimeoutException.
            LLMInferenceError: on non-2xx response, missing usage, or
                missing choices[0].message.content.
        """
        max_tok = max_tokens if max_tokens is not None else config.LLM_MAX_TOKENS
        temp = temperature if temperature is not None else config.LLM_TEMPERATURE

        body: dict[str, Any] = {
            "model": self._model_id,
            "messages": messages,
            "temperature": temp,
            "max_tokens": max_tok,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                },
            },
        }
        # Truthy check (not `is not None`): an explicit empty dict from the
        # caller means "no override" — treat it identically to None and
        # omit the field.
        if self._chat_template_kwargs:
            body["chat_template_kwargs"] = self._chat_template_kwargs

        _start_ns = time.perf_counter_ns()
        try:
            response = self._timed_post(_CHAT_COMPLETIONS_PATH, body)
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(
                model_id=self._model_id,
                timeout_us=int(self._timeout_s * 1_000_000),
            ) from exc
        except Exception as exc:
            _logger.debug("oMLX chat inference transport error", exc_info=exc)
            raise LLMInferenceError(
                model_id=self._model_id,
                cause=f"HTTP request failed: {type(exc).__name__}",
            ) from exc
        elapsed_us = (time.perf_counter_ns() - _start_ns) // 1_000

        if not response.is_success:
            raise LLMInferenceError(
                model_id=self._model_id,
                cause=f"oMLX returned HTTP {response.status_code}",
            )

        try:
            data = response.json()
            raw_content = data["choices"][0]["message"]["content"]
            # oMLX emits content=null on content-filter stops; without this guard the
            # None propagates to LLMResponse(text=...) and raises Pydantic ValidationError
            # outside the LLMClientError hierarchy — escaping the handler's refusal catch.
            if raw_content is None:
                raise LLMInferenceError(
                    model_id=self._model_id,
                    cause="oMLX chat response message.content is null (content-filter stop?)",
                )
            text: str = raw_content
            # finish_reason is at choices[0].finish_reason (sibling of
            # choices[0].message), not under message. Use .get() — legacy backends
            # may omit the field.
            finish_reason: str | None = data["choices"][0].get("finish_reason")
            if "usage" not in data:
                raise LLMInferenceError(
                    model_id=self._model_id,
                    cause="oMLX chat response missing usage",
                )
            usage: dict[str, Any] = data["usage"]
            if "prompt_tokens" not in usage or "completion_tokens" not in usage:
                raise LLMInferenceError(
                    model_id=self._model_id,
                    cause="oMLX chat response missing usage",
                )
            input_tokens: int = int(usage["prompt_tokens"])
            output_tokens: int = int(usage["completion_tokens"])
        except LLMInferenceError:
            raise
        except Exception as exc:
            raise LLMInferenceError(
                model_id=self._model_id,
                cause=f"Malformed oMLX chat response: {type(exc).__name__}: {exc}",
            ) from exc

        if input_tokens < 0 or output_tokens < 0:
            raise LLMInferenceError(
                model_id=self._model_id,
                cause="oMLX returned invalid token counts",
            )

        # Warn when the output hit the token budget — the response may
        # be silently truncated. Use >= (not ==) as a defensive guard: spec-
        # compliant OpenAI-style backends report `completion_tokens <= max_tokens`,
        # but downgrading to `>=` avoids silently missing any future backend or
        # off-by-one counting variation at the EOS boundary.
        if output_tokens >= max_tok:
            _logger.warning("LLM output reached max_tokens=%d; response may be truncated", max_tok)

        return LLMResponse(
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model_id=self._model_id,
            latency_us=elapsed_us,
            # LLMResponse's before-mode field_validator coerces unknown wire
            # values to None at runtime; mypy can't see across that boundary,
            # so the assignment from the wire-typed `str | None` here is fine.
            finish_reason=finish_reason,  # type: ignore[arg-type]
        )
