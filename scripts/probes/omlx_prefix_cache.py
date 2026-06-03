"""Probe oMLX for per-call prefix-cache telemetry (GH-294 enabler for GH-288).

Reproduces the findings recorded in docs/llm/omlx-prefix-cache-telemetry.md.
Run with the project's .env sourced so LLM_OMLX_BASE_URL, LLM_OMLX_AUTH_TOKEN,
and LLM_MODEL_NAME are present (LANGFUSE_* are optional — probe 7 skips
without them):

    set -a && source .env && set +a
    uv run python scripts/probes/omlx_prefix_cache.py

Prints one section per probe with the raw payload and round-trip latency.
This is a research / documentation artifact, not a library import path —
parallel to scripts/probes/omlx_capabilities.py.

What this probe answers (and what it does NOT):

  - DOES: enumerate which oMLX HTTP endpoints exist beyond /v1/* (so the
    GH-288 audit knows whether per-call cache telemetry is reachable via
    an admin/metrics API).
  - DOES: dump the full /v1/chat/completions usage block to show whether
    cached_tokens / model_load_duration / time_to_first_token / etc. are
    populated or null.
  - DOES: demonstrate cold-vs-warm latency bimodality so an operator can
    visually confirm the latency proxy hypothesis.
  - DOES: probe the Langfuse REST API to confirm no cache fields land on
    GENERATION observations (closes the downstream side of the
    investigation).
  - DOES NOT: scrape the oMLX admin dashboard (it requires login and a
    session cookie; a probe shouldn't carry write-capable credentials).
  - DOES NOT: probe streaming-mode usage events (samantha_server uses
    non-streaming today; the streaming `stream_options.include_usage`
    field is documented for SSE, not relevant for our call surface).

Exit code: 0 on clean run (including expected-skip on older oMLX versions
that lack newer endpoints); 1 if any probe encountered an UNEXPECTED
error (5xx, 4xx-other-than-404/405, malformed JSON, network failure).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

import httpx

_DISCOVERY_PATHS: tuple[str, ...] = (
    "/metrics",
    "/stats",
    "/v1/stats",
    "/v1/metrics",
    "/health",
    "/version",
    "/openapi.json",
    "/admin",
    "/admin/stats",
    "/admin/api",
    "/admin/metrics",
    "/admin/api/stats",
    "/api/stats",
    "/api/metrics",
)

# Endpoint-absence status codes per RFC: 404 Not Found, 405 Method Not Allowed.
# Distinct from 4xx-other (bad request shape, auth failure) and 5xx (server
# error). Probe 5/6 use this set so a transient 500 doesn't get misreported
# as "endpoint not available on this version."
_ENDPOINT_ABSENT_STATUSES: frozenset[int] = frozenset({404, 405})


def _classify_status_failure(status: int) -> tuple[str, str]:
    """Return (kind, message) for a non-200 status from a probe POST.

    kind: "absent" (endpoint not on this oMLX version — expected skip;
    doesn't fail the probe) or "error" (server fault / bad request —
    fails the probe, contributes to non-zero exit).
    """
    if status in _ENDPOINT_ABSENT_STATUSES:
        return "absent", f"HTTP {status} — endpoint not present on this oMLX version"
    if 400 <= status < 500:
        return "error", f"HTTP {status} — client-side error (bad request shape, auth, etc.)"
    if 500 <= status < 600:
        return "error", f"HTTP {status} — server-side error (likely a real oMLX bug)"
    return "error", f"HTTP {status} — unexpected non-2xx"


def _get(*, base_url: str, token: str, path: str, timeout_s: float = 5.0) -> tuple[int, str]:
    """GET ``base_url + path``; return (status_code, content_type)."""
    resp = httpx.get(
        f"{base_url}{path}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=timeout_s,
    )
    return resp.status_code, resp.headers.get("content-type", "")


def _post_chat(
    *,
    base_url: str,
    token: str,
    model: str,
    messages: list[dict[str, str]],
    max_tokens: int = 8,
    # 120s default accommodates cold model load on the first probe call
    # (oMLX lazy-loads models — first hit on a fresh server can take 60s+).
    # Later probes inherit the timeout but typically complete in <5s warm.
    timeout_s: float = 120.0,
    extra: dict[str, Any] | None = None,
) -> tuple[float, dict[str, Any], dict[str, str]]:
    """POST /v1/chat/completions; return (latency_s, body, response_headers)."""
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.0,
        "max_tokens": max_tokens,
    }
    if extra is not None:
        payload.update(extra)
    start = time.perf_counter()
    resp = httpx.post(
        f"{base_url}/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload,
        timeout=timeout_s,
    )
    elapsed = time.perf_counter() - start
    resp.raise_for_status()
    return elapsed, resp.json(), dict(resp.headers)


def probe_endpoint_discovery(base_url: str, token: str) -> str | None:
    """Probe 1: walk known oMLX endpoint paths; return the oMLX version string."""
    print("=" * 72)
    print("Probe 1: Endpoint discovery — does oMLX expose a metrics/stats surface?")
    print("=" * 72)
    print(f"\n{'Path':<24} {'Status':<8} Content-Type")
    print("-" * 72)
    for path in _DISCOVERY_PATHS:
        try:
            status, content_type = _get(base_url=base_url, token=token, path=path)
        except httpx.RequestError as exc:
            print(f"{path:<24} {'ERR':<8} {type(exc).__name__}")
            continue
        print(f"{path:<24} {status:<8} {content_type}")
    # Capture version + endpoint count from /openapi.json. Cache-field
    # support is version-dependent (per the findings doc: v0.3.8 added
    # populator on /v1/messages; v0.3.9.dev1 added populator on
    # /v1/responses via PR #1008), so anchoring the rest of the probe
    # output to the actual version is load-bearing for interpretation.
    # Use the _get helper so auth header is sent (consistent with the
    # discovery loop above).
    version: str | None = None
    try:
        status, _ = _get(base_url=base_url, token=token, path="/openapi.json")
        if status == 200:
            # _get only returns status + content-type; re-fetch the body for parse.
            resp = httpx.get(
                f"{base_url}/openapi.json",
                headers={"Authorization": f"Bearer {token}"},
                timeout=5.0,
            )
            spec = resp.json()
            info = spec.get("info", {})
            version = info.get("version")
            path_count = len(spec.get("paths", {}))
            print(
                f"\noMLX version: {version or '?'}  "
                f"(title={info.get('title', '?')!r}, endpoints={path_count})"
            )
        else:
            print(f"\noMLX version: unknown — /openapi.json returned HTTP {status}")
    except (httpx.RequestError, json.JSONDecodeError) as exc:
        print(f"\noMLX version: unknown — /openapi.json fetch/parse failed: {type(exc).__name__}")
    print("\nInterpretation: /admin/api/stats is the dashboard's data source for the")
    print("aggregate cache-hit-rate metric (behind admin login). The /admin HTML")
    print("login page confirms the dashboard exists; conventional Prometheus/stats")
    print("paths (404'd) are not exposed on this version.")
    return version


def probe_response_headers_and_usage(base_url: str, token: str, model: str) -> None:
    """Probe 2: dump /v1/chat/completions response headers + usage block."""
    print("\n" + "=" * 72)
    print("Probe 2: /v1/chat/completions — response headers + usage block shape")
    print("=" * 72)
    messages = [{"role": "user", "content": "OK"}]
    elapsed, body, headers = _post_chat(
        base_url=base_url, token=token, model=model, messages=messages, max_tokens=3
    )
    print(f"\nlatency={elapsed:.3f}s")
    print("\n[response headers — looking for x-mlx-*, x-cache-*, etc.]")
    for k, v in headers.items():
        print(f"  {k}: {v}")
    print("\n[response.usage]")
    print(json.dumps(body.get("usage", {}), indent=2))
    print("\nInterpretation: if the usage block contains fields like cached_tokens,")
    print("model_load_duration, time_to_first_token but they are all null, oMLX")
    print("RESERVES schema for these fields but does not POPULATE them on")
    print("non-streaming /v1/chat/completions calls. Compare against probes 5 / 6")
    print("(/v1/messages and /v1/responses) — those endpoints have their own")
    print("usage shapes and their own populator-correctness state per oMLX version.")


def probe_cold_vs_warm_latency(base_url: str, token: str, model: str) -> None:
    """Probe 3: 5 identical short-prompt calls to demonstrate latency bimodality."""
    print("\n" + "=" * 72)
    print("Probe 3: Cold-vs-warm latency bimodality (the latency-proxy signal)")
    print("=" * 72)
    messages = [{"role": "user", "content": "List three colors, comma-separated."}]
    print("\n[5 consecutive identical calls — expect first to be cold, rest warm]")
    latencies: list[float] = []
    for i in range(1, 6):
        elapsed, body, _ = _post_chat(
            base_url=base_url, token=token, model=model, messages=messages, max_tokens=16
        )
        usage = body.get("usage", {})
        text = body["choices"][0]["message"]["content"][:40]
        latencies.append(elapsed)
        print(
            f"  call {i}: latency={elapsed:.3f}s  "
            f"input_tokens={usage.get('input_tokens')} "
            f"output_tokens={usage.get('output_tokens')}  "
            f"output={text!r}"
        )
    if len(latencies) >= 2:
        gap = latencies[0] - min(latencies[1:])
        print(f"\nCold-to-warm gap: {gap:.3f}s")
        print("If gap > 1s, latency is a viable per-call prefix-hit proxy:")
        print("  - High latency ≫ baseline  → likely prefill (cache miss).")
        print("  - Latency ≈ baseline       → likely cache hit (skipped prefill).")
    print("\nThis is the signal GH-288 can use IF no server-side per-call surface exists.")
    print("Caveats: the gap also reflects model-load on the very first call (if the")
    print("model isn't already loaded in oMLX); subsequent cold-but-loaded calls are")
    print("the more representative baseline for cache miss vs hit.")


def probe_prefix_shared_vs_distinct(base_url: str, token: str, model: str) -> None:
    """Probe 4: long-system-prompt latency bimodality on shared vs distinct prefixes."""
    print("\n" + "=" * 72)
    print("Probe 4: Shared-prefix vs distinct-prefix latency")
    print("=" * 72)
    long_prefix = (
        "You are a senior pathology informatics engineer. "
        "Answer concisely. "
        "Context: this question is about routine specimen accessioning workflows. "
        "Background: laboratories receive specimens via courier; intake creates an "
        "order record; the LIS validates specimen type, fixative, fixation time, "
        "and patient demographics before generating accession numbers. "
    )
    msg_a = [
        {"role": "system", "content": long_prefix},
        {"role": "user", "content": "Q1: name one common fixative."},
    ]
    msg_b = [
        {"role": "system", "content": long_prefix},
        {"role": "user", "content": "Q2: name one common anatomic site."},
    ]
    msg_c = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Name one common fixative."},
    ]
    print("\n[shared long system prompt, different user — second should hit prefix cache]")
    for label, msgs in (("A", msg_a), ("B", msg_b), ("A-repeat", msg_a)):
        elapsed, body, _ = _post_chat(
            base_url=base_url, token=token, model=model, messages=msgs, max_tokens=16
        )
        text = body["choices"][0]["message"]["content"][:40]
        print(f"  variant {label}: latency={elapsed:.3f}s  output={text!r}")
    print("\n[short distinct system prompt — must repay prefill]")
    elapsed_c, body_c, _ = _post_chat(
        base_url=base_url, token=token, model=model, messages=msg_c, max_tokens=16
    )
    text_c = body_c["choices"][0]["message"]["content"][:40]
    print(f"  variant C: latency={elapsed_c:.3f}s  output={text_c!r}")
    print("\nInterpretation: B is faster than A and C means the prefix-cache is")
    print("working. A-repeat being similar to B confirms the cache persists across")
    print("calls. Differences are the empirical lever GH-288 audit will exploit by")
    print("reordering prompts to maximize byte-identical leading prefixes.")


def probe_anthropic_messages_endpoint(base_url: str, token: str, model: str) -> bool:
    """Probe 5: /v1/messages cache_creation/cache_read fields, with cache_control.

    Returns True on success (or expected endpoint-absent skip on older oMLX);
    False on an unexpected error (5xx, malformed JSON, network failure).

    Note on labels: this probe runs after probes 3 and 4, which warm oMLX's
    KV cache with their own prompts. The "first call" label below means
    "first call in THIS probe's loop" — KV state is inherited from earlier
    probes, not truly cold. Cache-field populator correctness is independent
    of KV-cold state; the latency comparison is approximate.
    """
    print("\n" + "=" * 72)
    print("Probe 5: /v1/messages (Anthropic-compat) — cache_creation/cache_read fields")
    print("=" * 72)
    long_system = (
        "You are a senior pathology informatics engineer. Answer concisely. "
        "Context: this question is about routine specimen accessioning workflows. "
        "Background: laboratories receive specimens via courier; intake creates an "
        "order record; the LIS validates specimen type, fixative, fixation time, "
        "and patient demographics before generating accession numbers."
    )
    body_with_cc = {
        "model": model,
        "system": [{"type": "text", "text": long_system, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": "Name one common fixative."}],
        "max_tokens": 16,
    }
    # CONTROL: identical request but no cache_control marker on the system block.
    # The Anthropic spec says cache_* fields should report 0 here. If oMLX
    # reports the same non-zero value as the cache_control'd request, the
    # populator is misnamed/broken — it's reporting prompt size, not cache
    # behavior. (This is exactly what we found on oMLX v0.3.8.)
    body_no_cc = {
        "model": model,
        "system": long_system,
        "messages": [{"role": "user", "content": "Name one common fixative."}],
        "max_tokens": 16,
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    captured: list[tuple[str, float, dict[str, Any]]] = []
    for label, body in (
        ("first call WITH cache_control (state inherited from probes 3+4)", body_with_cc),
        ("second call WITH cache_control (cache hit expected)", body_with_cc),
        ("CONTROL: identical prompt WITHOUT cache_control", body_no_cc),
    ):
        try:
            start = time.perf_counter()
            resp = httpx.post(f"{base_url}/v1/messages", headers=headers, json=body, timeout=120.0)
            elapsed = time.perf_counter() - start
        except httpx.RequestError as exc:
            print(f"\n[{label}] Probe 5 network error: {type(exc).__name__}: {exc}")
            return False
        if resp.status_code != 200:
            kind, msg = _classify_status_failure(resp.status_code)
            print(f"\n[{label}] {msg}")
            # Older oMLX without /v1/messages → "absent" = expected skip;
            # "error" (5xx, 4xx-other) → failure that contributes to non-zero exit.
            return kind == "absent"
        try:
            usage = resp.json().get("usage", {})
        except json.JSONDecodeError as exc:
            print(f"\n[{label}] Probe 5 (/v1/messages) JSON parse failed: {exc}")
            return False
        print(f"\n[{label}] latency={elapsed:.3f}s")
        print(f"  usage: {json.dumps(usage)}")
        captured.append((label, elapsed, usage))

    print("\nInterpretation — populator correctness check:")
    print("  A working populator shows the DIFFERENTIAL across the three calls:")
    print("    - call 1 (cold prefix WITH cache_control): cache_creation_input_tokens > 0,")
    print("      cache_read_input_tokens == 0.")
    print("    - call 2 (warm prefix WITH cache_control): cache_creation_input_tokens drops")
    print("      toward 0, cache_read_input_tokens > 0 (matches the prefix size).")
    print("    - CONTROL (WITHOUT cache_control): BOTH cache fields == 0 (no caching")
    print("      requested, so no cache events to report).")
    print("  A broken populator shows the SAME values across all three calls — the field")
    print("  is reporting prompt-token count regardless of cache_control or warm/cold")
    print("  state. This is the v0.3.8 behavior documented in the findings doc.")
    return True


def probe_responses_endpoint(base_url: str, token: str, model: str) -> bool:
    """Probe 6: /v1/responses cached_tokens field.

    Returns True on success (or expected endpoint-absent skip on older oMLX);
    False on unexpected error (5xx, malformed JSON, network failure).
    """
    print("\n" + "=" * 72)
    print("Probe 6: /v1/responses (OpenAI Responses API) — cached_tokens field")
    print("=" * 72)
    body = {"model": model, "input": "OK", "max_output_tokens": 3}
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        start = time.perf_counter()
        resp = httpx.post(f"{base_url}/v1/responses", headers=headers, json=body, timeout=120.0)
        elapsed = time.perf_counter() - start
    except httpx.RequestError as exc:
        print(f"\nProbe 6 network error: {type(exc).__name__}: {exc}")
        return False
    if resp.status_code != 200:
        kind, msg = _classify_status_failure(resp.status_code)
        print(f"\n{msg}")
        # "absent" = endpoint not on this oMLX version (expected skip on older builds);
        # "error" = real failure that contributes to non-zero exit.
        return kind == "absent"
    try:
        usage = resp.json().get("usage", {})
    except json.JSONDecodeError as exc:
        print(f"\nProbe 6 (/v1/responses) JSON parse failed: {exc}")
        return False
    print(f"\nlatency={elapsed:.3f}s")
    print(f"  usage: {json.dumps(usage, indent=2)}")
    print("\nInterpretation: on oMLX v0.3.9.dev1+, usage should include an")
    print("`input_tokens_details` block with `cached_tokens`. If the block is absent")
    print("entirely, the populator predates PR #1008 (i.e., < v0.3.9.dev1).")
    return True


def probe_langfuse_surface_check(
    base_url: str | None, public_key: str | None, secret_key: str | None
) -> bool:
    """Probe 7: Langfuse REST API check — confirm no cache fields appear in observations.

    Returns True on clean run (including expected-skip when Langfuse env is
    not configured); False on unexpected error.

    Langfuse is a downstream consumer of samantha_server's spans. Even if
    oMLX's HTTP API exposes no cache data (probes 2/5/6), Langfuse could in
    principle render `usage_details.cache_*` keys if our stamping code
    emitted them. Sampling recent GENERATION observations confirms the
    flow is one-way blocked at the oMLX boundary.
    """
    print("\n" + "=" * 72)
    print("Probe 7: Langfuse surface check — any cache fields downstream?")
    print("=" * 72)
    if not base_url or not public_key or not secret_key:
        print("\nLANGFUSE_BASE_URL / LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY not set —")
        print("skipping. (Expected on hosts without a local Langfuse; not an error.)")
        return True

    try:
        resp = httpx.get(
            f"{base_url}/api/public/observations?limit=20&type=GENERATION",
            auth=(public_key, secret_key),
            timeout=10.0,
        )
    except httpx.RequestError as exc:
        print(f"\nLangfuse request failed: {type(exc).__name__}: {exc}")
        return False
    if resp.status_code != 200:
        print(
            f"\nLangfuse returned HTTP {resp.status_code} — "
            f"check Langfuse is reachable + creds valid."
        )
        return False
    try:
        data = resp.json()
    except json.JSONDecodeError as exc:
        print(f"\nLangfuse response JSON parse failed: {exc}")
        return False

    observations: list[dict[str, Any]] = data.get("data", [])
    field_set: set[str] = set()
    cache_mentions: int = 0
    for obs in observations:
        for key in obs.get("usageDetails") or {}:
            field_set.add(f"usageDetails.{key}")
        for key in obs.get("metadata", {}).get("attributes") or {}:
            field_set.add(f"meta.attr.{key}")
        if "cache" in json.dumps(obs).lower():
            cache_mentions += 1

    print(f"\nobservations sampled: {len(observations)}")
    print(f"cache_mentions across all serialized fields: {cache_mentions}")
    print("unique fields observed:")
    for key in sorted(field_set):
        print(f"  {key}")
    print("\nInterpretation: zero cache_mentions confirms the flow is one-way blocked")
    print("at the oMLX → samantha_server boundary. Langfuse would render any")
    print("`usage_details.cache_*` keys if we sent them; we don't send them because")
    print("oMLX (per probes 5/6) doesn't expose them in a populator-correct way on")
    print("this version. Non-zero cache_mentions here would mean the boundary is")
    print("leakier than the doc claims — investigate the source.")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="oMLX prefix-cache telemetry probes (GH-294).")
    parser.add_argument("--base-url", default=os.environ.get("LLM_OMLX_BASE_URL"))
    parser.add_argument("--token", default=os.environ.get("LLM_OMLX_AUTH_TOKEN"))
    parser.add_argument("--model", default=os.environ.get("LLM_MODEL_NAME"))
    parser.add_argument("--langfuse-base-url", default=os.environ.get("LANGFUSE_BASE_URL"))
    parser.add_argument("--langfuse-public-key", default=os.environ.get("LANGFUSE_PUBLIC_KEY"))
    parser.add_argument("--langfuse-secret-key", default=os.environ.get("LANGFUSE_SECRET_KEY"))
    args = parser.parse_args()

    if not args.base_url or not args.token or not args.model:
        print(
            "error: missing one of LLM_OMLX_BASE_URL / LLM_OMLX_AUTH_TOKEN / "
            "LLM_MODEL_NAME. Source .env first or pass --base-url/--token/--model.",
            file=sys.stderr,
        )
        return 2

    print(f"Probing {args.base_url} with model={args.model}\n")
    probes_ok: list[bool] = []
    try:
        probe_endpoint_discovery(args.base_url, args.token)  # always-True via prints
        probe_response_headers_and_usage(args.base_url, args.token, args.model)
        probe_cold_vs_warm_latency(args.base_url, args.token, args.model)
        probe_prefix_shared_vs_distinct(args.base_url, args.token, args.model)
        probes_ok.append(probe_anthropic_messages_endpoint(args.base_url, args.token, args.model))
        probes_ok.append(probe_responses_endpoint(args.base_url, args.token, args.model))
        probes_ok.append(
            probe_langfuse_surface_check(
                args.langfuse_base_url, args.langfuse_public_key, args.langfuse_secret_key
            )
        )
    except httpx.HTTPStatusError as exc:
        print(
            f"\nHTTP error: {exc.response.status_code} {exc.response.text[:500]}",
            file=sys.stderr,
        )
        return 1
    except (httpx.RequestError, json.JSONDecodeError) as exc:
        print(f"\nProbe failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if all(probes_ok):
        print("\nDone. Update docs/llm/omlx-prefix-cache-telemetry.md if findings have changed.")
        return 0
    failed = sum(1 for ok in probes_ok if not ok)
    print(
        f"\nDone with {failed} probe(s) in UNEXPECTED-ERROR state. Re-read the probe output "
        f"above for the failing section and update docs accordingly.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
