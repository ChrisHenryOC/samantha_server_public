"""httpx wrapper for the Langfuse public API.

Reads credentials from environment variables (LANGFUSE_HOST,
LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY) with optional constructor
overrides. Auth is HTTP Basic with base64-encoded public:secret.
"""

from __future__ import annotations

import base64
import os
from typing import Any

import httpx


class LangfuseConfigError(ValueError):
    """Raised when required Langfuse configuration is missing or malformed."""


class LangfuseResponseError(ValueError):
    """Raised when a 2xx Langfuse response has an unexpected envelope shape."""


_ENV_VARS = ("LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise LangfuseConfigError(
            f"{name} environment variable is not set. "
            "Source samantha_server's .env (set -a && source .env && set +a) "
            "or pass the value via the LangfuseClient constructor."
        )
    return value


class LangfuseClient:
    """Thin httpx wrapper for Langfuse public API endpoints.

    Parameters
    ----------
    host:
        Base URL of the Langfuse server. Defaults to the LANGFUSE_HOST
        environment variable.
    public_key:
        Langfuse public key. Defaults to LANGFUSE_PUBLIC_KEY env var.
    secret_key:
        Langfuse secret key. Defaults to LANGFUSE_SECRET_KEY env var.
    transport:
        Optional httpx transport override (used in tests for mocking).

    Raises
    ------
    LangfuseConfigError
        If any of the three credentials cannot be resolved (constructor
        arg empty AND env var missing/empty).
    """

    def __init__(
        self,
        host: str | None = None,
        public_key: str | None = None,
        secret_key: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.host = host or _require_env("LANGFUSE_HOST")
        self.public_key = public_key or _require_env("LANGFUSE_PUBLIC_KEY")
        self.secret_key = secret_key or _require_env("LANGFUSE_SECRET_KEY")
        self._transport = transport

    def __repr__(self) -> str:
        # Mask credentials so a traceback's locals frame doesn't leak them.
        return f"LangfuseClient(host={self.host!r}, public_key=..., secret_key=...)"

    def _auth_header(self) -> str:
        credentials = base64.b64encode(f"{self.public_key}:{self.secret_key}".encode()).decode()
        return f"Basic {credentials}"

    def _client(self) -> httpx.Client:
        kwargs: dict[str, Any] = {
            "base_url": self.host,
            "headers": {"Authorization": self._auth_header()},
        }
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.Client(**kwargs)

    def fetch_traces(
        self,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Fetch traces from /api/public/traces.

        Parameters
        ----------
        filters:
            Optional query-string parameters to pass to the endpoint
            (e.g., ``{"userId": "u-1", "name": "run-x"}``).
        limit:
            Maximum number of traces to return.

        Returns
        -------
        list[dict]
            The ``data`` array from the Langfuse response.

        Raises
        ------
        httpx.HTTPStatusError
            On non-2xx response.
        LangfuseResponseError
            On a 2xx response whose body isn't JSON or whose envelope
            doesn't contain a ``data`` array (rate-limit envelope, HTML
            error page, schema drift).
        """
        params: dict[str, Any] = {"limit": limit}
        if filters:
            params.update(filters)

        with self._client() as client:
            response = client.get("/api/public/traces", params=params)
            response.raise_for_status()
            try:
                payload = response.json()
            except ValueError as exc:
                # httpx wraps JSONDecodeError as ValueError; ours rewraps with context.
                raise LangfuseResponseError(
                    "Langfuse /api/public/traces 2xx response body was not valid JSON. "
                    "Possible causes: rate-limit HTML envelope, proxy error page, or schema drift."
                ) from exc
            if not isinstance(payload, dict) or "data" not in payload:
                raise LangfuseResponseError(
                    "Langfuse /api/public/traces 2xx response envelope missing 'data' key. "
                    f"Got top-level keys: {sorted(payload.keys()) if isinstance(payload, dict) else type(payload).__name__}"
                )
            data = payload["data"]
            if not isinstance(data, list):
                raise LangfuseResponseError(
                    f"Langfuse /api/public/traces 'data' was not a list (got {type(data).__name__})."
                )
            return data
