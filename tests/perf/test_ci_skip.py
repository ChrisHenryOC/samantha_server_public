"""Perf marker registration smoke test.

CI-skip mechanism self-tests live in ``tests/perf_meta/test_ci_skip.py``
(outside ``tests/perf/``) so they are not subject to the ``autoskip_in_ci``
autouse fixture and can validate both directions of the CI-skip logic in any
environment, including CI itself.
"""

import pytest


# Drives marker registration in pyproject.toml.
# Applying the marker here confirms it can be used without an "unknown mark" warning.
@pytest.mark.perf
def test_perf_marker_present() -> None:
    """A perf-marked test exists — confirms the marker is registered."""
