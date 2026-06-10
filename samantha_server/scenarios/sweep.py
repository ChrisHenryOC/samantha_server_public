"""Multi-model sweep harness for the replay corpus.

Exposes a ``model_under_test`` pytest fixture parametrized over a list
of model names. The same scenario corpus runs once per model so a
downstream comparison reporter (``samantha_server.scenarios.compare``)
can produce per-scenario agreement / disagreement matrices grouped by
``model_id``.

Source-of-truth order for the model list (highest precedence first):

1. ``--models=A,B,C`` CLI flag (set, non-empty).
2. ``MODELS_TO_TEST=A,B,C`` env var (set, non-empty).
3. Fallback ``[config.LLM_MODEL_NAME]``.

A *set-but-empty* CLI flag (``--models=`` or ``--models=' , '``) is an
explicit "no override; use default" signal — it does NOT fall through
to the env var. Anything else would silently violate the documented
"CLI > env" precedence whenever an operator typoed the value.

Each parametrization patches ``cfg.LLM_MODEL_NAME`` and
``cfg.RECEIPTS_DB_PATH`` (not just the env vars) because the LLM
clients and receipt store read from the cfg module attributes that
are frozen at import time. The receipt-store write-conn cache is
also reset so a per-parametrization DB switch lands cleanly.

Wiring: ``pytest_addoption`` is registered in the rootdir conftest
(``tests/conftest.py``); ``pytest_generate_tests`` and the fixture
itself are re-exported in ``tests/scenarios/conftest.py``.
"""

from __future__ import annotations

import contextlib
import os
import pathlib
import re
from collections.abc import Iterator

import pytest


def _split_csv(value: str) -> list[str]:
    """Split a comma-separated list, stripping whitespace and dropping empties."""
    return [item.strip() for item in value.split(",") if item.strip()]


# Mirrors the resolution logic in samantha_server.config — kept in sync
# manually so this module reads env directly without importing config
# at collection time (config's test-sentinel guard rejects import when
# PYTEST_CURRENT_TEST isn't set, which is the case during pytest's
# generate-tests hook).
#
# Keep in sync with config.LLM_MODEL_PATH's default — duplicated only
# because importing config at collection time fails; the architectural
# model-ID guard accepts it via the nosec marker.
_DEFAULT_LLM_MODEL_PATH: str = "mlx-community/Llama-3.2-3B-Instruct-4bit"  # nosec: model-id


# Allowlist for path components built from a model name. Anything outside
# the set is replaced with ``_`` so a malicious / accidental NUL byte or
# ``..`` traversal token cannot escape the temp dir. Stricter than the
# previous ``replace("/", "_").replace(":", "_")`` chain.
_PATH_SAFE_RE: re.Pattern[str] = re.compile(r"[^A-Za-z0-9._-]")


def _default_model_name() -> str:
    """Mirror config.LLM_MODEL_NAME's env resolution without importing config."""
    if (name := os.environ.get("LLM_MODEL_NAME")) is not None:
        return name
    return os.environ.get("LLM_MODEL_PATH", _DEFAULT_LLM_MODEL_PATH)


def _resolve_models(config: pytest.Config) -> list[str]:
    """Return the model list per the documented source-order precedence.

    H8: ``--models=`` (empty) means "use default" — explicit no-op,
    bypasses the env-var fallback. ``--models`` not set at all (None)
    means "consult env then default".
    """
    cli_value: str | None = config.getoption("--models", default=None)
    if cli_value is not None:
        models = _split_csv(cli_value)
        if models:
            return models
        # Explicit empty / all-whitespace CLI value → use the default,
        # don't silently fall through to env.
        return [_default_model_name()]

    env_value = os.environ.get("MODELS_TO_TEST")
    if env_value:
        models = _split_csv(env_value)
        if models:
            return models

    return [_default_model_name()]


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register the ``--models`` flag.

    Pytest invokes this hook automatically when the parent conftest
    imports it from this module.
    """
    parser.addoption(
        "--models",
        action="store",
        default=None,
        help=(
            "Comma-separated list of LLM model names to sweep "
            ". Overrides MODELS_TO_TEST env var; falls back "
            "to config.LLM_MODEL_NAME. An explicit empty value "
            "(--models=) means 'use default' and bypasses env."
        ),
    )


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Parametrize tests requesting ``model_under_test`` with the resolved list."""
    if "model_under_test" not in metafunc.fixturenames:
        return
    models = _resolve_models(metafunc.config)
    metafunc.parametrize("model_under_test", models, indirect=True)


@pytest.fixture
def model_under_test(
    request: pytest.FixtureRequest,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[str]:
    """Yield the model name for this parametrization; configure cfg for the run.

    C1 + C2 fix: callers (LLM clients, receipt store) read
    ``cfg.LLM_MODEL_NAME`` / ``cfg.RECEIPTS_DB_PATH`` (frozen at import
    time), not the env vars. ``setattr`` on the cfg module is what
    actually parametrizes the run; the env-var setters are kept for
    subprocess-spawning paths and for parity with operator-set values.

    Ordering vs. ``receipts_test_isolation``: the autouse isolation
    fixture in ``tests/conftest.py`` sets ``cfg.RECEIPTS_DB_PATH`` to
    its own ``tmp_path``. We *consume* that fixture via
    ``request.getfixturevalue`` inside the fixture body so pytest
    evaluates it first; our patches then take precedence. The
    ``getfixturevalue`` call is wrapped in try/except so the
    pytester-driven unit tests of this module (which don't have the
    rootdir conftest's fixtures available) still work.
    """
    model = str(request.param)
    safe_name = _PATH_SAFE_RE.sub("_", model)
    db_path = tmp_path / f"receipts_{safe_name}.db"

    # Force receipts_test_isolation to evaluate first when available.
    # Inside production tests this is autouse anyway, but listing it
    # explicitly here pins the order so our cfg patches override its.
    # Wrapped in suppress because the pytester-driven unit tests of
    # this module don't have the rootdir conftest's fixtures.
    with contextlib.suppress(pytest.FixtureLookupError):
        request.getfixturevalue("receipts_test_isolation")

    # Lazy import: cfg's import-time guards run on first import. By the
    # time a fixture body runs, PYTEST_CURRENT_TEST is set, so the
    # test-sentinel rejection is suppressed.
    import samantha_server.config as cfg
    from samantha_server.receipts import store as _store

    monkeypatch.setenv("LLM_MODEL_NAME", model)
    monkeypatch.setenv("RECEIPTS_DB_PATH", str(db_path))
    monkeypatch.setattr(cfg, "LLM_MODEL_NAME", model)
    monkeypatch.setattr(cfg, "RECEIPTS_DB_PATH", str(db_path))

    # Reset the receipt-store write-conn cache so the per-parametrization
    # path actually opens a new connection on next write. ``receipts_test_isolation``
    # already cleared this once before; we clear it again because we just
    # changed the path under it.
    monkeypatch.setattr(_store, "_write_conn", None)
    monkeypatch.setattr(_store, "_write_conn_path", None)

    yield model


__all__ = [
    "model_under_test",
    "pytest_addoption",
    "pytest_generate_tests",
]
