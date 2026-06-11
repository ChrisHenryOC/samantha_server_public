"""Shared helper for caching computed values on frozen Pydantic models.

Frozen Pydantic models disallow normal attribute assignment after construction.
The model_validator (mode="after") pattern that caches a derived value must
use object.__setattr__ to bypass the frozen guard. This module centralises
that one-liner so each primitive does not repeat the anti-pattern inline.

Usage in a model_validator:
    from samantha_server.primitives._cache import set_cached

    @model_validator(mode="after")
    def _populate_canonical(self) -> "MyPrimitive":
        set_cached(self, "_canonical_value", some_computation())
        return self
"""

from __future__ import annotations

from typing import Any


def set_cached(model: object, name: str, value: Any) -> None:
    """Write *value* to *name* on a frozen Pydantic model via object.__setattr__.

    This is the only sanctioned bypass of the frozen-model guard for
    model_validator caches. Callers must only invoke this inside a
    model_validator (mode="after") — calling it elsewhere defeats the
    frozen-model contract.
    """
    object.__setattr__(model, name, value)


__all__ = ["set_cached"]
