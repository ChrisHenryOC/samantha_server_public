"""Tests for RBAC token sign/verify and require_capability dependency.

Covers:
- _sign_token / _verify_token round-trip
- RBACVerifyError raised on tampered/foreign-keyed/domain-separated tokens
- require_capability FastAPI dependency: 401 missing, 403 wrong cap, 401 expired
- Capability-list non-disclosure in 401/403 bodies
- /healthz and /readyz accept no token
- /version requires health:read
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers — test key setup
# ---------------------------------------------------------------------------

_TEST_KEY = bytes.fromhex("cafebabe" + "deadbeef" * 6 + "cafebabe")  # 32 bytes
_ALT_KEY = bytes.fromhex("01" * 32)  # a different 32-byte key (foreign-key scenario)


# ---------------------------------------------------------------------------
# Slice 3.1 — _sign_token / _verify_token round-trip
# ---------------------------------------------------------------------------


def test_sign_verify_round_trip() -> None:
    """_sign_token + _verify_token round-trip returns (capability, expires_at)."""
    from samantha_server.api.rbac import _sign_token, _verify_token

    now = int(time.time())
    token = _sign_token("events:submit", now, now + 3600, hmac_key=_TEST_KEY)
    cap, exp = _verify_token(token, hmac_key=_TEST_KEY)

    assert cap == "events:submit"
    assert exp == now + 3600


def test_sign_token_returns_ascii_string() -> None:
    """Token is an ASCII string (URL-safe b64url segments separated by '.')."""
    from samantha_server.api.rbac import _sign_token

    now = int(time.time())
    token = _sign_token("health:read", now, now + 86400, hmac_key=_TEST_KEY)

    assert isinstance(token, str)
    assert "." in token
    parts = token.split(".")
    assert len(parts) == 2


def test_sign_token_no_padding() -> None:
    """Token uses base64url without padding (no '=' characters)."""
    from samantha_server.api.rbac import _sign_token

    now = int(time.time())
    token = _sign_token("events:submit", now, now + 3600, hmac_key=_TEST_KEY)
    assert "=" not in token


def test_sign_token_different_keys_differ() -> None:
    """Tokens signed with different keys differ (HMAC is key-dependent)."""
    from samantha_server.api.rbac import _sign_token

    now = int(time.time())
    t1 = _sign_token("events:submit", now, now + 3600, hmac_key=_TEST_KEY)
    t2 = _sign_token("events:submit", now, now + 3600, hmac_key=_ALT_KEY)
    assert t1 != t2


# ---------------------------------------------------------------------------
# Slice 3.2 — RBACVerifyError raised on invalid tokens
# ---------------------------------------------------------------------------


def test_verify_tampered_payload_raises() -> None:
    """Flipping the expires_at in the payload invalidates the HMAC."""
    import base64

    from samantha_server.api.rbac import RBACVerifyError, _sign_token, _verify_token

    now = int(time.time())
    token = _sign_token("events:submit", now, now + 3600, hmac_key=_TEST_KEY)

    # Decode payload, tamper with it, re-encode
    payload_b64, hmac_b64 = token.split(".")
    # Re-pad for decoding
    padding = "=" * (-len(payload_b64) % 4)
    payload_bytes = base64.urlsafe_b64decode(payload_b64 + padding)
    parts = payload_bytes.decode().split("|")
    # Flip the expires_at
    parts[2] = str(int(parts[2]) + 9999)
    tampered_payload = "|".join(parts).encode()
    tampered_b64 = base64.urlsafe_b64encode(tampered_payload).rstrip(b"=").decode()
    tampered_token = f"{tampered_b64}.{hmac_b64}"

    with pytest.raises(RBACVerifyError):
        _verify_token(tampered_token, hmac_key=_TEST_KEY)


def test_verify_foreign_key_raises() -> None:
    """Token signed with _ALT_KEY fails verification with _TEST_KEY."""
    from samantha_server.api.rbac import RBACVerifyError, _sign_token, _verify_token

    now = int(time.time())
    token = _sign_token("events:submit", now, now + 3600, hmac_key=_ALT_KEY)

    with pytest.raises(RBACVerifyError):
        _verify_token(token, hmac_key=_TEST_KEY)


def test_verify_domain_separation_regression() -> None:
    """Token whose HMAC omits the b'rbac-token:' prefix fails verification.

    Constructs an HMAC directly over the payload (without the domain-separation
    prefix) and asserts that verify_token rejects it. Guards against any future
    change that accidentally drops the prefix.
    """
    import base64
    import hashlib
    import hmac as _hmac

    from samantha_server.api.rbac import RBACVerifyError, _verify_token

    now = int(time.time())
    payload = f"events:submit|{now}|{now + 3600}"
    payload_bytes = payload.encode()

    # Compute HMAC *without* the domain-separation prefix (wrong construction)
    mac = _hmac.new(_TEST_KEY, payload_bytes, hashlib.sha256).digest()

    payload_b64 = base64.urlsafe_b64encode(payload_bytes).rstrip(b"=").decode()
    mac_b64 = base64.urlsafe_b64encode(mac).rstrip(b"=").decode()
    forged_token = f"{payload_b64}.{mac_b64}"

    with pytest.raises(RBACVerifyError):
        _verify_token(forged_token, hmac_key=_TEST_KEY)


def test_verify_wrong_domain_prefix_raises() -> None:
    """Token whose HMAC uses a different domain prefix (e.g. b'other:') fails."""
    import base64
    import hashlib
    import hmac as _hmac

    from samantha_server.api.rbac import RBACVerifyError, _verify_token

    now = int(time.time())
    payload = f"events:submit|{now}|{now + 3600}"
    payload_bytes = payload.encode()

    # Compute HMAC with a different domain prefix
    mac = _hmac.new(_TEST_KEY, b"other:" + payload_bytes, hashlib.sha256).digest()

    payload_b64 = base64.urlsafe_b64encode(payload_bytes).rstrip(b"=").decode()
    mac_b64 = base64.urlsafe_b64encode(mac).rstrip(b"=").decode()
    forged_token = f"{payload_b64}.{mac_b64}"

    with pytest.raises(RBACVerifyError):
        _verify_token(forged_token, hmac_key=_TEST_KEY)


def test_verify_malformed_token_raises() -> None:
    """Non-two-segment string → RBACVerifyError."""
    from samantha_server.api.rbac import RBACVerifyError, _verify_token

    with pytest.raises(RBACVerifyError):
        _verify_token("not_a_valid_token", hmac_key=_TEST_KEY)


def test_verify_bad_b64_raises() -> None:
    """Undecodable b64url segment → RBACVerifyError."""
    from samantha_server.api.rbac import RBACVerifyError, _verify_token

    with pytest.raises(RBACVerifyError):
        _verify_token("$$$invalid$$$.$$invalidsig$$", hmac_key=_TEST_KEY)


# ---------------------------------------------------------------------------
# Slice 3.3 — require_capability FastAPI dependency
# ---------------------------------------------------------------------------


def _make_minimal_app_with_guarded_route(capability: str = "events:submit") -> FastAPI:
    """Build a FastAPI app with a GET /guarded route that requires *capability*."""
    from fastapi import Depends
    from fastapi.responses import JSONResponse

    from samantha_server.api.rbac import register_rbac_exception_handlers, require_capability

    app = FastAPI()
    register_rbac_exception_handlers(app)

    @app.get("/guarded")
    async def _guarded(_: None = Depends(require_capability(capability))) -> JSONResponse:
        return JSONResponse({"ok": True})

    return app


def test_require_capability_valid_token_returns_200() -> None:
    """Valid token with matching capability → 200."""
    from samantha_server.api.rbac import _sign_token

    app = _make_minimal_app_with_guarded_route("events:submit")
    now = int(time.time())
    token = _sign_token("events:submit", now, now + 3600, hmac_key=_TEST_KEY)

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/guarded", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200


def test_require_capability_missing_token_returns_401() -> None:
    """No Authorization header → 401."""
    app = _make_minimal_app_with_guarded_route("events:submit")

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/guarded")

    assert resp.status_code == 401
    assert resp.json() == {"error": "unauthorized"}


def test_require_capability_wrong_capability_returns_403() -> None:
    """Valid, non-expired token for 'health:read' presented to 'events:submit' route → 403."""
    from samantha_server.api.rbac import _sign_token

    app = _make_minimal_app_with_guarded_route("events:submit")
    now = int(time.time())
    # Non-expired token with wrong capability → 403 (valid HMAC + valid expiry + wrong cap)
    token = _sign_token("health:read", now, now + 3600, hmac_key=_TEST_KEY)

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/guarded", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 403
    assert resp.json() == {"error": "forbidden"}


def test_expired_wrong_capability_returns_401() -> None:
    """Expired token for wrong capability → 401, not 403.

    Expiry must be checked before capability comparison so that an
    expired+wrong-cap token does not leak that the HMAC is valid and
    the capability field is parseable.  A 403 would disclose that the
    token structure is recognizable, which violates the security
    invariant: 401 for expired, 403 only for valid HMAC + valid
    expiry + wrong capability.
    """
    from samantha_server.api.rbac import _sign_token

    app = _make_minimal_app_with_guarded_route("events:submit")
    past = int(time.time()) - 7200
    # Expired token for the WRONG capability — must return 401 (expired), not 403.
    token = _sign_token("health:read", past, past + 3600, hmac_key=_TEST_KEY)

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/guarded", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
    assert resp.json() == {"error": "unauthorized"}


def test_require_capability_expired_token_returns_401() -> None:
    """Expired token → 401."""
    from samantha_server.api.rbac import _sign_token

    app = _make_minimal_app_with_guarded_route("events:submit")
    past = int(time.time()) - 7200
    token = _sign_token("events:submit", past, past + 3600, hmac_key=_TEST_KEY)

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/guarded", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
    assert resp.json() == {"error": "unauthorized"}


def test_require_capability_foreign_key_returns_401() -> None:
    """Token signed with _ALT_KEY presented to app using _TEST_KEY → 401."""
    from samantha_server.api.rbac import _sign_token

    app = _make_minimal_app_with_guarded_route("events:submit")
    now = int(time.time())
    token = _sign_token("events:submit", now, now + 3600, hmac_key=_ALT_KEY)

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/guarded", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
    assert resp.json() == {"error": "unauthorized"}


def test_require_capability_tampered_token_returns_401() -> None:
    """Tampered token → 401."""
    import base64

    from samantha_server.api.rbac import _sign_token

    app = _make_minimal_app_with_guarded_route("events:submit")
    now = int(time.time())
    token = _sign_token("events:submit", now, now + 3600, hmac_key=_TEST_KEY)

    # Tamper with the payload
    payload_b64, hmac_b64 = token.split(".")
    padding = "=" * (-len(payload_b64) % 4)
    payload_bytes = base64.urlsafe_b64decode(payload_b64 + padding)
    parts = payload_bytes.decode().split("|")
    parts[2] = str(int(parts[2]) + 9999)
    tampered = "|".join(parts).encode()
    tampered_b64 = base64.urlsafe_b64encode(tampered).rstrip(b"=").decode()
    tampered_token = f"{tampered_b64}.{hmac_b64}"

    # Valid token still 200 (sanity check)
    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        valid_resp = client.get("/guarded", headers={"Authorization": f"Bearer {token}"})
    assert valid_resp.status_code == 200

    # Tampered token → 401
    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp2 = client.get("/guarded", headers={"Authorization": f"Bearer {tampered_token}"})

    assert resp2.status_code == 401


# ---------------------------------------------------------------------------
# Slice 3.4 — Capability-list non-disclosure
# ---------------------------------------------------------------------------

_CAPABILITY_STRINGS = [
    "events:submit",
    "receipts:read",
    "health:read",
    "health",
    "events",
    "receipts",
]


# ---------------------------------------------------------------------------
# Non-Bearer Authorization scheme coverage
# ---------------------------------------------------------------------------


def test_basic_auth_scheme_returns_401() -> None:
    """Authorization: Basic ... returns 401 (wrong scheme, not a bearer token)."""
    app = _make_minimal_app_with_guarded_route("events:submit")

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/guarded", headers={"Authorization": "Basic dXNlcjpwYXNz"})

    assert resp.status_code == 401
    assert resp.json() == {"error": "unauthorized"}


def test_token_auth_scheme_returns_401() -> None:
    """Authorization: Token ... returns 401 (wrong scheme)."""
    app = _make_minimal_app_with_guarded_route("events:submit")

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/guarded", headers={"Authorization": "Token sometoken"})

    assert resp.status_code == 401
    assert resp.json() == {"error": "unauthorized"}


def test_bearer_no_trailing_space_returns_401() -> None:
    """Authorization: bearer (without trailing space) returns 401.

    'bearer' without the trailing space isn't a valid Bearer scheme prefix
    — it's treated as the token itself (or malformed).
    """
    app = _make_minimal_app_with_guarded_route("events:submit")

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        # No space after 'bearer' — the space is required per RFC 6750
        resp = client.get("/guarded", headers={"Authorization": "bearertoken"})

    assert resp.status_code == 401
    assert resp.json() == {"error": "unauthorized"}


def test_401_body_does_not_enumerate_capabilities() -> None:
    """401 body must not contain any known capability string."""
    app = _make_minimal_app_with_guarded_route("events:submit")

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/guarded")

    assert resp.status_code == 401
    body_text = resp.text
    for cap in _CAPABILITY_STRINGS:
        assert cap not in body_text, f"Capability string {cap!r} leaked in 401 body"


def test_403_body_does_not_enumerate_capabilities() -> None:
    """403 body must not contain any known capability string."""
    from samantha_server.api.rbac import _sign_token

    app = _make_minimal_app_with_guarded_route("events:submit")
    now = int(time.time())
    token = _sign_token("health:read", now, now + 3600, hmac_key=_TEST_KEY)

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/guarded", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 403
    body_text = resp.text
    for cap in _CAPABILITY_STRINGS:
        assert cap not in body_text, f"Capability string {cap!r} leaked in 403 body"


def test_401_body_does_not_echo_token() -> None:
    """401 body must not echo the rejected token."""
    from samantha_server.api.rbac import _sign_token

    app = _make_minimal_app_with_guarded_route("events:submit")
    now = int(time.time())
    # Foreign-keyed token — will produce 401
    token = _sign_token("events:submit", now, now + 3600, hmac_key=_ALT_KEY)

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/guarded", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
    # The token itself (or a fragment) must not appear in the body
    assert token[:20] not in resp.text


# ---------------------------------------------------------------------------
# Slice 5.1 — issue_token() unit tests
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# TTL exact-boundary semantics
# ---------------------------------------------------------------------------


def test_rbac_valid_at_exact_expires_at() -> None:
    """require_capability returns 200 when expires_at == now (strict < semantics).

    The check is `expires_at < int(time.time())` so a token at exactly now is valid.

    """
    from samantha_server.api.rbac import _sign_token

    app = _make_minimal_app_with_guarded_route("events:submit")
    fixed_now = int(time.time())
    # Token expires_at == fixed_now (exactly now)
    token = _sign_token("events:submit", fixed_now - 60, fixed_now, hmac_key=_TEST_KEY)

    with (
        patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY),
        patch("samantha_server.api.rbac.time") as mock_time,
    ):
        mock_time.time.return_value = float(fixed_now)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/guarded", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200


def test_rbac_expired_one_second_after_expires_at() -> None:
    """require_capability returns 401 when now > expires_at by 1 second.


    """
    from samantha_server.api.rbac import _sign_token

    app = _make_minimal_app_with_guarded_route("events:submit")
    fixed_now = int(time.time())
    token = _sign_token("events:submit", fixed_now - 60, fixed_now, hmac_key=_TEST_KEY)

    with (
        patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY),
        patch("samantha_server.api.rbac.time") as mock_time,
    ):
        # One second past expires_at
        mock_time.time.return_value = float(fixed_now + 1)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/guarded", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
    assert resp.json() == {"error": "unauthorized"}


def test_issue_token_round_trip() -> None:
    """issue_token produces a token that _verify_token accepts, with the correct capability.

    Verifies issue_token reads RBAC_HMAC_KEY and produces a structurally valid,
    verifiable token with the requested capability and a future expires_at.
    """
    import samantha_server.config as cfg
    from samantha_server.api.rbac import _verify_token, issue_token

    result = issue_token("events:submit")
    # The result is a string (wire format).
    assert isinstance(result, str)
    # _verify_token should succeed with the configured key.
    cap, expires_at = _verify_token(result, hmac_key=cfg.RBAC_HMAC_KEY)
    assert cap == "events:submit"
    assert expires_at > int(time.time())


def test_issue_token_uses_config_ttl() -> None:
    """issue_token respects cfg.RBAC_TOKEN_TTL_SEC when computing expires_at."""
    import samantha_server.config as cfg
    from samantha_server.api.rbac import _verify_token, issue_token

    original_ttl = cfg.RBAC_TOKEN_TTL_SEC
    with patch.object(cfg, "RBAC_TOKEN_TTL_SEC", 60):
        now_before = int(time.time())
        result = issue_token("health:read")
        now_after = int(time.time())

    _, expires_at = _verify_token(result, hmac_key=cfg.RBAC_HMAC_KEY)
    # expires_at must be within [now_before+60, now_after+60] (±1 s tolerance).
    assert now_before + 60 <= expires_at <= now_after + 60 + 1
    # original_ttl may or may not be 60; the assertion above covers the actual invariant.
    _ = original_ttl


def test_issue_token_rejects_unknown_capability_via_cli() -> None:
    """python -m samantha_server.api.rbac --issue frob:nicate exits non-zero with stderr."""
    import os
    import subprocess
    import sys

    env = {k: v for k, v in os.environ.items()}
    result = subprocess.run(
        [sys.executable, "-m", "samantha_server.api.rbac", "--issue", "frob:nicate"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 2, (
        f"Expected exit code 2 for unknown capability, got {result.returncode}; "
        f"stdout: {result.stdout!r}; stderr: {result.stderr!r}"
    )
    assert (
        "frob:nicate" in result.stderr
        or "unknown" in result.stderr.lower()
        or "capability" in result.stderr.lower()
    )


def test_issue_token_missing_rbac_key_exits_cleanly(tmp_path: Path) -> None:
    """python -m samantha_server.api.rbac --issue events:submit with missing key exits 1.

    Runs from an empty ``tmp_path`` so the CLI's ``load_dotenv_for_cli``
    cannot find the repo's ``.env`` and back-fill ``RBAC_HMAC_KEY`` —
    the test exercises the *truly missing key* path. PYTHONPATH is set
    so the subprocess can still import ``samantha_server`` despite the
    foreign CWD (no editable .pth in site-packages — uv-style install).
    """
    import os
    import subprocess
    import sys

    env = {k: v for k, v in os.environ.items()}
    env.pop("RBAC_HMAC_KEY", None)
    env.pop("PYTEST_CURRENT_TEST", None)
    env["RECEIPT_SIGNING_KEY"] = "a" * 64
    env["PHI_HASH_SALT"] = "a" * 64
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    result = subprocess.run(
        [sys.executable, "-m", "samantha_server.api.rbac", "--issue", "events:submit"],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
    )
    # Should exit non-zero with a clean error, not a raw traceback
    assert result.returncode != 0
    # The error must mention RBAC_HMAC_KEY so the operator knows what to set.
    # Pre-fix this used `... or result.returncode == 1`, which was trivially
    # true for any non-zero exit and gave no real coverage of the message.
    assert "RBAC_HMAC_KEY" in result.stderr, (
        f"expected RBAC_HMAC_KEY mentioned in stderr, got:\n{result.stderr}"
    )


def test_issue_token_different_capabilities_differ() -> None:
    """Different capabilities produce different tokens (different payload → different HMAC)."""
    from samantha_server.api.rbac import issue_token

    t1 = issue_token("events:submit")
    t2 = issue_token("health:read")
    assert t1 != t2


def test_issue_token_cli_smoke() -> None:
    """python -m samantha_server.api.rbac --issue events:submit exits 0 and prints a token."""
    import subprocess
    import sys

    env = {k: v for k, v in __import__("os").environ.items()}
    # Subprocess will inherit RBAC_HMAC_KEY from the test session env (conftest sets it).
    result = subprocess.run(
        [sys.executable, "-m", "samantha_server.api.rbac", "--issue", "events:submit"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, f"CLI exited {result.returncode}; stderr: {result.stderr!r}"
    token = result.stdout.strip()
    # Token should have the b64url.b64url structure.
    assert "." in token, f"Unexpected CLI output: {token!r}"
    parts = token.split(".")
    assert len(parts) == 2
