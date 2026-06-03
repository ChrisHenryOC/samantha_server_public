"""Self-test for the ceiling assertion helper.

Verifies that ``_assert_under_ceiling`` fires with the expected message when
the measured latency exceeds the ceiling.  Without this test the bench would be
a recorder, not a gate — a regression that pushed median above the ceiling
might pass if the assertion was accidentally removed or weakened.

No ``@pytest.mark.perf`` decorator: this is an infrastructure self-test that
must run on every default ``pytest`` invocation.
"""

import pytest

from tests.perf.test_primitive_latency import ATOMIC_CEILING_S, _assert_under_ceiling


def test_assert_under_ceiling_raises_on_excess() -> None:
    """_assert_under_ceiling raises AssertionError with name and µs when over ceiling."""
    with pytest.raises(AssertionError, match=r"X\.evaluate\(\)"):
        _assert_under_ceiling("X", ATOMIC_CEILING_S * 2, ATOMIC_CEILING_S)


def test_assert_under_ceiling_message_contains_us_values() -> None:
    """AssertionError message includes both measured and ceiling values in µs."""
    with pytest.raises(AssertionError, match=r"µs"):
        _assert_under_ceiling("Y", ATOMIC_CEILING_S * 2, ATOMIC_CEILING_S)


def test_assert_under_ceiling_passes_when_below() -> None:
    """_assert_under_ceiling is a no-op when median is below the ceiling."""
    _assert_under_ceiling("Z", ATOMIC_CEILING_S * 0.5, ATOMIC_CEILING_S)
