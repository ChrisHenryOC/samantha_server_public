"""Tests for RBAC + dispatch-token config additions (GH-119 slice 1).

Validates eager-load behaviour of RBAC_HMAC_KEY, RBAC_TOKEN_TTL_SEC, and
DISPATCH_TOKEN_TTL_SEC in samantha_server.config.
"""

from __future__ import annotations

import os
import subprocess
import sys


def _make_env_with_valid_secrets(**overrides: str | None) -> dict[str, str]:
    """Return an env dict suitable for importing samantha_server.config."""
    env = {k: v for k, v in os.environ.items()}
    env["RECEIPT_SIGNING_KEY"] = "a" * 64
    env["PHI_HASH_SALT"] = "a" * 64
    env["RBAC_HMAC_KEY"] = "b" * 64  # valid 32-byte hex, not a test sentinel
    # Strip PYTEST_CURRENT_TEST so the sentinel guard fires in subprocesses
    env.pop("PYTEST_CURRENT_TEST", None)
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return env


def _import_config_in_subprocess(**env_overrides: str | None) -> subprocess.CompletedProcess[str]:
    env = _make_env_with_valid_secrets(**env_overrides)
    return subprocess.run(
        [sys.executable, "-c", "import samantha_server.config"],
        env=env,
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# RBAC_HMAC_KEY — required 32-byte hex
# ---------------------------------------------------------------------------


def test_rbac_hmac_key_missing_raises() -> None:
    """Missing RBAC_HMAC_KEY → MisconfiguredEnvironmentError."""
    result = _import_config_in_subprocess(RBAC_HMAC_KEY=None)
    assert result.returncode != 0
    assert "RBAC_HMAC_KEY" in result.stderr


def test_rbac_hmac_key_short_raises() -> None:
    """Too-short RBAC_HMAC_KEY → MisconfiguredEnvironmentError."""
    result = _import_config_in_subprocess(RBAC_HMAC_KEY="deadbeef")  # 4 bytes, not 32
    assert result.returncode != 0
    assert "RBAC_HMAC_KEY" in result.stderr


def test_rbac_hmac_key_non_hex_raises() -> None:
    """Non-hex RBAC_HMAC_KEY → MisconfiguredEnvironmentError."""
    result = _import_config_in_subprocess(RBAC_HMAC_KEY="NOTHEX" * 12)
    assert result.returncode != 0
    assert "RBAC_HMAC_KEY" in result.stderr


def test_rbac_hmac_key_test_sentinel_rejected_outside_pytest() -> None:
    """Test-sentinel RBAC_HMAC_KEY rejected outside pytest (no PYTEST_CURRENT_TEST)."""
    test_sentinel_hex = "de" + "ad" + "be" + "ef" + "de" + "ad" + "be" + "ef" + "00" * 24
    result = _import_config_in_subprocess(RBAC_HMAC_KEY=test_sentinel_hex)
    assert result.returncode != 0
    # Should mention the test-sentinel prefix
    assert "RBAC_HMAC_KEY" in result.stderr or "test-sentinel" in result.stderr.lower()


def test_rbac_hmac_key_conftest_cafebabe_sentinel_rejected_outside_pytest() -> None:
    """Conftest CAFEBABE-prefixed test sentinel rejected outside pytest.

    The conftest._TEST_RBAC_KEY starts with CAFEBABE... (32 hex bytes).
    If this value leaked into a production .env the sentinel guard must
    catch it.  This test drives the _TEST_RBAC_KEY_PREFIXES guard in
    config.py by running without PYTEST_CURRENT_TEST set.
    """
    # Replicate conftest._TEST_RBAC_KEY = "CAFEBABE" + "DEADBEEF" * 6 + "CAFEBABE"
    conftest_sentinel_hex = "CAFEBABE" + "DEADBEEF" * 6 + "CAFEBABE"
    result = _import_config_in_subprocess(RBAC_HMAC_KEY=conftest_sentinel_hex)
    assert result.returncode != 0
    assert "RBAC_HMAC_KEY" in result.stderr or "test-sentinel" in result.stderr.lower()


def test_rbac_hmac_key_valid_decodes_to_32_bytes() -> None:
    """Valid RBAC_HMAC_KEY loads as bytes in the test session."""
    import samantha_server.config as cfg

    assert isinstance(cfg.RBAC_HMAC_KEY, bytes)
    assert len(cfg.RBAC_HMAC_KEY) == 32


# ---------------------------------------------------------------------------
# RBAC_TOKEN_TTL_SEC — int, default 86400, reject <= 0
# ---------------------------------------------------------------------------


def test_rbac_token_ttl_sec_default_is_86400() -> None:
    """RBAC_TOKEN_TTL_SEC defaults to 86400."""
    import samantha_server.config as cfg

    # In the test session conftest sets RBAC_HMAC_KEY; TTL should use default.
    assert cfg.RBAC_TOKEN_TTL_SEC == 86400


def test_rbac_token_ttl_sec_zero_raises() -> None:
    """RBAC_TOKEN_TTL_SEC=0 → MisconfiguredEnvironmentError."""
    result = _import_config_in_subprocess(RBAC_TOKEN_TTL_SEC="0")
    assert result.returncode != 0
    assert "RBAC_TOKEN_TTL_SEC" in result.stderr


def test_rbac_token_ttl_sec_negative_raises() -> None:
    """RBAC_TOKEN_TTL_SEC=-1 → MisconfiguredEnvironmentError."""
    result = _import_config_in_subprocess(RBAC_TOKEN_TTL_SEC="-1")
    assert result.returncode != 0
    assert "RBAC_TOKEN_TTL_SEC" in result.stderr


def test_rbac_token_ttl_sec_non_integer_raises() -> None:
    """RBAC_TOKEN_TTL_SEC=abc → MisconfiguredEnvironmentError."""
    result = _import_config_in_subprocess(RBAC_TOKEN_TTL_SEC="abc")
    assert result.returncode != 0


def test_rbac_token_ttl_sec_positive_accepted() -> None:
    """RBAC_TOKEN_TTL_SEC=3600 is valid."""
    result = _import_config_in_subprocess(RBAC_TOKEN_TTL_SEC="3600")
    assert result.returncode == 0


# ---------------------------------------------------------------------------
# DISPATCH_TOKEN_TTL_SEC — int, default 60, reject <= 0
# ---------------------------------------------------------------------------


def test_dispatch_token_ttl_sec_default_is_60() -> None:
    """DISPATCH_TOKEN_TTL_SEC defaults to 60."""
    import samantha_server.config as cfg

    assert cfg.DISPATCH_TOKEN_TTL_SEC == 60


def test_dispatch_token_ttl_sec_zero_raises() -> None:
    """DISPATCH_TOKEN_TTL_SEC=0 → MisconfiguredEnvironmentError."""
    result = _import_config_in_subprocess(DISPATCH_TOKEN_TTL_SEC="0")
    assert result.returncode != 0
    assert "DISPATCH_TOKEN_TTL_SEC" in result.stderr


def test_dispatch_token_ttl_sec_negative_raises() -> None:
    """DISPATCH_TOKEN_TTL_SEC=-1 → MisconfiguredEnvironmentError."""
    result = _import_config_in_subprocess(DISPATCH_TOKEN_TTL_SEC="-1")
    assert result.returncode != 0
    assert "DISPATCH_TOKEN_TTL_SEC" in result.stderr


def test_dispatch_token_ttl_sec_non_integer_raises() -> None:
    """DISPATCH_TOKEN_TTL_SEC=xyz → MisconfiguredEnvironmentError."""
    result = _import_config_in_subprocess(DISPATCH_TOKEN_TTL_SEC="xyz")
    assert result.returncode != 0


def test_dispatch_token_ttl_sec_positive_accepted() -> None:
    """DISPATCH_TOKEN_TTL_SEC=120 is valid."""
    result = _import_config_in_subprocess(DISPATCH_TOKEN_TTL_SEC="120")
    assert result.returncode == 0
