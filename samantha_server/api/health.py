"""Diagnostic HTTP endpoints for the samantha_server FastAPI service.

Three routes:
  GET /healthz  (also HEAD) — process liveness; 200 = process is up.
  GET /readyz   (also HEAD) — two-tier readiness check.
  GET /version             — commit SHA + config snapshot (secrets redacted).

/readyz two-tier model:
  503 — any load-bearing dep failed (engine indices, signing keys, SQLite),
        or the service is draining (post-SIGTERM).
  200 — all load-bearing deps healthy; degraded JSON for non-blocking deps.

/version is unauthenticated in Step 1 and ships with /openapi.json
disabled so the schema doesn't leak (fix-review applying
the plan's spike-fallback floor); Step 5 adds the health:read RBAC gate
and re-enables the schema.
"""

from __future__ import annotations

import logging
import os
import subprocess
from typing import Any

from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import JSONResponse

from samantha_server.api.rbac import require_capability

_log = logging.getLogger(__name__)


def register_health_routes(app: FastAPI) -> None:
    """Attach /healthz, /readyz, and /version routes to *app*."""

    @app.get("/healthz")
    @app.head("/healthz")
    async def healthz() -> Response:
        """Process liveness probe. 200 means the process is running."""
        return Response(status_code=200)

    @app.get("/readyz")
    @app.head("/readyz")
    async def readyz(request: Request) -> Response:
        """Two-tier readiness probe.

        503 when:
          - service is draining (post-SIGTERM lifespan shutdown), OR
          - any load-bearing dep is unhealthy (engine indices missing,
            signing key absent, SQLite connection closed).

        200 with degraded JSON otherwise. The ``components`` block is
        always present and reports the configured state of non-blocking
        dependencies (Langfuse, drift webhook target). The ``counters``
        block is present only when at least one counter is non-zero.
        """
        state = request.app.state.engine

        if state.is_draining:
            return JSONResponse(
                {"status": "draining", "reason": "lifespan shutdown in progress"},
                status_code=503,
            )

        # Load-bearing runtime probes (Step 1 surface; RBAC HMAC check
        # is added by Step 5 when that dep lands).
        import samantha_server.config as cfg

        failures: list[str] = []
        if state.rule_index is None:
            failures.append("rule_index")
        if state.scenario_index is None:
            failures.append("scenario_index")
        if state.skill_index is None:
            failures.append("skill_index")
        # is_open() is a liveness FLAG (closed or write-failed), not a deep
        # connection probe. A dead-but-never-written connection reads True until
        # the first write fails (issue-preferred design).
        if not state.receipt_writer.is_open():
            failures.append("receipt_writer_sqlite")
        if not _signing_key_present():
            failures.append("signing_key")
        # Step 2: Queue is load-bearing. None indicates a partial
        # lifespan crash — should never happen in practice but surfaces
        # immediately rather than letting the consumer silently fail.
        if state.queue is None:
            failures.append("queue")

        if failures:
            return JSONResponse(
                {
                    "status": "unhealthy",
                    "failed_components": sorted(failures),
                },
                status_code=503,
            )

        # Reported but non-blocking deps. review L15: read
        # LANGFUSE_ENABLED from cfg (not live env) so /readyz agrees
        # with the lifespan-bound probe selection — they were drifting
        # before. The cfg attribute is the single source of truth.
        langfuse_enabled = cfg.LANGFUSE_ENABLED
        drift_webhook_configured = bool(os.environ.get("DRIFT_ALARM_WEBHOOK_URL", "").strip())

        # Refresh the cached probe so /readyz reports a current view.
        # current() respects the TTL and single-flight invariants — at most
        # one outbound probe per ttl_sec regardless of /readyz inbound rate.
        probe_value = await state.langfuse_probe.current()
        langfuse_age = state.langfuse_probe.last_probe_age_sec

        components: dict[str, Any] = {
            "langfuse": {
                "enabled": langfuse_enabled,
                "reachable": probe_value.get("reachable", False),
                "last_probe_age_sec": langfuse_age,
            },
            "drift_webhook": {
                "configured": drift_webhook_configured,
                "last_failure_age_sec": None,
            },
        }

        body: dict[str, Any] = {"status": "ok", "components": components}

        snap = state.counters.snapshot()
        if any(
            v > 0
            for k, v in snap.items()
            if k not in ("process_start_unix", "snapshot_unix") and isinstance(v, (int, float))
        ):
            body["counters"] = snap

        return JSONResponse(body, status_code=200)

    @app.get("/version")
    async def version(
        request: Request,
        _rbac: None = Depends(require_capability("health:read")),
    ) -> JSONResponse:
        """Commit SHA and redacted config snapshot.

        Secret values (RECEIPT_SIGNING_KEY, PHI_HASH_SALT, paths, etc.)
        are replaced with '<redacted>'. Names are preserved so operators
        can verify which variables are configured.

        Requires the health:read RBAC capability.
        """
        state = request.app.state.engine
        return JSONResponse(
            {
                "commit": state.commit_sha,
                "config": _build_config_snapshot(),
            }
        )


def _signing_key_present() -> bool:
    """True if RECEIPT_SIGNING_KEY decodes to a non-empty 32-byte value."""
    import samantha_server.config as cfg

    key = cfg.RECEIPT_SIGNING_KEY
    return isinstance(key, bytes) and len(key) == 32


def get_commit_sha() -> str:
    """Return the current git commit SHA, or 'unknown' if unavailable.

    Called once at lifespan startup and cached on AppState; do not call
    per-request (each call forks a subprocess). The tests treat this as
    a public helper.

    Spawn ``git`` with ``OTEL_EXPORTER_OTLP_HEADERS``
    stripped from the inherited env. The Langfuse Basic-auth credential
    lives in that env var when ``LANGFUSE_ENABLED=true`` (set earlier
    in the lifespan); ``git`` does not read or forward the header, but
    architecturally any subprocess we fan out to should not see the
    credential by default. CWE-214.
    """
    clean_env = {k: v for k, v in os.environ.items() if k != "OTEL_EXPORTER_OTLP_HEADERS"}
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
            env=clean_env,
        )
    except (FileNotFoundError, PermissionError, subprocess.TimeoutExpired, OSError):
        return "unknown"
    except Exception:
        # Genuinely unexpected — log so the silent-fallback isn't invisible.
        _log.exception("Unexpected error reading git commit SHA")
        return "unknown"
    if result.returncode != 0:
        return "unknown"
    return result.stdout.strip() or "unknown"


# Variable-name patterns whose values must be redacted from /version.
# - KEY / SALT / SECRET / PASSWORD: cryptographic material.
# - PATH: filesystem paths leak the OS username and directory layout.
#   (RECEIPTS_DB_PATH, SCENARIOS_DIR future entries, etc.)
# Note: TOKEN was removed — KEY/SECRET cover token-key
# variables and TOKEN was over-broad (would catch RBAC_TOKEN_TTL_SEC).
_SECRET_PATTERNS = ("KEY", "SALT", "SECRET", "PASSWORD", "PATH")


def _build_config_snapshot() -> dict[str, Any]:
    """Return a dict of config var names with secret/path values redacted.

    Secret names match _SECRET_PATTERNS. String values that *look like*
    filesystem paths (contain a ``/`` separator) are also redacted —
    catches the LLM_MODEL_NAME-defaults-to-LLM_MODEL_PATH leak case.
    """
    import samantha_server.config as cfg

    config_vars = {
        "LLM_PROVIDER": cfg.LLM_PROVIDER,
        "LLM_MODEL_NAME": cfg.LLM_MODEL_NAME,
        "LLM_TEMPERATURE": cfg.LLM_TEMPERATURE,
        "LLM_MAX_TOKENS": cfg.LLM_MAX_TOKENS,
        "RECEIPT_SIGNING_KEY": cfg.RECEIPT_SIGNING_KEY,
        "RECEIPT_SIGNING_KEY_PREVIOUS": cfg.RECEIPT_SIGNING_KEY_PREVIOUS,
        "RECEIPT_SIGNING_KEY_ID": cfg.RECEIPT_SIGNING_KEY_ID,
        "PHI_HASH_SALT": cfg.PHI_HASH_SALT,
        "SCENARIO_LOADER_TOLERATE_MALFORMED": cfg.SCENARIO_LOADER_TOLERATE_MALFORMED,
        "RECEIPTS_DB_PATH": cfg.RECEIPTS_DB_PATH,
        "QUEUE_BOUND": cfg.QUEUE_BOUND,
        "QUEUE_BACKPRESSURE_RETRY_AFTER_SEC": cfg.QUEUE_BACKPRESSURE_RETRY_AFTER_SEC,
        "MAX_REQUEST_BODY_BYTES": cfg.MAX_REQUEST_BODY_BYTES,
        "READYZ_LANGFUSE_PROBE_TTL_SEC": cfg.READYZ_LANGFUSE_PROBE_TTL_SEC,
        "READYZ_LANGFUSE_PROBE_TIMEOUT_SEC": cfg.READYZ_LANGFUSE_PROBE_TIMEOUT_SEC,
        "LANGFUSE_ENABLED": cfg.LANGFUSE_ENABLED,
        # LANGFUSE_BASE_URL would be redacted by _looks_like_path
        # (contains the URL "/"). Same pre-existing tension affects
        # LLM_OMLX_BASE_URL — out of scope for this PR. Operators
        # diagnosing a Langfuse misconfiguration can read it from
        # their .env directly.
        "SHUTDOWN_DRAIN_TIMEOUT_SEC": cfg.SHUTDOWN_DRAIN_TIMEOUT_SEC,
        "WEB_CONCURRENCY": cfg.WEB_CONCURRENCY,
        # RBAC + dispatch-token config.
        "RBAC_HMAC_KEY": cfg.RBAC_HMAC_KEY,
        "RBAC_TOKEN_TTL_SEC": cfg.RBAC_TOKEN_TTL_SEC,
        "DISPATCH_TOKEN_TTL_SEC": cfg.DISPATCH_TOKEN_TTL_SEC,
    }

    snapshot: dict[str, Any] = {}
    for name, value in config_vars.items():
        if _is_secret(name) or _looks_like_path(value):
            snapshot[name] = "<redacted>"
        else:
            snapshot[name] = value
    return snapshot


def _is_secret(var_name: str) -> bool:
    """True if *var_name* matches a secret-variable pattern."""
    upper = var_name.upper()
    return any(pattern in upper for pattern in _SECRET_PATTERNS)


def _looks_like_path(value: Any) -> bool:
    """True for string values that contain an OS path separator.

    Catches the LLM_MODEL_NAME-defaults-to-LLM_MODEL_PATH leak (
    L6) without requiring an exhaustive list of path-shaped variable
    names. Only string values are inspected; ints / bools / None pass
    through unchanged.
    """
    if not isinstance(value, str):
        return False
    return "/" in value


__all__ = ["get_commit_sha", "register_health_routes"]
