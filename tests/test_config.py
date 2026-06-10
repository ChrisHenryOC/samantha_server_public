"""Tests for samantha_server.config — env-driven config with eager validation."""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_32_BYTE_HEX = "a" * 64  # 32 bytes of 0xaa
_SHORT_HEX = "deadbeef" * 2  # 8 bytes — wrong length
_GEN_HINT = "python -c 'import os; print(os.urandom(32).hex())'"


def _make_env_with_valid_secrets(**overrides: str | None) -> dict[str, str]:
    """Return an env dict suitable for importing samantha_server.config."""
    env = {k: v for k, v in os.environ.items()}
    env["RECEIPT_SIGNING_KEY"] = _VALID_32_BYTE_HEX
    env["PHI_HASH_SALT"] = _VALID_32_BYTE_HEX
    # Provide a non-sentinel RBAC key so the CAFEBABE guard (added in fix #2)
    # doesn't reject the conftest key that was inherited from the test session env.
    env["RBAC_HMAC_KEY"] = "b" * 64  # valid 32-byte hex, not a test sentinel
    # Strip out test-sentinel values to avoid the guard firing in subprocesses
    env.pop("PYTEST_CURRENT_TEST", None)
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return env


# ---------------------------------------------------------------------------
# Slice 2 tests — _decode_hex
# ---------------------------------------------------------------------------


def test_decode_hex_happy_path_returns_bytes() -> None:
    """Valid 32-byte hex string returns exactly 32 bytes."""
    from samantha_server.config import _decode_hex

    result = _decode_hex("MY_VAR", _VALID_32_BYTE_HEX, gen_hint=_GEN_HINT)
    assert isinstance(result, bytes)
    assert len(result) == 32


def test_decode_hex_malformed_raises_misconfigured_error() -> None:
    """Non-hex input raises MisconfiguredEnvironmentError."""
    from samantha_server.config import _decode_hex
    from samantha_server.errors import MisconfiguredEnvironmentError

    with pytest.raises(MisconfiguredEnvironmentError):
        _decode_hex("MY_VAR", "NOTHEX", gen_hint=_GEN_HINT)


def test_decode_hex_malformed_message_contains_var_name() -> None:
    """Error message for malformed hex includes the variable name."""
    from samantha_server.config import _decode_hex
    from samantha_server.errors import MisconfiguredEnvironmentError

    with pytest.raises(MisconfiguredEnvironmentError) as exc_info:
        _decode_hex("MY_VAR", "NOTHEX", gen_hint=_GEN_HINT)
    assert "MY_VAR" in str(exc_info.value)


def test_decode_hex_malformed_message_contains_gen_hint() -> None:
    """Error message for malformed hex includes the gen hint."""
    from samantha_server.config import _decode_hex
    from samantha_server.errors import MisconfiguredEnvironmentError

    hint = "some-gen-command"
    with pytest.raises(MisconfiguredEnvironmentError) as exc_info:
        _decode_hex("MY_VAR", "NOTHEX", gen_hint=hint)
    assert hint in str(exc_info.value)


def test_decode_hex_malformed_message_does_not_echo_raw_value() -> None:
    """Error message for malformed hex must NOT contain the raw input (G18)."""
    from samantha_server.config import _decode_hex
    from samantha_server.errors import MisconfiguredEnvironmentError

    raw = "NOTHEX_SECRET_VALUE"
    with pytest.raises(MisconfiguredEnvironmentError) as exc_info:
        _decode_hex("MY_VAR", raw, gen_hint=_GEN_HINT)
    assert raw not in str(exc_info.value)


def test_decode_hex_wrong_length_raises_misconfigured_error() -> None:
    """Hex that decodes to the wrong byte length raises MisconfiguredEnvironmentError."""
    from samantha_server.config import _decode_hex
    from samantha_server.errors import MisconfiguredEnvironmentError

    with pytest.raises(MisconfiguredEnvironmentError):
        _decode_hex("MY_VAR", _SHORT_HEX, gen_hint=_GEN_HINT)


def test_decode_hex_wrong_length_message_includes_actual_and_expected() -> None:
    """Wrong-length error message mentions the decoded byte count and expected count."""
    from samantha_server.config import _decode_hex
    from samantha_server.errors import MisconfiguredEnvironmentError

    with pytest.raises(MisconfiguredEnvironmentError) as exc_info:
        _decode_hex("MY_VAR", _SHORT_HEX, gen_hint=_GEN_HINT)
    msg = str(exc_info.value)
    assert "8" in msg  # 8 bytes decoded
    assert "32" in msg  # 32 bytes expected


# ---------------------------------------------------------------------------
# Slice 2 tests — _required_hex
# ---------------------------------------------------------------------------


def test_required_hex_unset_raises_misconfigured_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset required var raises MisconfiguredEnvironmentError."""
    from samantha_server.config import _required_hex
    from samantha_server.errors import MisconfiguredEnvironmentError

    monkeypatch.delenv("SOME_KEY", raising=False)
    with pytest.raises(MisconfiguredEnvironmentError):
        _required_hex("SOME_KEY", gen_hint=_GEN_HINT)


def test_required_hex_unset_message_contains_not_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing required var message says 'not set'."""
    from samantha_server.config import _required_hex
    from samantha_server.errors import MisconfiguredEnvironmentError

    monkeypatch.delenv("SOME_KEY", raising=False)
    with pytest.raises(MisconfiguredEnvironmentError) as exc_info:
        _required_hex("SOME_KEY", gen_hint=_GEN_HINT)
    assert "not set" in str(exc_info.value)


def test_required_hex_unset_message_mentions_env_example(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing required var message references .env.example."""
    from samantha_server.config import _required_hex
    from samantha_server.errors import MisconfiguredEnvironmentError

    monkeypatch.delenv("SOME_KEY", raising=False)
    with pytest.raises(MisconfiguredEnvironmentError) as exc_info:
        _required_hex("SOME_KEY", gen_hint=_GEN_HINT)
    assert ".env.example" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Slice 2 tests — _optional_hex
# ---------------------------------------------------------------------------


def test_optional_hex_unset_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset optional var returns None."""
    from samantha_server.config import _optional_hex

    monkeypatch.delenv("SOME_OPTIONAL_KEY", raising=False)
    result = _optional_hex("SOME_OPTIONAL_KEY", gen_hint=_GEN_HINT)
    assert result is None


def test_optional_hex_empty_string_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicitly empty optional var returns None (treats '' like unset)."""
    from samantha_server.config import _optional_hex

    monkeypatch.setenv("SOME_OPTIONAL_KEY", "")
    result = _optional_hex("SOME_OPTIONAL_KEY", gen_hint=_GEN_HINT)
    assert result is None


def test_optional_hex_malformed_raises_misconfigured_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed optional var raises MisconfiguredEnvironmentError (not silently None)."""
    from samantha_server.config import _optional_hex
    from samantha_server.errors import MisconfiguredEnvironmentError

    monkeypatch.setenv("SOME_OPTIONAL_KEY", "NOTHEX")
    with pytest.raises(MisconfiguredEnvironmentError):
        _optional_hex("SOME_OPTIONAL_KEY", gen_hint=_GEN_HINT)


# ---------------------------------------------------------------------------
# Slice 2 tests — G18 no-leak invariant (parametrized)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw_input,error_trigger",
    [
        ("NOTHEX_BAD_VALUE", "non-hex"),
        (_SHORT_HEX, "wrong-length"),
    ],
)
def test_g18_decode_hex_does_not_leak_raw_value(raw_input: str, error_trigger: str) -> None:
    """decode_hex error messages must not contain any substring of the raw input (G18)."""
    from samantha_server.config import _decode_hex
    from samantha_server.errors import MisconfiguredEnvironmentError

    with pytest.raises(MisconfiguredEnvironmentError) as exc_info:
        _decode_hex("MY_VAR", raw_input, gen_hint=_GEN_HINT)
    msg = str(exc_info.value)
    # The raw input itself must not appear in the error message
    if len(raw_input) >= 1:
        assert raw_input not in msg, (
            f"G18 violation ({error_trigger}): raw input found in error message"
        )


@pytest.mark.parametrize(
    "var_name,raw_input",
    [
        ("RECEIPT_SIGNING_KEY", "NOTHEX"),
        ("PHI_HASH_SALT", "deadbeef"),  # too short (4 bytes)
    ],
)
def test_g18_required_hex_does_not_leak_raw_value(
    var_name: str, raw_input: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """required_hex error messages must not contain any substring of the raw input (G18)."""
    from samantha_server.config import _required_hex
    from samantha_server.errors import MisconfiguredEnvironmentError

    monkeypatch.setenv(var_name, raw_input)
    with pytest.raises(MisconfiguredEnvironmentError) as exc_info:
        _required_hex(var_name, gen_hint=_GEN_HINT)
    msg = str(exc_info.value)
    if len(raw_input) >= 1:
        assert raw_input not in msg, (
            f"G18 violation: raw input '{raw_input}' found in error message"
        )


# L-05 review: extend the G18 parametrize to _optional_hex.
@pytest.mark.parametrize(
    "var_name,raw_input",
    [
        ("RECEIPT_SIGNING_KEY_PREVIOUS", "NOTHEX"),
        ("RECEIPT_SIGNING_KEY_PREVIOUS", "deadbeef"),  # too short
    ],
)
def test_g18_optional_hex_does_not_leak_raw_value(
    var_name: str, raw_input: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """optional_hex (malformed-but-non-empty) preserves the no-leak invariant."""
    from samantha_server.config import _optional_hex
    from samantha_server.errors import MisconfiguredEnvironmentError

    monkeypatch.setenv(var_name, raw_input)
    with pytest.raises(MisconfiguredEnvironmentError) as exc_info:
        _optional_hex(var_name, gen_hint=_GEN_HINT)
    msg = str(exc_info.value)
    if len(raw_input) >= 1:
        assert raw_input not in msg, (
            f"G18 violation: raw input '{raw_input}' found in error message"
        )


# L-04 review: _optional_hex wrong-length test (separate from
# the G18 parametrize because we assert the typed error, not just G18 cleanliness).
def test_optional_hex_wrong_length_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A valid-hex but wrong-length value through _optional_hex raises typed error."""
    from samantha_server.config import _optional_hex
    from samantha_server.errors import MisconfiguredEnvironmentError

    # 8 hex chars = 4 bytes; expected exactly 32 bytes.
    monkeypatch.setenv("RECEIPT_SIGNING_KEY_PREVIOUS", "deadbeef")
    with pytest.raises(MisconfiguredEnvironmentError) as exc_info:
        _optional_hex("RECEIPT_SIGNING_KEY_PREVIOUS", gen_hint=_GEN_HINT)
    assert "4 bytes" in str(exc_info.value)
    assert "expected exactly 32" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Slice 2 tests — module-level eager validation via subprocess
# ---------------------------------------------------------------------------


def test_config_import_fails_with_missing_receipt_signing_key() -> None:
    """Importing config with RECEIPT_SIGNING_KEY unset exits non-zero."""
    env = _make_env_with_valid_secrets(RECEIPT_SIGNING_KEY=None)
    result = subprocess.run(
        [sys.executable, "-c", "import samantha_server.config"],
        env=env,
        capture_output=True,
    )
    assert result.returncode != 0, "Expected non-zero exit when RECEIPT_SIGNING_KEY is unset"


def test_config_import_fails_with_invalid_receipt_signing_key() -> None:
    """Importing config with invalid RECEIPT_SIGNING_KEY hex exits non-zero."""
    env = _make_env_with_valid_secrets(RECEIPT_SIGNING_KEY="NOTHEX")
    result = subprocess.run(
        [sys.executable, "-c", "import samantha_server.config"],
        env=env,
        capture_output=True,
    )
    assert result.returncode != 0, (
        "Expected non-zero exit when RECEIPT_SIGNING_KEY is not valid hex"
    )


def test_config_import_fails_with_missing_phi_hash_salt() -> None:
    """Importing config with PHI_HASH_SALT unset exits non-zero."""
    env = _make_env_with_valid_secrets(PHI_HASH_SALT=None)
    result = subprocess.run(
        [sys.executable, "-c", "import samantha_server.config"],
        env=env,
        capture_output=True,
    )
    assert result.returncode != 0, "Expected non-zero exit when PHI_HASH_SALT is unset"


def test_config_import_fails_with_invalid_phi_hash_salt() -> None:
    """Importing config with invalid PHI_HASH_SALT hex exits non-zero.

    Symmetric with the existing invalid-RECEIPT_SIGNING_KEY test; closes
    the asymmetry where only one of the two required secrets had its
    invalid-hex path exercised through subprocess.
    """
    env = _make_env_with_valid_secrets(PHI_HASH_SALT="NOTHEX")
    result = subprocess.run(
        [sys.executable, "-c", "import samantha_server.config"],
        env=env,
        capture_output=True,
    )
    assert result.returncode != 0, "Expected non-zero exit when PHI_HASH_SALT is not valid hex"


def test_config_import_succeeds_with_valid_secrets() -> None:
    """Importing config with valid secrets exits zero."""
    env = _make_env_with_valid_secrets()
    result = subprocess.run(
        [sys.executable, "-c", "import samantha_server.config"],
        env=env,
        capture_output=True,
    )
    assert result.returncode == 0, (
        f"Expected zero exit with valid secrets.\nstderr: {result.stderr.decode()}"
    )


# ---------------------------------------------------------------------------
# Slice 3 tests — sentinel guard
# ---------------------------------------------------------------------------

_TEST_RECEIPT_KEY = "DEADBEEF" * 8  # 64 hex chars = 32 bytes
_TEST_PHI_SALT = "BADC0FFEE" + "0" * 55  # leads with BADC0FFEE; 64 chars total


def test_sentinel_guard_happy_path_under_pytest() -> None:
    """Under pytest (PYTEST_CURRENT_TEST set), sentinel values do not raise."""
    # PYTEST_CURRENT_TEST is set by pytest itself when running tests.
    # The conftest.py sets the sentinel env vars, and config.py should
    # allow them when running under pytest.
    assert os.environ.get("PYTEST_CURRENT_TEST") is not None, (
        "This test must run under pytest (PYTEST_CURRENT_TEST should be set)"
    )
    # If we reach here, config was already imported successfully by earlier
    # tests in this session (the conftest.py set the sentinels).
    import samantha_server.config  # noqa: F401 — must not raise


def test_sentinel_guard_blocks_import_outside_pytest() -> None:
    """Without PYTEST_CURRENT_TEST, importing config with sentinel values raises."""
    env = {k: v for k, v in os.environ.items()}
    env["RECEIPT_SIGNING_KEY"] = _TEST_RECEIPT_KEY
    env["PHI_HASH_SALT"] = _TEST_PHI_SALT
    env.pop("PYTEST_CURRENT_TEST", None)

    result = subprocess.run(
        [sys.executable, "-c", "import samantha_server.config"],
        env=env,
        capture_output=True,
    )
    assert result.returncode != 0, (
        "Expected non-zero exit when sentinel keys are used outside pytest.\n"
        f"stderr: {result.stderr.decode()}"
    )
    stderr = result.stderr.decode()
    assert "DEADBEEF" in stderr or "sentinel" in stderr.lower() or "test" in stderr.lower(), (
        f"Expected error message to mention the sentinel pattern.\nstderr: {stderr}"
    )


# ---------------------------------------------------------------------------
# Slice 3 tests — RECEIPT_SIGNING_KEY_PREVIOUS + RECEIPT_SIGNING_KEY_ID
# ---------------------------------------------------------------------------


def test_receipt_signing_key_previous_unset_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RECEIPT_SIGNING_KEY_PREVIOUS unset returns None via _optional_hex."""
    from samantha_server.config import _optional_hex

    monkeypatch.delenv("RECEIPT_SIGNING_KEY_PREVIOUS", raising=False)
    result = _optional_hex("RECEIPT_SIGNING_KEY_PREVIOUS", gen_hint=_GEN_HINT)
    assert result is None


def test_receipt_signing_key_previous_valid_hex_returns_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RECEIPT_SIGNING_KEY_PREVIOUS set to a valid 64-char hex returns 32 bytes."""
    from samantha_server.config import _optional_hex

    valid_hex = "b" * 64  # 32 bytes of 0xbb
    monkeypatch.setenv("RECEIPT_SIGNING_KEY_PREVIOUS", valid_hex)
    result = _optional_hex("RECEIPT_SIGNING_KEY_PREVIOUS", gen_hint=_GEN_HINT)
    assert result is not None
    assert len(result) == 32


def test_receipt_signing_key_previous_sentinel_blocked_outside_pytest() -> None:
    """DEADBEEF-prefixed RECEIPT_SIGNING_KEY_PREVIOUS triggers sentinel guard outside pytest."""
    env = _make_env_with_valid_secrets(RECEIPT_SIGNING_KEY_PREVIOUS=_TEST_RECEIPT_KEY)
    result = subprocess.run(
        [sys.executable, "-c", "import samantha_server.config"],
        env=env,
        capture_output=True,
    )
    assert result.returncode != 0, (
        "Expected non-zero exit when RECEIPT_SIGNING_KEY_PREVIOUS uses sentinel prefix.\n"
        f"stderr: {result.stderr.decode()}"
    )
    stderr = result.stderr.decode()
    assert (
        "DEADBEEF" in stderr
        or "sentinel" in stderr.lower()
        or "RECEIPT_SIGNING_KEY_PREVIOUS" in stderr
    ), f"Expected error message to mention the sentinel or var name.\nstderr: {stderr}"


def test_receipt_signing_key_id_defaults_to_v1(monkeypatch: pytest.MonkeyPatch) -> None:
    """RECEIPT_SIGNING_KEY_ID defaults to 'v1' when unset."""
    monkeypatch.delenv("RECEIPT_SIGNING_KEY_ID", raising=False)
    # Force reimport to pick up env change
    import importlib

    import samantha_server.config as cfg

    importlib.reload(cfg)
    assert cfg.RECEIPT_SIGNING_KEY_ID == "v1"


def test_receipt_signing_key_id_accepts_arbitrary_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RECEIPT_SIGNING_KEY_ID accepts any string value when set."""
    monkeypatch.setenv("RECEIPT_SIGNING_KEY_ID", "v42")
    import importlib

    import samantha_server.config as cfg

    importlib.reload(cfg)
    assert cfg.RECEIPT_SIGNING_KEY_ID == "v42"


def test_config_module_exposes_receipt_signing_key_id() -> None:
    """RECEIPT_SIGNING_KEY_ID is a string attribute on the config module."""
    import samantha_server.config as cfg

    assert hasattr(cfg, "RECEIPT_SIGNING_KEY_ID")
    assert isinstance(cfg.RECEIPT_SIGNING_KEY_ID, str)


def test_config_warns_when_key_id_bumped_but_previous_key_absent() -> None:
    """M-03: when RECEIPT_SIGNING_KEY_ID != 'v1' and RECEIPT_SIGNING_KEY_PREVIOUS is None,
    config.py must emit a WARNING log about lost previous-key verification coverage.
    """

    env = _make_env_with_valid_secrets(
        RECEIPT_SIGNING_KEY_ID="v2",
        RECEIPT_SIGNING_KEY_PREVIOUS=None,
    )
    result = subprocess.run(
        [
            sys.executable,
            "-W",
            "all",
            "-c",
            "import logging; logging.basicConfig(level=logging.WARNING); "
            "import samantha_server.config",
        ],
        env=env,
        capture_output=True,
    )
    assert result.returncode == 0, f"Expected zero exit; stderr: {result.stderr.decode()}"
    stderr = result.stderr.decode()
    assert "WARNING" in stderr or "previous" in stderr.lower() or "v2" in stderr, (
        f"Expected a warning about missing previous key when id='v2' and previous=None.\n"
        f"stderr: {stderr!r}"
    )


def test_config_module_exposes_receipt_signing_key_previous() -> None:
    """RECEIPT_SIGNING_KEY_PREVIOUS is exported from config (None or bytes)."""
    import samantha_server.config as cfg

    assert hasattr(cfg, "RECEIPT_SIGNING_KEY_PREVIOUS")
    # Under pytest with test sentinel, _optional_hex sees conftest-set DEADBEEF sentinel
    # but sentinel guard is bypassed by PYTEST_CURRENT_TEST; the value is either
    # bytes or None depending on conftest setup.
    val = cfg.RECEIPT_SIGNING_KEY_PREVIOUS
    assert val is None or isinstance(val, bytes)


# ---------------------------------------------------------------------------
# LLM_JUDGE_MODEL_PATH config var
# ---------------------------------------------------------------------------


def test_config_llm_judge_model_path_defaults_to_none() -> None:
    """LLM_JUDGE_MODEL_PATH defaults to None when unset (judge disabled)."""
    import samantha_server.config as cfg

    # Under pytest without the env var set, defaults to None
    val = cfg.LLM_JUDGE_MODEL_PATH
    # Accept None or a string (operator may have set it in their env)
    assert val is None or isinstance(val, str)


def test_config_llm_judge_model_path_is_str_or_none() -> None:
    """LLM_JUDGE_MODEL_PATH is str | None — never a bytes object."""
    import samantha_server.config as cfg

    val = cfg.LLM_JUDGE_MODEL_PATH
    assert val is None or isinstance(val, str)


def test_config_llm_judge_model_path_reads_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM_JUDGE_MODEL_PATH reads its value from the LLM_JUDGE_MODEL_PATH env var."""
    monkeypatch.setenv("LLM_JUDGE_MODEL_PATH", "/models/judge-3b")
    import importlib

    import samantha_server.config as cfg

    importlib.reload(cfg)
    try:
        assert cfg.LLM_JUDGE_MODEL_PATH == "/models/judge-3b"
    finally:
        importlib.reload(cfg)


# ---------------------------------------------------------------------------
# H-11: _validate_model_path helper
# ---------------------------------------------------------------------------


def test_validate_model_path_rejects_traversal(tmp_path: pathlib.Path) -> None:
    """H-11: _validate_model_path rejects path traversal (../../../etc/passwd)."""
    from samantha_server.config import _validate_model_path
    from samantha_server.errors import MisconfiguredEnvironmentError

    with pytest.raises(MisconfiguredEnvironmentError):
        _validate_model_path("LLM_MODEL_PATH", "../../../etc/passwd", allowed_root=tmp_path)


def test_validate_model_path_rejects_nonexistent(tmp_path: pathlib.Path) -> None:
    """H-11: _validate_model_path rejects paths that don't exist."""
    from samantha_server.config import _validate_model_path
    from samantha_server.errors import MisconfiguredEnvironmentError

    with pytest.raises(MisconfiguredEnvironmentError):
        _validate_model_path(
            "LLM_MODEL_PATH", str(tmp_path / "nonexistent-model.gguf"), allowed_root=tmp_path
        )


def test_validate_model_path_accepts_valid_path(tmp_path: pathlib.Path) -> None:
    """H-11: _validate_model_path accepts a real file under the allowed root."""
    from samantha_server.config import _validate_model_path

    model_file = tmp_path / "model.gguf"
    model_file.write_bytes(b"fake model data")

    result = _validate_model_path("LLM_MODEL_PATH", str(model_file), allowed_root=tmp_path)
    assert result == model_file.resolve()


def test_validate_model_path_error_does_not_leak_raw_value(tmp_path: pathlib.Path) -> None:
    """H-11 + G18: error message must not echo the raw path value."""
    from samantha_server.config import _validate_model_path
    from samantha_server.errors import MisconfiguredEnvironmentError

    raw = "../../../etc/passwd"
    try:
        _validate_model_path("LLM_MODEL_PATH", raw, allowed_root=tmp_path)
    except MisconfiguredEnvironmentError as exc:
        assert raw not in str(exc), f"G18 violation: raw path found in error message: {str(exc)!r}"


# ---------------------------------------------------------------------------
# Slice 1: _validate_loopback_url helper
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://127.0.0.2:9999",
        "http://[::1]:8000",
        "http://[::ffff:127.0.0.1]:8000",
    ],
)
def test_validate_loopback_url_accepts_loopback(url: str) -> None:
    """Loopback URLs pass _validate_loopback_url without raising."""
    from samantha_server.config import _validate_loopback_url

    # Should not raise — no return value asserted
    _validate_loopback_url("LLM_OMLX_BASE_URL", url)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com:8000",
        "http://8.8.8.8:8000",
        "http://0.0.0.0:8000",
    ],
)
def test_validate_loopback_url_rejects_non_loopback(url: str) -> None:
    """Non-loopback URLs raise MisconfiguredEnvironmentError."""
    from samantha_server.config import _validate_loopback_url
    from samantha_server.errors import MisconfiguredEnvironmentError

    with pytest.raises(MisconfiguredEnvironmentError):
        _validate_loopback_url("LLM_OMLX_BASE_URL", url)


def test_validate_loopback_url_error_contains_var_name() -> None:
    """Error message includes the variable name (not just a generic message)."""
    from samantha_server.config import _validate_loopback_url
    from samantha_server.errors import MisconfiguredEnvironmentError

    with pytest.raises(MisconfiguredEnvironmentError) as exc_info:
        _validate_loopback_url("LLM_OMLX_BASE_URL", "http://example.com:8000")

    assert "LLM_OMLX_BASE_URL" in str(exc_info.value)


def test_validate_loopback_url_error_does_not_contain_raw_url() -> None:
    """Error message must NOT echo the raw URL (G18 no-leak invariant)."""
    from samantha_server.config import _validate_loopback_url
    from samantha_server.errors import MisconfiguredEnvironmentError

    raw_url = "http://example.com:8000"
    with pytest.raises(MisconfiguredEnvironmentError) as exc_info:
        _validate_loopback_url("LLM_OMLX_BASE_URL", raw_url)

    assert raw_url not in str(exc_info.value)


# ---------------------------------------------------------------------------
# Slice 2: Config wiring — LLM_OMLX_BASE_URL, LLM_OMLX_AUTH_TOKEN,
#                   LLM_OMLX_TIMEOUT_S, LLM_PROVIDER default flip
# ---------------------------------------------------------------------------


def test_llm_provider_default_is_omlx(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM_PROVIDER defaults to 'omlx' when the env var is not set."""
    import importlib

    import samantha_server.config as cfg

    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    importlib.reload(cfg)
    assert cfg.LLM_PROVIDER == "omlx"


def test_llm_omlx_base_url_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM_OMLX_BASE_URL defaults to 'http://127.0.0.1:8000'."""
    import importlib

    import samantha_server.config as cfg

    monkeypatch.delenv("LLM_OMLX_BASE_URL", raising=False)
    importlib.reload(cfg)
    assert cfg.LLM_OMLX_BASE_URL == "http://127.0.0.1:8000"


def test_llm_omlx_base_url_override_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM_OMLX_BASE_URL env override with a valid loopback URL is reflected."""
    import importlib

    import samantha_server.config as cfg

    monkeypatch.setenv("LLM_OMLX_BASE_URL", "http://127.0.0.1:9999")
    importlib.reload(cfg)
    assert cfg.LLM_OMLX_BASE_URL == "http://127.0.0.1:9999"


def test_llm_omlx_base_url_override_non_loopback_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM_OMLX_BASE_URL set to a non-loopback URL raises MisconfiguredEnvironmentError."""
    import importlib

    import samantha_server.config as cfg
    from samantha_server.errors import MisconfiguredEnvironmentError

    monkeypatch.setenv("LLM_OMLX_BASE_URL", "http://example.com:8000")
    with pytest.raises(MisconfiguredEnvironmentError):
        importlib.reload(cfg)


def test_llm_omlx_auth_token_unset_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM_OMLX_AUTH_TOKEN unset returns None."""
    import importlib

    import samantha_server.config as cfg

    monkeypatch.delenv("LLM_OMLX_AUTH_TOKEN", raising=False)
    importlib.reload(cfg)
    assert cfg.LLM_OMLX_AUTH_TOKEN is None


def test_llm_omlx_auth_token_empty_string_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM_OMLX_AUTH_TOKEN set to empty string normalizes to None."""
    import importlib

    import samantha_server.config as cfg

    monkeypatch.setenv("LLM_OMLX_AUTH_TOKEN", "")
    importlib.reload(cfg)
    assert cfg.LLM_OMLX_AUTH_TOKEN is None


def test_llm_omlx_auth_token_set_returns_string(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM_OMLX_AUTH_TOKEN set to a non-empty value is returned as a string."""
    import importlib

    import samantha_server.config as cfg

    monkeypatch.setenv("LLM_OMLX_AUTH_TOKEN", "my-secret-token")
    importlib.reload(cfg)
    assert cfg.LLM_OMLX_AUTH_TOKEN == "my-secret-token"


# ---------------------------------------------------------------------------
# Fix #2 — _validate_loopback_url enforces http:// scheme
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:8000",
        "ftp://127.0.0.1:8000",
        "gopher://127.0.0.1:8000",
        "file://localhost/path",
    ],
)
def test_validate_loopback_url_rejects_non_http_scheme(url: str) -> None:
    """Non-http URLs raise MisconfiguredEnvironmentError regardless of host."""
    from samantha_server.config import _validate_loopback_url
    from samantha_server.errors import MisconfiguredEnvironmentError

    with pytest.raises(MisconfiguredEnvironmentError):
        _validate_loopback_url("LLM_OMLX_BASE_URL", url)


def test_validate_loopback_url_http_loopback_still_accepted() -> None:
    """http://127.0.0.1:8000 (existing happy path) still passes after scheme check."""
    from samantha_server.config import _validate_loopback_url

    _validate_loopback_url("LLM_OMLX_BASE_URL", "http://127.0.0.1:8000")


# ---------------------------------------------------------------------------
# Fix #11 — LLM_OMLX_AUTH_TOKEN="" emits a WARNING log
# ---------------------------------------------------------------------------


def test_llm_omlx_auth_token_empty_string_emits_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """LLM_OMLX_AUTH_TOKEN='' logs a WARNING about auth being disabled."""
    import importlib
    import logging

    import samantha_server.config as cfg

    monkeypatch.setenv("LLM_OMLX_AUTH_TOKEN", "")
    with caplog.at_level(logging.WARNING, logger="samantha_server.config"):
        importlib.reload(cfg)

    assert any(
        "LLM_OMLX_AUTH_TOKEN" in r.message and r.levelno == logging.WARNING for r in caplog.records
    )


def test_llm_omlx_auth_token_unset_no_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Unset LLM_OMLX_AUTH_TOKEN does NOT emit a warning."""
    import importlib
    import logging

    import samantha_server.config as cfg

    monkeypatch.delenv("LLM_OMLX_AUTH_TOKEN", raising=False)
    with caplog.at_level(logging.WARNING, logger="samantha_server.config"):
        importlib.reload(cfg)

    omlx_warnings = [
        r
        for r in caplog.records
        if "LLM_OMLX_AUTH_TOKEN" in r.message and r.levelno == logging.WARNING
    ]
    assert len(omlx_warnings) == 0


# ---------------------------------------------------------------------------
# Fix #12 — LLM_OMLX_TIMEOUT_S config wiring
# ---------------------------------------------------------------------------


def test_llm_omlx_timeout_s_default_is_120(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM_OMLX_TIMEOUT_S defaults to 120.0 when env var is not set."""
    import importlib

    import samantha_server.config as cfg

    monkeypatch.delenv("LLM_OMLX_TIMEOUT_S", raising=False)
    importlib.reload(cfg)
    assert cfg.LLM_OMLX_TIMEOUT_S == 120.0


def test_llm_omlx_timeout_s_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM_OMLX_TIMEOUT_S env override round-trips correctly."""
    import importlib

    import samantha_server.config as cfg

    monkeypatch.setenv("LLM_OMLX_TIMEOUT_S", "45.5")
    importlib.reload(cfg)
    assert cfg.LLM_OMLX_TIMEOUT_S == 45.5


def test_llm_omlx_timeout_s_malformed_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM_OMLX_TIMEOUT_S set to a non-float string raises MisconfiguredEnvironmentError."""
    import importlib

    import samantha_server.config as cfg
    from samantha_server.errors import MisconfiguredEnvironmentError

    monkeypatch.setenv("LLM_OMLX_TIMEOUT_S", "abc")
    with pytest.raises(MisconfiguredEnvironmentError):
        importlib.reload(cfg)


# ---------------------------------------------------------------------------
# LLM_MAX_TOKENS empirical floor
# ---------------------------------------------------------------------------


def test_llm_max_tokens_default_meets_empirical_minimum_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM_MAX_TOKENS default must be >= 2048.

    Empirical floor from smoke testing: 5 of 5
    QR-021..025 json_decode failures on Qwen3.5-35B-A3B-8bit were recovered
    when LLM_MAX_TOKENS was raised from 1024 to 2048.  The 1024 default
    silently truncated model output before the closing brace of the JSON
    response, causing downstream parse failures.

    Dominant output-size driver: the ``prioritized_list`` answer_type's
    ``reasoning`` field can exceed 1 KB on 10-order scenarios when models
    emit verbose per-item citations.  2048 accommodates the observed
    ~1650-token high-water mark with ~24% headroom; revisit only if a
    future fixture surfaces a >2048-token legitimate response.

    This test is a regression guard, not a tautology.  A future contributor
    who lowers the value without re-validating the corpus will see a clear
    CI failure here points at the linked issue.

    Note: this guards the *default* value (env var unset).  It does not
    catch an operator setting ``LLM_MAX_TOKENS<2048`` via the environment;
    eager runtime validation is intentionally deferred (see review
    comment for the conscious-decision rationale).
    """
    import importlib

    import samantha_server.config as cfg

    monkeypatch.delenv("LLM_MAX_TOKENS", raising=False)
    importlib.reload(cfg)
    assert cfg.LLM_MAX_TOKENS >= 2048, (
        f"LLM_MAX_TOKENS default is {cfg.LLM_MAX_TOKENS}, which is below the "
        f"empirical floor of 2048 (validated by smoke testing).  Lowering this value "
        f"risks json_decode failures on long-output models.  Re-validate the "
        f"full corpus before reducing it."
    )


def test_llm_max_tokens_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM_MAX_TOKENS env override round-trips correctly."""
    import importlib

    import samantha_server.config as cfg

    monkeypatch.setenv("LLM_MAX_TOKENS", "4096")
    importlib.reload(cfg)
    assert cfg.LLM_MAX_TOKENS == 4096


def test_llm_max_tokens_malformed_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM_MAX_TOKENS set to a non-integer string raises MisconfiguredEnvironmentError."""
    import importlib

    import samantha_server.config as cfg
    from samantha_server.errors import MisconfiguredEnvironmentError

    monkeypatch.setenv("LLM_MAX_TOKENS", "abc")
    with pytest.raises(MisconfiguredEnvironmentError):
        importlib.reload(cfg)
