"""Tests for SAMANTHA_LLM_OUTPUT_MODE config var.

Tests:
- Default value is "json".
- "free_text" is accepted.
- Invalid value raises MisconfiguredEnvironmentError at module-reload time.
- LLM_PROVIDER=mlx + SAMANTHA_LLM_OUTPUT_MODE=json raises MisconfiguredEnvironmentError.
- LLM_PROVIDER=mlx + SAMANTHA_LLM_OUTPUT_MODE=free_text is valid.
"""

from __future__ import annotations

import importlib
import subprocess
import sys


def _make_env_with_valid_secrets(**overrides: str | None) -> dict[str, str]:
    """Return an env dict suitable for importing samantha_server.config."""
    import os

    env = {k: v for k, v in os.environ.items()}
    env["RECEIPT_SIGNING_KEY"] = "a" * 64
    env["PHI_HASH_SALT"] = "a" * 64
    env["RBAC_HMAC_KEY"] = "b" * 64
    env.pop("PYTEST_CURRENT_TEST", None)
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return env


def test_llm_output_mode_default_is_json(monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[name-defined]  # noqa: F821
    """SAMANTHA_LLM_OUTPUT_MODE defaults to 'json' when unset."""
    monkeypatch.delenv("SAMANTHA_LLM_OUTPUT_MODE", raising=False)
    import samantha_server.config as cfg

    importlib.reload(cfg)
    try:
        assert cfg.SAMANTHA_LLM_OUTPUT_MODE == "json"
    finally:
        importlib.reload(cfg)


def test_llm_output_mode_free_text_accepted(monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[name-defined]  # noqa: F821
    """SAMANTHA_LLM_OUTPUT_MODE='free_text' is a valid setting."""
    monkeypatch.setenv("SAMANTHA_LLM_OUTPUT_MODE", "free_text")
    import samantha_server.config as cfg

    importlib.reload(cfg)
    try:
        assert cfg.SAMANTHA_LLM_OUTPUT_MODE == "free_text"
    finally:
        importlib.reload(cfg)


def test_llm_output_mode_json_accepted(monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[name-defined]  # noqa: F821
    """SAMANTHA_LLM_OUTPUT_MODE='json' is a valid setting."""
    monkeypatch.setenv("SAMANTHA_LLM_OUTPUT_MODE", "json")
    import samantha_server.config as cfg

    importlib.reload(cfg)
    try:
        assert cfg.SAMANTHA_LLM_OUTPUT_MODE == "json"
    finally:
        importlib.reload(cfg)


def test_llm_output_mode_invalid_raises_at_startup() -> None:
    """SAMANTHA_LLM_OUTPUT_MODE='invalid' raises MisconfiguredEnvironmentError at import."""
    env = _make_env_with_valid_secrets(SAMANTHA_LLM_OUTPUT_MODE="invalid")
    result = subprocess.run(
        [sys.executable, "-c", "import samantha_server.config"],
        env=env,
        capture_output=True,
    )
    assert result.returncode != 0, "Expected non-zero exit for invalid SAMANTHA_LLM_OUTPUT_MODE"
    assert b"SAMANTHA_LLM_OUTPUT_MODE" in result.stderr


def test_mlx_provider_with_json_mode_raises_at_startup() -> None:
    """LLM_PROVIDER=mlx + SAMANTHA_LLM_OUTPUT_MODE=json raises MisconfiguredEnvironmentError.

    MLXClient.complete_json() raises NotImplementedError (not in LLMClientError hierarchy),
    so this combination must be rejected at startup before any traffic is served.
    """
    env = _make_env_with_valid_secrets(
        LLM_PROVIDER="mlx",
        SAMANTHA_LLM_OUTPUT_MODE="json",
    )
    result = subprocess.run(
        [sys.executable, "-c", "import samantha_server.config"],
        env=env,
        capture_output=True,
    )
    assert result.returncode != 0, (
        "Expected non-zero exit for LLM_PROVIDER=mlx + SAMANTHA_LLM_OUTPUT_MODE=json"
    )
    assert b"mlx" in result.stderr.lower() or b"SAMANTHA_LLM_OUTPUT_MODE" in result.stderr


def test_mlx_provider_with_free_text_mode_is_valid() -> None:
    """LLM_PROVIDER=mlx + SAMANTHA_LLM_OUTPUT_MODE=free_text is a valid combination."""
    env = _make_env_with_valid_secrets(
        LLM_PROVIDER="mlx",
        SAMANTHA_LLM_OUTPUT_MODE="free_text",
    )
    result = subprocess.run(
        [sys.executable, "-c", "import samantha_server.config"],
        env=env,
        capture_output=True,
    )
    assert result.returncode == 0, (
        f"LLM_PROVIDER=mlx + SAMANTHA_LLM_OUTPUT_MODE=free_text should be valid.\n"
        f"stderr: {result.stderr.decode()!r}"
    )
