"""Tests for the ``model_under_test`` fixture and ``--models`` CLI flag.

GH-141 sweep-harness behaviour: a single fixture parametrizes the
scenario corpus over a list of model names. Source order:

1. CLI flag (``--models=A,B,C``)
2. Env var (``MODELS_TO_TEST=A,B,C``)
3. Fallback ``[config.LLM_MODEL_NAME]``

Each parametrization gets its own ``RECEIPTS_DB_PATH`` temp file so
per-model receipts are persisted side-by-side for the comparison
reporter.

We exercise the fixture's resolution logic via the ``pytester``
plugin — it's the only way to assert that ``pytest_generate_tests``
actually parametrizes the way we expect, because parametrization
happens at collection time.
"""

from __future__ import annotations

import pytest

pytest_plugins = ["pytester"]


def test_model_under_test_falls_back_to_config_llm_model_name(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no --models flag and no MODELS_TO_TEST env, the fixture yields config.LLM_MODEL_NAME."""
    monkeypatch.delenv("MODELS_TO_TEST", raising=False)
    pytester.makeconftest(
        """
        from samantha_server.scenarios.sweep import (
            pytest_addoption,
            pytest_generate_tests,
            model_under_test,
        )
        """
    )
    pytester.makepyfile(
        """
        import samantha_server.config as cfg

        def test_one(model_under_test):
            assert model_under_test == cfg.LLM_MODEL_NAME
        """
    )
    result = pytester.runpytest("-v", "-p", "no:cacheprovider")
    result.assert_outcomes(passed=1)


def test_model_under_test_uses_env_var(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MODELS_TO_TEST env-var is parsed as a comma-separated list."""
    monkeypatch.setenv("MODELS_TO_TEST", "model-A,model-B,model-C")
    pytester.makeconftest(
        """
        from samantha_server.scenarios.sweep import (
            pytest_addoption,
            pytest_generate_tests,
            model_under_test,
        )
        """
    )
    pytester.makepyfile(
        """
        def test_each(model_under_test):
            assert model_under_test in ("model-A", "model-B", "model-C")
        """
    )
    result = pytester.runpytest("-v", "-p", "no:cacheprovider")
    # 3 parametrizations.
    result.assert_outcomes(passed=3)


def test_model_under_test_cli_flag_overrides_env(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--models=... wins over MODELS_TO_TEST env."""
    monkeypatch.setenv("MODELS_TO_TEST", "env-only-model")
    pytester.makeconftest(
        """
        from samantha_server.scenarios.sweep import (
            pytest_addoption,
            pytest_generate_tests,
            model_under_test,
        )
        """
    )
    pytester.makepyfile(
        """
        def test_each(model_under_test):
            assert model_under_test in ("cli-A", "cli-B")
            assert model_under_test != "env-only-model"
        """
    )
    result = pytester.runpytest("-v", "-p", "no:cacheprovider", "--models=cli-A,cli-B")
    result.assert_outcomes(passed=2)


def test_model_under_test_sets_per_parametrization_receipts_db_path(
    pytester: pytest.Pytester,
) -> None:
    """Each parametrization gets a unique RECEIPTS_DB_PATH (sweep persistence isolation)."""
    pytester.makeconftest(
        """
        from samantha_server.scenarios.sweep import (
            pytest_addoption,
            pytest_generate_tests,
            model_under_test,
        )
        """
    )
    pytester.makepyfile(
        """
        import os

        _seen_paths = set()

        def test_each(model_under_test):
            db_path = os.environ["RECEIPTS_DB_PATH"]
            # Each parametrization sees a different RECEIPTS_DB_PATH.
            _seen_paths.add(db_path)
            # Path is not the empty string.
            assert db_path

        def test_paths_were_distinct():
            # Two prior parametrizations populated _seen_paths;
            # both must have been distinct.
            assert len(_seen_paths) == 2, f"got {_seen_paths!r}"
        """
    )
    result = pytester.runpytest("-v", "-p", "no:cacheprovider", "--models=A,B")
    # 2 parametrizations + 1 trailing assertion.
    result.assert_outcomes(passed=3)


# L22: ``_split_csv`` is a pure function — direct unit tests cover the
# whitespace + empty handling without spawning a sub-pytest. The four
# remaining ``pytester`` tests above legitimately need the sub-process
# (they exercise pytest's collection-time hooks).
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  A  , , B ", ["A", "B"]),
        ("A,B,C", ["A", "B", "C"]),
        ("", []),
        (" , , , ", []),
        ("A,", ["A"]),
        (",A", ["A"]),
        ("A , , B,", ["A", "B"]),
    ],
)
def test_split_csv_strips_whitespace_and_drops_empty(raw: str, expected: list[str]) -> None:
    """L22: direct test of the comma-separated-list parser."""
    from samantha_server.scenarios.sweep import _split_csv

    assert _split_csv(raw) == expected
