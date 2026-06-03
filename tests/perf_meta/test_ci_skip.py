"""Infrastructure self-tests for the CI-skip mechanism.

These tests live outside ``tests/perf/`` so they are NOT subject to the
``autoskip_in_ci`` autouse fixture from ``tests/perf/conftest.py``.  That
fixture runs *before* any test body inside ``tests/perf/``, which means a
test there that intends to verify the skip mechanism is itself skipped in CI —
exactly the case it was meant to validate.

By relocating here, both directions (CI set → skip; CI unset → no skip) can
be exercised in any environment, including CI.

No ``@pytest.mark.perf`` decorator: these are infrastructure tests, not
latency benchmarks, so they run on every default ``pytest`` invocation.
"""

import pytest

from tests.perf.conftest import skip_if_ci


def test_skip_if_ci_raises_when_ci_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """skip_if_ci() raises pytest.skip.Exception when the CI env var is set."""
    monkeypatch.setenv("CI", "1")
    with pytest.raises(pytest.skip.Exception):
        skip_if_ci()


def test_skip_if_ci_noop_when_ci_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """skip_if_ci() is a no-op when CI is not set."""
    monkeypatch.delenv("CI", raising=False)
    skip_if_ci()  # must not raise
