# samantha_server

A reference architecture for **hybrid clinical-order automation**: a
deterministic rule engine that handles the cases it can prove, a local
LLM consulted only at a narrow chokepoint for the cases it cannot, and a
**signed receipt** emitted for every decision so the whole system is
auditable after the fact.

This repository is a sanitized public mirror of the working
`samantha_server` codebase. It accompanies the CONVEX 2026 talk and is
meant to be read as an architecture, not run as a product. The full
scenario corpus, rule catalog, skill catalog, evaluation harness, and
charting pipeline are all included.

## External references

The talk and this codebase draw on a set of external articles, standards,
and guides. The verified, organized list lives in
[`docs/references.md`](docs/references.md).

## Why hybrid

Pure-LLM pipelines are hard to audit and hard to make deterministic.
Pure rule engines cannot handle open-ended natural-language input. The
design here splits the problem:

- **Deterministic path.** Specimen-routing decisions that can be decided
  by rule flow through a rule engine built from a locked vocabulary of
  9 primitives. This path never imports the LLM client. Same input, same
  output, every time.
- **LLM chokepoint.** Only the inputs the deterministic path cannot
  decide (free-text queries, unknown input, hallucination checks) reach
  the LLM, and only through a single gated dispatcher. The LLM proposes;
  the engine still decides.
- **Signed receipts.** Every decision on either path emits a receipt
  signed with an Ed25519 key (libsodium via PyNaCl). A receipt records
  what was decided, by which path, against which rule, over what input
  hash, so any decision can be verified later without re-running it.

A strict **PHI boundary** sits in front of the LLM: patient identifiers
are stripped or hashed before any model payload is built.

## Repository layout

| Path | What lives there |
|------|------------------|
| `samantha_server/engine/` | The rule engine and the `list_applicable_rules` dispatcher gate every transition flows through. |
| `samantha_server/primitives/` | The 9 locked decision primitives (100% test-coverage gated). |
| `samantha_server/rules/specs/` | The rule catalog: one YAML per rule (44 rules). |
| `samantha_server/skills/specs/<name>/SKILL.md` | The LLM-consultable skill catalog (agentskills.io format). |
| `samantha_server/receipts/` | Receipt signing, storage, and audit (`signing.py`, `store.py`, `audit.py`, `schema.sql`). |
| `samantha_server/llm/` | The LLM client, handlers, and chat-template config (local MLX-family models). |
| `samantha_server/scenarios/` | The replay harness that drives the corpus through the real engine. |
| `tests/fixtures/scenarios/` | The 149-scenario evaluation corpus (synthetic data only). |
| `charts/` | The matplotlib charting pipeline; rendered PNGs in `charts/output/`. |

### Where to start reading

- **The primitive vocabulary** (the 9 primitives and why the set is
  locked): [`docs/rule-breakdown/decision-gate.md`](docs/rule-breakdown/decision-gate.md).
- **Rule vs. skill boundary** (when a case is deterministic vs. when it
  goes to the LLM): [`docs/rule-breakdown/rules-vs-skills.md`](docs/rule-breakdown/rules-vs-skills.md).
- **Authoring conventions** for rules and skills:
  [`docs/rule-authoring-methodology.md`](docs/rule-authoring-methodology.md)
  and [`docs/rules/conventions.md`](docs/rules/conventions.md).
- **Worked examples** of the deterministic and LLM paths end to end:
  [`docs/methodology-transcripts/`](docs/methodology-transcripts/).

## Quickstart

This project uses [`uv`](https://docs.astral.sh/uv/). Python 3.12+.

```bash
uv sync --all-extras
cp .env.example .env          # generic placeholders; fill in to run the LLM path
```

### Run the replay harness

The replay harness drives the vendored corpus through the **real**
engine entry point and reports accuracy:

```bash
uv run python -m samantha_server.scenarios.replay tests/fixtures/scenarios/
```

The deterministic categories (`rule_coverage`, `multi_rule`,
`accumulated_state`) run with no LLM and are guaranteed reproducible. The
LLM-path categories (`query`, `unknown_input`, `hallucination`) require a
local OpenAI-compatible MLX server (oMLX) configured via `.env`; without
one, run the deterministic categories only. The harness exits non-zero on
any regression below the accuracy gate.

> Note: the gate hooks under `.githooks/` and the local CI script
> `scripts/ci/run.sh` assume a local oMLX server is reachable (the test
> suite probes it). They are included to document the development gate,
> not as a turnkey setup for external clones.

## How to read a receipt

Every decision emits a signed receipt. A receipt is a JSON object whose
core fields record the decision (`decision`, `outcome`, the rule that
fired, the routing path) plus an `event_data_hash` over the input and a
`signature`. The signing key id (`RECEIPT_SIGNING_KEY_ID`) lets a
verifier select the right key, and two-key rolling rotation is supported
so receipts remain verifiable across a key change.

To see real receipts in context, read the worked transcripts in
[`docs/methodology-transcripts/`](docs/methodology-transcripts/) (each
ends with the emitted receipt), and the signing and verification logic in
[`samantha_server/receipts/signing.py`](samantha_server/receipts/signing.py).

## License

[MIT](LICENSE).
