# Project conventions for `samantha_server`

Operational pointers for any Claude Code session working in this repo.
See [`README.md`](README.md) for an architecture overview and quickstart.

## Where to author rules and skills

- **Deterministic rules** live in
  [`samantha_server/rules/specs/`](samantha_server/rules/specs/) as one
  YAML per rule (`<RULE_ID>.yaml`). The 9-primitive vocabulary is
  locked in
  [`docs/rule-breakdown/decision-gate.md`](docs/rule-breakdown/decision-gate.md).
  Outcome prefixes and field-naming conventions:
  [`docs/rules/conventions.md`](docs/rules/conventions.md).

- **LLM-consultable skills** live in
  [`samantha_server/skills/specs/<skill_name>/SKILL.md`](samantha_server/skills/specs/)
  in the agentskills.io format (YAML frontmatter + prose body). The
  decision boundary between a rule and a skill is documented in
  [`docs/rule-breakdown/rules-vs-skills.md`](docs/rule-breakdown/rules-vs-skills.md).

- **The operational guide** for adding either is
  [`docs/rule-authoring-methodology.md`](docs/rule-authoring-methodology.md):
  read it before authoring a new rule or skill, extending the
  primitive set, or touching the action-handler boundary.

## Architectural invariants

Verify before pushing any change that touches the engine:

- **Deterministic-path purity** — code in `rules_engine.py` and
  `samantha_server/rules/` must not import the LLM client.
- **`list_applicable_rules` enforcement** — every code path proposing
  a transition flows through the kernel's dispatcher gate.
- **Receipt emission** — every decision path emits a signed receipt.
- **Model-agnostic discipline** — no hardcoded model IDs outside
  `config.py` / `.env`.
- **PHI boundary** — any LLM-payload construction strips or hashes PHI
  fields. Exception: `order_id` is deliberately a pass-through. It is a
  synthetic LIS identifier (not a HIPAA Safe Harbor element) and the
  model runs on oMLX (a local OpenAI-compatible MLX server) inside the
  local trust boundary. `patient_name` and `patient_sex` remain
  STRIPPED; `event_data_hash` remains HASHED.
  `patient_name` and `patient_sex` remain STRIPPED; `event_data_hash`
  remains HASHED.

## Workflow

- Test-first: never write production code without a failing test that
  requires it.
- Validation pass before push: run **`scripts/ci/run.sh`**, the single
  source of truth for the gate. It runs, in order: `uv lock --locked`,
  `uv sync --all-extras`, `ruff format --check`, `ruff check`, `mypy
  --strict`, `pytest --cov` (the `fail_under = 100` gate on
  `samantha_server/primitives/**/*` is authoritative; bare `pytest`
  skips it, since `addopts` intentionally omits `--cov`),
  `markdownlint-cli2`, and `gitleaks detect`.

## Local gate

There is no remote CI. The gate is enforced locally by the
`.githooks/pre-push` hook, which runs `scripts/ci/run.sh` before every
push. Enable it once per clone with
[`scripts/bootstrap/setup-local-ci.sh`](scripts/bootstrap/setup-local-ci.sh)
(sets `core.hooksPath=.githooks` and installs the non-Python gate tools
`gitleaks` and `markdownlint-cli2`). The pytest portion probes oMLX, so
the LLM-path tests require it running.

**`uv lock --locked` failure.** Run `uv lock`, commit the updated
`uv.lock`, and re-run the gate. The failure means `pyproject.toml` and
`uv.lock` are out of sync.
