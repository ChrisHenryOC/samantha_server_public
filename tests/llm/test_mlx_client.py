"""Tests for MLXClient — require mlx-lm installed (Apple Silicon).

All tests are decorated @pytest.mark.local_mlx so CI's addopts
filter (-m 'not local_mlx') excludes them. Run locally with:
    uv run pytest tests/llm/test_mlx_client.py -m local_mlx

These tests perform real model inference and exercise the MLXClient
transport specifically — distinct from the transport-agnostic
``live_llm`` marker, which tests scenarios against whatever
``LLM_PROVIDER`` selects. They require the mlx-lm dependency group to
be installed:
    uv sync --group local-llm

The model path defaults to config.LLM_MODEL_PATH. Override via the
TEST_LLM_MODEL_PATH environment variable to point at a locally
downloaded model.
"""

from __future__ import annotations

import os

import pytest

from samantha_server.errors import LLMModelLoadError
from samantha_server.llm.client import LLMResponse

pytestmark = pytest.mark.local_mlx

_DEFAULT_MODEL_PATH = "mlx-community/Llama-3.2-3B-Instruct-4bit"  # nosec: model-id


@pytest.fixture(scope="module")
def model_path() -> str:
    """Model path for live tests. Override via TEST_LLM_MODEL_PATH env var."""
    # Import config inside the fixture so conftest sentinel env vars are set
    # before the eager validation in config.py runs.
    from samantha_server import config

    return os.environ.get("TEST_LLM_MODEL_PATH", config.LLM_MODEL_PATH)


@pytest.fixture(scope="module")
def mlx_client(model_path: str) -> object:
    """Construct a live MLXClient; skip if mlx-lm is not installed."""
    pytest.importorskip("mlx_lm", reason="mlx-lm not installed; run: uv sync --group local-llm")
    from samantha_server.llm.mlx_client import MLXClient

    return MLXClient(model_path=model_path)


def test_construction_sets_model_id(mlx_client: object) -> None:
    """MLXClient construction succeeds and model_id is a non-empty string."""
    from samantha_server.llm.mlx_client import MLXClient

    assert isinstance(mlx_client, MLXClient)
    assert isinstance(mlx_client.model_id, str)
    assert len(mlx_client.model_id) > 0


def test_complete_returns_llm_response(mlx_client: object) -> None:
    """complete() returns a valid LLMResponse with non-empty output."""
    from samantha_server.llm.mlx_client import MLXClient

    assert isinstance(mlx_client, MLXClient)
    result = mlx_client.complete("Say 'hello'", max_tokens=16)

    assert isinstance(result, LLMResponse)
    assert len(result.text) > 0
    assert result.input_tokens > 0
    assert result.output_tokens > 0
    assert result.model_id == mlx_client.model_id
    assert result.latency_us > 0


def test_model_load_failure_raises_model_load_error() -> None:
    """MLXClient with a non-existent model path raises LLMModelLoadError."""
    pytest.importorskip("mlx_lm", reason="mlx-lm not installed; run: uv sync --group local-llm")
    from samantha_server.llm.mlx_client import MLXClient

    with pytest.raises(LLMModelLoadError) as exc_info:
        MLXClient(model_path="this/does/not/exist")

    assert exc_info.value.model_path == "this/does/not/exist"
