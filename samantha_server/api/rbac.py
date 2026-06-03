"""RBAC capability-token signing, verification, and FastAPI dependency factory.

GH-119 Phase 3 Step 5.

Wire format — ASCII string ``<b64url(payload)>.<b64url(hmac)>``:
- ``payload`` (UTF-8): ``<capability>|<issued_at_unix>|<expires_at_unix>``
  e.g. ``events:submit|1714000000|1714086400``.
- ``hmac`` (32 raw bytes): HMAC-SHA256(RBAC_HMAC_KEY, b"rbac-token:" + payload_bytes).
- Both segments use base64url **without padding** (RFC 4648 §5).

The ``b"rbac-token:"`` prefix is a domain-separation tag on the HMAC input
only (not on the wire payload).  It prevents cross-protocol forgeries if a
future MAC consumer ever shares the key.

Security invariants:
- 401 body is always ``{"error": "unauthorized"}``.
- 403 body is always ``{"error": "forbidden"}``.
- Neither body ever echoes the rejected token or enumerates valid capabilities.
- Verification reasons are not returned to callers (existence-oracle protection).

CLI:
    python -m samantha_server.api.rbac --issue <capability>

reads RBAC_HMAC_KEY and RBAC_TOKEN_TTL_SEC from samantha_server.config,
prints the token to stdout.
"""

from __future__ import annotations

import base64
import functools
import hashlib
import hmac as _hmac
import time
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# Domain-separation prefix on the HMAC input (not on the wire payload).
_DOMAIN_PREFIX: bytes = b"rbac-token:"


class RBACVerifyError(Exception):
    """Raised when token verification fails for any reason.

    Callers must map this to a 401 without leaking the reason.
    """


class _RBACUnauthorized(Exception):
    """Internal sentinel: HTTP 401. Never surfaces the verification reason."""


class _RBACForbidden(Exception):
    """Internal sentinel: HTTP 403 (valid token, wrong capability)."""


def register_rbac_exception_handlers(app: FastAPI) -> None:
    """Register 401/403 exception handlers that produce the exact required bodies.

    Must be called on the FastAPI app before requests are served. Called by
    ``create_app()`` and by test factories that wire RBAC routes.

    Response contract:
    - 401: ``{"error": "unauthorized"}``
    - 403: ``{"error": "forbidden"}``
    Neither body enumerates capabilities or echoes the rejected token.
    """

    @app.exception_handler(_RBACUnauthorized)
    async def _rbac_unauthorized_handler(request: Request, exc: _RBACUnauthorized) -> JSONResponse:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    @app.exception_handler(_RBACForbidden)
    async def _rbac_forbidden_handler(request: Request, exc: _RBACForbidden) -> JSONResponse:
        return JSONResponse({"error": "forbidden"}, status_code=403)


@functools.cache
def _get_rbac_hmac_key() -> bytes:
    """Lazy config read — avoids triggering the sentinel at collection time.

    @functools.cache so the import and attribute lookup happen once per process
    (not per authenticated request). The cache is safe: RBAC_HMAC_KEY is a
    module-level constant in config.py and never changes after process start.
    """
    import samantha_server.config as cfg

    return cfg.RBAC_HMAC_KEY


def _b64url_encode(data: bytes) -> str:
    """Encode bytes as base64url without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    """Decode base64url string (re-pad as needed). Raises ValueError on bad input."""
    # Re-pad to a multiple of 4
    padding = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + padding)


def _sign_token(
    capability: str,
    issued_at: int,
    expires_at: int,
    *,
    hmac_key: bytes,
) -> str:
    """Return a signed capability token string.

    Parameters
    ----------
    capability:
        The capability name (e.g. ``"events:submit"``).
    issued_at:
        Unix timestamp when the token was issued.
    expires_at:
        Unix timestamp when the token expires.
    hmac_key:
        32-byte HMAC-SHA256 key.

    Returns
    -------
    str
        ASCII string ``<b64url(payload)>.<b64url(mac)>`` with no ``=`` padding.
    """
    payload = f"{capability}|{issued_at}|{expires_at}"
    payload_bytes = payload.encode("utf-8")
    mac = _hmac.new(hmac_key, _DOMAIN_PREFIX + payload_bytes, hashlib.sha256).digest()
    return f"{_b64url_encode(payload_bytes)}.{_b64url_encode(mac)}"


def _verify_token(token: str, *, hmac_key: bytes) -> tuple[str, int]:
    """Verify a token and return ``(capability, expires_at)``.

    Raises
    ------
    RBACVerifyError
        On any verification failure (malformed, tampered, foreign-keyed).
        The caller must not propagate the reason to clients.
    """
    try:
        parts = token.split(".")
        if len(parts) != 2:
            raise RBACVerifyError("token must have exactly two segments")
        payload_b64, mac_b64 = parts

        payload_bytes = _b64url_decode(payload_b64)
        submitted_mac = _b64url_decode(mac_b64)
    except Exception as exc:
        raise RBACVerifyError("token decode failed") from exc

    # Recompute HMAC with domain-separation prefix.
    expected_mac = _hmac.new(hmac_key, _DOMAIN_PREFIX + payload_bytes, hashlib.sha256).digest()

    if not _hmac.compare_digest(expected_mac, submitted_mac):
        raise RBACVerifyError("HMAC mismatch")

    # Parse payload.
    try:
        decoded = payload_bytes.decode("utf-8")
        cap, _, expires_at_str = decoded.split("|")  # _ = issued_at (discarded; not checked)
        expires_at = int(expires_at_str)
    except Exception as exc:
        raise RBACVerifyError("payload parse failed") from exc

    return cap, expires_at


def _extract_bearer_token(request: Request) -> str | None:
    """Extract the bearer token from the Authorization header, or return None.

    Uses auth[:7].lower() == "bearer " (7 chars) rather than
    auth.lower().startswith("bearer ") — bounds the lowercase allocation to 7
    bytes instead of materializing a full copy of the header.
    """
    auth = request.headers.get("Authorization", "")
    if auth[:7].lower() == "bearer ":
        return auth[7:].strip() or None
    return None


def require_capability(capability: str) -> Callable[..., Awaitable[None]]:
    """Return a FastAPI dependency that enforces *capability* on the request.

    Usage::

        @app.post("/events")
        async def post_events(
            ...,
            _: None = Depends(require_capability("events:submit")),
        ) -> ...:
            ...

    Responses:
    - 401 ``{"error": "unauthorized"}`` — missing/invalid/expired/foreign-keyed token.
    - 403 ``{"error": "forbidden"}`` — valid token but wrong capability.

    Neither response enumerates valid capabilities or echoes the token.
    """

    async def _check(request: Request) -> None:
        token = _extract_bearer_token(request)
        if token is None:
            raise _RBACUnauthorized()

        hmac_key = _get_rbac_hmac_key()

        try:
            verified_capability, expires_at = _verify_token(token, hmac_key=hmac_key)
        except RBACVerifyError:
            raise _RBACUnauthorized() from None

        # Expiry check before capability comparison: an expired token for the
        # wrong capability must return 401 (expired/unauthorized), not 403
        # (forbidden). Checking capability first would leak that the HMAC is
        # valid and the capability field is parseable — a 403 discloses more
        # than the caller should know about a rejected token.
        if expires_at < int(time.time()):
            raise _RBACUnauthorized()

        if verified_capability != capability:
            raise _RBACForbidden()

    return _check


def issue_token(capability: str) -> str:
    """Issue a capability token using the configured RBAC_HMAC_KEY and TTL.

    Reads config.RBAC_HMAC_KEY and config.RBAC_TOKEN_TTL_SEC. Prints to stdout
    via the CLI entry point below.
    """
    import samantha_server.config as cfg

    now = int(time.time())
    expires_at = now + cfg.RBAC_TOKEN_TTL_SEC
    return _sign_token(capability, now, expires_at, hmac_key=cfg.RBAC_HMAC_KEY)


_KNOWN_CAPABILITIES: frozenset[str] = frozenset(("events:submit", "receipts:read", "health:read"))

if __name__ == "__main__":
    import argparse
    import sys

    from samantha_server.errors import MisconfiguredEnvironmentError

    parser = argparse.ArgumentParser(description="Issue an RBAC capability token.")
    parser.add_argument("--issue", required=True, help="Capability to issue (e.g. events:submit)")
    args = parser.parse_args()

    # Load `.env` before issue_token() triggers the lazy config import.
    from samantha_server._cli import load_dotenv_for_cli

    load_dotenv_for_cli()

    if args.issue not in _KNOWN_CAPABILITIES:
        print(
            f"Error: unknown capability {args.issue!r}. "
            f"Known capabilities: {', '.join(sorted(_KNOWN_CAPABILITIES))}",
            file=sys.stderr,
        )
        sys.exit(2)

    try:
        print(issue_token(args.issue))
    except MisconfiguredEnvironmentError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        sys.exit(1)
    sys.exit(0)


__all__ = [
    "RBACVerifyError",
    "issue_token",
    "require_capability",
]
