"""Slice 4: LangfuseClient httpx wrapper with auth."""

from __future__ import annotations

import base64

import httpx
import pytest

FAKE_HOST = "http://langfuse.test"
FAKE_PUBLIC = "pk-test-public"
FAKE_SECRET = "sk-test-secret"

FAKE_TRACES_RESPONSE = {
    "data": [
        {"id": "trace-001", "name": "run-1", "timestamp": "2026-05-01T00:00:00Z"},
        {"id": "trace-002", "name": "run-2", "timestamp": "2026-05-02T00:00:00Z"},
    ],
    "meta": {"totalItems": 2, "page": 1},
}


def _make_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/public/traces":
            return httpx.Response(200, json=FAKE_TRACES_RESPONSE)
        return httpx.Response(404, json={"error": "not found"})

    return httpx.MockTransport(handler)


def test_fetch_traces_returns_list() -> None:
    from samantha_charts.data.langfuse_client import LangfuseClient

    client = LangfuseClient(
        host=FAKE_HOST,
        public_key=FAKE_PUBLIC,
        secret_key=FAKE_SECRET,
        transport=_make_transport(),
    )
    traces = client.fetch_traces()

    assert isinstance(traces, list)
    assert len(traces) == 2
    assert traces[0]["id"] == "trace-001"


def test_fetch_traces_sends_basic_auth() -> None:
    from samantha_charts.data.langfuse_client import LangfuseClient

    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=FAKE_TRACES_RESPONSE)

    client = LangfuseClient(
        host=FAKE_HOST,
        public_key=FAKE_PUBLIC,
        secret_key=FAKE_SECRET,
        transport=httpx.MockTransport(handler),
    )
    client.fetch_traces()

    assert len(captured) == 1
    auth_header = captured[0].headers.get("authorization", "")
    assert auth_header.startswith("Basic ")

    decoded = base64.b64decode(auth_header[6:]).decode()
    assert decoded == f"{FAKE_PUBLIC}:{FAKE_SECRET}"


def test_fetch_traces_respects_limit() -> None:
    from samantha_charts.data.langfuse_client import LangfuseClient

    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=FAKE_TRACES_RESPONSE)

    client = LangfuseClient(
        host=FAKE_HOST,
        public_key=FAKE_PUBLIC,
        secret_key=FAKE_SECRET,
        transport=httpx.MockTransport(handler),
    )
    client.fetch_traces(limit=50)

    assert len(captured) == 1
    params = dict(captured[0].url.params)
    assert params.get("limit") == "50"


def test_fetch_traces_with_filters() -> None:
    from samantha_charts.data.langfuse_client import LangfuseClient

    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=FAKE_TRACES_RESPONSE)

    client = LangfuseClient(
        host=FAKE_HOST,
        public_key=FAKE_PUBLIC,
        secret_key=FAKE_SECRET,
        transport=httpx.MockTransport(handler),
    )
    client.fetch_traces(filters={"userId": "user-1"})

    params = dict(captured[0].url.params)
    assert params.get("userId") == "user-1"


def test_constructor_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from samantha_charts.data.langfuse_client import LangfuseClient

    monkeypatch.setenv("LANGFUSE_HOST", FAKE_HOST)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", FAKE_PUBLIC)
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", FAKE_SECRET)

    client = LangfuseClient(transport=_make_transport())

    assert client.host == FAKE_HOST
    assert client.public_key == FAKE_PUBLIC
    assert client.secret_key == FAKE_SECRET


def test_constructor_missing_env_raises_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from samantha_charts.data.langfuse_client import LangfuseClient, LangfuseConfigError

    monkeypatch.delenv("LANGFUSE_HOST", raising=False)
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)

    with pytest.raises(LangfuseConfigError, match="LANGFUSE_HOST"):
        LangfuseClient()


def test_constructor_partial_env_raises_config_error_on_missing_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Host set but public_key missing → diagnostic targets the missing var.

    Without the var-by-var check, the next assignment's KeyError would
    surface instead, misleading the operator about which var is wrong.
    """
    from samantha_charts.data.langfuse_client import LangfuseClient, LangfuseConfigError

    monkeypatch.setenv("LANGFUSE_HOST", FAKE_HOST)
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", FAKE_SECRET)

    with pytest.raises(LangfuseConfigError, match="LANGFUSE_PUBLIC_KEY"):
        LangfuseClient()


def test_repr_masks_credentials() -> None:
    """A traceback's locals frame must not leak the bearer credentials."""
    from samantha_charts.data.langfuse_client import LangfuseClient

    client = LangfuseClient(
        host=FAKE_HOST,
        public_key=FAKE_PUBLIC,
        secret_key=FAKE_SECRET,
        transport=_make_transport(),
    )
    rendered = repr(client)
    assert FAKE_HOST in rendered
    assert FAKE_PUBLIC not in rendered
    assert FAKE_SECRET not in rendered


def test_fetch_traces_raises_on_missing_data_key() -> None:
    """A 2xx with envelope missing 'data' must surface a typed error, not KeyError."""
    from samantha_charts.data.langfuse_client import LangfuseClient, LangfuseResponseError

    def handler(request: httpx.Request) -> httpx.Response:
        # Spec-compliant 2xx but no 'data' (e.g., rate-limit info envelope).
        return httpx.Response(200, json={"meta": {"rate_limited": True}})

    client = LangfuseClient(
        host=FAKE_HOST,
        public_key=FAKE_PUBLIC,
        secret_key=FAKE_SECRET,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(LangfuseResponseError, match="missing 'data'"):
        client.fetch_traces()


def test_fetch_traces_raises_on_non_json_body() -> None:
    """A 2xx whose body isn't JSON (e.g., proxy HTML error page) must raise a typed error."""
    from samantha_charts.data.langfuse_client import LangfuseClient, LangfuseResponseError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>upstream proxy error</html>")

    client = LangfuseClient(
        host=FAKE_HOST,
        public_key=FAKE_PUBLIC,
        secret_key=FAKE_SECRET,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(LangfuseResponseError, match="not valid JSON"):
        client.fetch_traces()


def test_fetch_traces_raises_when_data_is_not_a_list() -> None:
    """If 'data' is present but not a list (e.g., schema drift), error explicitly."""
    from samantha_charts.data.langfuse_client import LangfuseClient, LangfuseResponseError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"id": "trace-001"}})  # dict not list

    client = LangfuseClient(
        host=FAKE_HOST,
        public_key=FAKE_PUBLIC,
        secret_key=FAKE_SECRET,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(LangfuseResponseError, match="not a list"):
        client.fetch_traces()
