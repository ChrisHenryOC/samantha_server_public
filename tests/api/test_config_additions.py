"""Tests for Phase 3 config additions to samantha_server.config."""

from __future__ import annotations

import subprocess
import sys


def _make_env_with_valid_secrets(**overrides: str | None) -> dict[str, str]:
    """Return an env dict suitable for importing samantha_server.config."""
    import os

    env = {k: v for k, v in os.environ.items()}
    env["RECEIPT_SIGNING_KEY"] = "a" * 64
    env["PHI_HASH_SALT"] = "a" * 64
    env.pop("PYTEST_CURRENT_TEST", None)
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return env


def test_queue_bound_defaults_to_256(monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[name-defined]  # noqa: F821
    """QUEUE_BOUND defaults to 256 when unset."""
    import importlib

    monkeypatch.delenv("QUEUE_BOUND", raising=False)
    import samantha_server.config as cfg

    importlib.reload(cfg)
    try:
        assert cfg.QUEUE_BOUND == 256
    finally:
        importlib.reload(cfg)


def test_queue_bound_reads_from_env(monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[name-defined]  # noqa: F821
    """QUEUE_BOUND reads its value from the env var."""
    import importlib

    monkeypatch.setenv("QUEUE_BOUND", "512")
    import samantha_server.config as cfg

    importlib.reload(cfg)
    try:
        assert cfg.QUEUE_BOUND == 512
    finally:
        importlib.reload(cfg)


def test_queue_bound_zero_raises_at_startup() -> None:
    """QUEUE_BOUND=0 raises MisconfiguredEnvironmentError at config import."""
    env = _make_env_with_valid_secrets(QUEUE_BOUND="0")
    result = subprocess.run(
        [sys.executable, "-c", "import samantha_server.config"],
        env=env,
        capture_output=True,
    )
    assert result.returncode != 0, "Expected non-zero exit when QUEUE_BOUND=0"
    assert b"QUEUE_BOUND" in result.stderr


def test_queue_bound_non_integer_raises() -> None:
    """Non-integer QUEUE_BOUND raises MisconfiguredEnvironmentError."""
    env = _make_env_with_valid_secrets(QUEUE_BOUND="notanint")
    result = subprocess.run(
        [sys.executable, "-c", "import samantha_server.config"],
        env=env,
        capture_output=True,
    )
    assert result.returncode != 0


def test_queue_backpressure_retry_after_sec_defaults_to_5(
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[name-defined]  # noqa: F821
) -> None:
    """QUEUE_BACKPRESSURE_RETRY_AFTER_SEC defaults to 5 when unset."""
    import importlib

    monkeypatch.delenv("QUEUE_BACKPRESSURE_RETRY_AFTER_SEC", raising=False)
    import samantha_server.config as cfg

    importlib.reload(cfg)
    try:
        assert cfg.QUEUE_BACKPRESSURE_RETRY_AFTER_SEC == 5
    finally:
        importlib.reload(cfg)


def test_max_request_body_bytes_defaults_to_1mb(
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[name-defined]  # noqa: F821
) -> None:
    """MAX_REQUEST_BODY_BYTES defaults to 1048576 (1 MB) when unset."""
    import importlib

    monkeypatch.delenv("MAX_REQUEST_BODY_BYTES", raising=False)
    import samantha_server.config as cfg

    importlib.reload(cfg)
    try:
        assert cfg.MAX_REQUEST_BODY_BYTES == 1048576
    finally:
        importlib.reload(cfg)


def test_readyz_langfuse_probe_ttl_sec_defaults_to_5(
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[name-defined]  # noqa: F821
) -> None:
    """READYZ_LANGFUSE_PROBE_TTL_SEC defaults to 5 when unset."""
    import importlib

    monkeypatch.delenv("READYZ_LANGFUSE_PROBE_TTL_SEC", raising=False)
    import samantha_server.config as cfg

    importlib.reload(cfg)
    try:
        assert cfg.READYZ_LANGFUSE_PROBE_TTL_SEC == 5
    finally:
        importlib.reload(cfg)


def test_shutdown_drain_timeout_sec_defaults_to_30(
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[name-defined]  # noqa: F821
) -> None:
    """SHUTDOWN_DRAIN_TIMEOUT_SEC defaults to 30 when unset."""
    import importlib

    monkeypatch.delenv("SHUTDOWN_DRAIN_TIMEOUT_SEC", raising=False)
    import samantha_server.config as cfg

    importlib.reload(cfg)
    try:
        assert cfg.SHUTDOWN_DRAIN_TIMEOUT_SEC == 30
    finally:
        importlib.reload(cfg)


def test_web_concurrency_defaults_to_1(
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[name-defined]  # noqa: F821
) -> None:
    """WEB_CONCURRENCY defaults to 1 when unset."""
    import importlib

    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    import samantha_server.config as cfg

    importlib.reload(cfg)
    try:
        assert cfg.WEB_CONCURRENCY == 1
    finally:
        importlib.reload(cfg)


# ---------------------------------------------------------------------------
# Env-override coverage for the 5 Phase 3 vars.
# Defaults are tested above; these assert the env var actually drives the
# config value (regression guard if a future edit breaks the read).
# ---------------------------------------------------------------------------


def _reload_with_env(monkeypatch, var: str, value: str):  # type: ignore[no-untyped-def]
    import importlib

    monkeypatch.setenv(var, value)
    import samantha_server.config as cfg

    importlib.reload(cfg)
    return cfg


def test_queue_backpressure_retry_after_sec_reads_from_env(
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[name-defined]  # noqa: F821
) -> None:
    import importlib

    cfg = _reload_with_env(monkeypatch, "QUEUE_BACKPRESSURE_RETRY_AFTER_SEC", "17")
    try:
        assert cfg.QUEUE_BACKPRESSURE_RETRY_AFTER_SEC == 17
    finally:
        importlib.reload(cfg)


def test_max_request_body_bytes_reads_from_env(
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[name-defined]  # noqa: F821
) -> None:
    import importlib

    cfg = _reload_with_env(monkeypatch, "MAX_REQUEST_BODY_BYTES", "65536")
    try:
        assert cfg.MAX_REQUEST_BODY_BYTES == 65536
    finally:
        importlib.reload(cfg)


def test_readyz_langfuse_probe_ttl_sec_reads_from_env(
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[name-defined]  # noqa: F821
) -> None:
    import importlib

    cfg = _reload_with_env(monkeypatch, "READYZ_LANGFUSE_PROBE_TTL_SEC", "12")
    try:
        assert cfg.READYZ_LANGFUSE_PROBE_TTL_SEC == 12
    finally:
        importlib.reload(cfg)


def test_shutdown_drain_timeout_sec_reads_from_env(
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[name-defined]  # noqa: F821
) -> None:
    import importlib

    cfg = _reload_with_env(monkeypatch, "SHUTDOWN_DRAIN_TIMEOUT_SEC", "45")
    try:
        assert cfg.SHUTDOWN_DRAIN_TIMEOUT_SEC == 45
    finally:
        importlib.reload(cfg)


def test_web_concurrency_reads_from_env(
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[name-defined]  # noqa: F821
) -> None:
    import importlib

    cfg = _reload_with_env(monkeypatch, "WEB_CONCURRENCY", "3")
    try:
        assert cfg.WEB_CONCURRENCY == 3
    finally:
        importlib.reload(cfg)
