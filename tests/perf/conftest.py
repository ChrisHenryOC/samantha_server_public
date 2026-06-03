"""Shared fixtures and helpers for perf benchmarks.

CI-skip strategy
----------------
Two layers of protection keep noisy latency tests off shared CI runners:

1. Marker filter: ``addopts = "-m 'not perf'"`` in pyproject.toml means the
   default ``pytest`` invocation never collects perf tests.  Users opt in with
   ``pytest -m perf``.

2. Env-var guard: every perf test calls ``skip_if_ci()`` (or the
   ``autoskip_in_ci`` autouse fixture) so that even if someone runs
   ``pytest tests/perf/`` directly in a CI environment the bench is skipped
   with a clear message rather than producing invalid latency numbers.
"""

import os

import pytest


def skip_if_ci() -> None:
    """Skip the calling test when running inside a CI environment.

    GitHub Actions (and most hosted CI systems) set the ``CI`` env var to
    a truthy string.  Calling this function at the top of a perf test is
    belt-and-suspenders insurance on top of the ``-m 'not perf'`` addopts
    filter.
    """
    if os.environ.get("CI"):
        pytest.skip("CI environment — perf benches are noisy on shared runners")


@pytest.fixture(autouse=True)
def autoskip_in_ci() -> None:
    """Autouse fixture: skip every test in tests/perf/ when CI is set."""
    skip_if_ci()
