"""Tests for the Langfuse OTLP-auth-header injection in build_app_state.

Per § "Exporter authentication wiring": when LANGFUSE_ENABLED
is true, the lifespan computes a Basic-auth header from
LANGFUSE_PUBLIC_KEY + LANGFUSE_SECRET_KEY and writes it to the
``OTEL_EXPORTER_OTLP_HEADERS`` env var **before** ``configure_otel``
constructs the OTLP exporter. The exporter reads that env var at
construction time, so the ordering is load-bearing.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

import pytest


def test_langfuse_disabled_does_not_set_otlp_headers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """LANGFUSE_ENABLED=false → OTEL_EXPORTER_OTLP_HEADERS is not touched."""
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_HEADERS", raising=False)
    monkeypatch.setenv("RECEIPTS_DB_PATH", str(tmp_path / "r.db"))

    import samantha_server.config as cfg

    monkeypatch.setattr(cfg, "LANGFUSE_ENABLED", False)

    from samantha_server.api.lifespan import _seed_langfuse_otlp_headers

    _seed_langfuse_otlp_headers()
    assert "OTEL_EXPORTER_OTLP_HEADERS" not in os.environ


def test_langfuse_enabled_seeds_basic_auth_header(monkeypatch: pytest.MonkeyPatch) -> None:
    """LANGFUSE_ENABLED=true → OTEL_EXPORTER_OTLP_HEADERS carries Basic auth."""
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_HEADERS", raising=False)

    import samantha_server.config as cfg

    monkeypatch.setattr(cfg, "LANGFUSE_ENABLED", True)
    monkeypatch.setattr(cfg, "LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setattr(cfg, "LANGFUSE_SECRET_KEY", "sk-test")

    from samantha_server.api.lifespan import _seed_langfuse_otlp_headers

    _seed_langfuse_otlp_headers()

    encoded = base64.b64encode(b"pk-test:sk-test").decode("ascii")
    expected = f"Authorization=Basic {encoded}"
    assert os.environ["OTEL_EXPORTER_OTLP_HEADERS"] == expected


def test_langfuse_enabled_with_empty_public_key_raises_misconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ENABLED=true + empty PUBLIC_KEY at lifespan time → MisconfiguredEnvironmentError.

    Defence in depth: config-load already rejects this combination, but the
    lifespan helper double-checks because ``cfg.LANGFUSE_*`` can be
    monkeypatched in tests. A future caller that bypasses config validation
    must still get a hard error here.
    """
    import samantha_server.config as cfg
    from samantha_server.errors import MisconfiguredEnvironmentError

    monkeypatch.setattr(cfg, "LANGFUSE_ENABLED", True)
    monkeypatch.setattr(cfg, "LANGFUSE_PUBLIC_KEY", "")
    monkeypatch.setattr(cfg, "LANGFUSE_SECRET_KEY", "sk-test")

    from samantha_server.api.lifespan import _seed_langfuse_otlp_headers

    with pytest.raises(MisconfiguredEnvironmentError, match="LANGFUSE_PUBLIC_KEY"):
        _seed_langfuse_otlp_headers()


def test_langfuse_enabled_with_empty_secret_key_raises_misconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H2: symmetric to the public-key check — ENABLED=true + empty SECRET_KEY raises."""
    import samantha_server.config as cfg
    from samantha_server.errors import MisconfiguredEnvironmentError

    monkeypatch.setattr(cfg, "LANGFUSE_ENABLED", True)
    monkeypatch.setattr(cfg, "LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setattr(cfg, "LANGFUSE_SECRET_KEY", "")

    from samantha_server.api.lifespan import _seed_langfuse_otlp_headers

    with pytest.raises(MisconfiguredEnvironmentError, match="LANGFUSE_SECRET_KEY"):
        _seed_langfuse_otlp_headers()


def test_langfuse_seed_preserves_existing_non_authorization_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L14: operator-set non-Authorization headers are preserved on merge."""
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "X-Tenant=lab,X-Trace-Tag=alpha")

    import samantha_server.config as cfg

    monkeypatch.setattr(cfg, "LANGFUSE_ENABLED", True)
    monkeypatch.setattr(cfg, "LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setattr(cfg, "LANGFUSE_SECRET_KEY", "sk")

    from samantha_server.api.lifespan import _seed_langfuse_otlp_headers

    _seed_langfuse_otlp_headers()

    headers_str = os.environ["OTEL_EXPORTER_OTLP_HEADERS"]
    pairs = [p.strip() for p in headers_str.split(",")]
    assert "X-Tenant=lab" in pairs
    assert "X-Trace-Tag=alpha" in pairs
    auth_pair = next(p for p in pairs if p.startswith("Authorization="))
    encoded = base64.b64encode(b"pk:sk").decode("ascii")
    assert auth_pair == f"Authorization=Basic {encoded}"


def test_langfuse_seed_replaces_existing_authorization_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L14: an operator-set Authorization header is replaced with the Langfuse one."""
    encoded_old = base64.b64encode(b"old-pk:old-sk").decode("ascii")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", f"Authorization=Basic {encoded_old}")

    import samantha_server.config as cfg

    monkeypatch.setattr(cfg, "LANGFUSE_ENABLED", True)
    monkeypatch.setattr(cfg, "LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setattr(cfg, "LANGFUSE_SECRET_KEY", "sk")

    from samantha_server.api.lifespan import _seed_langfuse_otlp_headers

    _seed_langfuse_otlp_headers()

    headers_str = os.environ["OTEL_EXPORTER_OTLP_HEADERS"]
    encoded_new = base64.b64encode(b"pk:sk").decode("ascii")
    assert headers_str == f"Authorization=Basic {encoded_new}"
    assert encoded_old not in headers_str
