"""Direct tests for primitives._cache.set_cached.

The helper was previously covered only transitively through the three
primitives that use it; these tests pin the module contract at its own
boundary: set_cached bypasses the frozen-model guard, while normal
assignment on the same model still raises.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError


class _FrozenModel(BaseModel, frozen=True):
    value: int


def test_set_cached_bypasses_frozen_guard() -> None:
    from samantha_server.primitives._cache import set_cached

    m = _FrozenModel(value=1)
    set_cached(m, "_derived", "cached")
    assert m._derived == "cached"  # type: ignore[attr-defined]


def test_normal_assignment_on_frozen_model_still_raises() -> None:
    """set_cached must be the only write path — direct assignment stays blocked."""
    m = _FrozenModel(value=1)
    with pytest.raises(ValidationError):
        m.value = 2  # type: ignore[misc]


def test_set_cached_overwrites_existing_cache() -> None:
    from samantha_server.primitives._cache import set_cached

    m = _FrozenModel(value=1)
    set_cached(m, "_derived", "first")
    set_cached(m, "_derived", "second")
    assert m._derived == "second"  # type: ignore[attr-defined]
