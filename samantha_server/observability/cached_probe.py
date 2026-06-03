"""Async cached health probe with TTL and single-flight refresh.

CachedProbe wraps an async probe_fn with a TTL-based cache. Concurrent
callers during a refresh race observe the same in-flight result via an
asyncio.Lock (single-flight pattern). No cross-coroutine thundering herd.

In Step 1 the only caller is /readyz's Langfuse-probe field; the probe_fn
for Langfuse is a no-op stub (make_langfuse_stub_probe) returning
{"reachable": False, "reason": "not_configured"}. Step 9 wires the real
Langfuse health-endpoint probe.
"""

from __future__ import annotations

import asyncio
import functools
import time
from collections.abc import Awaitable, Callable
from typing import Any


class CachedProbe:
    """Async probe whose result is cached for ttl_sec seconds.

    probe_fn is called at most once per TTL window. Concurrent calls
    during an in-flight refresh all wait for the same result (single-flight
    via asyncio.Lock).

    last_probe_age_sec returns the age of the cached result in seconds, or
    None if the probe has not yet been executed.
    """

    def __init__(
        self,
        probe_fn: Callable[[], Awaitable[dict[str, Any]]],
        *,
        ttl_sec: float,
    ) -> None:
        self._probe_fn = probe_fn
        self._ttl_sec = ttl_sec
        self._cached: dict[str, Any] | None = None
        self._last_probe_at: float | None = None
        self._lock: asyncio.Lock = asyncio.Lock()

    @property
    def last_probe_age_sec(self) -> float | None:
        """Age of the cached result in seconds, or None before first probe."""
        if self._last_probe_at is None:
            return None
        return time.monotonic() - self._last_probe_at

    @property
    def cached(self) -> dict[str, Any] | None:
        """Most recent cached probe value, or None before first probe.

        Public read accessor for sync callers (e.g. /readyz, which is a
        sync FastAPI handler and cannot await). Sync callers should treat
        a None return as "no probe yet"; async callers should use
        ``current()`` to refresh on staleness.
        """
        return self._cached

    async def current(self) -> dict[str, Any]:
        """Return the cached probe result, refreshing if older than ttl_sec.

        Single-flight: if a refresh is already in progress (lock held), this
        call waits for it to complete and returns the updated cached value.
        """
        # Fast path: cache is fresh, no lock needed.
        if self._cached is not None and self._last_probe_at is not None:
            age = time.monotonic() - self._last_probe_at
            if age < self._ttl_sec:
                return self._cached

        # Slow path: acquire lock, re-check (another waiter may have refreshed).
        async with self._lock:
            if self._cached is not None and self._last_probe_at is not None:
                age = time.monotonic() - self._last_probe_at
                if age < self._ttl_sec:
                    return self._cached

            self._cached = await self._probe_fn()
            self._last_probe_at = time.monotonic()
            return self._cached


def make_langfuse_stub_probe(*, ttl_sec: float = 5.0) -> CachedProbe:
    """Return a CachedProbe whose probe_fn always reports Langfuse as not configured.

    Used as the placeholder when ``LANGFUSE_ENABLED=false`` (test / CI
    default). When the operator enables Langfuse, the lifespan swaps
    in :func:`make_langfuse_probe` for the real health-endpoint probe.
    """

    async def _stub() -> dict[str, Any]:
        return {"reachable": False, "reason": "not_configured"}

    return CachedProbe(_stub, ttl_sec=ttl_sec)


async def _langfuse_probe(base_url: str, *, timeout_sec: float = 0.5) -> dict[str, Any]:
    """One-shot Langfuse health probe — hits ``{base_url}/api/public/health``.

    Returns a dict with ``reachable: bool`` and (when not reachable) a
    ``reason`` string. The probe NEVER raises; an unreachable Langfuse
    must surface as degraded readiness JSON, not as a /readyz 5xx.

    Catches both ``httpx.RequestError`` (transport-level failures —
    ConnectError, TimeoutException, etc.) AND ``httpx.InvalidURL`` /
    ``OSError``. ``httpx.InvalidURL`` extends ``HTTPError``, not
    ``RequestError``, so a malformed ``LANGFUSE_BASE_URL`` would
    otherwise propagate up to /readyz as a 500. ``OSError`` covers
    any low-level transport-init failure (TLS / socket-layer issues
    that could escape the ``async with`` enter).

    Lazy import of ``httpx`` keeps the cost off the import path of every
    test that touches observability.
    """
    import httpx

    url = f"{base_url.rstrip('/')}/api/public/health"
    try:
        async with httpx.AsyncClient(timeout=timeout_sec) as client:
            response = await client.get(url)
    except (httpx.RequestError, httpx.InvalidURL, OSError) as exc:
        return {"reachable": False, "reason": f"connect_error: {type(exc).__name__}"}

    if response.status_code == 200:
        return {"reachable": True}
    return {"reachable": False, "reason": f"http_{response.status_code}"}


def make_langfuse_probe(
    *, base_url: str, ttl_sec: float = 5.0, timeout_sec: float = 0.5
) -> CachedProbe:
    """Return a CachedProbe that checks ``{base_url}/api/public/health``.

    The probe is wrapped in a ``CachedProbe`` so a flood of unauthenticated
    /readyz calls does not amplify into outbound traffic. Single-flight +
    TTL semantics are inherited from the wrapper.

    ``timeout_sec`` is forwarded to ``_langfuse_probe`` and ultimately
    to ``httpx.AsyncClient(timeout=...)``. The default mirrors
    ``cfg.READYZ_LANGFUSE_PROBE_TIMEOUT_SEC``.
    """
    return CachedProbe(
        functools.partial(_langfuse_probe, base_url, timeout_sec=timeout_sec),
        ttl_sec=ttl_sec,
    )


__all__ = [
    "CachedProbe",
    "make_langfuse_probe",
    "make_langfuse_stub_probe",
]
