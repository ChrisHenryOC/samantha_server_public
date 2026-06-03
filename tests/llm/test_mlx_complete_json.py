"""Tests for MLXClient.complete_json stub (GH-192 Slice 6).

MLXClient raises NotImplementedError because JSON mode requires oMLX
(per GH-191 capability probe decision).

This test works whether or not mlx-lm is installed:
- When mlx-lm IS installed: constructs a real client via a mock model path.
- When mlx-lm is NOT installed: patches the import so MLXClient can be
  constructed, then verifies complete_json raises NotImplementedError.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from types import ModuleType
from unittest.mock import MagicMock

import pytest


def _build_mock_mlx_module() -> ModuleType:
    """Build a fake mlx_lm module suitable for patching into sys.modules."""
    # Use MagicMock without spec so arbitrary attributes work (mlx_lm.load etc.)
    fake_mlx_lm = MagicMock()
    fake_model = MagicMock()
    fake_tokenizer = MagicMock()
    fake_tokenizer.encode.return_value = [1, 2, 3]
    fake_mlx_lm.load.return_value = (fake_model, fake_tokenizer)
    fake_mlx_lm.generate.return_value = "fake output"
    return fake_mlx_lm  # type: ignore[return-value]


def test_mlx_complete_json_raises_not_implemented_error() -> None:
    """MLXClient.complete_json raises NotImplementedError.

    Works whether mlx-lm is installed or not by patching sys.modules.
    """
    # Patch mlx_lm into sys.modules so MLXClient can be constructed
    # even when the real mlx-lm is absent (CI environment).
    fake_mlx_lm = _build_mock_mlx_module()
    original = sys.modules.get("mlx_lm")
    sys.modules["mlx_lm"] = fake_mlx_lm  # type: ignore[assignment]
    try:
        # Force a reimport of mlx_client to pick up the patched module.
        import samantha_server.llm.mlx_client as mlx_mod

        importlib.reload(mlx_mod)
        client = mlx_mod.MLXClient()
        with pytest.raises(NotImplementedError) as exc_info:
            client.complete_json(
                [{"role": "user", "content": "hello"}],
                schema={"type": "object"},
                schema_name="test",
            )
        # Error message should point at oMLX / GH-191 decision.
        assert "omlx" in str(exc_info.value).lower() or "json" in str(exc_info.value).lower()
    finally:
        if original is None:
            del sys.modules["mlx_lm"]
        else:
            sys.modules["mlx_lm"] = original
        # Reload to restore original state
        import samantha_server.llm.mlx_client as mlx_mod  # noqa: F811

        importlib.reload(mlx_mod)
