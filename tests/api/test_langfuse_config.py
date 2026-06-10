"""Tests for the LANGFUSE_* config variables."""

from __future__ import annotations

import importlib

import pytest


def _reload_config(monkeypatch: pytest.MonkeyPatch, **env: str) -> object:
    """Reload samantha_server.config with the given env overrides applied.

    The config module evaluates env at import time; reloading is the
    only way to exercise different LANGFUSE_* values from a test.
    """
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import samantha_server.config as cfg

    return importlib.reload(cfg)


def test_langfuse_enabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """LANGFUSE_ENABLED defaults to true (reverses G9: on by default for human runs)."""
    monkeypatch.delenv("LANGFUSE_ENABLED", raising=False)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    cfg = _reload_config(monkeypatch)
    assert cfg.LANGFUSE_ENABLED is True  # type: ignore[attr-defined]


def test_langfuse_default_true_without_keys_is_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the default flipped to true, an unset flag + missing keys stays fail-loud."""
    from samantha_server.errors import MisconfiguredEnvironmentError

    monkeypatch.delenv("LANGFUSE_ENABLED", raising=False)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "")
    # Delete the OTLP endpoint too so the public-key guard is asserted
    # order-independently: pytest's match= matches anywhere in the message,
    # so without this a validation-order change could pass against the
    # wrong guard while still matching "LANGFUSE_PUBLIC_KEY".
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)

    with pytest.raises(MisconfiguredEnvironmentError, match="LANGFUSE_PUBLIC_KEY"):
        _reload_config(monkeypatch)


def test_langfuse_base_url_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """LANGFUSE_BASE_URL defaults to http://localhost:3000."""
    monkeypatch.delenv("LANGFUSE_BASE_URL", raising=False)
    # Pin the flag false explicitly: the default is now true, so without this
    # the reload would hit the fail-loud key validation if this test were run
    # in isolation (outside the conftest session pin).
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    cfg = _reload_config(monkeypatch)
    assert cfg.LANGFUSE_BASE_URL == "http://localhost:3000"  # type: ignore[attr-defined]


def test_langfuse_enabled_requires_public_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """LANGFUSE_ENABLED=true with empty LANGFUSE_PUBLIC_KEY is startup-fatal."""
    from samantha_server.errors import MisconfiguredEnvironmentError

    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "secret")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")

    with pytest.raises(MisconfiguredEnvironmentError, match="LANGFUSE_PUBLIC_KEY"):
        _reload_config(monkeypatch)


def test_langfuse_enabled_requires_secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """LANGFUSE_ENABLED=true with empty LANGFUSE_SECRET_KEY is startup-fatal."""
    from samantha_server.errors import MisconfiguredEnvironmentError

    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")

    with pytest.raises(MisconfiguredEnvironmentError, match="LANGFUSE_SECRET_KEY"):
        _reload_config(monkeypatch)


def test_langfuse_enabled_with_both_keys_loads_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both keys present + ENABLED=true + OTLP endpoint set is a valid configuration."""
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")

    cfg = _reload_config(monkeypatch)
    assert cfg.LANGFUSE_ENABLED is True  # type: ignore[attr-defined]
    assert cfg.LANGFUSE_PUBLIC_KEY == "pk-test"  # type: ignore[attr-defined]
    assert cfg.LANGFUSE_SECRET_KEY == "sk-test"  # type: ignore[attr-defined]


def test_langfuse_disabled_skips_key_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    """LANGFUSE_ENABLED=false → empty keys are fine (test/CI path)."""
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "")

    cfg = _reload_config(monkeypatch)
    assert cfg.LANGFUSE_ENABLED is False  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# H1: non-canonical LANGFUSE_ENABLED values
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["1", "yes", "on", "TRUE", "True", "  true  ", "YES"])
def test_langfuse_enabled_accepts_truthy_values(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    """H1: docker-compose conventions like ``"1"`` / ``"yes"`` / ``"on"`` enable Langfuse."""
    monkeypatch.setenv("LANGFUSE_ENABLED", raw)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")

    cfg = _reload_config(monkeypatch)
    assert cfg.LANGFUSE_ENABLED is True  # type: ignore[attr-defined]


@pytest.mark.parametrize("raw", ["0", "no", "off", "FALSE", "  false  ", ""])
def test_langfuse_enabled_accepts_falsy_values(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    """H1: ``"0"`` / ``"no"`` / ``"off"`` / empty all resolve to False."""
    monkeypatch.setenv("LANGFUSE_ENABLED", raw)
    cfg = _reload_config(monkeypatch)
    assert cfg.LANGFUSE_ENABLED is False  # type: ignore[attr-defined]


def test_langfuse_enabled_rejects_unrecognized_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H1: garbage values are startup-fatal — silent fall-through is the bug being fixed."""
    from samantha_server.errors import MisconfiguredEnvironmentError

    monkeypatch.setenv("LANGFUSE_ENABLED", "maybe")
    with pytest.raises(MisconfiguredEnvironmentError, match="LANGFUSE_ENABLED"):
        _reload_config(monkeypatch)


# ---------------------------------------------------------------------------
# M4: loopback validation for LANGFUSE_BASE_URL
# ---------------------------------------------------------------------------


def test_langfuse_base_url_rejects_non_loopback_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M4: a remote host is rejected at import (CWE-918)."""
    from samantha_server.errors import MisconfiguredEnvironmentError

    monkeypatch.setenv("LANGFUSE_BASE_URL", "http://attacker.example/")
    with pytest.raises(MisconfiguredEnvironmentError, match="LANGFUSE_BASE_URL"):
        _reload_config(monkeypatch)


def test_langfuse_base_url_rejects_https_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    """M4: only http:// is accepted, mirroring LLM_OMLX_BASE_URL."""
    from samantha_server.errors import MisconfiguredEnvironmentError

    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://localhost:3000")
    with pytest.raises(MisconfiguredEnvironmentError, match="LANGFUSE_BASE_URL"):
        _reload_config(monkeypatch)


def test_langfuse_base_url_accepts_127_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    """M4: explicit 127.0.0.1 is accepted (matches G10 docker bind interface)."""
    monkeypatch.setenv("LANGFUSE_BASE_URL", "http://127.0.0.1:3000")
    cfg = _reload_config(monkeypatch)
    assert cfg.LANGFUSE_BASE_URL == "http://127.0.0.1:3000"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# M6: OTEL_EXPORTER_OTLP_ENDPOINT requirement
# ---------------------------------------------------------------------------


def test_langfuse_enabled_requires_otlp_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """M6: ENABLED=true without OTEL_EXPORTER_OTLP_ENDPOINT is startup-fatal."""
    from samantha_server.errors import MisconfiguredEnvironmentError

    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)

    with pytest.raises(MisconfiguredEnvironmentError, match="OTEL_EXPORTER_OTLP_ENDPOINT"):
        _reload_config(monkeypatch)


# ---------------------------------------------------------------------------
# M5: probe-timeout config knob
# ---------------------------------------------------------------------------


def test_langfuse_probe_timeout_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """M5: the probe-timeout default is 0.5 s."""
    monkeypatch.delenv("READYZ_LANGFUSE_PROBE_TIMEOUT_SEC", raising=False)
    cfg = _reload_config(monkeypatch)
    assert cfg.READYZ_LANGFUSE_PROBE_TIMEOUT_SEC == 0.5  # type: ignore[attr-defined]


def test_langfuse_probe_timeout_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """M5: operator override is honored."""
    monkeypatch.setenv("READYZ_LANGFUSE_PROBE_TIMEOUT_SEC", "2.0")
    cfg = _reload_config(monkeypatch)
    assert cfg.READYZ_LANGFUSE_PROBE_TIMEOUT_SEC == 2.0  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def _restore_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reload config back to module-load state at end of test.

    Without this, a test that mutates LANGFUSE_* and reloads would
    leak state into subsequent tests in this module. Strip every
    LANGFUSE_* var before reloading, and force LANGFUSE_ENABLED=false
    so the teardown reload is clean: since the config default is now
    true, an unset flag + empty keys would itself raise during teardown.
    """
    yield
    import samantha_server.config as cfg

    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_BASE_URL", raising=False)
    monkeypatch.delenv("READYZ_LANGFUSE_PROBE_TIMEOUT_SEC", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    importlib.reload(cfg)
