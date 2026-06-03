"""Tests for MLXClient when mlx-lm is NOT installed.

This file runs on CI (where mlx-lm is absent) and skips locally when
mlx-lm is installed. It verifies the import-error path produces the
correct LLMModelLoadError with an actionable message.

No @pytest.mark.local_mlx — this must execute in CI environments.
"""

from __future__ import annotations

import importlib.util

import pytest

# Skip this entire module when mlx-lm IS installed — the test is
# specifically for the missing-mlx-lm error path. Local devs with
# mlx-lm get the real tests in test_mlx_client.py instead.
if importlib.util.find_spec("mlx_lm") is not None:
    pytest.skip(
        "mlx-lm is installed; this module verifies the MISSING case only",
        allow_module_level=True,
    )


def test_mlx_missing_raises_model_load_error() -> None:
    """When mlx-lm is not installed, MLXClient raises LLMModelLoadError."""
    from samantha_server.errors import LLMModelLoadError
    from samantha_server.llm.mlx_client import MLXClient

    with pytest.raises(LLMModelLoadError) as exc_info:
        MLXClient(model_path="/dummy")

    error_msg = str(exc_info.value)
    assert "mlx-lm not installed" in error_msg
    assert "uv sync --group local-llm" in error_msg


def test_mlx_missing_error_has_model_path_attribute() -> None:
    """LLMModelLoadError from missing mlx-lm has model_path attribute set."""
    from samantha_server.errors import LLMModelLoadError
    from samantha_server.llm.mlx_client import MLXClient

    with pytest.raises(LLMModelLoadError) as exc_info:
        MLXClient(model_path="/some/path")

    assert exc_info.value.model_path == "/some/path"
