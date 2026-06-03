"""Shared LLM-stub helpers for scenarios tests (PR210 review #6).

Shared by scenario tests that need a minimal LLMClient stub without loading MLX.
Construct MagicMock-backed LLMClient stubs with the same model_id, latency, and
token-count signature, so a future test author writing a new scenario test
doesn't need to recreate them.
"""

from __future__ import annotations

import json as _json
from unittest.mock import MagicMock

from samantha_server.llm.client import LLMResponse

_STUB_MODEL_ID: str = "stub-model"


def make_stub_llm(response_text: str) -> MagicMock:
    """Return a stub LLMClient returning the given text for free_text mode.

    complete() returns the raw response_text (free_text mode).
    complete_json() returns the same text verbatim.
    Note: response_text here is a plain-English string for these tests
    (not valid JSON). Use with SAMANTHA_LLM_OUTPUT_MODE=free_text.
    """
    mock = MagicMock()
    mock.model_id = _STUB_MODEL_ID
    _resp = LLMResponse(
        text=response_text,
        input_tokens=10,
        output_tokens=5,
        model_id=_STUB_MODEL_ID,
        latency_us=500,
    )
    mock.complete.return_value = _resp
    mock.complete_json.return_value = _resp
    return mock


def make_stub_llm_json(order_ids: list[str]) -> MagicMock:
    """Return a stub LLMClient whose complete_json() returns valid QueryResponseV1 JSON.

    Suitable for SAMANTHA_LLM_OUTPUT_MODE=json tests. The response includes the
    given order_ids so the primary assertion path (parsed_order_ids set-containment)
    succeeds for fixtures with those expected IDs.
    """
    payload = _json.dumps(
        {
            "answer_type": "order_list",
            "order_ids": order_ids,
            "reasoning": "test stub response",
            "caveats": "",
        }
    )
    mock = MagicMock()
    mock.model_id = _STUB_MODEL_ID
    _free_resp = LLMResponse(
        text="stub free_text response",
        input_tokens=10,
        output_tokens=5,
        model_id=_STUB_MODEL_ID,
        latency_us=500,
    )
    _json_resp = LLMResponse(
        text=payload,
        input_tokens=10,
        output_tokens=5,
        model_id=_STUB_MODEL_ID,
        latency_us=500,
    )
    mock.complete.return_value = _free_resp
    mock.complete_json.return_value = _json_resp
    return mock
