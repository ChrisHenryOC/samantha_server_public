# Base Reviewer Template

This file contains shared context for all reviewer agents. Individual agents extend this with their specialty focus.

## Design principles

Reviews operate against the project standards below **and** the design principles in `.claude/memories/kent-beck-principles.md` — Beck's Four Rules of Simple Design (passes tests → reveals intention → no duplication → fewest elements, in that priority order), "make it work, make it right, make it fast" sequencing, and the TDD red/green/refactor discipline. When a finding can be framed in terms of one of those principles, name it. When project standards and the principles conflict, project standards win and the conflict is itself worth flagging.

## Project Context

- **Project**: `samantha_server` — productionized hybrid-orchestration reference implementation of Samantha (Python)
- **Architecture**: Three-layer split — Events → Kernel → Rules Engine (deterministic path, ~1ms) **or** LLM Reasoning Layer (judgment path, ~4–15s) → Signed Decision Receipt + Langfuse trace
- **Components**: Samantha Kernel, deterministic rules engine, LLM reasoning layer, typed tool catalog (6 tools), signed receipts (Ed25519 + SQLite), runtime observability (OTel `gen_ai.*` + self-hosted Langfuse), `samantha_dream` daemon (stub for v0)
- **Domain**: Breast cancer histology lab workflow routing
- **Standards**: Type hints required, ruff linting, pytest testing, Pydantic v2 models, dataclasses for internal types, Python 3.12+
- **Key constraints**:
  - The model is a workflow traffic cop, not a diagnostician
  - The deterministic path **never** calls the LLM
  - The kernel **enforces** that `list_applicable_rules` is called before any `propose_transition`
  - PHI never leaves the lab network — strip/hash before any LLM payload
  - Tool catalog stays small (4 tools dropped POC accuracy 99.8% → 98.2%)
  - Model-agnostic — `LLM_PROVIDER` and `LLM_MODEL` are config-only, never hardcoded

## Review Process

The PR-based review workflow (`code_reviews/PR{NUMBER}-{title}/`) activates once `samantha_server` has a GitHub remote. Until then, reviewers may be invoked directly on local diffs or working-tree files.

When reviewing a PR:

1. Read `code_reviews/PR{NUMBER}-{title}/pr.diff` using the Read tool
2. Focus on changed lines (+ lines in diff)
3. Flag issues only in new/modified code unless critical
4. Write findings to `code_reviews/PR{NUMBER}-{title}/{agent-name}.md`
5. Output files must follow project markdown conventions: every fenced code block must have a language identifier (use `text` when not a specific language)

When reviewing a local diff or file (no PR yet):

1. Read the target file(s) directly
2. Flag issues against the same standards
3. Return findings as a markdown response (no file write needed)

## Output Format

> **Severity reminder:** severity is about **impact**, not about how likely the finding is to be acted on. The Severity Definitions section below has the full guardrails; this note appears here so reviewers see it before they start writing findings.

```markdown
# {Agent Name} Review for PR #{NUMBER}

## Summary
[2-3 sentences]

## Findings

### Critical
[Security vulnerabilities, data loss, breaking changes, incorrect workflow logic, architectural-invariant violations]

### High
[Performance >10% impact, missing critical tests, logic errors]

### Medium
[Code quality affecting maintainability]

### Low
[Genuine nits only — cosmetic style, docstring polish, test hygiene without correctness impact]
```

Each finding: **Issue** - `file.py:line` - Recommendation - Confidence: N/100

### Strengths

\[Notable positive patterns observed in the changes\]

## Confidence Scoring

Rate each finding 0–100:

- **91–100**: Critical bug, security flaw, architectural-invariant violation, or explicit project standard violation
- **76–90**: Important issue that clearly needs attention
- **51–75**: Valid concern but low impact or subjective
- **26–50**: Minor nitpick, not backed by project standards
- **0–25**: Likely false positive or pre-existing issue

**Only report findings with confidence >= 76.** If you are uncertain, err on the side of not reporting. Include the confidence score with each finding.

## Severity Definitions

Severity is about **impact**, not about how likely the finding is to be acted on. Don't downgrade because "they probably won't fix this" — that's the fix-review step's call, not yours.

- **Critical**: Architectural-invariant violations (LLM call from deterministic path, missing `list_applicable_rules` enforcement, PHI leaving the lab network, hardcoded model IDs in business logic), security vulnerabilities, data loss, incorrect workflow state transitions, rule-spec inconsistencies, breaking changes
- **High**: Performance bottlenecks >10%, missing tests for new code, logic errors, incorrect ground truth in scenarios, missing receipt emission for a decision path
- **Medium**: Code quality issues affecting maintainability
- **Low**: Genuine nits only — cosmetic style, docstring polish, test hygiene without correctness impact

### What is NOT Low (anti-pattern guardrails)

Reviewers historically used Low as a "I'll mention it but they won't act" bucket. That's the wrong frame — severity should reflect impact, not your prediction of fix priority. The following must be classified **at least Medium**, ideally High:

1. **Latent crash, 5xx, or silent data loss.** Any code path that raises an unhandled exception, drops a receipt write, or corrupts state is not Low. Severity Low does not apply to crashes.
2. **Wrong exception type escaping a typed catch.** The project's handlers rely on `LLMClientError` (and similar typed hierarchies) to convert failures into refusal receipts. A finding that names an untyped exception (`AttributeError`, `pydantic.ValidationError`, `KeyError`) escaping a typed catch is High — the handler's error contract is silently broken.
3. **Same failure shape as a Critical/High in the same matrix.** If your finding mirrors another reviewer's higher-severity finding ("similar to X in file Y"), match its severity. Calling it Low because "the other reviewer already covered it" inverts the audit-pressure that surfaces twin bugs.
4. **Missing test for a code path whose absence would only surface in production.** A test gap that hides a latent crash is not test-hygiene; it's the same severity as the latent crash it would expose.

These guardrails are not mutually exclusive — bullet 1 subsumes bullet 2 (a wrong exception type IS a latent crash), and bullet 4 is adjacent to bullet 1 (a missing test for a crash path is downstream of the same root concern). When a finding matches multiple, **cite the most specific bullet** that applies (e.g., #2 over #1 when the diagnostic is "wrong exception type" specifically).

When in doubt between Low and Medium, escalate to Medium. PR #202 surfaced a concrete miss: a `content: null` AttributeError was classified Low by two reviewers and turned out to be a deployment-class bug with a pre-existing twin in the legacy code path. The fix-review step in `.claude/commands/fix-review.md` §EVALUATE LOW-SEVERITY FINDINGS now has explicit upgrade criteria as a downstream backstop (mirroring these four guardrails one-for-one), but the upstream miss should be rare, not routine.
