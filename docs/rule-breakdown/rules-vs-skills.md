# Rules vs. skills (W1 Step 13)

Two LLM-consultable knowledge formats coexist in `samantha_server`: the
**rule** (deterministic primitive-composed predicate) and the **skill**
(prose playbook authored to the [agentskills.io](https://agentskills.io/home)
standard). This doc captures when to author each and why both are needed.

## Quick reference

| Aspect | Rule | Skill |
|---|---|---|
| Format | Python predicate built from the locked primitive vocabulary (see [`decision-gate.md`](decision-gate.md)) | `SKILL.md` folder with YAML frontmatter (`name`, `description`) and prose body |
| Runtime | Pure-function evaluation in the deterministic engine; µs-latency | LLM consults the body in-context; latency dominated by model call |
| Correctness | Fully testable against the scenario corpus; behavior pinned to source | Validated by scenario replay through the LLM path; prose can drift from intent |
| Audit trail | Receipt records the matched `RuleSpec` ID | Receipt records the activated skill name and the LLM's resulting decision |
| Mutability | Frozen at import; changes require code review and a regression suite pass | Editable as prose; changes require scenario-replay validation |
| Authoring agent | `rule-primitive-author` / `test-first-implementer` | Domain SME prose, reviewed by `documentation-accuracy-reviewer` |

## When to author a rule

Author a deterministic rule when **all** of the following hold:

- The decision reduces to a primitive-composed predicate (`IsNull`,
  `Equals`, `ThresholdGTE`, `ThresholdLTE`, `InEnum`, `Contains`,
  `BooleanAnd`, `BooleanOr`, `Not` — see
  [`decision-gate.md` § "Final primitive set"](decision-gate.md#final-primitive-set)).
- The trigger fields are present and structurally typed in the order/event
  payload (no free-text interpretation, no inference across messages).
- µs-scale latency matters — the rule is on a hot routing path or fires
  on every event in a step.
- The rule is correctness-critical: a mismatch between intent and
  behavior is a safety regression, not a UX paper-cut.

The 40 rules in [`inventory.md`](inventory.md) all satisfy these criteria
and decompose cleanly into the 9 primitives. That is the rule lane.

## When to author a skill

Author a skill when the decision **does not** fit the primitive vocabulary,
typically because:

- It requires reading or summarizing free-text fields (e.g., a pathology
  narrative, a clinician note).
- It requires cross-event reasoning the dispatch contract does not surface
  (e.g., "given the last three events, was the QC trend deteriorating?").
- It is a soft heuristic where the cost of a wrong answer is acceptable
  and the benefit of a probabilistic call is high (triage-style routing,
  ambiguity resolution before a human review).
- The decision space is open-ended — no enumeration of inputs makes a
  predicate tractable.

Skills are LLM-consultable prose: the loader (Step 13) hands the body to
the model in-context, and the model returns a decision constrained by
the playbook. The W1 Phase 3 LLM path will consume them; until then
`SkillResult.execute()` raises `NotImplementedError`.

## Why both formats coexist

A purely deterministic engine cannot cover open-ended reasoning. A purely
LLM-driven engine cannot meet the latency, auditability, and regression
guarantees the rule lane provides. The two formats partition the
decision space:

- **Rule lane** owns every decision the primitive vocabulary expresses.
  This is the bulk of routing — accessioning gates, sample-prep
  transitions, IHC dispatch, resulting flow. The kernel's
  `list_applicable_rules` gate enforces that any deterministic transition
  flows through a rule.
- **Skill lane** owns the residue — the open-ended interpretive calls
  that would otherwise force ad-hoc primitive expansion or hand-rolled
  prompt templates.

The dispatch contract (see
[`decision-gate.md` § "Dispatch contract"](decision-gate.md#dispatch-contract))
remains the boundary: rules run first; skills are consulted only when no
rule matches or when an explicit `applies_at` slot is reserved for an
LLM-consultable decision.

## How agentskills.io provides the runtime contract

[agentskills.io](https://agentskills.io/home) defines a three-stage
lifecycle that the loader (`samantha_server/skills/loader.py`)
implements:

1. **Discovery.** Scan `samantha_server/skills/specs/*/SKILL.md`, parse
   YAML frontmatter, build an in-memory index of `(name, description,
   path)`. Designed to be called **once at engine init** and the index
   passed to subsequent `load()` calls; the loader supports the
   no-index convenience form for ad-hoc callers but re-globs the
   filesystem when the index is omitted. The `description` field is the
   activation-matching key in the W1 Phase 3 LLM path.
2. **Activation.** `load(name, index=...)` reads the full `SKILL.md`
   body on demand and returns the prose the model will receive in its
   prompt. Hot-path callers should pass the pre-built index from
   discovery; the convenience signature without `index` re-runs
   discovery and is intended for tests and one-shot scripts.
3. **Execution.** `execute(name, ctx)` is stubbed with
   `NotImplementedError` until W1 Phase 3 lands the LLM invocation. The
   slot exists so callers can wire the seam now and the implementation
   can drop in without API churn.

The standard locks a uniform on-disk shape (`SKILL.md` with `name` and
`description` frontmatter) and a uniform load contract. It does **not**
constrain the prose body — that remains domain-authored.

## Directory disambiguation

`samantha_server/skills/specs/` is **project-local**. These skills live
inside the `samantha_server` package and are loaded only by the engine.
They do **not** install into Claude Code's global `~/.claude/skills/`,
and they do **not** conflict with the repo's `.claude/skills/` directory
(which holds Claude Code *harness* skills like `init`,
`frontend-design`, used during development). The two namespaces never
cross.

## Format-evolution risk

The agentskills.io standard is young; the frontmatter schema may evolve.
The loader keeps the parse surface narrow (two required fields) so
schema additions land as additive frontmatter keys without breaking the
index. If the standard introduces structural changes (e.g., a manifest
file, a different on-disk layout), the loader is the only adapter point.
