"""Env-driven config module — eager validation at import.

If any required env var is missing or malformed, raises
MisconfiguredEnvironmentError immediately. The engine refuses to
start without valid secrets — silent crypto-key absence is a
security smell.

PER G18 (no-leak invariant): error messages MUST NOT include the raw
env value or any substring of it. The variable name and the
generation hint are sufficient.
"""

from __future__ import annotations

import ipaddress
import logging
import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from samantha_server.errors import MisconfiguredEnvironmentError

_log = logging.getLogger(__name__)

_KEY_LEN_BYTES: int = 32  # Ed25519 seed / HMAC salt — both 32 bytes


def _decode_hex(var_name: str, raw: str, *, gen_hint: str) -> bytes:
    """Decode hex; raise with actionable message but no raw-value echo."""
    try:
        decoded = bytes.fromhex(raw)
    except ValueError:
        # NO `raw` in the error message — could leak partial keys (G18).
        raise MisconfiguredEnvironmentError(
            f"Environment variable {var_name!r} is not valid hex. "
            f"Generate a fresh value via: {gen_hint}."
        ) from None
    if len(decoded) != _KEY_LEN_BYTES:
        raise MisconfiguredEnvironmentError(
            f"Environment variable {var_name!r} decoded to "
            f"{len(decoded)} bytes; expected exactly {_KEY_LEN_BYTES}. "
            f"Generate a fresh value via: {gen_hint}."
        )
    return decoded


def _required_hex(var_name: str, *, gen_hint: str) -> bytes:
    raw = os.environ.get(var_name)
    if raw is None:
        raise MisconfiguredEnvironmentError(
            f"Required environment variable {var_name!r} is not set. "
            f"Generate via: {gen_hint}. "
            f"See .env.example for the expected shape."
        )
    return _decode_hex(var_name, raw, gen_hint=gen_hint)


def _optional_hex(var_name: str, *, gen_hint: str) -> bytes | None:
    """Return None when unset OR explicitly empty; raise on malformed/wrong-length values.

    The empty-string treatment is intentional — operators routinely
    "blank out" an env var by setting `RECEIPT_SIGNING_KEY_PREVIOUS=` in
    a `.env` file, and we treat that as semantically equivalent to
    leaving the variable unset. The trade-off: a developer who *typoed*
    an empty value during a key-rotation window silently gets `None`
    (no previous-key verification). The receipt-signing playbook in
    CLAUDE.md flags rotation-window verification as a manual checkpoint.
    """
    raw = os.environ.get(var_name)
    if raw is None or raw == "":
        return None
    return _decode_hex(var_name, raw, gen_hint=gen_hint)


def _required_float(var_name: str, *, default: float) -> float:
    """Read a float from env; raise MisconfiguredEnvironmentError on bad input.

    Mirrors `_required_hex`'s typed-error contract for numeric env vars
    so a caller `except MisconfiguredEnvironmentError` catches every
    config-load failure mode (PR #97 review H-01).
    """
    raw = os.environ.get(var_name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        # G18: do not echo the raw value — could surface in operator logs.
        raise MisconfiguredEnvironmentError(
            f"Environment variable {var_name!r} is not a valid float."
        ) from None


def _validate_model_path(
    var_name: str,
    raw: str,
    *,
    allowed_root: Path | None = None,
) -> Path:
    """Resolve and validate a model file path (H-11).

    Steps:
    1. Resolve ``raw`` via ``Path(raw).resolve()`` to canonicalize.
    2. Anchor: the resolved path must be under ``allowed_root`` (default:
       ``Path.home()``). Rejects traversal attacks like ``../../../etc/passwd``.
    3. Existence: the resolved path must exist on disk.

    Raises MisconfiguredEnvironmentError on any violation. The error message
    does NOT include ``raw`` (G18 no-leak invariant).

    Parameters
    ----------
    var_name:
        The env var name, used in the error message.
    raw:
        The raw string value from the env var or default.
    allowed_root:
        The directory that the resolved path must be under. Defaults to
        ``Path.home()`` (the developer's home directory) for local-LLM
        topology where models live under ~/models or ~/hf-cache.
    """
    if allowed_root is None:
        allowed_root = Path.home()

    resolved = Path(raw).resolve()

    try:
        resolved.relative_to(allowed_root.resolve())
    except ValueError:
        raise MisconfiguredEnvironmentError(
            f"{var_name} resolves outside the allowed model root "
            f"({allowed_root}). Check the value and ensure it is under "
            f"the permitted directory. (Raw value omitted per G18 no-leak.)"
        ) from None

    if not resolved.exists():
        raise MisconfiguredEnvironmentError(
            f"{var_name} path does not exist. "
            f"Ensure the model file is present at the configured location. "
            f"(Raw value omitted per G18 no-leak.)"
        )

    return resolved


def _validate_loopback_url(var_name: str, raw: str) -> None:
    """Validate that *raw* is an http:// URL whose host is loopback.

    Raises MisconfiguredEnvironmentError if the scheme is not 'http' or
    if the host is non-loopback. Error messages do NOT echo the raw URL
    (G18 no-leak invariant).

    A host is accepted as loopback if it equals 'localhost' OR if
    ``ipaddress.ip_address(host).is_loopback`` is True.
    """
    parsed = urlsplit(raw)

    # Enforce http:// scheme — oMLX is loopback HTTP only (CWE-184).
    if parsed.scheme != "http":
        raise MisconfiguredEnvironmentError(
            f"Environment variable {var_name!r} must use the http:// scheme; "
            f"got a non-http scheme. "
            f"(Raw value omitted per G18 no-leak.)"
        )

    host = parsed.hostname or ""

    is_loopback = host == "localhost"
    if not is_loopback:
        try:
            is_loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            is_loopback = False

    if not is_loopback:
        raise MisconfiguredEnvironmentError(
            f"Environment variable {var_name!r} must be a loopback URL "
            f"(host must be 'localhost' or a loopback IP). "
            f"(Raw value omitted per G18 no-leak.)"
        )


def _required_int(var_name: str, *, default: int) -> int:
    """Read an int from env; raise MisconfiguredEnvironmentError on bad input.

    Mirrors `_required_hex`'s typed-error contract for numeric env vars
    (PR #97 review H-01).
    """
    raw = os.environ.get(var_name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        # G18: do not echo the raw value.
        raise MisconfiguredEnvironmentError(
            f"Environment variable {var_name!r} is not a valid integer."
        ) from None


# Sentinel byte prefixes set by tests/conftest.py for the test session only.
# These are obviously-fake patterns that no production deployment should ever use.
# A bypass via `PYTEST_CURRENT_TEST=fake` exported in the shell exists by
# design — the guard is a developer ergonomic, not a security control.
# (PR #97 review L-02.)
_TEST_KEY_PREFIXES: tuple[bytes, ...] = (b"\xde\xad\xbe\xef\xde\xad\xbe\xef",)
_TEST_SALT_PREFIXES: tuple[bytes, ...] = (b"\xba\xdc\x0f\xfe\xe0",)


def _reject_test_sentinel(
    var_name: str,
    decoded: bytes,
    sentinel_prefixes: tuple[bytes, ...],
    *,
    gen_hint: str,
) -> None:
    """Raise if decoded starts with a test-sentinel prefix and we are outside pytest.

    The error message describes the matched prefix in hex form (computed
    from the actual bytes that matched) rather than hardcoding any
    sentinel name. This keeps the message accurate regardless of which
    variable's sentinel matched (PR #97 review M-04).
    """
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return
    for prefix in sentinel_prefixes:
        if decoded.startswith(prefix):
            # Render the matched prefix's hex inline. Test-sentinel patterns
            # are publicly documented (DEADBEEF / BADC0FFEE), so echoing
            # the prefix is not a G18 violation — it's deliberately recognizable.
            prefix_hex = prefix.hex().upper()
            raise MisconfiguredEnvironmentError(
                f"Refusing to import samantha_server.config: "
                f"{var_name} starts with the test-sentinel prefix "
                f"{prefix_hex}.... This pattern is set by tests/conftest.py "
                f"for the test session only; production deployments must "
                f"generate fresh keys via {gen_hint}."
            )


# --- LLM-related (typed validation via _required_float/_required_int helpers) ---
LLM_PROVIDER: str = os.environ.get("LLM_PROVIDER", "omlx")
LLM_MODEL_PATH: str = os.environ.get(
    "LLM_MODEL_PATH",
    "mlx-community/Llama-3.2-3B-Instruct-4bit",
)
LLM_MODEL_NAME: str = os.environ.get("LLM_MODEL_NAME", LLM_MODEL_PATH)
# H-01: typed errors via the helpers, not bare float()/int() casts.
LLM_TEMPERATURE: float = _required_float("LLM_TEMPERATURE", default=0.0)
LLM_MAX_TOKENS: int = _required_int("LLM_MAX_TOKENS", default=2048)
LLM_JUDGE_MODEL_PATH: str | None = os.environ.get("LLM_JUDGE_MODEL_PATH")

# --- oMLX client config (GH-139) ---
# LLM_OMLX_BASE_URL: loopback HTTP base URL for the oMLX server.
# Validated at import: must be a loopback host (G18 no-leak on errors).
_LLM_OMLX_BASE_URL_RAW: str = os.environ.get("LLM_OMLX_BASE_URL", "http://127.0.0.1:8000")
_validate_loopback_url("LLM_OMLX_BASE_URL", _LLM_OMLX_BASE_URL_RAW)
LLM_OMLX_BASE_URL: str = _LLM_OMLX_BASE_URL_RAW

# LLM_OMLX_AUTH_TOKEN: optional bearer token. Empty string normalizes to None.
# When set but empty, log a WARNING — a copy-paste-and-forget operator gets auth
# silently disabled with no startup signal (fix #11).
_LLM_OMLX_AUTH_TOKEN_RAW: str | None = os.environ.get("LLM_OMLX_AUTH_TOKEN")
if _LLM_OMLX_AUTH_TOKEN_RAW == "":
    _log.warning(
        "LLM_OMLX_AUTH_TOKEN is set but empty; auth disabled. "
        "Unset the variable to silence this warning."
    )
LLM_OMLX_AUTH_TOKEN: str | None = _LLM_OMLX_AUTH_TOKEN_RAW if _LLM_OMLX_AUTH_TOKEN_RAW else None

# LLM_OMLX_TIMEOUT_S: HTTP request timeout in seconds for oMLX calls. Default 120.
LLM_OMLX_TIMEOUT_S: float = _required_float("LLM_OMLX_TIMEOUT_S", default=120.0)

# --- Receipt signing keys (eager validation) ---
_GEN_KEY_HINT = "python -m samantha_server.receipts.signing --gen-key"
RECEIPT_SIGNING_KEY: bytes = _required_hex("RECEIPT_SIGNING_KEY", gen_hint=_GEN_KEY_HINT)
_reject_test_sentinel(
    "RECEIPT_SIGNING_KEY", RECEIPT_SIGNING_KEY, _TEST_KEY_PREFIXES, gen_hint=_GEN_KEY_HINT
)
RECEIPT_SIGNING_KEY_PREVIOUS: bytes | None = _optional_hex(
    "RECEIPT_SIGNING_KEY_PREVIOUS", gen_hint=_GEN_KEY_HINT
)
# M-01: also gate the previous-key slot through the sentinel guard.
# A copy-pasted .env carrying the conftest sentinel into RECEIPT_SIGNING_KEY_PREVIOUS
# would otherwise be silently accepted for the previous-key verification path.
if RECEIPT_SIGNING_KEY_PREVIOUS is not None:
    _reject_test_sentinel(
        "RECEIPT_SIGNING_KEY_PREVIOUS",
        RECEIPT_SIGNING_KEY_PREVIOUS,
        _TEST_KEY_PREFIXES,
        gen_hint=_GEN_KEY_HINT,
    )
RECEIPT_SIGNING_KEY_ID: str = os.environ.get("RECEIPT_SIGNING_KEY_ID", "v1")

# M-03: warn when the key id has been bumped but no previous key is configured.
# Previous-key receipts will return KEY_EXPIRED instead of VALID — audit
# coverage gap. The operator may have intentionally blanked the previous key,
# but it is more likely a rotation-window oversight.
if RECEIPT_SIGNING_KEY_ID != "v1" and RECEIPT_SIGNING_KEY_PREVIOUS is None:
    _log.warning(
        "RECEIPT_SIGNING_KEY_ID=%r but RECEIPT_SIGNING_KEY_PREVIOUS is not set. "
        "Receipts signed under the previous key ID will return KEY_EXPIRED on "
        "verification. Set RECEIPT_SIGNING_KEY_PREVIOUS to retain audit coverage "
        "during the rotation window.",
        RECEIPT_SIGNING_KEY_ID,
    )

# --- Step 5 PHI hash salt (eager validation) ---
_GEN_SALT_HINT = "python -c 'import os; print(os.urandom(32).hex())'"
PHI_HASH_SALT: bytes = _required_hex("PHI_HASH_SALT", gen_hint=_GEN_SALT_HINT)
_reject_test_sentinel("PHI_HASH_SALT", PHI_HASH_SALT, _TEST_SALT_PREFIXES, gen_hint=_GEN_SALT_HINT)

# --- Step 3 scenarios loader toggle ---
SCENARIO_LOADER_TOLERATE_MALFORMED: bool = (
    os.environ.get("SCENARIO_LOADER_TOLERATE_MALFORMED", "false").lower() == "true"
)

# --- Receipt persistence path ---
# Default: ~/.samantha_server/receipts.db (local developer machine).
# Override via RECEIPTS_DB_PATH env var (e.g., in .env or tests/conftest.py).
_DEFAULT_RECEIPTS_DB = str(Path.home() / ".samantha_server" / "receipts.db")
RECEIPTS_DB_PATH: str = os.environ.get("RECEIPTS_DB_PATH", _DEFAULT_RECEIPTS_DB)

# --- Phase 3 orchestrator config ---

# QUEUE_BOUND: max events in the priority queue before overflow rejections begin.
# Reject 0 at config-load: an unbounded queue is a latent OOM; fail-closed.
QUEUE_BOUND: int = _required_int("QUEUE_BOUND", default=256)
if QUEUE_BOUND == 0:
    raise MisconfiguredEnvironmentError(
        "Environment variable 'QUEUE_BOUND' must be > 0. "
        "A queue bound of 0 disables backpressure and risks OOM under load."
    )

# QUEUE_BACKPRESSURE_RETRY_AFTER_SEC: seconds to suggest the caller wait
# before retrying when the queue is full.
QUEUE_BACKPRESSURE_RETRY_AFTER_SEC: int = _required_int(
    "QUEUE_BACKPRESSURE_RETRY_AFTER_SEC", default=5
)

# MAX_REQUEST_BODY_BYTES: maximum allowed request body size in bytes.
# Default: 1 MiB. Requests exceeding this limit are rejected with 413.
MAX_REQUEST_BODY_BYTES: int = _required_int("MAX_REQUEST_BODY_BYTES", default=1048576)

# READYZ_LANGFUSE_PROBE_TTL_SEC: TTL for the cached Langfuse readiness probe.
# The real health-endpoint probe is used when LANGFUSE_ENABLED=true; otherwise a
# stub reports not_configured (probe selected in lifespan.build_app_state).
READYZ_LANGFUSE_PROBE_TTL_SEC: int = _required_int("READYZ_LANGFUSE_PROBE_TTL_SEC", default=5)

# SHUTDOWN_DRAIN_TIMEOUT_SEC: graceful-shutdown drain window in seconds.
# The lifespan waits up to this long for in-flight requests to complete.
SHUTDOWN_DRAIN_TIMEOUT_SEC: int = _required_int("SHUTDOWN_DRAIN_TIMEOUT_SEC", default=30)

# WEB_CONCURRENCY: number of Uvicorn worker processes.
# Must be 1 for v0 (per G2 — single-worker for session-state correctness).
# The lifespan raises MisconfiguredEnvironmentError if > 1.
WEB_CONCURRENCY: int = _required_int("WEB_CONCURRENCY", default=1)

# EVENT_DISPATCH_TIMEOUT_SEC: maximum seconds the POST /events handler waits
# for the consumer to resolve the dispatch future. Protects against a consumer
# crash that leaves the future unresolved indefinitely. Returns 504 on timeout.
EVENT_DISPATCH_TIMEOUT_SEC: int = _required_int("EVENT_DISPATCH_TIMEOUT_SEC", default=60)

# --- Step 5 RBAC + dispatch-token config (GH-119) ---

# Test-sentinel prefixes for RBAC_HMAC_KEY. Mirrors _TEST_KEY_PREFIXES pattern.
# Production deployments must generate fresh keys; the sentinels are recognizable
# as test-only via their leading DEADBEEF or CAFEBABE prefixes.
# CAFEBABE... matches the conftest._TEST_RBAC_KEY sentinel (64 hex = 32 bytes,
# starts "CAFEBABE..."). Including it here means the guard catches a leaked
# conftest key even if it reaches a production .env.
_TEST_RBAC_KEY_PREFIXES: tuple[bytes, ...] = (
    b"\xde\xad\xbe\xef\xde\xad\xbe\xef",
    b"\xca\xfe\xba\xbe\xde\xad\xbe\xef",
)

_GEN_RBAC_KEY_HINT = "python -c 'import os; print(os.urandom(32).hex())'"

# RBAC_HMAC_KEY: 32-byte hex. Required. Used to sign/verify RBAC capability tokens.
RBAC_HMAC_KEY: bytes = _required_hex("RBAC_HMAC_KEY", gen_hint=_GEN_RBAC_KEY_HINT)
_reject_test_sentinel(
    "RBAC_HMAC_KEY",
    RBAC_HMAC_KEY,
    _TEST_RBAC_KEY_PREFIXES,
    gen_hint=_GEN_RBAC_KEY_HINT,
)

# RBAC_TOKEN_TTL_SEC: seconds a capability token is valid. Default 86400 (24h).
# Reject <= 0 at config-load time — a zero or negative TTL is a security mistake.
RBAC_TOKEN_TTL_SEC: int = _required_int("RBAC_TOKEN_TTL_SEC", default=86400)
if RBAC_TOKEN_TTL_SEC <= 0:
    raise MisconfiguredEnvironmentError(
        "Environment variable 'RBAC_TOKEN_TTL_SEC' must be > 0. "
        "A zero or negative TTL would make all RBAC tokens immediately expired."
    )

# DISPATCH_TOKEN_TTL_SEC: seconds a dispatch token is valid. Default 60.
# Reject <= 0 at config-load time.
DISPATCH_TOKEN_TTL_SEC: int = _required_int("DISPATCH_TOKEN_TTL_SEC", default=60)
if DISPATCH_TOKEN_TTL_SEC <= 0:
    raise MisconfiguredEnvironmentError(
        "Environment variable 'DISPATCH_TOKEN_TTL_SEC' must be > 0. "
        "A zero or negative TTL would make all dispatch tokens immediately expired."
    )


# --- Step 9 Langfuse config (GH-124) ---

_TRUE_BOOL_LITERALS: frozenset[str] = frozenset({"true", "1", "yes", "on"})
_FALSE_BOOL_LITERALS: frozenset[str] = frozenset({"false", "0", "no", "off", ""})


def _parse_strict_bool(var_name: str, *, default: bool) -> bool:
    """Parse a boolean env var with strict literal acceptance.

    Accepts ``true`` / ``false`` / ``1`` / ``0`` / ``yes`` / ``no`` /
    ``on`` / ``off`` (case-insensitive, whitespace-trimmed). Anything
    else is a hard error — the previous ``.lower() == "true"`` form
    silently fell to ``False`` for ``"1"`` / ``"yes"`` / ``"on"``,
    common in docker-compose env files (PR #145 review H1).
    """
    raw = os.environ.get(var_name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in _TRUE_BOOL_LITERALS:
        return True
    if normalized in _FALSE_BOOL_LITERALS:
        return False
    raise MisconfiguredEnvironmentError(
        f"Environment variable {var_name!r} has an unrecognized value. "
        "Accepted: true / false / 1 / 0 / yes / no / on / off "
        "(case-insensitive). (Raw value omitted per G18.)"
    )


# LANGFUSE_BASE_URL: client URL for the self-hosted Langfuse instance.
# Default mirrors the docker-compose setup (loopback, port 3000). The
# variable is named BASE_URL (not HOST) for symmetry with the Langfuse
# SDK convention; G10 documents the docker-side bind interface
# (127.0.0.1) which differs from the client URL by design.
#
# PR #145 review M4: validated as a loopback URL via the same helper
# used for LLM_OMLX_BASE_URL. The plan's G10 ("Docker bind interface
# is 127.0.0.1") implies loopback; the validator catches a non-loopback
# misconfiguration at import time rather than letting it become an
# SSRF surface. CWE-918.
_LANGFUSE_BASE_URL_RAW: str = os.environ.get("LANGFUSE_BASE_URL", "http://localhost:3000")
_validate_loopback_url("LANGFUSE_BASE_URL", _LANGFUSE_BASE_URL_RAW)
LANGFUSE_BASE_URL: str = _LANGFUSE_BASE_URL_RAW

# LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY: Basic-auth credentials for
# the OTLP ingest endpoint. Validated below when LANGFUSE_ENABLED=true.
LANGFUSE_PUBLIC_KEY: str = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET_KEY: str = os.environ.get("LANGFUSE_SECRET_KEY", "")

# LANGFUSE_ENABLED: toggle for Langfuse trace export and the /readyz
# Langfuse probe. Defaults true — human-run sessions (CLI replay, the
# server) get tracing without remembering to flip a flag (the lab .env
# already provisions keys). This reverses the original opt-in G9 default;
# fail-loud is preserved — true without keys/endpoint is still startup-fatal
# (validation below). The test suite explicitly pins "false" in
# tests/conftest.py so CI runs without a Langfuse dependency.
LANGFUSE_ENABLED: bool = _parse_strict_bool("LANGFUSE_ENABLED", default=True)

# READYZ_LANGFUSE_PROBE_TIMEOUT_SEC: per-call timeout for the Langfuse
# probe (PR #145 review M5). On loopback, 0.5 s is more than enough;
# operators on slower lab links can raise the knob without code change.
# httpx.Timeout takes float seconds, so we read float-typed.
READYZ_LANGFUSE_PROBE_TIMEOUT_SEC: float = _required_float(
    "READYZ_LANGFUSE_PROBE_TIMEOUT_SEC", default=0.5
)

if LANGFUSE_ENABLED:
    if not LANGFUSE_PUBLIC_KEY:
        raise MisconfiguredEnvironmentError(
            "LANGFUSE_ENABLED=true requires a non-empty LANGFUSE_PUBLIC_KEY. "
            "Provision keys via the Langfuse console; commented placeholders "
            "live in .env.example."
        )
    if not LANGFUSE_SECRET_KEY:
        raise MisconfiguredEnvironmentError(
            "LANGFUSE_ENABLED=true requires a non-empty LANGFUSE_SECRET_KEY. "
            "Provision keys via the Langfuse console; commented placeholders "
            "live in .env.example."
        )
    # PR #145 review M6: trace_url deeplinks point at traces that the
    # OTel exporter never sent unless OTEL_EXPORTER_OTLP_ENDPOINT is set.
    # Refusing the dead-deeplink configuration at startup beats the
    # silent disagreement.
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        raise MisconfiguredEnvironmentError(
            "LANGFUSE_ENABLED=true requires OTEL_EXPORTER_OTLP_ENDPOINT to be "
            "set so the OTel exporter actually sends traces to Langfuse. "
            "Without it, the response's trace_url field would deeplink to "
            "traces that were never exported."
        )


# --- Structured JSON output mode ---
_VALID_LLM_OUTPUT_MODES: frozenset[str] = frozenset({"json", "free_text"})
_SAMANTHA_LLM_OUTPUT_MODE_RAW: str = os.environ.get("SAMANTHA_LLM_OUTPUT_MODE", "json")
if _SAMANTHA_LLM_OUTPUT_MODE_RAW not in _VALID_LLM_OUTPUT_MODES:
    raise MisconfiguredEnvironmentError(
        f"Environment variable 'SAMANTHA_LLM_OUTPUT_MODE' must be one of "
        f"{sorted(_VALID_LLM_OUTPUT_MODES)!r}. "
        f"(Raw value omitted per G18.)"
    )
SAMANTHA_LLM_OUTPUT_MODE: Literal["json", "free_text"] = _SAMANTHA_LLM_OUTPUT_MODE_RAW  # type: ignore[assignment]
if LLM_PROVIDER == "mlx" and SAMANTHA_LLM_OUTPUT_MODE == "json":
    raise MisconfiguredEnvironmentError(
        "LLM_PROVIDER=mlx is incompatible with SAMANTHA_LLM_OUTPUT_MODE=json. "
        "Set SAMANTHA_LLM_OUTPUT_MODE=free_text, or use LLM_PROVIDER=omlx. "
        "See docs/llm/omlx-capabilities.md."
    )


# --- Step 12 Drift-alarm config (GH-127) ---


def _validate_http_url_scheme(var_name: str, raw: str) -> None:
    """Validate that *raw* is an http:// or https:// URL with a host.

    Unlike ``_validate_loopback_url``, the drift webhook is expected
    to be an external endpoint, so loopback is not enforced. The
    scheme check still rejects ``file://`` / ``ftp://`` / etc. at
    startup so a typo cannot fall through to the per-minute
    failure-counter loop. Error messages omit the raw URL (G18).
    """
    parsed = urlsplit(raw)
    if parsed.scheme not in ("http", "https"):
        raise MisconfiguredEnvironmentError(
            f"Environment variable {var_name!r} must use the http:// or https:// scheme. "
            f"(Raw value omitted per G18 no-leak.)"
        )
    if not parsed.hostname:
        raise MisconfiguredEnvironmentError(
            f"Environment variable {var_name!r} must include a host. "
            f"(Raw value omitted per G18 no-leak.)"
        )


# DRIFT_ALARM_WEBHOOK_URL: webhook target the drift monitor POSTs to
# when the rolling-window LLM-call rate exceeds DRIFT_ALARM_THRESHOLD.
# Unset (or empty) disables the alarm cleanly. The monitor still
# maintains the rolling deque so /readyz can surface the current rate
# via degraded JSON. Validated at startup so a typo or non-http scheme
# fails loudly rather than falling into the per-minute failure-counter
# loop.
_DRIFT_ALARM_WEBHOOK_URL_RAW = os.environ.get("DRIFT_ALARM_WEBHOOK_URL") or None
if _DRIFT_ALARM_WEBHOOK_URL_RAW is not None:
    _validate_http_url_scheme("DRIFT_ALARM_WEBHOOK_URL", _DRIFT_ALARM_WEBHOOK_URL_RAW)
DRIFT_ALARM_WEBHOOK_URL: str | None = _DRIFT_ALARM_WEBHOOK_URL_RAW

# DRIFT_ALARM_THRESHOLD: LLM-call-rate threshold above which the webhook
# fires. Default 0.05 (5%) per project-plan target. Phase 3 closeout
# records the empirical adjustment (G12 — threshold tuning is empirical).
# Range-guarded to (0.0, 1.0]: a value > 1.0 makes the alarm permanently
# silent (rate is bounded at 1.0); a value <= 0 makes every check fire.
DRIFT_ALARM_THRESHOLD: float = _required_float("DRIFT_ALARM_THRESHOLD", default=0.05)
if not (0.0 < DRIFT_ALARM_THRESHOLD <= 1.0):
    raise MisconfiguredEnvironmentError(
        f"Environment variable 'DRIFT_ALARM_THRESHOLD' must be in the half-open "
        f"interval (0.0, 1.0]; got {DRIFT_ALARM_THRESHOLD!r}."
    )

# DRIFT_ALARM_WINDOW_SEC: rolling-window length in seconds for the rate
# computation. Default 3600 (1h) per the issue title.
DRIFT_ALARM_WINDOW_SEC: int = _required_int("DRIFT_ALARM_WINDOW_SEC", default=3600)
if DRIFT_ALARM_WINDOW_SEC <= 0:
    raise MisconfiguredEnvironmentError(
        f"Environment variable 'DRIFT_ALARM_WINDOW_SEC' must be > 0; "
        f"got {DRIFT_ALARM_WINDOW_SEC!r}."
    )

# DRIFT_ALARM_CHECK_INTERVAL_SEC: how often the drift task wakes,
# computes the rate, and fires the webhook if over threshold.
# Default 60s — bounds detection latency to ≤ interval seconds and
# webhook firing rate to ≤ 1 per interval.
DRIFT_ALARM_CHECK_INTERVAL_SEC: int = _required_int("DRIFT_ALARM_CHECK_INTERVAL_SEC", default=60)
if DRIFT_ALARM_CHECK_INTERVAL_SEC <= 0:
    raise MisconfiguredEnvironmentError(
        f"Environment variable 'DRIFT_ALARM_CHECK_INTERVAL_SEC' must be > 0; "
        f"got {DRIFT_ALARM_CHECK_INTERVAL_SEC!r}."
    )

# DRIFT_DEQUE_MAXLEN: defensive count cap on the per-priority rolling
# deque. Default 100k — bounds resident memory under pathological flood.
# Time-bound eviction on DRIFT_ALARM_WINDOW_SEC is the primary mechanism;
# the count cap is the belt-and-suspenders for the synthetic-load case
# where time-eviction has not yet run.
DRIFT_DEQUE_MAXLEN: int = _required_int("DRIFT_DEQUE_MAXLEN", default=100_000)
if DRIFT_DEQUE_MAXLEN <= 0:
    raise MisconfiguredEnvironmentError(
        f"Environment variable 'DRIFT_DEQUE_MAXLEN' must be > 0; got {DRIFT_DEQUE_MAXLEN!r}."
    )
