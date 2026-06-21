# Installing samantha_server from scratch

This guide takes you from a bare machine to a running `samantha_server` in
tiers. Each tier builds on the previous one, and you can stop at whichever
tier matches what you want to do.

This repository is a reference architecture meant to be read as much as run.
**Tier 1 (the deterministic replay) runs anywhere Python and `uv` run.** The
LLM path and the development gate (Tiers 2 and 3) need Apple Silicon and extra
services, so they are clearly marked optional.

## What each tier gives you

| Tier | You get | Requirements beyond Tier 0 |
|------|---------|-----------------------------|
| 0 | Source tree + dependencies installed | `git`, Python 3.12+, [`uv`](https://docs.astral.sh/uv/) |
| 1 | The deterministic rule engine, replayed over the vendored corpus (no LLM) | none |
| 2 | The full hybrid path, including the local-LLM chokepoint | Apple Silicon, macOS 15+, a running oMLX server + a model |
| 3 | The maintainer development gate (lint, types, coverage, secret scan) and optional tracing | Homebrew, `npm`; optionally a Langfuse instance |

> **Why secrets are needed even for Tier 1.** `samantha_server` validates its
> signing and hashing keys *at import time*. Any code path that imports the
> config, including the no-LLM replay, fails fast with
> `MisconfiguredEnvironmentError` if those keys are missing. Tier 1 therefore
> still requires the three secrets in step 2 below (but no LLM and no Langfuse).

## Prerequisites

All tiers:

- **`git`**.
- **Python 3.12 or newer** (`requires-python = ">=3.12"`).
- **[`uv`](https://docs.astral.sh/uv/)** — the project's package and environment
  manager. Install it with the official one-liner:

  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```

Tier 2 adds: **Apple Silicon (M1 or newer)** and **macOS 15.0+ (Sequoia)** — required
by the local inference server (oMLX). Tier 3 adds: **Homebrew** and **`npm`**
(for the `gitleaks` and `markdownlint-cli2` gate tools), and optionally a
**Langfuse** instance for tracing.

## Tier 0 — Clone and install dependencies

```bash
git clone https://github.com/ChrisHenryOC/samantha_server_public.git
cd samantha_server_public
uv sync --all-extras
```

`uv sync` creates a local virtual environment and installs the project plus the
`dev` dependency group. (`--all-extras` matches the development gate; the project
defines no extras today, so a plain `uv sync` is equivalent.)

> The default LLM transport is `omlx`, which talks to the oMLX server over HTTP
> and needs nothing extra here. Only the alternative *in-process* `mlx` provider
> needs the Apple-Silicon-only dependency group: `uv sync --group local-llm`.

## Step 1 — Create your `.env`

```bash
cp .env.example .env
```

`.env` is gitignored. The project's CLI entry points load it automatically (via
`python-dotenv`) when you run them through `uv run`, so you do **not** need to
`source` it manually.

## Step 2 — Generate the required secrets

Three secrets are required on every startup. Each is a 32-byte value rendered as
64 hex characters. Generate fresh values and paste each into the matching line in
`.env`.

```bash
# RECEIPT_SIGNING_KEY — Ed25519 receipt-signing key (libsodium via PyNaCl)
uv run python -m samantha_server.receipts.signing --gen-key

# PHI_HASH_SALT — salt for hashing PHI fields before any LLM payload is built
uv run python -c 'import os; print(os.urandom(32).hex())'

# RBAC_HMAC_KEY — HMAC key for the orchestrator's capability tokens
uv run python -c 'import os; print(os.urandom(32).hex())'
```

Set them in `.env` (values quoted), and disable tracing so the run needs no
Langfuse instance:

```ini
RECEIPT_SIGNING_KEY="<64-hex-from-the-first-command>"
PHI_HASH_SALT="<64-hex-from-the-second-command>"
RBAC_HMAC_KEY="<64-hex-from-the-third-command>"
LANGFUSE_ENABLED="false"
```

> Tracing is **enabled by default**. If you leave `LANGFUSE_ENABLED` unset (or
> `"true"`) without provisioning `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`,
> and `OTEL_EXPORTER_OTLP_ENDPOINT`, startup aborts on purpose (fail-loud). Set
> it to `"false"` for Tiers 1 and 2 unless you have a Langfuse instance (Tier 3).

## Tier 1 — Run the deterministic replay (no LLM)

The replay harness drives the vendored scenario corpus through the **real**
engine entry point and reports accuracy. Run the deterministic categories, which
need no LLM and are fully reproducible:

```bash
uv run python -m samantha_server.scenarios.replay \
  tests/fixtures/scenarios/ \
  --include-category rule_coverage,multi_rule,accumulated_state \
  --no-warm-up
```

**Success check:** the command prints a per-category accuracy summary and exits
`0`, reporting `Included accuracy : 100.0%`. A non-zero exit means a gate failed
(the accuracy gate is `included_accuracy >= 0.995`).

`--no-warm-up` skips the harness's optional LLM warm-up probe. Without it, a
deterministic-only run still pings the LLM endpoint once and prints a non-fatal
`Warm-up failed ...` line when no oMLX server is up; the run continues and the
result is unaffected, but the flag keeps the no-LLM output clean.

Drop `--include-category` to run the full corpus, but the LLM categories
(`query`, `unknown_input`, `hallucination`) will error without a configured oMLX
server — that is Tier 2.

## Tier 2 — The LLM path (optional, Apple Silicon)

The LLM-routed categories reach a local OpenAI-compatible MLX server (oMLX) at a
single gated chokepoint. This tier requires Apple Silicon.

### 2a. Install oMLX

[oMLX](https://omlx.ai) is a third-party, Apache-2.0 local inference server
([github.com/jundot/omlx](https://github.com/jundot/omlx)). Install it via
Homebrew (this gives you the `omlx` CLI):

```bash
brew tap jundot/omlx https://github.com/jundot/omlx
brew install omlx
```

Alternatively, download the macOS `.dmg` from the
[releases page](https://github.com/jundot/omlx/releases) (note: the app alone
does not install the `omlx` CLI), or install from source per the oMLX README.

### 2b. Download a model and start the server

Start the server (default port **8000**):

```bash
omlx serve --model-dir ~/models
```

Then open the admin dashboard at <http://localhost:8000/admin> and use the model
downloader to fetch an MLX-format model. Two options:

- **Light, for a quick smoke test:** `mlx-community/Llama-3.2-3B-Instruct-4bit`
  (this is also `samantha_server`'s default `LLM_MODEL_PATH`).
- **Recommended baseline:** `Qwen3-Next-80B-A3B-Instruct-4bit`. This is the
  validated baseline (full-corpus N=5 ≈ 147/149), but it needs **~45 GB
  resident, verified on 64 GB unified memory**. On smaller machines, use the
  light model.

The OpenAI-compatible endpoint is then `http://localhost:8000/v1`.

### 2c. Point `samantha_server` at oMLX

The defaults already target a loopback oMLX, so you typically only need to set
the model. In `.env`:

```ini
LLM_PROVIDER="omlx"
LLM_OMLX_BASE_URL="http://127.0.0.1:8000"
LLM_MODEL_PATH="mlx-community/Llama-3.2-3B-Instruct-4bit"
# Optional: a clean display name for audit logs (defaults to LLM_MODEL_PATH)
LLM_MODEL_NAME="Llama-3.2-3B-Instruct-4bit"
```

`LLM_OMLX_BASE_URL` must be a loopback host (`127.0.0.1`, `localhost`, or `::1`);
this is validated at import. A default `omlx serve` needs no authentication; if
you started oMLX with `--api-key`, also set `LLM_OMLX_AUTH_TOKEN` to that value.

### 2d. Run the LLM-path replay

```bash
uv run python -m samantha_server.scenarios.replay \
  tests/fixtures/scenarios/ \
  --include-category query,unknown_input,hallucination \
  --n-sweeps 5 --progress
```

`--n-sweeps 5` repeats each scenario five times. At `temperature=0.0` on MLX, a
single sweep on LLM-routed categories is close to a coin flip on borderline
fixtures, so report results over five sweeps. `--progress` prints one line per
scenario.

### Alternative: Ollama (or another OpenAI-compatible server)

oMLX is the supported and validated inference server, but the LLM client is a
plain OpenAI-compatible HTTP client, so other local servers can work too. This
path is **unsupported and unvalidated** — the accuracy baselines were measured on
oMLX with specific MLX-quantized models, so "it runs" does not imply "it routes
identically." Use it for experimentation, not as a substitute for the reference
setup.

The server must satisfy three contracts the client depends on:

1. `GET /v1/models` returns a JSON body with a `data` array (checked at startup;
   a failure raises `LLMModelLoadError`).
2. `POST /v1/chat/completions` honors
   `response_format: {"type": "json_schema", "json_schema": {"strict": true, ...}}`
   and returns schema-valid JSON in `choices[0].message.content`. This is the
   load-bearing requirement: if the server ignores `response_format`, the model
   emits free-form text, JSON parsing fails, and every LLM scenario refuses.
3. Responses include `usage.prompt_tokens` and `usage.completion_tokens`.

[Ollama](https://ollama.com) satisfies all three (it does grammar-constrained
structured output via llama.cpp). To use it:

```bash
# Install Ollama (see https://ollama.com/download), then pull a model:
ollama pull llama3.2:3b
```

Ollama serves an OpenAI-compatible API on port **11434** by default. Point
`samantha_server` at it via the existing `omlx` provider (there is no separate
Ollama provider — you reuse the OpenAI-compatible HTTP client). In `.env`:

```ini
LLM_PROVIDER="omlx"
LLM_OMLX_BASE_URL="http://127.0.0.1:11434"   # loopback; note Ollama's port
LLM_OMLX_AUTH_TOKEN=""                         # Ollama needs no auth by default
LLM_MODEL_NAME="llama3.2:3b"                    # the Ollama model tag, verbatim
```

Then run the replay exactly as in step 2d.

> **"Thinking" models need token headroom.** Reasoning models (e.g. some Qwen3.x
> variants) emit a long reasoning stream before the schema-constrained answer. If
> `LLM_MAX_TOKENS` is too small, the budget is spent reasoning and
> `message.content` comes back empty, which the harness treats as a refusal. Keep
> `LLM_MAX_TOKENS` at the default (2048) or higher, or prefer a non-thinking
> instruct model. oMLX's per-model `enable_thinking=False` handling is not applied
> to arbitrary Ollama tags.
>
> **The same model can route differently across backends.** Accuracy is a function
> of the (server, model build) pair, not the model name alone. The same model can
> score noticeably lower under one server than another because of (a) different
> quantizations of the "same" weights (e.g. an MLX 4-bit build vs a GGUF Q4 build)
> and (b) how strictly each server's structured-output decoder constrains
> generation — aggressive grammar enforcement can push some models into degenerate,
> repetitive output. Treat any non-oMLX backend as a fresh experiment and measure
> with the replay harness before trusting its results.

## Tier 3 — Development gate (optional)

The repo carries the maintainer's local gate. It is included to document the
development workflow, not as a turnkey setup — note that `scripts/ci/run.sh`
probes a live oMLX server, so it expects Tier 2 to be running.

Enable the tracked git hooks and install the non-Python gate tools once per
clone:

```bash
scripts/bootstrap/setup-local-ci.sh
```

Run the full gate on demand (lock check, `uv sync`, `ruff` format + lint, `mypy
--strict`, `pytest --cov`, `markdownlint-cli2`, `gitleaks`):

```bash
scripts/ci/run.sh
```

**Tracing (optional).** To capture traces, stand up a
[Langfuse](https://langfuse.com/docs/deployment/self-host) instance, then in
`.env` set `LANGFUSE_ENABLED="true"`, `LANGFUSE_BASE_URL` (a loopback
`http://` URL, default `http://localhost:3000`), `LANGFUSE_PUBLIC_KEY`,
`LANGFUSE_SECRET_KEY`, and `OTEL_EXPORTER_OTLP_ENDPOINT`. See
[`docs/operations/deployment.md`](operations/deployment.md) and the trace schema
in [`docs/observability/trace-schema.md`](observability/trace-schema.md).

## Troubleshooting

- **`MisconfiguredEnvironmentError` at startup.** A required secret is missing or
  malformed. Confirm `RECEIPT_SIGNING_KEY`, `PHI_HASH_SALT`, and `RBAC_HMAC_KEY`
  are each set to a 64-hex-character value, and that `LANGFUSE_ENABLED="false"`
  unless you have provisioned the three Langfuse variables.
- **Startup complains about Langfuse keys.** Tracing is on by default; set
  `LANGFUSE_ENABLED="false"`.
- **`LLM_OMLX_BASE_URL` rejected at import.** The host must be loopback
  (`127.0.0.1`, `localhost`, or `::1`). A non-loopback URL is refused by design.
- **oMLX unreachable / connection refused.** Confirm the server is running and on
  the expected port: `curl http://127.0.0.1:8000/v1/models`. The default port is
  8000; if you changed it, update `LLM_OMLX_BASE_URL` to match.
- **Out of memory loading the model.** The recommended baseline needs ~45 GB
  resident. Switch `LLM_MODEL_PATH` to the light
  `mlx-community/Llama-3.2-3B-Instruct-4bit`, or use a 64 GB machine.

## Where to go next

- The 9-primitive decision vocabulary:
  [`docs/rule-breakdown/decision-gate.md`](rule-breakdown/decision-gate.md).
- Worked transcripts of both the deterministic and LLM paths (each ending with a
  signed receipt): [`docs/methodology-transcripts/`](methodology-transcripts/).
- Authoring conventions for rules and skills:
  [`docs/rule-authoring-methodology.md`](rule-authoring-methodology.md).
