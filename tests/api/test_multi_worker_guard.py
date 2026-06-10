"""Tests for the WEB_CONCURRENCY > 1 hard-fail guard (G2)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _make_env_with_valid_secrets(**overrides: str | None) -> dict[str, str]:
    """Return an env dict suitable for importing samantha_server.config."""
    import os

    env = {k: v for k, v in os.environ.items()}
    env["RECEIPT_SIGNING_KEY"] = "a" * 64
    env["PHI_HASH_SALT"] = "a" * 64
    # Provide a non-sentinel RBAC key so the CAFEBABE guard
    # doesn't reject the conftest sentinel in subprocess environments.
    env["RBAC_HMAC_KEY"] = "b" * 64
    env.pop("PYTEST_CURRENT_TEST", None)
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return env


def test_build_app_state_fails_with_web_concurrency_2(
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[name-defined]  # noqa: F821
    tmp_path: Path,  # type: ignore[name-defined]  # noqa: F821
) -> None:
    """build_app_state raises MisconfiguredEnvironmentError when WEB_CONCURRENCY=2."""

    import pytest

    import samantha_server.config as cfg

    monkeypatch.setattr(cfg, "WEB_CONCURRENCY", 2)

    from samantha_server.api.lifespan import build_app_state
    from samantha_server.errors import MisconfiguredEnvironmentError

    with pytest.raises(MisconfiguredEnvironmentError, match="WEB_CONCURRENCY"):
        build_app_state()


def test_build_app_state_succeeds_with_web_concurrency_1(
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[name-defined]  # noqa: F821
    tmp_path: Path,  # type: ignore[name-defined]  # noqa: F821
    receipts_test_isolation: None,
) -> None:
    """build_app_state succeeds when WEB_CONCURRENCY=1 (default)."""
    import asyncio
    from unittest.mock import MagicMock

    import samantha_server.config as cfg

    monkeypatch.setattr(cfg, "WEB_CONCURRENCY", 1)
    monkeypatch.setattr(cfg, "RECEIPTS_DB_PATH", str(tmp_path / "receipts.db"))
    monkeypatch.setenv("RECEIPTS_DB_PATH", str(tmp_path / "receipts.db"))

    from samantha_server.api.lifespan import AppState, build_app_state

    mock_llm = MagicMock()
    mock_llm.model_id = "test-model"
    state = build_app_state(_llm_client_override=mock_llm)
    assert isinstance(state, AppState)
    asyncio.run(state.aclose())


def test_web_concurrency_gt_1_subprocess_fails() -> None:
    """Running with WEB_CONCURRENCY=2 exits non-zero at build_app_state()."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        env = _make_env_with_valid_secrets(
            WEB_CONCURRENCY="2",
            RECEIPTS_DB_PATH=f"{tmpdir}/receipts.db",
        )
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "from unittest.mock import MagicMock; "
                    "m = MagicMock(); m.model_id = 'test'; "
                    "from samantha_server.api.lifespan import build_app_state; "
                    "build_app_state(_llm_client_override=m)"
                ),
            ],
            env=env,
            capture_output=True,
        )
        assert result.returncode != 0
        assert b"WEB_CONCURRENCY" in result.stderr


def test_uvicorn_workers_cli_flag_is_not_caught_by_env_guard() -> None:
    """Document the known gap: ``uvicorn --workers N`` does NOT trip the guard.

     M8: the plan's Step 1 done-when said both the env-var path
    *and* the ``--workers N`` CLI form should hard-fail. In the pinned
    Uvicorn version, ``--workers N`` does not propagate
    ``WEB_CONCURRENCY`` to worker environments — the env-var-based guard
    is bypassed.

    Mitigation today (POC): the deployment doc names "set
    ``WEB_CONCURRENCY=1`` explicitly in production env" as a
    release-checklist item, and operators are told not to use
    ``--workers N`` on Uvicorn for this service. The structural gap is
    tracked under (multi-worker session-state swap), which will
    introduce a robust multiprocess detection mechanism (e.g.,
    ``multiprocessing.current_process().name``) when it lands.

    This test exists to prevent silently regressing past this
    documentation: if a future Uvicorn version *does* start propagating
    WEB_CONCURRENCY, the assertion below fails and we should revisit
    the guard contract.
    """
    import uvicorn.supervisors.multiprocess as mp_module

    src = mp_module.__file__ or ""
    text = Path(src).read_text(encoding="utf-8") if src else ""
    assert "WEB_CONCURRENCY" not in text, (
        "Uvicorn now references WEB_CONCURRENCY in its multiprocess "
        "supervisor — the env-var guard now does cover --workers N. "
        "Update the deployment doc and remove the gap notice in this "
        "test's docstring."
    )


def test_uvicorn_reload_does_not_trip_guard() -> None:
    """--reload uses a child-process model but does NOT set WEB_CONCURRENCY > 1.

     M8 + plan Step 1 done-when "negative case": Uvicorn's
    ``--reload`` flag spawns a watcher + a worker, but worker count is
    still 1 — the guard must not fire.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        env = _make_env_with_valid_secrets(
            RECEIPTS_DB_PATH=f"{tmpdir}/receipts.db",
        )
        env.pop("WEB_CONCURRENCY", None)
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "from unittest.mock import MagicMock; "
                    "m = MagicMock(); m.model_id = 'test'; "
                    "from samantha_server.api.lifespan import build_app_state; "
                    "state = build_app_state(_llm_client_override=m); "
                    "assert state is not None"
                ),
            ],
            env=env,
            capture_output=True,
        )
        assert result.returncode == 0, (
            f"Build failed when WEB_CONCURRENCY unset (--reload analog): "
            f"stderr={result.stderr.decode()}"
        )
