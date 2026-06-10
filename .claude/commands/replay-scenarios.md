---
description: Replay the vendored scenario corpus against the deterministic rule engine and report accuracy
allowed-tools: Bash(uv run python:*),Read
---

Replay the vendored scenario corpus and report accuracy: $ARGUMENTS

`$ARGUMENTS` may be empty (run all scenarios) or a path to a specific scenarios directory.

## 1. SETUP

Confirm the vendored scenarios exist:

```bash
ls tests/fixtures/scenarios/
```

If the directory is empty or missing, run the vendor script first:

```bash
uv run python -m scripts.vendor_scenarios
```

## 2. RUN

Invoke the canonical replay entry point:

```bash
uv run python -m samantha_server.scenarios.replay tests/fixtures/scenarios/
```

The canonical flags:

- `--models=A,B` — sequential multi-model sweep; each model gets its own report section
  and a combined cross-model summary is printed at the end.
- `--include-category=query,llm_review` — run only the specified categories (useful for
  the live_llm shim tests and for targeted debugging).
- `--receipts-db-path=PATH` — persist replay receipts to this SQLite file
  instead of the default in-memory store. Required upstream of the
  receipts-evidence chart bundle. Pointing at the configured production
  `RECEIPTS_DB_PATH` is refused. When combined with `--models`, each model's
  receipts go to a per-model file (`<stem>-<safe_model_id><suffix>`) so chart
  consumers can demux without relying on Langfuse trace ids.

For the default deterministic gate only (no LLM required):

```bash
uv run python -m samantha_server.scenarios.replay tests/fixtures/scenarios/
```

It exits 0 if `included_accuracy >= 99.5%`, else exits 1.

## 3. PARSE OUTPUT

The harness reports:

- **Included accuracy**: pass rate for deterministic-path categories
  (`rule_coverage`, `multi_rule`, `accumulated_state`) plus LLM-path categories
  (`query`, `unknown_input`, `hallucination`). This is the regression gate.
- **Overall accuracy**: pass rate across all categories (informational).
- **Failed scenarios**: per-scenario, per-step mismatch details (state, rules, flags).
  LLM-content correctness (query/llm_review) is enforced by the engine content
  gate (#194) as part of the `included_accuracy` verdict ladder.

## 4. REGRESSION GATE

| Metric | Target |
|---|---|
| Included accuracy (`rule_coverage` + `multi_rule` + `accumulated_state`) | ≥99.5% |
| Deterministic path (no LLM calls) | guaranteed by design |

Any included-accuracy breach is a regression. The harness exits 1 and prints
the per-scenario mismatches verbatim. Do not merge if the gate fails.

## 5. SKIPLIST

Some scenarios are intentionally excluded from the accuracy gate via
`tests/fixtures/scenarios/.skiplist.json`. This is appropriate when a scenario
reveals a known upstream issue (corpus error, unimplemented rule, deferred
behavior) that is tracked as a GitHub issue.

**What it is**: A JSON file with a `"skipped"` object mapping scenario IDs to
tracking issue URLs.

**How to add an entry**:

1. Create or find a GitHub issue that describes the underlying problem.
   If you create a new issue, also add a row to the Follow-ups table in
   `docs/project/implementation-todo.md` with the new `GH-<N>` ID, `Open`
   status, the description, source `Replay corpus audit`, and any
   currently-open blocking issues in `Depends on`. Insert in
   execution-readiness order among the Open rows. Read
   `docs/project/implementation-todo-schema.md`
   first if you're unsure what goes in each column or what the em-dash
   sentinel means. Run
   `markdownlint-cli2 --fix "docs/project/implementation-todo.md"` and stage
   the change in the same commit as the `.skiplist.json` update.
2. Add the scenario ID and the issue URL to `.skiplist.json`:
   ```json
   {
     "skipped": {
       "SC-XXX": "https://github.com/ChrisHenryOC/samantha_server/issues/NNN"
     }
   }
   ```
3. Every entry **must** include a non-empty tracking URL — the harness raises
   `ValueError` on empty or null URLs.

**Excluded from both numerator and denominator**: Skipped scenarios do not
contribute to `included_accuracy` either way. They are excluded from both
the pass count and the total count. This is intentional — deferred work should
be tracked, not hidden in the denominator.

**Orphan warning**: If a skiplisted scenario currently passes, the harness emits
a warning to stderr. Remove the scenario from the skiplist when the underlying
issue is resolved.

## 6. REPORT

Output a summary:

- Included accuracy + pass/fail count.
- Overall accuracy (informational).
- Any failed scenarios: scenario ID, category, step number, mismatch type
  (mismatch_state / mismatch_rules / mismatch_flags / dispatch_empty).
- Regression gate: PASS or FAIL.

If the gate fails, end with "Regression detected — do not merge" and list the
scenario IDs that changed since the last passing run (use `git diff` on the
relevant spec YAML files).
