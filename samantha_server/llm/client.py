"""LLMClient Protocol + LLMResponse return shape.

Imports nothing from `samantha_server.engine` / `primitives` / `rules`
(deterministic-path purity). Implementations under
`samantha_server/llm/` may import this module freely.
"""

from __future__ import annotations

from typing import Any, Final, Literal, Protocol, TypedDict

from pydantic import BaseModel, Field, field_validator

# OpenAI-compatible finish_reason vocabulary. Backends that emit a value
# outside this set are coerced to None by LLMResponse's validator (
# review #4 — wire robustness while keeping the type narrow for callers).
_KNOWN_FINISH_REASONS: Final[frozenset[str]] = frozenset(
    {"stop", "length", "content_filter", "tool_calls"}
)

FinishReason = Literal["stop", "length", "content_filter", "tool_calls"]


class ChatMessage(TypedDict):
    """A single chat message for /v1/chat/completions.

    role: one of "system", "user", or "assistant" — the full set of
    roles in the OpenAI-compatible chat completions API.
    content: the message body text.
    """

    role: Literal["system", "user", "assistant"]
    content: str


class LLMResponse(BaseModel, frozen=True):
    """The (immutable) result of one LLMClient.complete() call.

    finish_reason: closed OpenAI-compatible vocabulary
    (``stop`` / ``length`` / ``content_filter`` / ``tool_calls``); see
    ``FinishReason``. Backends that emit any other value are coerced
    to ``None`` by the validator below — preserves type narrowness for
    callers while staying robust to wire drift. Legacy backends that
    omit the field entirely also produce ``None``.
    """

    text: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    model_id: str
    latency_us: int = Field(ge=0)
    # Populated from choices[0].finish_reason on the wire; None when
    # the backend does not emit the field (legacy oMLX, MLXClient).
    finish_reason: FinishReason | None = None

    @field_validator("finish_reason", mode="before")
    @classmethod
    def _coerce_unknown_finish_reason(cls, v: object) -> object:
        """Coerce unknown wire values to ``None``.

        Pydantic's ``Literal`` validation rejects unknown strings; without
        this coercion, a future backend emitting (e.g.) ``"function_call"``
        would surface as a ValidationError from ``LLMResponse`` construction,
        escaping the typed-error contract on the LLM-client boundary. The
        signal is downgraded — unknown finish_reasons are simply not
        reported — but the call succeeds and downstream parsing proceeds.
        """
        if v is None or (isinstance(v, str) and v in _KNOWN_FINISH_REASONS):
            return v
        return None


class LLMClient(Protocol):
    """Protocol every LLM backend implements.

    Defaults source-of-truth: `max_tokens` and `temperature` default to
    `None`, meaning the implementation reads `config.LLM_MAX_TOKENS` /
    `config.LLM_TEMPERATURE`. Callers pass overrides only for cases the
    call site cares about (e.g., `temperature=0.0` for deterministic
    grounded-judge calls).

    Error contract: implementations raise typed exceptions only —
    `LLMClientError` (base, defined in `samantha_server.errors`) with
    subclasses `LLMTimeoutError`, `LLMModelLoadError`,
    `LLMInferenceError`. Callers MUST NOT swallow the error or fall
    back to a default response.

    `model_id` invariant: a given LLMClient instance returns the same
    `model_id` for every `complete()` call. If the underlying model
    is swapped (e.g., via config reload), the caller MUST construct a
    new client. This ensures audit consistency between the `model_id`
    in the receipt's traces and the actual model that produced the
    response.
    """

    @property
    def model_id(self) -> str:
        """Constant model identifier this client returns in every LLMResponse."""
        ...

    def complete(
        self,
        prompt: str,
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        """Run inference; raises typed `LLMClientError` subclasses on failure;
        never returns falsy/partial response on error."""
        ...

    def complete_json(
        self,
        messages: list[ChatMessage],
        *,
        schema: dict[str, Any],
        schema_name: str,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        """Constrained JSON output via /v1/chat/completions.

        Parameters
        ----------
        messages:
            Ordered list of ChatMessage dicts (role + content). Each role must
            be one of "system", "user", or "assistant".
        schema:
            A JSON Schema dict to pass as ``response_format.json_schema.schema``.
            Must be a strict-mode-compatible schema (``additionalProperties: false``
            recursively, ``required`` listing all properties). Callers should pass
            a module-level constant; the implementation may pass the dict directly
            to the wire, so mutation after the call is not safe.
        schema_name:
            A short identifier for the schema (e.g. "QueryResponseV1"). Used
            in the ``response_format.json_schema.name`` field.
        max_tokens:
            Override for the maximum output tokens. None uses config.LLM_MAX_TOKENS.
        temperature:
            Override for sampling temperature. None uses config.LLM_TEMPERATURE.

        Returns
        -------
        LLMResponse
            .text contains a JSON-shaped string matching the provided schema.
            Parse failures (truncation against max_tokens, schema violations)
            are the caller's responsibility.

        Raises
        ------
        LLMClientError (and subclasses)
            On transport failure. Never returns falsy/partial response on error.

        Notes
        -----
        SAMANTHA_LLM_OUTPUT_MODE=json requires LLM_PROVIDER=omlx.
        MLXClient raises LLMInferenceError — JSON mode is oMLX-only
        per the capability probe decision (see docs/llm/omlx-capabilities.md).
        Typed LLMInferenceError (not NotImplementedError) so
        handlers' `except LLMClientError` catches it and emits a receipt.
        """
        ...
