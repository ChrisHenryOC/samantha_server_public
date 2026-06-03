"""Architectural-invariant tests.

Each module under this package pins one of the architectural
invariants documented in
[`/CLAUDE.md`](../../CLAUDE.md#architectural-invariants) so that
violations fire in CI rather than slipping past reviewer attention.

Modules:

- `test_deterministic_purity.py` — `primitives/`, `rules/`, `engine/`,
  `models/`, `scenarios/`, `receipts/`, `skills/` import no LLM,
  network, or non-determinism modules. Allowlist for justified
  exceptions lives inline in the test module.
- `test_model_agnostic.py` — no hardcoded model IDs anywhere except
  the (currently nonexistent) `samantha_server/config.py` and
  `samantha_server/llm/config.py`. Phase 2 will create those files;
  the test passes vacuously today and binds the moment they appear.

The `list_applicable_rules` enforcement invariant is **not** pinned
here — it is already covered by
[`tests/engine/test_evaluator.py::TestUndispatchedRuleError`](../engine/test_evaluator.py)
(three cases: wrong token, wrong rules, empty dispatch with wrong
token). That class predates this directory and is the authoritative
contract pin; duplicating under `tests/architectural/` would be
DRY-hostile.

The PHI-payload-side strip/hash invariant lands here when the first
LLM-payload module exists (see CI plan Step 8).
"""
