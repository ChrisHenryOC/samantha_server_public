# Rule-authoring methodology

The operational guide for adding deterministic rules and LLM-consultable
skills to `samantha_server`. Pairs with the design rationale in
[`rule-breakdown/decision-gate.md`](rule-breakdown/decision-gate.md) and
[`rule-breakdown/rules-vs-skills.md`](rule-breakdown/rules-vs-skills.md);
this doc tells you **how to do it**, not **why the format looks the way
it does**.

> **Wiki canonical copy.** A matching page at
> `~/llm_wiki/raw/samantha-server-rule-authoring-methodology.md` is the
> wiki-side stub; its body just points back here. Operational copy and
> worked examples live in this repo so they version with the engine.

## 1. Primitive vocabulary

Nine atoms compose every deterministic predicate. The full table with
null-semantics rationale lives in
[`decision-gate.md` § "Final primitive set"](rule-breakdown/decision-gate.md#final-primitive-set);
the one-paragraph-each summary:

- **`IsNull(field)`** — true iff the named field is absent or null on
  the order/event payload. The **only** primitive that asserts
  "missing"; every other primitive treats missing as predicate-false.
- **`Equals(field, value)`** — true iff `field == value` (scalar
  compare). On null `field`, returns false unless `value is None`.
- **`ThresholdGTE(field, value)`** — true iff `field >= value`
  (numeric). On null `field`, returns false (fail-closed).
- **`ThresholdLTE(field, value)`** — true iff `field <= value`
  (numeric). On null `field`, returns false (fail-closed). Use
  `Not(ThresholdLTE(...))` for strict-greater-than; `ThresholdGT` and
  `ThresholdLT` were intentionally collapsed.
- **`InEnum(field, set)`** — true iff the scalar `field`'s value lies
  in the literal `set`. On null `field`, returns false.
- **`Contains(collection_field, value)`** — true iff `value` is an
  element of the collection-typed `collection_field` (lists, sets,
  frozensets). On null, treats the collection as empty (returns false).
- **`BooleanAnd(p1, p2, …)`** — true iff every sub-predicate is true.
  Short-circuits on first false.
- **`BooleanOr(p1, p2, …)`** — true iff at least one sub-predicate is
  true. Short-circuits on first true.
- **`Not(p)`** — true iff `p` is false. Predicates are total over the
  field domain — they never raise or return null — so `Not` is a clean
  Boolean negation.

**Null discipline (load-bearing).** Every field-reading primitive
fails-closed on a missing field. `IsNull` is the only way to assert
absence. This is what makes `Not(p)` safe to compose without a
three-valued logic. Do not try to recover null-propagation by hand
inside a rule — guard with `IsNull` at the top level, or rely on the
dispatch contract below.

## 2. The dispatch contract

The primitive vocabulary expresses **trigger conditions only.** Three
filters run **before** any predicate evaluates, in this order, and are
the kernel's responsibility — not the rule author's:

1. **`step`** — every rule carries
   `step: <ACCESSIONING|SAMPLE_PREP|HE_QC|PATHOLOGIST_HE_REVIEW|IHC|RESULTING>`.
   Rules outside the current step never evaluate.
2. **`applies_at`** — IHC rules carry an explicit `applies_at: <state>`
   key (IHC_STAINING, IHC_QC, IHC_SCORING, SUGGEST_FISH_REFLEX,
   FISH_SEND_OUT). Other phases set `applies_at: null` because their
   state-to-rule mapping is one-to-one.
3. **`event_type`** — every event carries an `event_type`, and **every
   rule's YAML carries an explicit `event_type:` key** (one of
   `order_received`, `processing_complete`, `embedding_complete`,
   `sectioning_complete`, `qc_complete`, `pathologist_he_review`,
   `ihc_staining_complete`, `ihc_qc`, `ihc_scoring_complete`,
   `pathologist_decision`, `fish_received`, `resulting_review`,
   `missing_info_received`, `pathologist_signout`, `report_generated`).
   The kernel dispatches on this field to keep predicates from
   accidentally firing on the wrong event shape — prose-only or
   step-implied event-type binding is **not sufficient** and would
   defeat the SP-001/SP-004 and RES-001/RES-002 collision defenses
   below.

**Predicates do not test `current_state` or `event_type`.** That is the
dispatcher's job. See
[`decision-gate.md` § "Dispatch contract"](rule-breakdown/decision-gate.md#dispatch-contract)
for the collision-risk worked examples (SP-001/SP-004,
IHC-002/003 vs IHC-004/005, RES-001 vs RES-002).

The `list_applicable_rules` kernel gate is the boundary that enforces
this contract. Any deterministic transition flows through it.

## 3. Rules vs. skills

Two LLM-consultable formats coexist. The decision boundary in one
sentence:

- **Rule** — primitive-composed predicate evaluated in pure Python by
  the deterministic engine. µs-latency, fully testable, frozen at
  import. Receipt records the matched `RuleSpec` ID.
- **Skill** — agentskills.io `SKILL.md` playbook (YAML frontmatter +
  prose body) consulted by the LLM in-context. LLM-call latency,
  validated by scenario replay, editable as prose. Receipt records the
  activated skill name and the LLM's resulting decision.

The full when-to-author-each table (correctness criteria, latency,
authoring agents) is in
[`rule-breakdown/rules-vs-skills.md`](rule-breakdown/rules-vs-skills.md).
**Author a rule** when the decision reduces to the primitive vocabulary
**and** the trigger fields are structurally typed **and** correctness
matters. **Author a skill** when the decision needs free-text
interpretation, cross-event reasoning the dispatch contract does not
surface, soft-heuristic tolerance, or an open-ended decision space.

The agentskills.io standard is what makes the skill lane uniform: the
loader (`samantha_server/skills/loader.py`) parses YAML frontmatter
(`name`, `description`), indexes skills at engine init, and hands the
prose body to the model in-context at activation time. The on-disk
shape (`SKILL.md` per skill, under
`samantha_server/skills/specs/<name>/`) is the runtime contract.

## 4. How to add a new rule

The flow from SOP excerpt to engine-tested rule, in order:

1. **Locate the SOP fragment.** Quote the operational sentence(s) the
   rule encodes. Save as the `source:` field of the YAML — the path
   into `knowledge_base/sops/` plus an anchor (e.g.,
   `knowledge_base/sops/accessioning.md#3.1`). Without a source, the
   rule is unreviewable.
2. **Write the `primitive_expression`.** Compose the nine atoms into a
   single predicate tree. Conventions:
   - Name the **dominant case** in the primitive — prefer
     `IsNull(field)` over `Not(NotNull(field))`; prefer `Contains` in
     the direction the rule actually reads. Outer `Not(...)` wrappers
     are a smell; rework the predicate before adding one.
   - For numeric ranges, compose two `Threshold` primitives under
     `BooleanAnd` (in-tolerance) or `BooleanOr` of two negations
     (out-of-tolerance) — see ACC-006.
   - HER2-conditional rules follow the
     `BooleanAnd(Contains('ordered_tests', 'HER2'), <check>)` shape;
     reuse it.
3. **Author the YAML spec** under
   `samantha_server/rules/specs/<RULE_ID>.yaml`. Required fields:
   `rule_id`, `step`, `applies_at` (null unless IHC), `event_type`,
   `severity` (ACCESSIONING) or `priority` (everything else), `when:`
   (the predicate tree), `action:` (`transition`, `set_flags`,
   `clear_flags`, `outcome`, `panel`, `branch`), and `source:`. Follow
   the **outcome verb-prefix convention** in
   [`docs/rules/conventions.md`](rules/conventions.md) — `held_*`,
   `rejected_*`, `proceeding_*`, `accessioning_*` for the four
   accessioning severities; past-tense action verbs for other phases.

   **Priority uniqueness.** Within a dispatch bucket — `step` for
   non-IHC non-ACCESSIONING rules, or `applies_at` for IHC rules —
   priorities must be unique. `RuleIndex` raises `ValueError` at
   construction if two rules share a priority within the same bucket.
   Pick the next available integer; consult the inventory before
   reusing a slot. **Priority 0 is reserved for preemption rules**
   (e.g., SP-007 preempts SP-001) and should be chosen only with
   explicit justification — its rule must clearly preempt a documented
   priority-1 rule on a narrower predicate, not just want to "go first".
4. **Verify field naming.** The `when:` predicate must reference fields
   that exist on `Order` (`samantha_server/models/context.py`) or under
   `event.<key>`. Typos fail silently at evaluate time —
   `SpecimenContext.field()` returns `None` via `getattr` fallback.
   `grep` the model before authoring.
5. **Add a covering scenario** to `samantha_server/scenarios/` (or the
   vendored `scenarios/rule_coverage/` corpus) that exercises the
   rule's trigger and any near-miss. The scenario's
   `expected_output.outcome` matches the rule's `action.outcome`.
6. **Write the engine test.** Add a round-trip assertion in
   `tests/rules/test_<phase>_specs.py` and a corpus-level smoke entry
   in `test_corpus_smoke.py` if the rule introduces a new shape. The
   round-trip test is the runtime guard against silent field-name
   typos.
7. **Run the suite.** `uv run pytest` (also enforced by pre-commit).

### Pre-flight: check for accidental fixture matches

Before opening a PR, run the fixture-impact scanner to confirm the
new rule's predicate does not silently match any existing fixture's
first event:

```sh
python -m samantha_server.tools.fixture_impact samantha_server/rules/specs/<RULE_ID>.yaml
```

The tool is read-only: it loads the candidate YAML, iterates every
fixture in `tests/fixtures/scenarios/`, evaluates the candidate's
predicate against each fixture's first event, and prints a plain-text
report listing matches (scenario\_id, scenario-expected rules, and
which atomic predicate clauses became true). If nothing matches, it
prints `<RULE_ID> predicate matches no existing fixtures.` Use
`--specs-dir` and `--fixtures-dir` to override the default paths.

**Limitations.** The scanner evaluates the candidate's predicate only —
it bypasses the dispatcher's `step` / `applies_at` / `event_type`
filters. Every fixture is evaluated against a synthesised
`current_state="ACCESSIONING"`, `flags=frozenset()` first-event
context. A predicate match here does **not** mean the rule would fire at
runtime; the scanner is intentionally over-permissive to surface
accidental matches the dispatcher would otherwise hide. For
non-ACCESSIONING candidates the scanner still runs but its output should
be read as "predicate would match" not "rule would fire".

## 5. How to add a new skill

Skills are agentskills.io playbooks under
`samantha_server/skills/specs/<skill_name>/SKILL.md`. The flow:

1. **Locate the SOP playbook.** A skill encodes a procedure the LLM
   executes against in-context order/event data. Quote or paraphrase
   the operational steps from the SOP.
2. **Author the `SKILL.md` frontmatter.** Two required fields:
   - `name:` — kebab-case identifier (e.g.,
     `accessioning-routing`); the loader uses this as the activation
     key.
   - `description:` — one sentence describing **what the skill
     decides**. The W1 Phase 3 LLM path matches activation candidates
     against this description, so phrase it as the problem the model is
     solving, not the implementation.
3. **Author the prose body.** Structure it for an LLM reader, not a
   human reviewer:
   - Lead with the activation context ("evaluate every rule below",
     "use the order data, not the event data").
   - Use checklists or numbered steps. Worked examples close the
     interpretation gap — see
     [`samantha_server/skills/specs/accessioning/SKILL.md`](../samantha_server/skills/specs/accessioning/SKILL.md)
     for three (single defect, multiple defects, HOLD severity).
   - Call out edge cases the model would otherwise mis-handle (null
     fixation time, severity hierarchy, hard pass/fail boundaries).
   - **No real PHI in the body.** Use synthesised values only —
     `TESTPATIENT-NNNN` (matching the scenario-corpus convention),
     `Jane Doe` style placeholders, and made-up MRNs / DOBs / SSNs.
     There is no programmatic check at the skill-loader boundary at
     POC stage; PR review is the enforcement seam, and a discover-time
     scan is a queued production-hardening follow-up
     ([GH-110](https://github.com/ChrisHenryOC/samantha_server/issues/110)
     — closed as deferred).
4. **Place under `samantha_server/skills/specs/<skill_name>/`.** One
   directory per skill. The `SKILL.md` filename is mandatory; the
   loader globs for it during discovery.
5. **Validate via scenario replay.** Until W1 Phase 3 lands the LLM
   invocation, `SkillResult.execute()` raises `NotImplementedError`;
   exercise the loader (`tests/skills/test_loader.py` patterns) and
   defer behavioral validation to the LLM-path PR.

## 6. How to extend the primitive set

**Default answer: don't.** The 40-rule corpus locks at 9 primitives;
the categorization in
[`decision-gate.md`](rule-breakdown/decision-gate.md) explicitly
defers `AnyMatch`, `LengthGTE`, and the `ThresholdGT`/`ThresholdLT`
pair. Extension is a **methodology revision**, not an in-PR decision:

1. **Demonstrate the forcing function.** Identify a rule the existing
   primitives genuinely cannot express — not "would be cleaner with",
   but "cannot encode". The IHC-006/007 case (deferred `AnyMatch`,
   currently routed via the simulator's `event.any_equivocal`
   pre-computed boolean) is the canonical example of a near-miss that
   did **not** justify extension.
2. **Open a `decision-gate.md` revision.** Update the final primitive
   set table, the coverage summary, and the null-semantics section.
   The revision is the gate; the code change follows.
3. **Update this doc and `rules-vs-skills.md`.** Both summarize the
   primitive list — they drift if extension lands silently.
4. **Backfill round-trip tests.** Every new primitive needs the same
   parity coverage as the existing nine
   (`tests/rules/test_predicate_builder.py` patterns).

The simulator-helper escape hatch (computed boolean fields on the
event, populated by the producer) is preferred over a new primitive
when the shape would only be needed for one or two rules — see the
[`event.any_equivocal`
contract](rule-breakdown/decision-gate.md#simulator-helper-contract-ihc-006--ihc-007).

## 7. Action handler boundary

Predicates capture **trigger** conditions. Actions may carry their own
conditional logic, and that is **deliberate**:

- **Inside primitives** — anything the rules engine evaluates to decide
  *whether* a rule fires.
- **Inside actions** — anything the action computes to decide *what
  state to transition to* or *what flags to set*, given that the
  predicate matched. HE-006 is the canonical example: every DCIS
  diagnosis routes to PROCEED_IHC, but the panel constructor inside the
  action handler reads `ordered_tests` to decide whether HER2 is
  included.

Action-handler conditionals do **not** need to be expressible in the
primitive vocabulary, but they **do** need to be:

- Deterministic — same inputs, same outputs.
- Side-effect-free over the order/event payload (no mutation of
  inputs, no I/O).
- Test-covered.
- **Receipt-recorded** — the receipt for each rule firing must capture
  the action's chosen branch (`outcome: cleared` vs `still_held`,
  constructed panel composition, etc.) so audit trails reconstruct the
  decision.

The split keeps the primitive vocabulary small without leaking
interpretation into the kernel. When in doubt: if the conditional needs
order/event-shape inspection that the primitives express, it goes in
the predicate; if it needs computation over the matched rule's
*action shape* (panel construction, flag set arithmetic), it goes in
the handler. See
[`decision-gate.md` § "Action handler
boundary"](rule-breakdown/decision-gate.md#action-handler-boundary).

## 8. Worked-example transcripts

Three end-to-end transcripts demonstrate the methodology against the
real corpus, spanning the rule-complexity range:

- **Tier 1 — trivial single-primitive (ACC-001).** One field, one
  primitive (`IsNull('patient_name')`). Proves an LLM-author session
  can produce a valid YAML spec from a one-sentence SOP fragment in
  one shot.
  <!-- TRANSCRIPT-LINK-ACC-001 -->
  Transcript:
  [`methodology-transcripts/acc-001.md`](methodology-transcripts/acc-001.md).

- **Tier 2 — numeric threshold (ACC-006).** Combines `Contains` with
  the threshold family and a disjunction over a numeric range
  (`BooleanAnd(Contains('ordered_tests', 'HER2'),
  BooleanOr(Not(ThresholdGTE('fixation_time_hours', 6.0)),
  Not(ThresholdLTE('fixation_time_hours', 72.0))))`). Tests whether
  the methodology handles compound predicates with numeric tolerances.
  <!-- TRANSCRIPT-LINK-ACC-006 -->
  Transcript:
  [`methodology-transcripts/acc-006.md`](methodology-transcripts/acc-006.md).

- **Tier 3 — compositional with ≥2 primitives (IHC-001).** The most
  nested predicate in the corpus: 3 levels deep, 6 distinct
  primitives, two cross-cutting concerns (HER2 add at IHC time +
  fixation-out-of-tolerance check that mirrors ACC-005/006). If the
  methodology survives this rule, it survives the corpus.
  <!-- TRANSCRIPT-LINK-IHC-001 -->
  Transcript:
  [`methodology-transcripts/ihc-001.md`](methodology-transcripts/ihc-001.md).

Documented fallbacks (per the W2.4 worked-example tier picks): ACC-005
as the simpler tier-2 alternative; ACC-008 as the alternate tier-3
demonstrating the meta-predicate pattern over the rule registry. Each
transcript backfills its placeholder above with a live link in the same
PR that lands the transcript file under
`docs/methodology-transcripts/`.

## 9. CI gates

Every change to a rule, skill, primitive, or engine module runs
through CI before merge. The pre-merge contract is **convention-based**
(branch protection is unavailable on the free private GitHub plan),
so reviewers must check that all CI jobs are green before merging.

The points most relevant to rule and skill authors:

### 9.1 Per-PR cadence

CI runs one tier: **per-PR validate**, on every PR and every push to
`main`. Full pytest, ruff, mypy `--strict`, markdownlint, gitleaks,
plus the architectural-invariant tests. Runs from
`.github/workflows/ci.yml`. The
[`conftest.py`](../conftest.py) `_forbid_real_network` fixture
blocks outbound calls to non-loopback hosts as a defense-in-depth
guard.

There is no scheduled real-API tier. The project uses a local
LLM (MLX-family) and does not call cloud LLM APIs by design.
GitHub Actions runners cannot run the local model anyway (no GPU /
no Apple Silicon), so per-PR LLM-touching tests run against an
in-process fake. Full-LLM validation against the real loaded model
happens on developer machines via the `/replay-scenarios` slash
command.

### 9.2 Architectural-invariant tests

All four CLAUDE.md "Architectural invariants" are pinned in CI:

- **Deterministic-path purity** —
  [`tests/architectural/test_deterministic_purity.py`](../tests/architectural/test_deterministic_purity.py)
  walks the AST under `primitives/`, `rules/`, `engine/`, `models/`,
  `scenarios/`, `receipts/`, `skills/` and forbids LLM/network/
  non-determinism imports. **`samantha_server/llm/` and
  `samantha_server/tools/` are out of scope by design** — that's
  where Phase 2 LLM-bearing code lands without fighting the test.
  An allowlist constant at the top of the file holds reviewer-visible
  exceptions (one entry today: `evaluator.py` may import `time` for
  `perf_counter_ns`).
- **`list_applicable_rules` enforcement** —
  [`tests/engine/test_evaluator.py`](../tests/engine/test_evaluator.py)
  (the `TestUndispatchedRuleError` class)
  pins the HMAC-token gate via three failure modes (wrong token,
  wrong rules, empty dispatch with wrong token).
- **Receipt emission** — covered by
  [`tests/engine/test_decision.py`](../tests/engine/test_decision.py).
- **Model-agnostic** —
  [`tests/architectural/test_model_agnostic.py`](../tests/architectural/test_model_agnostic.py)
  scans `samantha_server/**/*.py` for known provider model-ID patterns
  and allows them only in `samantha_server/config.py` and
  `samantha_server/llm/config.py` (forward-pointing; both files arrive
  in Phase 2).

The PHI payload-side strip/hash invariant lands when the first LLM-payload
module exists.

### 9.3 Adding a rule or skill — what CI checks

When you ship a rule (`samantha_server/rules/specs/<id>.yaml`) or a
skill (`samantha_server/skills/specs/<name>/SKILL.md`):

- The rule loader's per-PR pytest run picks up the new YAML via the
  corpus-level smoke test (Phase 1 Step 10). A malformed spec fails
  loudly.
- The PR change-summary comment surfaces the rule-corpus count so a
  reviewer notices when the count moves (drift caught the SP-007
  add in Phase 1 closeout § 2).
- Skills with malformed frontmatter fail
  [`tests/skills/test_loader.py`](../tests/skills/test_loader.py).

When you touch a primitive or engine module:

- The 100% coverage gate on `samantha_server/primitives/**/*` is
  authoritative (see `pyproject.toml` `[tool.coverage.report]`).
- The architectural-purity test fires if a deterministic-core module
  picks up a forbidden import.

### 9.4 Phase 2 CI surface (forward-pointer)

Phase 2 introduces:

- An in-process LLM fake at
  `samantha_server/llm/testing/fake_client.py` that mirrors the
  real local-LLM client interface and returns fixture-keyed
  responses. Per-PR tests use the fake; CI never loads the real
  model (4–8 GB, no compatible runners).
- The PHI boundary AST + property tests under
  `tests/architectural/test_phi_boundary.py`, which gate any
  LLM-payload code path against PHI leakage.
- A developer-side LLM determinism strategy: structural assertions
  for routing tests against the real model, tolerance-based
  assertions for accuracy/eval tests. Per-PR fake-substituted tests
  are deterministic by construction.

Real-LLM regression validation happens on developer machines via
`/replay-scenarios` against the loaded local model — not in CI.

## 10. LLM-path methodology examples (Phase 2)

Phase 2 introduced three LLM-bound runtime categories — `query/`,
`unknown_input/`, and `hallucination/` — each exercising a different
part of the router-shim, handler set, and receipt schema. Where Phase
1 picked one canonical worked example per *complexity tier* of rule
(trivial / numeric / compositional, see § 8), Phase 2 picks one
canonical worked example per *category*: there is no tier hierarchy
across the LLM-bound categories, and each category exercises a
methodology shape distinct from the others (free-text retrieval vs.
preflight clarification vs. Stage B grounded-check refusal).

The three transcripts trace the full runtime flow per category —
`SpecimenContext` setup, `phi_safe` transform, prompt body, raw LLM
output, `SignedReceipt` JSON, signature-verification step, and
expected-vs-actual verdict — and audit the PHI boundary at every
stage.

- **`query/` — free-text clinical lookup (QR-001).** Skill body
  inline; `clinical_query` event → `handle_clinical_query` →
  `QueryTrace` receipt. No transition, no flag changes,
  `applied_rule_id=None`.
  Transcript:
  [`methodology-transcripts/llm-query.md`](methodology-transcripts/llm-query.md).

- **`unknown_input/` — preflight clarification.** A canonical-pick-list
  miss on a field that no rule owns
  (`fixative="ethanol"`; `specimen_type` is excluded because ACC-010
  already handles it). `preflight()` →
  `PreflightMissing(unknown_canonical_fields=("fixative",))` →
  `handle_clarification` → `ClarificationTrace` receipt with
  `outcome="needs_clarification"`. Demonstrates the PHI strip on a
  populated `patient_name` so the visible disappearance is auditable.
  Transcript:
  [`methodology-transcripts/llm-unknown.md`](methodology-transcripts/llm-unknown.md).

- **`hallucination/` — Stage B grounded-check refusal.** Uses SC-106
  (BRCA1 mutation, prior mastectomy, in-tolerance fixation) as the
  context, but pivots the worked example onto the Stage B path: the
  LLM mis-picks ACC-006, the judge re-prompts against the verbatim
  skill body and the safe-context, and returns
  `GroundedVerdict(kind="ungrounded")`. `propose_transition` emits a
  `RefusalTrace(refusal_reason="STAGE_B_UNGROUNDED",
  refusal_stage="B", judge_verdict="ungrounded")` receipt with
  `outcome="refused_ungrounded_stage_b"` and `latency_us=0`
  (refusal decisions are synthetic). No rule fires. Demonstrates that
  stripping `clinical_notes` (PHI guard) is also the hallucination
  guard.
  Transcript:
  [`methodology-transcripts/llm-hallucination.md`](methodology-transcripts/llm-hallucination.md).

### Re-raise gate vs. accuracy bucket

All three LLM-path categories above are members of
`_LLM_PATH_CATEGORIES` (not `_DETERMINISTIC_CATEGORIES`) in
[`samantha_server/scenarios/replay.py`](../samantha_server/scenarios/replay.py).
Two consequences worth pinning down before reading the transcripts:

- **Exception handling.** If a step raises an unexpected exception,
  the harness re-raises only for `_DETERMINISTIC_CATEGORIES`. For LLM-
  path scenarios the exception is logged-and-continued and the step
  is recorded as `status="error"`. The transcripts show happy paths;
  in production an unparseable LLM response or a `PHIBoundaryError`
  produces a refusal receipt (`STAGE_B_UNPARSEABLE` /
  `STAGE_PRE_PHI_BOUNDARY`) rather than crashing the harness.
- **Accuracy bucket.** All three categories *do* count toward
  `included_accuracy` via `_INCLUDED_CATEGORIES`. An `error` step in
  an LLM-path scenario fails its scenario, so it counts against the
  ≥99.5% gate. This is intentional: error verdicts in the LLM bucket
  surface real regressions, but they don't crash the test run.

## See also

- [`docs/rule-breakdown/decision-gate.md`](rule-breakdown/decision-gate.md)
  — the design rationale and primitive-set lock.
- [`docs/rule-breakdown/rules-vs-skills.md`](rule-breakdown/rules-vs-skills.md)
  — the rule-vs-skill decision table with full criteria.
- [`docs/rule-breakdown/inventory.md`](rule-breakdown/inventory.md) —
  the 40-rule corpus that anchors every primitive choice.
- [`docs/rules/conventions.md`](rules/conventions.md) — outcome
  prefix and field-naming rules for the YAML specs.
- `samantha_server/rules/specs/` — the 40 authored rule YAMLs.
- `samantha_server/skills/specs/` — the six authored skill playbooks.
