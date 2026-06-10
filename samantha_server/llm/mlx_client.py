"""MLX-family LLMClient implementation.

Apple Silicon only — `mlx-lm` is in the `local-llm` dependency group,
not the default install. This module is import-safe everywhere
(the mlx import is lazy inside MLXClient.__init__); constructing an
MLXClient on a host without mlx-lm raises LLMModelLoadError.

MLXClient transport tests are decorated `@pytest.mark.local_mlx` so
CI's `-m "not local_mlx"` filter excludes them. Transport-agnostic
scenario tests use the `live_llm` marker instead.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from samantha_server import config
from samantha_server.errors import (
    LLMInferenceError,
    LLMModelLoadError,
)
from samantha_server.llm.client import ChatMessage, LLMResponse

_logger = logging.getLogger(__name__)


class MLXClient:
    """Concrete LLMClient against an MLX-family model.

    Loads the model at construction; subsequent complete() calls reuse
    the loaded weights. The model_id is stable for the client's
    lifetime (per the LLMClient.model_id invariant).
    """

    def __init__(self, *, model_path: str | None = None) -> None:
        """Load the model at *model_path* (default: config.LLM_MODEL_PATH).

        Raises LLMModelLoadError on any failure (mlx-lm not installed,
        model artifact missing, wrong format, etc.).
        """
        path = model_path or config.LLM_MODEL_PATH
        try:
            # Lazy import — mlx-lm is in the `local-llm` dep group, optional.
            # Missing-import suppressed via [[tool.mypy.overrides]] in pyproject.toml.
            from mlx_lm import load
        except ImportError as exc:
            raise LLMModelLoadError(
                model_path=path,
                cause=(
                    f"mlx-lm not installed (ImportError: {exc}). "
                    f"Install via: uv sync --group local-llm"
                ),
            ) from None
        try:
            self._model: Any
            self._tokenizer: Any
            self._model, self._tokenizer = load(path)
        except Exception as exc:
            # Broad catch is intentional: any failure mode coming from mlx-lm's
            # load() (FileNotFoundError, RuntimeError, MemoryError, library-
            # internal exceptions) is translated into the project's typed
            # LLMModelLoadError so callers can `except LLMClientError` uniformly.
            # Note: cause may include host filesystem paths from the underlying
            # exception's __str__ (e.g., a FileNotFoundError carries the path it
            # tried). Acceptable for a developer tool; do NOT surface .cause
            # verbatim to remote audit logs.
            raise LLMModelLoadError(model_path=path, cause=str(exc)) from exc
        self._model_id: str = config.LLM_MODEL_NAME

    @property
    def model_id(self) -> str:
        return self._model_id

    def complete(
        self,
        prompt: str,
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        """Run inference; raise LLMInferenceError on failure."""
        from mlx_lm import generate

        max_tok = max_tokens if max_tokens is not None else config.LLM_MAX_TOKENS
        temp = temperature if temperature is not None else config.LLM_TEMPERATURE

        start_ns = time.perf_counter_ns()
        try:
            text = generate(
                self._model,
                self._tokenizer,
                prompt=prompt,
                max_tokens=max_tok,
                temp=temp,
                verbose=False,
            )
        except Exception as exc:
            # Broad catch is intentional: translate any mlx-lm internal failure
            # to the typed LLMInferenceError so the LLMClient Protocol's error
            # contract holds for callers.
            raise LLMInferenceError(model_id=self._model_id, cause=str(exc)) from exc
        elapsed_us = (time.perf_counter_ns() - start_ns) // 1_000

        # M-02: token-count calls go through the same typed-error contract.
        # tokenizer.encode() can raise (AttributeError, TypeError, mlx-internal
        # exceptions on malformed inputs); without this guard those would
        # escape as untyped exceptions and break the LLMClient Protocol's
        # "raises typed LLMClientError subclasses on failure" contract.
        # TODO(mlx_lm>=X): drop this fallback once mlx_lm.generate() returns a
        # `usage` field with token counts.
        try:
            # Best-effort token counts — mlx-lm doesn't always report them
            # cleanly; fall back to a tokenizer-based count.
            input_tokens = len(self._tokenizer.encode(prompt))
            output_tokens = len(self._tokenizer.encode(text))
        except Exception as exc:
            raise LLMInferenceError(model_id=self._model_id, cause=str(exc)) from exc

        # Protocol-agnostic numeric guard for silent
        # truncation. `finish_reason` is unavailable from mlx_lm.generate(),
        # so the token-count comparison is the only signal MLX path can
        # emit; matches the OMLXClient.complete()/complete_json() WARNING.
        if output_tokens >= max_tok:
            _logger.warning(
                "LLM output reached max_tokens=%d; response may be truncated",
                max_tok,
            )

        return LLMResponse(
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model_id=self._model_id,
            latency_us=elapsed_us,
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
        """Not implemented — JSON mode requires oMLX (LLM_PROVIDER=omlx).

        Per the capability probe decision, structured JSON output
        via response_format=json_schema is only supported on the oMLX
        server (/v1/chat/completions). The in-process MLX path does not
        support constrained decoding at this time.

        Set LLM_PROVIDER=omlx and SAMANTHA_LLM_OUTPUT_MODE=json to use
        JSON mode with the oMLX backend.
        """
        raise NotImplementedError(
            "MLXClient.complete_json is not supported. "
            "JSON output mode requires LLM_PROVIDER=omlx (oMLX server). "
            "See the capability probe decision."
        )
