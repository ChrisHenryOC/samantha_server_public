"""Tests for samantha_server.llm.client — LLMResponse and LLMClient Protocol."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from samantha_server.llm.client import LLMClient, LLMResponse

# ---------------------------------------------------------------------------
# LLMResponse field validation
# ---------------------------------------------------------------------------


class TestLLMResponseValidation:
    """LLMResponse validates Field(ge=0) constraints."""

    def test_negative_input_tokens_raises(self) -> None:
        with pytest.raises(ValidationError):
            LLMResponse(
                text="hi",
                input_tokens=-1,
                output_tokens=10,
                model_id="m",
                latency_us=100,
            )

    def test_negative_output_tokens_raises(self) -> None:
        with pytest.raises(ValidationError):
            LLMResponse(
                text="hi",
                input_tokens=5,
                output_tokens=-1,
                model_id="m",
                latency_us=100,
            )

    def test_negative_latency_us_raises(self) -> None:
        with pytest.raises(ValidationError):
            LLMResponse(
                text="hi",
                input_tokens=5,
                output_tokens=10,
                model_id="m",
                latency_us=-1,
            )

    def test_zero_values_are_valid(self) -> None:
        """ge=0 means zero is allowed."""
        response = LLMResponse(
            text="",
            input_tokens=0,
            output_tokens=0,
            model_id="m",
            latency_us=0,
        )
        assert response.input_tokens == 0
        assert response.output_tokens == 0
        assert response.latency_us == 0

    def test_positive_values_are_valid(self) -> None:
        response = LLMResponse(
            text="hello",
            input_tokens=5,
            output_tokens=10,
            model_id="test-model",
            latency_us=1234,
        )
        assert response.text == "hello"
        assert response.input_tokens == 5
        assert response.output_tokens == 10
        assert response.model_id == "test-model"
        assert response.latency_us == 1234


# ---------------------------------------------------------------------------
# LLMResponse is frozen (immutable)
# ---------------------------------------------------------------------------


class TestLLMResponseFrozen:
    """LLMResponse is a frozen Pydantic model — fields cannot be reassigned."""

    def test_assignment_raises(self) -> None:
        response = LLMResponse(
            text="hi",
            input_tokens=5,
            output_tokens=10,
            model_id="m",
            latency_us=100,
        )
        with pytest.raises(ValidationError):
            response.text = "new"  # type: ignore[misc]

    def test_field_reassignment_raises(self) -> None:
        response = LLMResponse(
            text="hi",
            input_tokens=5,
            output_tokens=10,
            model_id="m",
            latency_us=100,
        )
        with pytest.raises(ValidationError):
            response.input_tokens = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# LLMResponse JSON round-trip
# ---------------------------------------------------------------------------


class TestLLMResponseRoundTrip:
    """LLMResponse round-trips through model_dump_json() / model_validate_json()."""

    def test_round_trip(self) -> None:
        original = LLMResponse(
            text="hi",
            input_tokens=5,
            output_tokens=10,
            model_id="m",
            latency_us=1234,
        )
        json_str = original.model_dump_json()
        # Confirm it's valid JSON
        parsed_dict = json.loads(json_str)
        assert parsed_dict["text"] == "hi"
        assert parsed_dict["input_tokens"] == 5

        # Reconstruct from JSON
        restored = LLMResponse.model_validate_json(json_str)
        assert restored == original

    def test_round_trip_preserves_all_fields(self) -> None:
        original = LLMResponse(
            text="longer response text",
            input_tokens=42,
            output_tokens=128,
            model_id="mlx-community/some-model",  # nosec: model-id
            latency_us=987654,
        )
        restored = LLMResponse.model_validate_json(original.model_dump_json())
        assert restored.text == original.text
        assert restored.input_tokens == original.input_tokens
        assert restored.output_tokens == original.output_tokens
        assert restored.model_id == original.model_id
        assert restored.latency_us == original.latency_us


# ---------------------------------------------------------------------------
# LLMClient Protocol structural compatibility
# ---------------------------------------------------------------------------


class TestLLMClientProtocol:
    """A stub that implements the LLMClient Protocol is accepted by a Protocol-typed function."""

    def test_stub_satisfies_protocol_structurally(self) -> None:
        """A stub implementing model_id + complete() can be used as LLMClient."""

        class _StubClient:
            @property
            def model_id(self) -> str:
                return "stub-model"

            def complete(
                self,
                prompt: str,
                *,
                max_tokens: int | None = None,
                temperature: float | None = None,
            ) -> LLMResponse:
                return LLMResponse(
                    text=f"stub: {prompt}",
                    input_tokens=len(prompt),
                    output_tokens=5,
                    model_id=self.model_id,
                    latency_us=100,
                )

        def _call_client(client: LLMClient, prompt: str) -> LLMResponse:
            return client.complete(prompt)

        stub = _StubClient()
        result = _call_client(stub, "hello")  # type: ignore[arg-type]
        assert result.text == "stub: hello"
        assert result.model_id == "stub-model"

    def test_stub_model_id_property_works(self) -> None:
        """The model_id property is accessible and returns a string."""

        class _StubClient:
            @property
            def model_id(self) -> str:
                return "my-model"

            def complete(
                self,
                prompt: str,
                *,
                max_tokens: int | None = None,
                temperature: float | None = None,
            ) -> LLMResponse:
                return LLMResponse(
                    text="",
                    input_tokens=0,
                    output_tokens=0,
                    model_id=self.model_id,
                    latency_us=0,
                )

        stub = _StubClient()
        assert stub.model_id == "my-model"
