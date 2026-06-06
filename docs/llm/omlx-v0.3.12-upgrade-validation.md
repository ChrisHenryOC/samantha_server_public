# oMLX v0.3.9 to v0.3.12 upgrade validation (GH-352)

**Date:** 2026-05-28
**Box:** lab/CI host (64 GiB unified memory, `iogpu.wired_limit_mb` unset)
**Server:** `oMLX` at `http://127.0.0.1:8000` (loopback, bearer auth)
**Baseline model:** `Qwen3-Next-80B-A3B-Instruct-4bit`
**Install:** editable checkout of oMLX, branch `local/customizations-v0.3.12` (off tag `v0.3.12`)

The 0.3.9 adoption was driven by memory stability under high-context
load, and the 0.3.9 to 0.3.12 range is dominated by that same work
(dynamic memory-guard tiers, per-request MLX cache clearing, adaptive
prefill throttle). This document records the upgrade and its validation
against the locked corpus baseline.

## Summary

| Acceptance criterion | Result |
|---|---|
| oMLX reports v0.3.12, `/health` healthy, 5 models | Pass |
| `settings.json` `memory` block migrated to tier schema, no stale keys | Pass |
| Corpus replay holds at locked 149/149 stable (N=5) | Pass (149/149 stable, 745/745 raw) |
| No memory-stability regression under high-context 80B | Pass (no eviction, throttle, abort, or panic) |
| GH-288 reassessed via post-upgrade `cached_tokens` probe | Still blocked (expected) |

## Settings schema migration

v0.3.10+ drops `max_process_memory` / `max_model_memory` and adds
`memory_guard_tier` (default `balanced`), `memory_guard_custom_ceiling_gb`,
`prefill_safe_zone_ratio` (0.80), and `prefill_min_chunk_tokens` (32).
`MemorySettings.from_dict` reads each key with a default, so the stale
keys are ignored on load and the block is rewritten on the next save.

The migration was applied through the production code path
(`GlobalSettings.load().save()`) after a zero-risk dry-run diff confirmed
the only changes were the two dropped keys and the four added tier keys,
with `prefill_memory_guard` / `soft_threshold` / `hard_threshold`
preserved and every other settings section byte-identical.
`~/.omlx/settings.json.bak-v0.3.9` holds the pre-migration file.

## Corpus re-baseline (step 9)

N=5 full-corpus sweep against the vendored corpus
(`tests/fixtures/scenarios/`, 149 total scenarios) pinned to the
baseline 80B model:

```text
N-sweep summary (N=5)
Stable accuracy:  149/149 (100.00%)
Raw accuracy:     745/745 (100.00%)
```

Every individual sweep cleared 149/149. This matches the locked
2026-05-16 baseline exactly, with no flaky fixtures. (The report's
per-sweep "Included accuracy" line reads 143/143: that denominator is
the 149 total minus the 6 skiplisted / out-of-bucket scenarios. Both
the included 143/143 and the overall 149/149 are 100%, so the headline
149/149 stable figure is unaffected.)

**Corpus-path note.** The baseline is the vendored corpus at
`tests/fixtures/scenarios/`, which the `/replay-scenarios` command
targets. It is not `$SAMANTHA_POC_CORPUS_PATH`: that env var points at
the separate
public parity-discovery corpus, which ships older ground truth (expects
pre-ACC-010/011/012 outcomes), enumerates fewer scenarios, and lacks the
`llm_review` category. Replaying the baseline against the public corpus
produces a misleading sub-100 result with zero LLM calls. Use the
vendored corpus for baseline validation.

## Memory stability (step 10)

The startup log emits the predicted clamp warning, since
`iogpu.wired_limit_mb` is unset on this box:

```text
Metal cap (51.8GB, Apple max_recommended_working_set_size) is below the
oMLX static ceiling (56.0GB); Metal will clamp allocations to the cap
and panic if a request exceeds it.
```

Across the full N=5 run (745 LLM-exercising scenario executions,
including the `llm_review` and `query` categories), the server log
contains exactly one memory-guard line: the startup clamp warning. There
were zero LRU evictions, prefill throttles or aborts, active-memory
reclaims, OOMs, or Metal panics. Loaded-model memory held flat at about
47.1 GB, roughly 4.7 GB under the 51.8 GB Metal clamp, even during the
multi-step and LLM-routed scenarios.

**Conclusion on the clamp warning.** Raising
`iogpu.wired_limit_mb` is not necessary for this workload. The 80B-A3B-4bit
model at this context peaks well under the 51.8 GB clamp, so the
`balanced` tier is sufficient. On a 64 GiB box, wiring 56 GB to the GPU
would leave only about 8 GB for the rest of the system, so Apple's
conservative default is the safer choice unless a future workload
approaches the clamp. Tuning the tier is out of scope per GH-352.

## GH-288 reassessment

GH-288 (oMLX prefix-cache telemetry) stays blocked. Two identical
659-token-prefix calls back to back both report
`usage.prompt_tokens_details.cached_tokens = 0`, and the `/v1/responses`
endpoint exists but does not populate cached-token telemetry either. No
commit in the 0.3.9 to 0.3.12 range touches the populator, so the
upgrade does not unblock GH-288, as expected.

## Rollback

Non-destructive and verified during this validation (the rollback path
was exercised as part of root-causing the corpus-path confusion):
`git checkout local/customizations` (8cad121 == v0.3.9),
`uv pip install -e .` in the omlx venv, restart. `settings.json` is
forward/backward tolerant (v0.3.9's `from_dict` ignores the new keys),
and `settings.json.bak-v0.3.9` is the belt-and-suspenders restore.
