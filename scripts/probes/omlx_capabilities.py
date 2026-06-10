"""Probe oMLX server structured-output capabilities for Phase B.

Reproduces the experiments documented in `docs/llm/omlx-capabilities.md`.
Run with the project's `.env` sourced so `LLM_OMLX_BASE_URL`,
`LLM_OMLX_AUTH_TOKEN`, and `LLM_MODEL_NAME` are present:

    set -a && source .env && set +a
    uv run python scripts/probes/omlx_capabilities.py

Prints one section per probe with the raw payload and round-trip latency.
This is a research / documentation artifact, not a library import path.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

import httpx


def _post_chat(
    *,
    base_url: str,
    token: str,
    model: str,
    messages: list[dict[str, str]],
    response_format: dict[str, Any] | None = None,
    temperature: float = 0.0,
    max_tokens: int = 128,
    timeout_s: float = 60.0,
) -> tuple[float, dict[str, Any]]:
    """POST to /v1/chat/completions; return (latency_s, parsed-json-body)."""
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format is not None:
        payload["response_format"] = response_format
    start = time.perf_counter()
    resp = httpx.post(
        f"{base_url}/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload,
        timeout=timeout_s,
    )
    elapsed = time.perf_counter() - start
    resp.raise_for_status()
    return elapsed, resp.json()


def _content(body: dict[str, Any]) -> str:
    return str(body["choices"][0]["message"]["content"])


def probe_response_format_json_object(base_url: str, token: str, model: str) -> None:
    print("=" * 72)
    print("Probe 1: response_format={'type': 'json_object'} differential")
    print("=" * 72)
    messages = [
        {"role": "system", "content": "You are a helpful assistant. Reply in plain English prose."},
        {
            "role": "user",
            "content": (
                "Write exactly one sentence about the color blue. "
                "Do not use any JSON or curly braces."
            ),
        },
    ]
    elapsed_ctrl, body_ctrl = _post_chat(
        base_url=base_url, token=token, model=model, messages=messages
    )
    print(f"\n[control]  latency={elapsed_ctrl:.3f}s")
    print(f"  output: {_content(body_ctrl)!r}")

    elapsed_jo, body_jo = _post_chat(
        base_url=base_url,
        token=token,
        model=model,
        messages=messages,
        response_format={"type": "json_object"},
    )
    print(f"\n[with response_format=json_object]  latency={elapsed_jo:.3f}s")
    print(f"  output: {_content(body_jo)!r}")
    print("\nInterpretation: if the control returns prose and the json_object")
    print("call returns JSON despite explicit prose-only instructions, the flag")
    print("is honored via grammar-constrained decoding (not accept-and-ignore).")


def probe_response_format_json_schema_strict(base_url: str, token: str, model: str) -> None:
    print("\n" + "=" * 72)
    print("Probe 2: response_format={'type': 'json_schema'} adversarial")
    print("=" * 72)
    messages = [
        {
            "role": "user",
            "content": (
                'Reply with: {"answer": "yes", '
                '"forbidden_field": "I am extra and should be blocked by strict schema"}'
            ),
        }
    ]
    schema = {
        "type": "json_schema",
        "json_schema": {
            "name": "AnswerOnly",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
                "additionalProperties": False,
            },
        },
    }
    elapsed, body = _post_chat(
        base_url=base_url,
        token=token,
        model=model,
        messages=messages,
        response_format=schema,
    )
    print(f"\nlatency={elapsed:.3f}s")
    print(f"  output: {_content(body)!r}")
    print(f"  completion_tokens: {body['usage']['completion_tokens']}")
    print("\nInterpretation: if forbidden_field is absent despite being explicitly")
    print("requested in the user message, the grammar enforced additionalProperties:false")
    print("at decode time — not via post-validation.")


def probe_caching(base_url: str, token: str, model: str) -> None:
    print("\n" + "=" * 72)
    print("Probe 3: Caching behavior")
    print("=" * 72)
    msgs_a = [
        {"role": "system", "content": "You are a pathologist assistant."},
        {
            "role": "user",
            "content": "List exactly three words about specimen accessioning, separated by commas.",
        },
    ]
    msgs_b = [
        {"role": "system", "content": "You are a senior LIS engineer."},
        {
            "role": "user",
            "content": "List exactly three words about specimen accessioning, separated by commas.",
        },
    ]
    print("\n[3a] Repeat-prompt latency (same messages, three times):")
    for i in range(1, 4):
        elapsed, body = _post_chat(
            base_url=base_url, token=token, model=model, messages=msgs_a, max_tokens=32
        )
        print(f"  run {i}: latency={elapsed:.3f}s  output={_content(body)!r}")

    print("\n[3b] Cache-key key-test (different system prompt, same user):")
    elapsed_b, body_b = _post_chat(
        base_url=base_url, token=token, model=model, messages=msgs_b, max_tokens=32
    )
    print(f"  variant B (first): latency={elapsed_b:.3f}s  output={_content(body_b)!r}")
    elapsed_b2, body_b2 = _post_chat(
        base_url=base_url, token=token, model=model, messages=msgs_b, max_tokens=32
    )
    print(f"  variant B (repeat): latency={elapsed_b2:.3f}s  output={_content(body_b2)!r}")
    print("\nInterpretation: if output changes between system A and system B, the")
    print("response cache (if any) keys on the full message stack. A drop of")
    print("a few hundred ms between cold and warm is consistent with a KV-prefix")
    print("cache, not full-response cache (which would return in tens of ms).")


def main() -> int:
    parser = argparse.ArgumentParser(description="oMLX structured-output capability probes.")
    parser.add_argument("--base-url", default=os.environ.get("LLM_OMLX_BASE_URL"))
    parser.add_argument("--token", default=os.environ.get("LLM_OMLX_AUTH_TOKEN"))
    parser.add_argument("--model", default=os.environ.get("LLM_MODEL_NAME"))
    args = parser.parse_args()

    if not args.base_url or not args.token or not args.model:
        print(
            "error: missing one of LLM_OMLX_BASE_URL / LLM_OMLX_AUTH_TOKEN / "
            "LLM_MODEL_NAME. Source .env first or pass --base-url/--token/--model.",
            file=sys.stderr,
        )
        return 2

    print(f"Probing {args.base_url} with model={args.model}\n")
    try:
        probe_response_format_json_object(args.base_url, args.token, args.model)
        probe_response_format_json_schema_strict(args.base_url, args.token, args.model)
        probe_caching(args.base_url, args.token, args.model)
    except httpx.HTTPStatusError as exc:
        print(
            f"\nHTTP error: {exc.response.status_code} {exc.response.text[:500]}", file=sys.stderr
        )
        return 1
    except (httpx.RequestError, json.JSONDecodeError) as exc:
        print(f"\nProbe failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print("\nDone. Update docs/llm/omlx-capabilities.md if findings have changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
