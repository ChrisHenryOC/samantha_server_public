---
allowed-tools: Bash(gh pr comment:*),Bash(gh pr diff:*),Bash(gh pr view:*),Bash(mkdir:*),Bash(gh api:*),Bash(gh pr list:*)
description: Review a pull request
---

Review PR $ARGUMENTS (auto-detect from current branch if empty).

**Argument parsing.** Before Step 1, tokenize `$ARGUMENTS` by splitting on whitespace, then compare each token by **exact equality** (never substring) against the keywords `auto` and `full`. Strip both if present. Set `AUTO_CHAIN=true` if a token equal to `auto` was present and `FULL=true` if a token equal to `full` was present. Defaults: both flags are `false` if their keyword token is absent. Duplicate occurrences are idempotent — `auto auto` is treated the same as a single `auto`. Aspect keywords (e.g., `tests`, `security`) and the PR number are unaffected. If a token's match is ambiguous, default to **not** setting the flag and warn — never substring-match.

`full` alone (without `auto`) is meaningless in this command — log a warning to stdout, do **not** set `FULL`, and continue execution normally (do not halt). Only `/merge-pr` consumes `full`; this command propagates it through the chain. Without `auto`, this command stops after Step 4 as documented.

> Note: this command activates once `samantha_server` has a GitHub remote. Until then, reviewer agents can be invoked directly on local diffs or working-tree files via the `Agent` tool.

## Step 1: Setup

```bash
gh pr view $ARGUMENTS --json title,number -q '"\(.number) \(.title)"'
mkdir -p code_reviews/PR$ARGUMENTS-<sanitized-title>
gh pr diff $ARGUMENTS > code_reviews/PR$ARGUMENTS-<sanitized-title>/pr.diff
```

Directory name: PR number + lowercase title with non-alphanumeric replaced by hyphens.
The diff is saved to `code_reviews/PR{N}-{title}/pr.diff` for reuse by `/fix-review`.

## Step 2: Select and Launch Agents

### Aspect Filtering

If `$ARGUMENTS` includes aspect keywords after the PR number (e.g., `42 tests security`), only launch matching agents:

| Keyword | Agent |
|---------|-------|
| `quality` | code-quality-reviewer |
| `perf`, `performance` | performance-reviewer |
| `tests`, `testing` | test-coverage-reviewer |
| `docs`, `documentation` | documentation-accuracy-reviewer |
| `security`, `sec` | security-code-reviewer |
| `workflow`, `logic` | workflow-logic-reviewer |
| `errors`, `failures` | silent-failure-hunter |
| `types` | type-design-reviewer |
| `simplify` | code-simplifier |

### Auto-Detection (No Aspect Keywords)

If no aspect keywords are provided, auto-detect from the diff:

First, decide whether the PR is **docs-only**. Both conditions must hold:

- **(a)** Every changed path matches `*.md` or lives under `docs/`.
- **(b)** No changed path lives under `.github/workflows/`, `.claude/`, source code (e.g. `samantha_server/`), tests (e.g. `tests/`), or any behavior-carrying config (`pyproject.toml`, `uv.lock`, `Makefile`, `.markdownlint-cli2.jsonc`, `.gitleaksignore`, similar). These carry behavior even when Markdown-shaped (`.claude/commands/*.md` is a slash command; `.github/workflows/*.yml` is CI). Files like `.github/CODEOWNERS` (no extension, access-control config) also disqualify.

Both must hold — (a) without (b) misclassifies slash-command edits as docs-only.

1. **If the PR is docs-only**: run only documentation-accuracy-reviewer. The other "always run" agents have no code to reason about; running them produces noise and burns tokens. Skip all conditional rules below (they all key off code paths).
2. **Otherwise, always run**: code-quality-reviewer, workflow-logic-reviewer, silent-failure-hunter. Then layer the conditional rules below.
3. **If test files changed** (`tests/`): test-coverage-reviewer.
4. **If docs changed** (`docs/`, `*.md`): documentation-accuracy-reviewer.
5. **If kernel, rules engine, LLM layer, or eval harness code changed**: performance-reviewer.
6. **If API keys, config, signing keys, PHI-handling code, or access-control files (e.g. `.github/CODEOWNERS`) changed**: security-code-reviewer.
7. **If Pydantic models, dataclass definitions, or tool signatures changed**: type-design-reviewer.

When in doubt about whether a file is "code-bearing" — i.e. whether (b) trips — include the agent. It costs less than missing a real issue. The docs-only carve-out is a hard rule (both conditions must hold), not a judgment call.

### Launch

Launch selected agents in parallel. Each agent reads `code_reviews/PR$ARGUMENTS-<title>/pr.diff` and saves findings to `code_reviews/PR$ARGUMENTS-<title>/{agent}.md`.

## Step 3: Consolidate

After agents complete, create `PR$ARGUMENTS-CONSOLIDATED-REVIEW.md`.

### Verification Gate (before dedup)

For each finding, confirm it names concrete triggering inputs/state and the resulting wrong output, crash, or invariant breach. Classify:

- CONFIRMED: triggering inputs and wrong outcome are nameable. Keep.
- PLAUSIBLE: mechanism is real but the trigger can't be pinned from the diff. Keep, but mark "(unverified)" in the Issue column.
- REFUTED: cannot ground a failure scenario. Drop, and list dropped findings in a "Refuted at consolidation" appendix so the signal isn't silently lost.

Critical-severity findings (per `_base-reviewer.md`: security vulnerabilities, data loss, breaking changes, incorrect workflow logic, architectural-invariant violations) are never REFUTED. If a Critical cannot be grounded to a concrete trigger from the diff, classify it PLAUSIBLE "(unverified)" and keep it; an invariant breach is reportable whether or not a runtime trigger is nameable. This prevents a top-severity finding from being silently dropped and from flipping the Step 5 actionable-count to zero.

When the same finding is raised by more than one agent with different labels, take the strongest: CONFIRMED over PLAUSIBLE, and never REFUTE a finding any agent grounds as CONFIRMED.

### Deduplication Rules

Before building the Issue Matrix:

1. **Merge duplicates**: If two or more agents flag the same issue (same file, same line, same root cause), merge them into a single row. List all reporting agents in the Reviewer(s) column.
2. **Severity conflicts**: When agents disagree on severity, use the highest level.
3. **Complementary findings**: If agents flag the same location but for different reasons (e.g., security-code-reviewer flags an injection risk AND code-quality-reviewer flags missing validation), keep them as separate rows — they require different fixes.

### Output Format

```markdown
# Consolidated Review for PR #$ARGUMENTS

## Summary
[2-3 sentences]

## Issue Matrix
(Use format from `.claude/memories/review-issue-matrix.md`, with columns in the exact same order as in the template)

## Actionable Issues
[Issues where In PR Scope AND Actionable are Yes]

## Deferred Issues
[Issues where either is No, with reason]
```

## Step 4: Post Comment

```bash
gh pr comment $ARGUMENTS --body "[summary by severity]"
```

## Step 5: Auto-chain (only when `AUTO_CHAIN=true`)

Skip this section entirely if `auto` was not in `$ARGUMENTS`.

Inspect the consolidated Issue Matrix and count rows where **In PR Scope = Yes AND Actionable = Yes AND Severity ∈ {Critical, High, Medium}**.

Build the chain args: `{PR#} auto` if `FULL` is unset, or `{PR#} auto full` if `FULL=true`.

- **If that count is zero** (no actionable Medium-or-higher issues): print `Auto-chain: no actionable issues at Medium+ — invoking /merge-pr {chain-args} — interrupt to stop.` Then invoke the Skill tool with `skill="merge-pr"`, `args="{chain-args}"`.
- **Otherwise**: print `Auto-chain: invoking /fix-review {chain-args} — interrupt to stop.` Then invoke the Skill tool with `skill="fix-review"`, `args="{chain-args}"`.

If any reviewer agent failed or the consolidated review could not be written, do not chain — surface the error and stop.

---

## Agent Instructions

Each agent:
1. Read `code_reviews/PR{NUMBER}-<title>/pr.diff` using the Read tool
2. Save findings to `code_reviews/PR{NUMBER}-<title>/{agent-name}.md` with:
   - Summary (2-3 sentences)
   - Findings by severity
   - File:line references
