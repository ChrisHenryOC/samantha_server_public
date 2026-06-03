"""Memory pre-flight smoke test for Qwen3-Next-80B-A3B-Instruct-4bit.

Reported resident size on first load is ~44.8 GB, tight against the
64 GB Apple Silicon unified-memory budget once OS + KV cache are added. This
script issues a single small completion against the loopback oMLX
server (which must be configured to serve Qwen3-Next-80B) and reports
system-wide memory pressure plus completion latency.

Usage:
    # psutil is in the `dev` dependency group (not main deps — kept out
    # of production runtime installs). `uv sync` (the default, includes
    # dev) is sufficient. If you've run `uv sync --no-dev`, re-run
    # `uv sync` to install psutil before running this script. GH-275 /
    # PR #280 review.

    set -a && source .env && set +a
    export LLM_MODEL_NAME=mlx-community/Qwen3-Next-80B-A3B-Instruct-4bit  # nosec: model-id
    uv run python -m scripts.qwen3_next_80b_memory_preflight

Threshold contract (GH-275; pre-GH-275 the script measured the wrong
process and the threshold was unit-mismatched):

- **pass** — `psutil.virtual_memory().available > 12 GB` after a single
  completion. Safe to queue the corpus sweep; clean headroom for KV
  cache growth + OS during sustained load.
- **warn** — `8 GB <= available <= 12 GB`. Tight against the OOM ceiling;
  proceed but mark the candidate row `tight` in the results doc so
  reviewers know the budget was close.
- **fail** — `available < 8 GB`. Don't queue the corpus sweep; the
  sustained-load LLM-routed scenarios are likely to OOM under KV
  cache growth.

Exit codes:
    0 — pre-flight pass or warn (candidate sweep is safe / tight-safe to queue).
    1 — pre-flight fail (transport error, empty completion, or memory pressure
        below 8 GB available); mark the candidate row `OOM` / `fail` in the
        results doc and skip its corpus sweep.

Why system-wide and not server-process RSS:
    The oMLX server runs as a separate process at loopback HTTP. Its
    PID changes between deployments and a single-process RSS reading
    wouldn't capture KV-cache pressure from concurrent activity. The
    operational question is: "will the next sweep OOM the system?" —
    answered most reliably by system-wide available memory. The
    pre-GH-275 implementation used `resource.getrusage(RUSAGE_SELF)`
    which measured the Python client's RSS (~40 MB), meaningless for
    the actual decision.

This script intentionally lives outside the test suite — running the
preflight against a live oMLX server is not reproducible in CI. Filed
under scripts/ alongside vendor_scenarios.py. The pure classification
function (`_classify_memory_pressure`) IS unit-tested in
tests/scripts/test_qwen3_next_80b_memory_preflight.py.
"""

from __future__ import annotations

import sys
import time
from typing import Final, Literal

import psutil

from samantha_server.llm.client import LLMResponse
from samantha_server.llm.omlx_client import OMLXClient

# Threshold band (GB available) for system-wide memory pressure after
# completion. See module docstring for the verdict contract.
_FAIL_BELOW_GB: Final[float] = 8.0
_WARN_AT_OR_BELOW_GB: Final[float] = 12.0

# A representative LR-006-shaped prompt — short, schema-constrained,
# triggers chat-template processing without consuming much KV cache.
_PROMPT = 'Return the JSON object {"status": "ok"}. Output ONLY the JSON object, no prose.'


def _system_memory_available_gb() -> float:
    """Return system-wide available memory in gigabytes.

    Wraps `psutil.virtual_memory().available` and converts bytes → GB.
    Returned value is what the OS reports as immediately allocatable
    (free + cached-reclaimable), which is the relevant signal for
    "would the next big allocation succeed?".
    """
    return psutil.virtual_memory().available / (1024**3)


def _classify_memory_pressure(available_gb: float) -> Literal["pass", "warn", "fail"]:
    """Classify the candidate sweep's safety based on available memory.

    Pure function — no I/O, no side effects. Tested independently in
    tests/scripts/test_qwen3_next_80b_memory_preflight.py.

    Returns one of:
        "pass" — available > 12 GB; safe to queue the corpus sweep.
        "warn" — 8 GB <= available <= 12 GB; tight but proceed.
        "fail" — available < 8 GB; don't queue, OOM risk.
    """
    if available_gb < _FAIL_BELOW_GB:
        return "fail"
    if available_gb <= _WARN_AT_OR_BELOW_GB:
        return "warn"
    return "pass"


def main() -> int:
    print("Qwen3-Next-80B-A3B memory pre-flight")
    print("=" * 60)

    # Construct client — this issues a probe to /v1/models, confirming
    # the oMLX server is up and the target model is loaded.
    try:
        client = OMLXClient()
    except Exception as exc:
        print(f"FAIL: OMLXClient construction failed: {type(exc).__name__}: {exc}")
        print("Check that oMLX server is running and the model is loaded.")
        return 1

    print(f"oMLX client constructed; model_id={client.model_id}")
    # PR #280 review #1: wrap psutil call so an OSError / AccessDenied
    # surface a labeled FAIL: message rather than a raw traceback.
    # Operators / monitoring keying on the FAIL: prefix would miss the
    # event otherwise.
    try:
        pre_available = _system_memory_available_gb()
    except Exception as exc:
        print(f"FAIL: psutil.virtual_memory() raised: {type(exc).__name__}: {exc}")
        return 1
    print(f"System memory available (pre-completion): {pre_available:.2f} GB")

    # Single completion to exercise the full inference path. Latency captures
    # cold-start cost; we report it as informational signal even when the
    # memory verdict is the gate.
    start = time.perf_counter()
    try:
        response: LLMResponse = client.complete(_PROMPT)
    except Exception as exc:
        print(f"FAIL: client.complete() raised: {type(exc).__name__}: {exc}")
        return 1
    latency_s = time.perf_counter() - start

    if not response.text:
        print("FAIL: completion returned empty text")
        return 1

    # PR #280 review #1: wrap psutil call (same rationale as above).
    try:
        post_available = _system_memory_available_gb()
    except Exception as exc:
        print(f"FAIL: psutil.virtual_memory() raised: {type(exc).__name__}: {exc}")
        return 1
    print(f"Completion latency: {latency_s:.2f} s")
    print(f"Completion input_tokens: {response.input_tokens}")
    print(f"Completion output_tokens: {response.output_tokens}")
    print(f"System memory available (post-completion): {post_available:.2f} GB")
    print("=" * 60)

    verdict = _classify_memory_pressure(post_available)
    if verdict == "fail":
        print(
            f"FAIL: available memory {post_available:.2f} GB < {_FAIL_BELOW_GB} GB. "
            f"Corpus sweep is likely to OOM under sustained load + KV-cache "
            f"growth. Mark candidate row `fail` / `OOM` in results doc and skip."
        )
        return 1

    if verdict == "warn":
        print(
            f"WARN: available memory {post_available:.2f} GB within tight band "
            f"({_FAIL_BELOW_GB}-{_WARN_AT_OR_BELOW_GB} GB). Corpus sweep can "
            f"proceed but mark candidate row `tight` in results doc — the "
            f"OOM ceiling is close."
        )
        return 0

    print(
        f"PASS: available memory {post_available:.2f} GB > {_WARN_AT_OR_BELOW_GB} GB. "
        f"Candidate corpus sweep is safe to queue."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
