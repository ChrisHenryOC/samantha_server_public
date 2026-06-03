---
allowed-tools: Bash(gh issue view:*),Bash(gh issue list:*),Bash(git checkout:*),Bash(git branch:*),Bash(git push:*),Bash(uv run pytest:*),Bash(uv run ruff:*),Bash(uv run mypy:*),Read,Write,Edit,Glob,Grep,TodoWrite
description: Analyze and fix a GitHub issue using test-driven development
---

# Analyze and fix GitHub issue using TDD: $ARGUMENTS

All features and fixes in `samantha_server` are implemented test-first. This command is the single entry point — there is no separate non-TDD path. Read `.claude/memories/kent-beck-principles.md` before starting work: red/green/refactor loop, Beck's two TDD rules (no production code without a failing test; eliminate duplication), the Four Rules of Simple Design as the refactor checklist, and "make it work → make it right → make it fast" as the sequencing.

**Argument parsing.** Before § 1, tokenize `$ARGUMENTS` by splitting on whitespace, then compare each token by **exact equality** (never substring) against the keywords `auto` and `full`. Strip both if present. Set `AUTO_CHAIN=true` if a token equal to `auto` was present and `FULL=true` if a token equal to `full` was present. Defaults: both flags are `false` if their keyword token is absent. Duplicate occurrences are idempotent — `auto auto` is treated the same as a single `auto`. The remaining tokens are the issue argument. If a token's match is ambiguous, default to **not** setting the flag and warn — never substring-match.

`full` alone (without `auto`) is meaningless in this command — log a warning to stdout, do **not** set `FULL`, and continue execution normally (do not halt). Only `/merge-pr` consumes `full`; this command's role is to propagate it through the chain. Without `auto`, this command stops after § 8 as documented.

> Note: the GitHub steps below activate once `samantha_server` has a GitHub remote. Until then, treat `$ARGUMENTS` as a free-form feature description, skip the `gh` invocations, and proceed from § 2 onward — branch creation and PR steps no-op gracefully without a remote.

## 1. IDENTIFY & APPROVE

**Required before ANY work** (skip if no GitHub remote yet):

1. Find issue:
   - **Numeric `$ARGUMENTS`**: run `gh issue view $ARGUMENTS --json number,title,state`. If the call succeeds and `state == "OPEN"`, the user has unambiguously chosen the work — **proceed directly to § 2 without invoking `AskUserQuestion`**. Briefly state which issue you're starting (number + title) so the user can interrupt if it's the wrong one. Note: this is about *which issue to start*, not the issue's resolution status — the GitHub issue itself is still open and untouched.
   - **`"next"` or no argument**: run `gh issue list --state open --sort created` and **use `AskUserQuestion`** to confirm which issue to work on.
   - **Free-form text** (no GitHub remote, or `$ARGUMENTS` not a valid issue number): treat as a feature description. **Use `AskUserQuestion`** to confirm scope before proceeding.
2. If the issue lookup fails (404, closed issue, network error), surface the error and **use `AskUserQuestion`** to ask how to proceed (retry, treat as free-form, abort).
3. `AskUserQuestion` is the gate only when scope is ambiguous. A valid open issue number is unambiguous — trust the user's explicit choice and continue.

## 2. PLAN

**Use Sequential Thinking MCP** for complex issues to break down the problem:
- Call `mcp__sequential-thinking__sequentialthinking` to reason through the approach
- Identify dependencies, affected files, and potential risks
- Revise thinking as you explore the codebase

Steps:

1. `gh issue view` for full details (or use `$ARGUMENTS` directly if no remote yet).
2. Read the relevant existing code. Use `Glob` and `Grep` first; only `Read` files that look directly relevant.
3. For complex issues, launch the **Explore agent** (quick) to find similar patterns in source.
4. **Decompose into TDD slices.** Break the feature into the smallest possible behavior slices. Each slice = one red-green-refactor cycle = one new failing test driving one minimal implementation step. Record slices with `TodoWrite`.

## 3. BRANCH

1. Create branch: `feature/issue-{number}-{desc}` or `fix/issue-{number}-{desc}` (skip if no remote yet — work on the current branch).
2. All commits go to the feature branch — never commit to main.

## 4. IMPLEMENT (delegate to `test-first-implementer`)

Launch the `test-first-implementer` agent with the feature description and the slice plan from § 2. The agent owns the loop:

- **Red**: write a failing test, run it, confirm it fails for the right reason (real assertion failure, not a typo or missing import).
- **Green**: minimum code to pass.
- **Refactor**: improve clarity. Invoke `code-simplifier` if the implementation grew non-trivial. Re-run tests after each refactor pass.
- Repeat per slice until the feature is done.

The agent will run the full validation pass at the end (`ruff format`, `ruff check --fix`, `mypy`, full `pytest`).

**Constraints (non-negotiable):**

- Never write production code without a failing test that requires it. This includes "obvious" code, error handling, and edge cases. (Beck's TDD rule 1.)
- Never broaden a test to make it pass. Fix the implementation, not the assertion.
- Never skip the refactor pass when the implementation grew non-trivial.
- Never commit during the loop. Commits happen at slice boundaries or feature completion, after the full suite is green.
- Never abstract before you have multiple use cases. Speculative abstraction is a Beck anti-pattern — inline first, abstract on the second use.

## 5. ARCHITECTURAL INVARIANTS

Before pushing, verify the change doesn't violate any of:

- **Deterministic path purity** — code in `rules_engine.py` and `rules/` doesn't import the LLM client.
- **`list_applicable_rules` enforcement** — any code path proposing a transition flows through the kernel's gate.
- **Receipt emission** — every decision path emits a signed receipt.
- **Model-agnostic discipline** — no hardcoded model IDs outside `config.py` / `.env`.
- **PHI boundary** — any LLM-payload construction strips/hashes PHI fields.

If any are at risk, launch the `workflow-logic-reviewer` or `code-quality-reviewer` agent on the changed files before pushing.

## 6. VALIDATION (sanity check after `test-first-implementer` reports done)

1. `uv run pytest` (also enforced by pre-commit hook on `git commit`).
2. `uv run ruff format samantha_server/ tests/ && uv run ruff check samantha_server/ tests/ --fix`.
3. `markdownlint-cli2 --fix "**/*.md"`.
4. `uv run mypy samantha_server/` (per-file mypy runs automatically on Edit/Write via hook; this is the full project pass).
5. Confirm all functions have type annotations.

## 7. PUSH & PR

(Skip if no GitHub remote yet — stop here and report locally.)

1. Push: `git push -u origin {branch}`.
2. Create PR: `gh pr create` with title `{feat|fix}: description`, body includes `Closes #{github_issue_number}`.

Code review happens via `/review-pr` after PR creation.

## 8. REPORT

Summarize back to the user:

- Issue (or feature) implemented.
- Slices completed (test file → production file mapping).
- Number of tests added.
- Files changed.
- Any deferred follow-up (with rationale).
- PR URL (if pushed).

## 9. AUTO-CHAIN (only when `AUTO_CHAIN=true`)

Skip this section entirely if `AUTO_CHAIN` is not `true`.

**Preconditions for chaining** (all must hold):

- `AskUserQuestion` was **not** invoked at any point during § 1 through § 8 — covers § 1's scope-choice path AND any § 2 / § 4 / § 5 path that may surface a question (e.g., Sequential Thinking flagging an unresolved decision, the `test-first-implementer` agent halting for guidance, or an architectural-invariant reviewer escalating). The chain only fires when the entire issue completed without human adjudication.
- § 7 successfully pushed a branch and created a PR (a PR number is known).
- No step in § 4–§ 6 reported a failure.

If any precondition fails, stop after § 8 and tell the user why the chain didn't fire.

If all preconditions hold:

1. Build the chain args: `{PR#} auto` if `FULL` is unset, or `{PR#} auto full` if `FULL=true`.
2. Print one line: `Auto-chain: invoking /review-pr {chain-args} — interrupt to stop.`
3. Invoke the Skill tool with `skill="review-pr"`, `args="{chain-args}"`.
