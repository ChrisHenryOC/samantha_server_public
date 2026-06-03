"""Root conftest.py — project-wide pytest configuration.

Collection-time hooks that enforce project conventions across all test paths.

Also installs a session-scope no-network guard. The project's intentional
architecture has no outbound calls (local LLMs, no cloud APIs), so this is
defense-in-depth: it catches accidental cloud-API regressions if a future
PR reaches for ``httpx``/``anthropic``/etc. The guard fails loudly with a
clear message on accidental escapes — a class of bug that's silent locally
but fails CI for the wrong reason (auth error in fork PR, not "real network
forbidden").

Both ``socket.socket.connect`` (raising) and ``socket.socket.connect_ex``
(errno-returning) are patched so that async libraries using the non-raising
variant (asyncio proactor, aiohttp, trio) can't silently escape the guard.

Loopback addresses are always permitted to support in-process test servers
(e.g. an MCP STDIO transport binding to a Unix socket on loopback, or a
local OpenAPI server fixture). The check uses ``ipaddress.ip_address`` so
all valid loopback addresses pass through (including ``127.0.0.2`` and
IPv4-mapped IPv6 ``::ffff:127.0.0.1``), not just the canonical names.

The escape hatch ``ALLOW_REAL_NETWORK=1`` opts the test session out of the
guard entirely. It is **not used by any current workflow** — the project
has no real-API CI tier — but exists for the rare developer-side case
where a test legitimately needs real network access. When the hatch is
taken, a warning is emitted via ``warnings.warn`` so CI logs visibly
record the session ran without the guard.
"""

from __future__ import annotations

import ipaddress
import os
import socket
import warnings
from collections.abc import Iterator
from typing import Any

import pytest

# String hostnames that resolve to loopback. The numeric loopback range
# (127.0.0.0/8, ::1) is checked separately via ipaddress so all valid
# loopback addresses pass through.
_LOOPBACK_NAMES: frozenset[str] = frozenset({"localhost"})


def _is_loopback(host: object) -> bool:
    """Return True if *host* is a loopback name or a loopback IP literal."""
    if not isinstance(host, str):
        return False
    if host in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _block_message(host: object) -> str:
    return (
        f"real network forbidden by conftest.py; got connect to {host!r}. "
        "Use the LLM fake. Set ALLOW_REAL_NETWORK=1 to override (intentional "
        "developer-side escape hatch; not used by any current workflow)."
    )


@pytest.fixture(autouse=True, scope="session")
def _forbid_real_network() -> Iterator[None]:
    """Block real outbound network calls for the duration of the test session.

    Patches both ``socket.socket.connect`` (raising) and
    ``socket.socket.connect_ex`` (errno-returning) so async libraries
    can't escape the guard.

    See CI plan § 5.2 for the no-network invariant rationale.
    """
    if os.environ.get("ALLOW_REAL_NETWORK"):
        # The escape hatch was taken. Warn loudly so CI logs and pytest
        # output both visibly record that the guard is inactive — silent
        # bypass would let a developer's stale .env disable network
        # protection for an entire session without anyone noticing.
        warnings.warn(
            "ALLOW_REAL_NETWORK is set; the no-network guard is INACTIVE. "
            "Tests in this session may make real outbound connections.",
            stacklevel=2,
        )
        yield
        return

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def _blocked_connect(self: socket.socket, address: Any, /) -> None:
        host = address[0] if isinstance(address, tuple) else address
        if _is_loopback(host):
            real_connect(self, address)
            return
        raise RuntimeError(_block_message(host))

    def _blocked_connect_ex(self: socket.socket, address: Any, /) -> int:
        host = address[0] if isinstance(address, tuple) else address
        if _is_loopback(host):
            return real_connect_ex(self, address)
        # Mirror connect's behavior: raise rather than silently returning
        # an errno. The non-raising variant exists for callers that want
        # to handle errno; here we choose loud failure over silent block
        # because connect_ex callers (asyncio, aiohttp) may otherwise
        # treat a returned errno as a normal connection refusal and
        # silently retry against another endpoint.
        raise RuntimeError(_block_message(host))

    socket.socket.connect = _blocked_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = _blocked_connect_ex  # type: ignore[method-assign]
    try:
        yield
    finally:
        socket.socket.connect = real_connect  # type: ignore[method-assign]
        socket.socket.connect_ex = real_connect_ex  # type: ignore[method-assign]


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Enforce: @pytest.mark.perf is only allowed on tests under tests/perf/.

    The ``addopts = "-m 'not perf'"`` in pyproject.toml silently drops any
    test that carries the perf marker from the default run.  A test outside
    ``tests/perf/`` that accidentally receives ``@pytest.mark.perf`` would
    become invisible to CI without any error.  This hook makes the scope
    violation a hard error at collection time.
    """
    for item in items:
        if item.get_closest_marker("perf") and "tests/perf/" not in str(item.fspath):
            raise pytest.UsageError(
                f"@pytest.mark.perf used outside tests/perf/: {item.nodeid}. "
                "Tests under other paths would be silently dropped from the default "
                "run by `addopts = -m 'not perf'`."
            )
