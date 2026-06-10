"""Tests for LLMClient.complete_json Protocol method.

Tests:
- A concrete class implementing complete_json satisfies the LLMClient protocol
  structurally.
- The method signature accepts messages/schema/schema_name/max_tokens/temperature.
"""

from __future__ import annotations

from typing import Any


def _make_fake_json_client() -> object:
    """Return a minimal fake LLMClient that implements complete_json."""
    from samantha_server.llm.client import LLMResponse

    class _FakeJSONClient:
        @property
        def model_id(self) -> str:
            return "fake-model"

        def complete(
            self,
            prompt: str,
            *,
            max_tokens: int | None = None,
            temperature: float | None = None,
        ) -> LLMResponse:
            return LLMResponse(
                text="fake",
                input_tokens=1,
                output_tokens=1,
                model_id=self.model_id,
                latency_us=1,
            )

        def complete_json(
            self,
            messages: list[dict[str, str]],
            *,
            schema: dict[str, Any],
            schema_name: str,
            max_tokens: int | None = None,
            temperature: float | None = None,
        ) -> LLMResponse:
            return LLMResponse(
                text='{"answer_type": "no_orders"}',
                input_tokens=5,
                output_tokens=5,
                model_id=self.model_id,
                latency_us=1,
            )

    return _FakeJSONClient()


def test_complete_json_protocol_callable() -> None:
    """_FakeJSONClient.complete_json is callable and returns LLMResponse."""
    from samantha_server.llm.client import LLMResponse

    client = _make_fake_json_client()
    result = client.complete_json(  # type: ignore[union-attr]
        [{"role": "user", "content": "hello"}],
        schema={"type": "object"},
        schema_name="test",
    )
    assert isinstance(result, LLMResponse)


def test_complete_json_protocol_returns_text_with_json() -> None:
    """complete_json .text contains a JSON-shaped string."""
    import json

    client = _make_fake_json_client()
    result = client.complete_json(  # type: ignore[union-attr]
        [{"role": "user", "content": "hello"}],
        schema={"type": "object"},
        schema_name="test",
    )
    # Must be parseable JSON
    parsed = json.loads(result.text)
    assert isinstance(parsed, dict)


def test_llm_client_protocol_declares_complete_json() -> None:
    """LLMClient Protocol must declare complete_json."""
    from samantha_server.llm.client import LLMClient

    # The Protocol declares the interface via its __protocol_attrs__
    # (Python 3.12+) or via inspection of the class body.
    # Both complete() and complete_json() must be declared.
    assert hasattr(LLMClient, "complete_json"), (
        "LLMClient Protocol must declare complete_json for JSON mode"
    )
