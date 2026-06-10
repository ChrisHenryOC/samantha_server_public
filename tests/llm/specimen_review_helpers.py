"""Shared test helpers for specimen-review JSON path.

Extracted from tests/llm/test_handlers.py and tests/receipts/test_architectural.py
to remove duplication. PR204 review #7.
"""

from __future__ import annotations

import json as _json
from unittest.mock import MagicMock

from samantha_server.llm.client import LLMResponse

_DISPOSITIONS = frozenset({"accepted", "rejected", "escalated"})


def make_mock_llm_review_client(
    disposition_or_json: str = "accepted",
    *,
    model_id: str = "test-review-model",
    latency_us: int = 2000,
) -> MagicMock:
    """LLMClient mock for handle_pending_llm_review (JSON path, hard cutover).

    Accepts either a disposition keyword ("accepted", "rejected", "escalated") or
    a raw string. Disposition keywords are wrapped into valid
    SpecimenReviewResponseV1 JSON automatically. Any other string is passed
    through as-is (for tests that need invalid JSON or non-disposition text).
    """
    if disposition_or_json in _DISPOSITIONS:
        json_text = _json.dumps(
            {"disposition": disposition_or_json, "reasoning": "Test reasoning."}
        )
    else:
        json_text = disposition_or_json

    mock = MagicMock()
    mock.model_id = model_id
    mock.complete_json.return_value = LLMResponse(
        text=json_text,
        input_tokens=20,
        output_tokens=3,
        model_id=model_id,
        latency_us=latency_us,
    )
    return mock
