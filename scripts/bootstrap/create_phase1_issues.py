"""Create the 19 GitHub issues for Phase 1 implementation.

Reads the issue specs below, creates one issue per step via `gh issue create`,
captures the resulting issue numbers, then does a second pass via
`gh issue edit` to substitute dependency markers like `{{step-1}}` with the
actual GitHub issue numbers.

This script is idempotent only on first run. If you re-run it after issues
exist, it will create duplicates. To reset: delete all created issues first.

Usage:
    cd /path/to/samantha_server
    python scripts/bootstrap/create_phase1_issues.py
"""

from __future__ import annotations

import re
import subprocess
from typing import NamedTuple

PLAN_URL = (
    "https://github.com/ChrisHenryOC/samantha_server/blob/main/docs/plans/phase-1-implementation.md"
)


class IssueSpec(NamedTuple):
    step_id: str  # "1", "5", "10", etc. — used in dependency markers
    title: str
    body: str  # may contain {{step-N}} markers


SPECS: list[IssueSpec] = [
    IssueSpec(
        step_id="1",
        title="Step 1 — Repo skeleton, pyproject.toml, uv lock, CI smoke",
        body=f"""## What

Stand up the `samantha-server` Python package skeleton. Create
`pyproject.toml` (Python ≥3.12, deps: pydantic≥2.6, pyyaml, dev:
pytest, pytest-cov, ruff, mypy) plus `uv` lockfile, `ruff.toml`,
`mypy.ini`, and the package directory tree
(`samantha_server/{{primitives,models,rules,engine,scenarios,receipts}}/`
plus mirroring `tests/`).

## Done when

- `uv sync && uv run pytest && uv run ruff check && uv run mypy samantha_server`
  all green on a fresh checkout.
- `pyproject.toml` pins exact dep versions and sets
  `[tool.coverage.report] fail_under = 100` for `samantha_server/primitives/`.
- A smoke test asserts `import samantha_server` works.

## Dependencies

None.

## Suggested agents

`test-first-implementer` (smoke test first), `type-design-reviewer`
(reviews `pyproject.toml` deps).

## See

[Phase 1 plan § Step 1]({PLAN_URL}#step-1--repo-skeleton-pyprojecttoml-uv-lock-ci-smoke) for the full spec.
""",
    ),
    IssueSpec(
        step_id="2",
        title="Step 2 — SpecimenContext and event dataclasses",
        body=f"""## What

Author `samantha_server/models/context.py`: `Order` (Pydantic frozen
model, ~10 fields), `Event` (Pydantic frozen, with
`event_data: Mapping[str, Any]` — typed event-data classes deferred
to W1 Phase 3 per G1), and `SpecimenContext` (frozen, with a
`field(name)` accessor implementing the namespace conventions from
`inventory.md`). Port `VALID_STATES`, `VALID_FLAGS`, `FIELD_MAX_LENGTHS`
from `samantha-public/src/workflow/models.py`.

## Done when

- 100% unit coverage on `SpecimenContext.field()` for the namespace
  rules (bare names → order; `event.X` → event_data; `flags` → set
  membership; missing → `None`).
- Order/Event/SpecimenContext type-narrowing tests; `FIELD_MAX_LENGTHS`
  validators raise on over-long input.

## Dependencies

Blocked by {{{{step-1}}}}.

## Suggested agents

`type-design-reviewer` (shapes), `test-first-implementer` (writes
them), `silent-failure-hunter` (null/missing semantics).

## See

[Phase 1 plan § Step 2]({PLAN_URL}#step-2--specimencontext-and-event-dataclasses) for the full spec.
""",
    ),
    IssueSpec(
        step_id="3",
        title="Step 3 — Primitive library (the 9 atoms)",
        body=f"""## What

Implement the 9 frozen-Pydantic primitives under
`samantha_server/primitives/`: `IsNull`, `Equals`, `ThresholdGTE`,
`ThresholdLTE`, `InEnum`, `Contains`, `BooleanAnd`, `BooleanOr`,
`Not`. Each has `evaluate(ctx) -> bool` and `trace(ctx) -> PrimitiveTrace`
(schema pinned in plan). Add a registry in `__init__.py` so the YAML
loader (Step 5) can resolve `is_null` → `IsNull` etc. Boolean
combinators take `tuple[Primitive, ...]` (recursive type via
`model_rebuild()`).

Null behavior: fail-closed per `decision-gate.md` § "Null semantics
rationale".

## Done when

- 100% unit coverage per primitive: positive case, negative case,
  null-field case, short-circuit case (combinators).
- Coverage gate enforced in CI on `samantha_server/primitives/` (set
  in Step 1).

## Dependencies

Blocked by {{{{step-2}}}}.

## Suggested agents

`rule-primitive-author` (design + name), `test-first-implementer`
(red-green-refactor per primitive), `type-design-reviewer` (registry
typing).

## See

[Phase 1 plan § Step 3]({PLAN_URL}#step-3--primitive-library-the-9-atoms) for the full spec.
""",
    ),
    IssueSpec(
        step_id="4",
        title="Step 4 — Primitive latency micro-bench",
        body=f"""## What

Add `tests/perf/test_primitive_latency.py`. For each of the 9
primitives, instantiate a representative shape and run 10,000
evaluations against a fixed `SpecimenContext`. This is the canary that
catches a Step 3-redesign requirement *before* the loader (Step 5) and
specs (Steps 6–10) commit to the primitive shape.

## Done when

- Atomic primitives: <100µs per evaluation (median).
- 3-level-nested combinator: <500µs per evaluation (median).
- Bench passes locally; baseline recorded as
  `results/perf/primitive-baseline-<date>.json`.
- Bench skipped in CI (latency tests are noisy under shared runners).

## Dependencies

Blocked by {{{{step-3}}}}.

## Suggested agents

`performance-reviewer` (writes the bench), `type-design-reviewer`
(reviews fall-back compilation strategy if the bench fails).

## See

[Phase 1 plan § Step 4]({PLAN_URL}#step-4--primitive-latency-micro-bench) for the full spec.
""",
    ),
    IssueSpec(
        step_id="5",
        title="Step 5 — YAML rule-spec format and loader",
        body=f"""## What

Define the native YAML rule-spec format with the predicate grammar (one
form per primitive plus meta-rule reference for ACC-008) and the action
schema (`transition`, `set_flags`, `clear_flags`, `outcome`, optional
`panel:` for HE-006, optional `branch:` for RES-002).

Implement `samantha_server/rules/loader.py` validating against a
Pydantic `RuleSpec` model; recursively constructs predicate trees via
the Step 3 primitive registry; builds `_rules_by_step` and
`_rules_by_applies_at` indexes.

## Done when

Loader unit tests cover: valid spec parses, invalid primitive name
rejected, missing required key rejected, unknown step rejected,
accessioning rule without severity rejected, non-accessioning rule
without priority rejected, IHC rule without `applies_at` rejected,
duplicate `rule_id` rejected.

## Dependencies

Blocked by {{{{step-4}}}} (latency canary must pass before the loader
commits to the primitive shape; transitively requires Step 3).

## Suggested agents

`type-design-reviewer` (schema), `test-first-implementer` (loader),
`silent-failure-hunter` (error paths).

## See

[Phase 1 plan § Step 5]({PLAN_URL}#step-5--yaml-rule-spec-format-and-loader) for the full spec.
""",
    ),
    IssueSpec(
        step_id="6",
        title="Step 6 — Author accessioning rule specs (ACC-001..ACC-009)",
        body=f"""## What

Author 9 YAML specs in `samantha_server/rules/specs/` for the
accessioning rules ACC-001..ACC-009. Accessioning uses `all_match`
mode with severity hierarchy `REJECT > HOLD > PROCEED > ACCEPT`.
Includes ACC-008, the meta-predicate that's the first concrete use of
the meta-rule reference syntax (`rule_ref:`).

**Reviewer note:** Step 10's issue (RES specs + final smoke gate)
should be reviewed in the same sitting as this one. ACC-008 introduces
the meta-rule reference syntax that Step 10's `len(loaded_rules) == 40`
smoke test depends on; schema drift between this step and Step 10
needs to be caught before either lands.

## Done when

- All 9 ACC specs load via the Step 5 loader without error.
- `rule_id`s match the ACC rows in `inventory.csv`.
- `workflow-logic-reviewer` confirms each `action:` translation
  against `workflow_states.yaml`.

## Dependencies

Blocked by {{{{step-5}}}}.

## Suggested agents

`rule-primitive-author`, `workflow-logic-reviewer`.

## See

[Phase 1 plan § Step 6]({PLAN_URL}#step-6--author-accessioning-rule-specs-acc-001acc-009) for the full spec.
""",
    ),
    IssueSpec(
        step_id="7",
        title="Step 7 — Author sample-prep rule specs (SP-001..SP-006)",
        body=f"""## What

Author 6 YAML specs in `samantha_server/rules/specs/` for the
sample-prep rules SP-001..SP-006. SP rules use first-match priority on
`event.outcome`; each spec's `priority:` field is a positive integer.

## Done when

- All 6 SP specs load via the Step 5 loader without error.
- `rule_id`s match the SP rows in `inventory.csv`.
- `workflow-logic-reviewer` confirms each `action:` translation.

## Dependencies

Blocked by {{{{step-5}}}}.

## Suggested agents

`rule-primitive-author`, `workflow-logic-reviewer`.

## See

[Phase 1 plan § Step 7]({PLAN_URL}#step-7--author-sample-prep-rule-specs-sp-001sp-006) for the full spec.
""",
    ),
    IssueSpec(
        step_id="8",
        title="Step 8 — Author H&E rule specs (HE-001..HE-009)",
        body=f"""## What

Author 9 YAML specs in `samantha_server/rules/specs/` for the H&E QC
and pathologist H&E review rules HE-001..HE-009. Includes HE-006's
`panel:` conditional (the canonical example of the action-handler
boundary per `decision-gate.md` § "Action handler boundary") and
HE-008's `clear_flags:` example.

## Done when

- All 9 HE specs load via the Step 5 loader without error.
- `rule_id`s match the HE rows in `inventory.csv`.
- `workflow-logic-reviewer` confirms each `action:` translation,
  particularly HE-006's panel-construction logic.

## Dependencies

Blocked by {{{{step-5}}}}.

## Suggested agents

`rule-primitive-author`, `workflow-logic-reviewer`.

## See

[Phase 1 plan § Step 8]({PLAN_URL}#step-8--author-he-rule-specs-he-001he-009) for the full spec.
""",
    ),
    IssueSpec(
        step_id="9",
        title="Step 9 — Author IHC rule specs (IHC-001..IHC-011)",
        body=f"""## What

Author 11 YAML specs in `samantha_server/rules/specs/` for the IHC
rules IHC-001..IHC-011. IHC rules use first-match priority *and*
`applies_at` scoping (one of `IHC_STAINING`, `IHC_QC`, `IHC_SCORING`,
`SUGGEST_FISH_REFLEX`, `FISH_SEND_OUT`); each spec's `applies_at:`
field is required.

Includes IHC-001, the deepest-nested predicate in the corpus and the
canonical example of the fail-closed null-semantics behavior on
`fixation_time_hours`.

## Done when

- All 11 IHC specs load via the Step 5 loader without error.
- `rule_id`s match the IHC rows in `inventory.csv`.
- Loader rejects any IHC spec missing `applies_at:`.
- `workflow-logic-reviewer` confirms each `action:` translation.

## Dependencies

Blocked by {{{{step-5}}}}.

## Suggested agents

`rule-primitive-author`, `workflow-logic-reviewer`.

## See

[Phase 1 plan § Step 9]({PLAN_URL}#step-9--author-ihc-rule-specs-ihc-001ihc-011) for the full spec.
""",
    ),
    IssueSpec(
        step_id="10",
        title="Step 10 — Author resulting rule specs (RES-001..RES-005) + final smoke gate",
        body=f"""## What

Author 5 YAML specs in `samantha_server/rules/specs/` for the
resulting rules RES-001..RES-005. RES rules use first-match priority
on `event.outcome` plus the order-accumulated `flags` field. Includes
RES-002's `branch:` field for the cleared-vs-still-held action outcome
(the canonical receipt-branching example).

This step also ships the **final smoke gate** asserting the entire
40-rule corpus loads correctly: `len(loaded_rules) == 40` with the
exact `rule_id` set from `inventory.csv`.

**Reviewer note:** Step 6's issue (ACC specs) should be reviewed in
the same sitting as this one. Schema drift between Step 6 (first
concrete uses of the meta-rule reference syntax) and Step 10
(corpus-level assertion) needs to be caught before either lands.

## Done when

- All 5 RES specs load via the Step 5 loader without error.
- `rule_id`s match the RES rows in `inventory.csv`.
- Corpus-level smoke test asserts `len(loaded_rules) == 40` with the
  exact `rule_id` set from `inventory.csv`.
- `workflow-logic-reviewer` confirms each `action:` translation,
  particularly RES-002's branch field.

## Dependencies

Blocked by {{{{step-5}}}} (loader), {{{{step-6}}}}, {{{{step-7}}}},
{{{{step-8}}}}, {{{{step-9}}}} (rule specs must exist for the
corpus-level smoke test to assert against).

## Suggested agents

`rule-primitive-author`, `workflow-logic-reviewer`.

## See

[Phase 1 plan § Step 10]({PLAN_URL}#step-10--author-resulting-rule-specs-res-001res-005--final-smoke-gate) for the full spec.
""",
    ),
    IssueSpec(
        step_id="11",
        title="Step 11 — Rules engine kernel (dispatcher + evaluator)",
        body=f"""## What

Implement `samantha_server/engine/dispatcher.py` (the
`list_applicable_rules` boundary that filters by step → applies_at →
event_type) and `samantha_server/engine/evaluator.py` (severity-based
all-match for accessioning, priority-based first-match for other
phases). Returns an `EngineDecision` (frozen Pydantic model — schema
in the plan, including sha256 `event_input_hash`, opaque
`_dispatch_token` for boundary enforcement, `latency_us`).

## Done when

Unit tests cover:
- Dispatch correctly filters by step + applies_at + event_type
  (SP-001/SP-004 collision case).
- Severity hierarchy resolves accessioning correctly.
- Priority order resolves first-match for non-accessioning.
- The engine refuses to evaluate a rule not returned by
  `dispatcher.list_applicable_rules` (architectural invariant test).
- IHC dispatch by `applies_at` works.

## Dependencies

Blocked by {{{{step-10}}}} (the engine kernel needs all 40 rule specs
loadable for its dispatch fixtures and architectural-invariant test;
Step 10's smoke gate confirms the corpus is intact).

## Suggested agents

`test-first-implementer`, `workflow-logic-reviewer`,
`code-quality-reviewer`.

## See

[Phase 1 plan § Step 11]({PLAN_URL}#step-11--rules-engine-kernel-enginedispatcherpy-engineevaluatorpy) for the full spec.
""",
    ),
    IssueSpec(
        step_id="12",
        title="Step 12 — Scenario import + replay harness",
        body=f"""## What

`samantha_server/scenarios/loader.py` reads vendored scenario JSON
into typed `Scenario` / `ScenarioStep` dataclasses;
`samantha_server/scenarios/replay.py` runs each scenario step-by-step
against the Step 11 engine, threading accumulated `flags` and
`current_state`. Returns per-scenario verdicts plus an aggregated
accuracy report (`included_accuracy` / `overall_accuracy`).

`scripts/vendor_scenarios.py` does the JSON copy from
the upstream POC repo's `scenarios/` into
`tests/fixtures/scenarios/`, appends `routing_path: deterministic` to
every `expected_output`, idempotent with provenance metadata in
`.vendor.json`.

Wire `/replay-scenarios` (already in `.claude/commands/`) to invoke
the harness.

## Done when

- `uv run python -m samantha_server.scenarios.replay tests/fixtures/scenarios/`
  exits 0 with `included_accuracy >= 0.995`.
- `/replay-scenarios` slash command returns the same report inside
  Claude Code.
- Vendor script re-runnable without diff if source hasn't changed.

## Dependencies

Blocked by {{{{step-11}}}}.

## Suggested agents

`test-first-implementer` (harness), `performance-reviewer` (latency
hot spots), `silent-failure-hunter` (mismatch reporting).

## See

[Phase 1 plan § Step 12]({PLAN_URL}#step-12--scenario-import--replay-harness) for the full spec.
""",
    ),
    IssueSpec(
        step_id="13",
        title="Step 13 — Skill format adoption + loader (agentskills.io)",
        body=f"""## What

Adopt the [agentskills.io](https://agentskills.io/home) standard for
LLM-consultable SOP knowledge. Three deliverables in one PR:

1. Author `docs/rule-breakdown/rules-vs-skills.md` — short ADR
   distinguishing rules (deterministic primitive-composed predicates)
   from skills (LLM-consultable prose playbooks).
2. Vendor + restructure 6 step-skills from
   the upstream POC repo's `knowledge_base/skills/` into
   `samantha_server/skills/specs/<step>/SKILL.md` folders. Frontmatter
   descriptions are pinned in the plan (6-row table, no TBDs).
3. Build `samantha_server/skills/loader.py` with the agentskills.io
   3-stage lifecycle: discovery (frontmatter index), activation
   (read-on-demand), execute (stubbed `NotImplementedError` until W1
   Phase 3).

## Done when

- Loader unit tests cover discovery, activation, malformed frontmatter
  rejection, missing-required-field rejection, duplicate-name
  rejection, `execute()` raises NotImplementedError.
- `rules-vs-skills.md` passes `documentation-accuracy-reviewer`.

## Dependencies

Blocked by {{{{step-1}}}}. Independent of Steps 2–12 (parallel
thread).

## Suggested agents

`test-first-implementer` (loader), `documentation-accuracy-reviewer`
(ADR), `type-design-reviewer` (loader return shapes).

## See

[Phase 1 plan § Step 13]({PLAN_URL}#step-13--skill-format-adoption--loader-agentskillsio) for the full spec.
""",
    ),
    IssueSpec(
        step_id="14",
        title="Step 14 — Tool-catalog readiness doc (vendor-neutral)",
        body=f"""## What

Author `docs/tool-catalog.md`, a vendor-neutral Phase-2 readiness
document listing the 4 LLM-facing tool surfaces:

1. `list_applicable_rules(state, event) -> tuple[RuleSpec, ...]`
2. `propose_transition(state, target, rule_id) -> EngineDecision`
3. `lookup_scenario(scenario_id) -> Scenario`
4. `lookup_skill(name) -> str`

Each entry: Python signature, one-line description, format-agnostic
input/output spec. The doc explicitly notes that protocol selection
(MCP vs OpenAPI vs OpenAI function-calling vs custom) is deferred to
the Phase 2 entry spike (see § 9 of the plan).

## Done when

- `docs/tool-catalog.md` exists with all 4 tools enumerated.
- Doc explicitly defers protocol choice to the Phase 2 entry spike.
- Cross-links to the plan and to the Phase 2 spike doc location.
- Passes `documentation-accuracy-reviewer`.

## Dependencies

Blocked by {{{{step-1}}}} (tool sigs reference modules in the package
layout).

## Suggested agents

`documentation-accuracy-reviewer`. No code, no tests.

## See

[Phase 1 plan § Step 14]({PLAN_URL}#step-14--tool-catalog-readiness-doc-vendor-neutral) for the full spec.
""",
    ),
    IssueSpec(
        step_id="15",
        title="Step 15 — Eval-harness regression anchors enforced in CI",
        body=f"""## What

Wrap the Step 12 replay harness into a pytest regression test and wire
it into CI. Add `tests/regression/test_eval_anchors.py` asserting:

- `included_accuracy >= 0.995` (per Step 12's bucket definition).
- `p99_latency_us < 10_000` over per-decision
  `EngineDecision.latency_us` values.

Emit `results/regression/<timestamp>.json` for trend tracking. Author
`docs/eval-anchors.md` documenting the bucket definition (in:
`rule_coverage/`, `multi_rule/`, `accumulated_state/`; out:
`hallucination/`, `unknown_input(s)/`, `query/`), the latency budget,
and the LLM-path scenarios deferred to W1 Phase 3+. Add CI workflow
file.

## Done when

- CI runs `tests/regression/` on every PR.
- First green run captures the baseline; subsequent regressions block
  merges.
- `docs/eval-anchors.md` passes `documentation-accuracy-reviewer`.

## Dependencies

Blocked by {{{{step-12}}}}.

## Suggested agents

`performance-reviewer`, `test-coverage-reviewer`,
`test-first-implementer` (the test).

## See

[Phase 1 plan § Step 15]({PLAN_URL}#step-15--eval-harness-regression-anchors-enforced-in-ci) for the full spec.
""",
    ),
    IssueSpec(
        step_id="16",
        title="Step 16 — Methodology doc skeleton (rule-authoring-methodology.md)",
        body=f"""## What

Author the operational methodology doc `docs/rule-authoring-methodology.md`
covering: primitive vocabulary, dispatch contract, rules vs skills,
how to add a rule, how to add a skill, how to extend the primitive
set, action handler boundary, and cross-links to the three
worked-example transcripts (placeholder syntax — Steps 17/18/19
backfill).

Also patch project-root `CLAUDE.md` with pointers to
`samantha_server/rules/specs/`, `samantha_server/skills/specs/`, and
this doc.

Open a separate-but-lockstep wiki PR at
`~/llm_wiki/raw/samantha-server-rule-authoring-methodology.md` whose
body just says "see the operational doc in `samantha_server`."

## Done when

- Doc renders without broken links (placeholder syntax is not a
  broken-link false positive).
- `documentation-accuracy-reviewer` passes.
- CLAUDE.md update lints clean.
- Wiki PR opened (separate repo).

## Dependencies

Blocked by {{{{step-10}}}} (final rule-spec smoke gate; transitively
requires Steps 6, 7, 8, 9), {{{{step-13}}}} (skill format adopted;
`rules-vs-skills.md` exists to link), and {{{{step-14}}}}
(`tool-catalog.md` exists to cross-reference).

## Suggested agents

`documentation-accuracy-reviewer`. No code.

## See

[Phase 1 plan § Step 16]({PLAN_URL}#step-16--methodology-doc-skeleton-docsrule-authoring-methodologymd) for the full spec.
""",
    ),
    IssueSpec(
        step_id="17",
        title="Step 17 — Worked-example transcript: ACC-001 (trivial tier)",
        body=f"""## What

Run the methodology end-to-end against ACC-001 in a Claude Code
session. Inputs: the SOP fragment (`patient_name` missing → REJECT),
covering scenarios per `inventory.md`, and the Step 3 primitive
catalog as a tool catalog in the prompt. Capture the full transcript
(input + tool calls + final YAML spec) and save as
`docs/methodology-transcripts/acc-001.md` with annotations: "what
worked", "what required correction", "what surprised".

Backfills `<!-- TRANSCRIPT-LINK-ACC-001 -->` placeholder in
`docs/rule-authoring-methodology.md` (Step 16) with a live link.

## Done when

- Transcript exists.
- The YAML the transcript emits **loads via the Step 5 loader and
  produces a `RuleSpec` whose predicate tree is structurally identical
  to `samantha_server/rules/specs/ACC-001.yaml`'s** (same primitive
  types, same field/value literals, same composition order).
- The rule's covering scenarios route identically against the engine.
- Transcript markdown passes `documentation-accuracy-reviewer`.
- Step 16 placeholder is backfilled in this PR.

## Dependencies

Blocked by {{{{step-6}}}} (the canonical ACC-001 spec exists for
structural-equivalence comparison) and {{{{step-16}}}} (methodology
doc exists to backfill the placeholder cross-link).

## Suggested agents

Manual session; `documentation-accuracy-reviewer` validates the
writeup.

## See

[Phase 1 plan § Step 17]({PLAN_URL}#step-17--worked-example-transcript-acc-001-trivial-tier) for the full spec.
""",
    ),
    IssueSpec(
        step_id="18",
        title="Step 18 — Worked-example transcript: ACC-006 (numeric tier)",
        body=f"""## What

Same shape as Step 17 for ACC-006:
`BooleanAnd(Contains('ordered_tests', 'HER2'), BooleanOr(Not(ThresholdGTE('fixation_time_hours', 6.0)), Not(ThresholdLTE('fixation_time_hours', 72.0))))`.
The tier-2 deliverable specifically tests whether the methodology
handles compound predicates with numeric tolerances and the new
`Contains` primitive.

Backfills `<!-- TRANSCRIPT-LINK-ACC-006 -->` placeholder in Step 16's
doc.

## Done when

Same semantic-equivalence criterion as Step 17, applied to ACC-006.
Step 16 placeholder backfilled.

## Dependencies

Blocked by {{{{step-6}}}} (the canonical ACC-006 spec exists; ACC-006
is in the accessioning bundle) and {{{{step-16}}}} (methodology doc
exists to backfill the placeholder). Independent of Steps 17 and 19.

## Suggested agents

Manual session; `documentation-accuracy-reviewer` validates the
writeup.

## See

[Phase 1 plan § Step 18]({PLAN_URL}#step-18--worked-example-transcript-acc-006-numeric-tier) for the full spec.
""",
    ),
    IssueSpec(
        step_id="19",
        title="Step 19 — Worked-example transcript: IHC-001 (compositional tier)",
        body=f"""## What

Same shape as Step 17 for IHC-001 — the most nested predicate in the
corpus (3 levels, 6 distinct primitives). Per `decision-gate.md` §
"Null semantics rationale", this is also where the fail-closed null
behavior for `fixation_time_hours` matters. The transcript should
explicitly surface that case in the annotation section.

Backfills `<!-- TRANSCRIPT-LINK-IHC-001 -->` placeholder in Step 16's
doc.

## Done when

Same semantic-equivalence criterion as Step 17, applied to IHC-001.
Step 16 placeholder backfilled.

## Dependencies

Blocked by {{{{step-9}}}} (the canonical IHC-001 spec exists; IHC-001
is in the IHC bundle) and {{{{step-16}}}} (methodology doc exists to
backfill the placeholder). Independent of Steps 17 and 18.

## Suggested agents

Manual session; `documentation-accuracy-reviewer` validates the
writeup.

## See

[Phase 1 plan § Step 19]({PLAN_URL}#step-19--worked-example-transcript-ihc-001-compositional-tier) for the full spec.
""",
    ),
]


def gh(*args: str, capture: bool = True) -> str:
    """Run a `gh` command, return stdout (stripped). Raises on nonzero exit."""
    result = subprocess.run(
        ["gh", *args],
        capture_output=capture,
        text=True,
        check=True,
    )
    return result.stdout.strip() if capture else ""


def create_one(spec: IssueSpec) -> int:
    """Create the issue, return its number."""
    url = gh("issue", "create", "--title", spec.title, "--body", spec.body)
    # gh returns the URL; parse the issue number from the trailing path.
    match = re.search(r"/issues/(\d+)$", url)
    if not match:
        raise RuntimeError(f"Could not parse issue number from {url!r}")
    number = int(match.group(1))
    print(f"  Step {spec.step_id} → #{number}")
    return number


def substitute_dependencies(body: str, step_to_number: dict[str, int]) -> str:
    """Replace `{{step-N}}` markers with `#<actual-number>`."""

    def repl(m: re.Match[str]) -> str:
        step_id = m.group(1)
        if step_id not in step_to_number:
            raise KeyError(f"Marker references unknown step: {step_id!r}")
        return f"#{step_to_number[step_id]}"

    return re.sub(r"\{\{step-([0-9]+)\}\}", repl, body)


def main() -> None:
    print(f"Creating {len(SPECS)} Phase 1 issues...")
    step_to_number: dict[str, int] = {}
    for spec in SPECS:
        step_to_number[spec.step_id] = create_one(spec)

    print()
    print("Substituting dependency markers...")
    for spec in SPECS:
        new_body = substitute_dependencies(spec.body, step_to_number)
        if new_body == spec.body:
            print(f"  Step {spec.step_id} (#{step_to_number[spec.step_id]}): no markers, skipped")
            continue
        gh(
            "issue",
            "edit",
            str(step_to_number[spec.step_id]),
            "--body",
            new_body,
        )
        print(f"  Step {spec.step_id} (#{step_to_number[spec.step_id]}): markers substituted")

    print()
    print("Done. Step → issue mapping:")
    for step_id, number in step_to_number.items():
        print(f"  Step {step_id} → #{number}")


if __name__ == "__main__":
    main()
