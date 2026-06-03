"""Tests for samantha_server.observability.cached_probe — async CachedProbe."""

from __future__ import annotations

import asyncio


async def _make_probe(
    return_value: dict,  # type: ignore[type-arg]
    *,
    ttl_sec: float = 5.0,
    call_count_box: list[int] | None = None,
) -> CachedProbe:  # type: ignore[name-defined]  # noqa: F821
    from samantha_server.observability.cached_probe import CachedProbe

    box = call_count_box if call_count_box is not None else []

    async def probe_fn() -> dict:  # type: ignore[type-arg]
        box.append(1)
        return return_value

    return CachedProbe(probe_fn, ttl_sec=ttl_sec)


def test_cached_probe_current_returns_probe_value() -> None:
    """current() returns the value produced by probe_fn."""
    from samantha_server.observability.cached_probe import CachedProbe

    async def run() -> None:
        async def probe() -> dict:  # type: ignore[type-arg]
            return {"reachable": True}

        cp = CachedProbe(probe, ttl_sec=5.0)
        result = await cp.current()
        assert result == {"reachable": True}

    asyncio.run(run())


def test_cached_probe_caches_result_within_ttl() -> None:
    """probe_fn is called only once within the TTL window."""
    from samantha_server.observability.cached_probe import CachedProbe

    async def run() -> None:
        call_count = [0]

        async def probe() -> dict:  # type: ignore[type-arg]
            call_count[0] += 1
            return {"reachable": True}

        cp = CachedProbe(probe, ttl_sec=60.0)
        await cp.current()
        await cp.current()
        assert call_count[0] == 1

    asyncio.run(run())


def test_cached_probe_refreshes_after_ttl() -> None:
    """probe_fn is called again after the TTL expires."""
    from samantha_server.observability.cached_probe import CachedProbe

    async def run() -> None:
        call_count = [0]

        async def probe() -> dict:  # type: ignore[type-arg]
            call_count[0] += 1
            return {"count": call_count[0]}

        cp = CachedProbe(probe, ttl_sec=0.05)  # 50ms TTL
        first = await cp.current()
        assert first == {"count": 1}

        await asyncio.sleep(0.1)  # Wait for TTL to expire

        second = await cp.current()
        assert second == {"count": 2}
        assert call_count[0] == 2

    asyncio.run(run())


def test_cached_probe_last_probe_age_sec_none_before_first_call() -> None:
    """last_probe_age_sec is None before the first probe is executed."""
    from samantha_server.observability.cached_probe import CachedProbe

    async def probe() -> dict:  # type: ignore[type-arg]
        return {}

    cp = CachedProbe(probe, ttl_sec=5.0)
    assert cp.last_probe_age_sec is None


def test_cached_probe_last_probe_age_sec_after_first_call() -> None:
    """last_probe_age_sec is a non-negative float after the first probe."""

    async def run() -> None:
        from samantha_server.observability.cached_probe import CachedProbe

        async def probe() -> dict:  # type: ignore[type-arg]
            return {}

        cp = CachedProbe(probe, ttl_sec=5.0)
        await cp.current()
        age = cp.last_probe_age_sec
        assert age is not None
        assert age >= 0.0

    asyncio.run(run())


def test_cached_probe_single_flight_under_concurrent_callers() -> None:
    """Concurrent calls during a refresh do not result in multiple probe invocations."""
    from samantha_server.observability.cached_probe import CachedProbe

    async def run() -> None:
        call_count = [0]

        async def slow_probe() -> dict:  # type: ignore[type-arg]
            call_count[0] += 1
            await asyncio.sleep(0.05)
            return {"count": call_count[0]}

        cp = CachedProbe(slow_probe, ttl_sec=60.0)

        # Fire 5 concurrent calls simultaneously (all see expired cache)
        results = await asyncio.gather(*[cp.current() for _ in range(5)])

        # All calls should get the same result, and probe_fn called exactly once
        assert call_count[0] == 1
        assert all(r == results[0] for r in results)

    asyncio.run(run())


def test_cached_probe_stub_returns_not_configured() -> None:
    """Default no-op stub returns the 'not_configured' shape."""
    from samantha_server.observability.cached_probe import make_langfuse_stub_probe

    async def run() -> None:
        cp = make_langfuse_stub_probe()
        result = await cp.current()
        assert result == {"reachable": False, "reason": "not_configured"}

    asyncio.run(run())
