Fix high and medium severity issues from code review for PR $ARGUMENTS.

**Argument parsing.** Before SETUP, tokenize `$ARGUMENTS` by splitting on whitespace, then compare each token by **exact equality** (never substring) against the keywords `auto` and `full`. Strip both if present. Set `AUTO_CHAIN=true` if a token equal to `auto` was present and `FULL=true` if a token equal to `full` was present. Defaults: both flags are `false` if their keyword token is absent. Duplicate occurrences are idempotent — `auto auto` is treated the same as a single `auto`. The remaining tokens are the PR number. If a token's match is ambiguous, default to **not** setting the flag and warn — never substring-match.

`full` alone (without `auto`) is meaningless in this command — log a warning to stdout, do **not** set `FULL`, and continue execution normally (do not halt). Only `/merge-pr` consumes `full`; this command propagates it through the chain. Without `auto`, this command stops after FINAL SUMMARY as documented.

## SETUP

```bash
# Find the review directory (diff file was saved by /review-pr)
ls -d code_reviews/PR$ARGUMENTS-* 2>/dev/null | head -1
```

The diff file is at `code_reviews/PR$ARGUMENTS-<title>/pr.diff` (saved by `/review-pr`).

## CHECK FOR COMMENTS
ONLY If requested, check for @claude comments:
```bash
gh api repos/{owner}/{repo}/pulls/$ARGUMENTS/comments --jq '.[] | select(.body | contains("@claude")) | {id, path, body: .body[:80]}'
```

## GATHER FINDINGS

Read `CONSOLIDATED-REVIEW.md` from the review directory. It contains the Issue Matrix with severity, scope, and actionability already determined.

Also add any @claude PR comments as High severity issues (record comment ID for later reply).

## BUILD ISSUE MATRIX

Before implementing fixes, create a matrix of ALL issues using format from `.claude/memories/review-issue-matrix.md`.

## CREATE TODO LIST

Use TodoWrite to track actionable issues:
- One todo per issue with severity prefix (e.g., "[High] Fix docstring")
- Mark deferred items separately

## IMPLEMENT

**Use Sequential Thinking MCP** when fixes have dependencies or require tracing through code:
- Call `mcp__sequential-thinking__sequentialthinking` to reason through complex fixes
- Identify if fixing one issue affects others
- Plan the order of fixes to avoid conflicts

For each issue (Critical > High > Medium):
1. Mark todo in_progress
2. Read the file before editing
3. **If the fix introduces new behavior, delegate to `test-first-implementer`** so a failing test exists before the fix lands. Don't bypass TDD discipline for review-fix work — see `.claude/memories/kent-beck-principles.md` for the rules this agent enforces.
4. Implement the fix.
5. Mark todo completed.
6. Reply to @claude comments if applicable:
   ```bash
   gh api repos/{owner}/{repo}/pulls/$ARGUMENTS/comments/{ID}/replies --method POST -f body="Fixed. [description]"
   ```

## VALIDATE AND COMMIT

```bash
uv run ruff format samantha_server/ tests/
uv run ruff check samantha_server/ tests/
git add <changed-files>
git commit -m "fix: Address code review findings"
git push
```

Note: The pre-commit hook runs `uv run pytest` automatically before committing.
The mypy hook checks edited files individually; for a full project check run `uv run mypy samantha_server/`.

## EVALUATE LOW-SEVERITY FINDINGS

Run AFTER the High/Medium fixes are validated and committed, BEFORE the simplification pass.

**Scope.** This step is the **misclassified-Low backstop**. Critical/High/Medium findings are handled by IMPLEMENT (which iterates `Critical > High > Medium`). The reviewer template `.claude/agents/_base-reviewer.md` §"What is NOT Low" recommends reviewers target at-least-Medium for anti-pattern findings; this audit catches cases where a Low slipped through anyway. Medium-classified findings of the same shape are already in IMPLEMENT scope and don't need re-evaluation here.

**Do not auto-skip Lows.** A reviewer's severity label is a best guess; some Lows are misclassified bugs, not nits. Evaluate each Low row from `CONSOLIDATED-REVIEW.md` against the criteria below. Lesson that motivated this step: PR #202's "Low" #20 (`content: null` AttributeError) was a deployment-class bug — same shape as the Critical (#1) already in the matrix — and audit pressure surfaced a pre-existing twin in the legacy `complete()` path.

### Upgrade criteria — re-classify as effective High and fix now

A Low is effectively High if **any** of:

1. **Wrong exception type.** The finding describes a code path that raises an exception NOT in the project's typed-exception hierarchy (e.g., `AttributeError`, `pydantic.ValidationError`, `KeyError`) where a typed exception was warranted. Unhandled exception types escape upstream catch blocks and surface as 500s with no recovery path.
2. **Latent crash / data loss.** The finding describes an unhandled exception, a silent receipt-write failure, or a state-corrupting code path. Severity Low does not apply to crashes.
3. **Same failure shape as a Critical/High already in this PR's matrix.** If a Low explicitly or implicitly mirrors a higher-severity finding ("same as X in file Y"), it was misclassified. Apply the same fix pattern.
4. **Missing test for a production-only crash path.** The finding describes a test gap whose absence would hide a latent crash that only surfaces in production. This is the same severity as the crash it would expose — not test hygiene.

These four mirror the four anti-pattern guardrails in `.claude/agents/_base-reviewer.md` §"What is NOT Low" one-for-one. If you change one list, change both.

### Fix-while-cheap criteria — worth bundling

A Low is worth fixing now (even if not upgraded) when **both**:

- The fix is small (under ~10 lines including any test).
- The file is already in `git diff --name-only origin/main..HEAD` (touched by this PR).

These don't merit their own PR but are free to bundle while context is fresh.

### Genuinely-Low — skip with explicit rationale

- Cosmetic typing/style without correctness impact.
- Docstring polish that doesn't fix a misleading claim.
- Test hygiene that doesn't pin a real bug.

### Audit-for-twins

When fixing an upgraded Low, briefly check whether the underlying bug pattern (null guard, exception-type guard, off-by-one, missing await, etc.) appears elsewhere in the same module or in a parallel module (e.g., chat-completions ↔ legacy completions). One targeted grep is usually enough. If twins are found, fix them in the same audit pass — the audit-pressure window closes once this PR merges. PR #202 surfaced exactly such a twin (legacy `complete()` had the same null-text vulnerability as the new `complete_json()`).

Concern-parallel twins (modules that share an abstract concern but not a directory-parallel layout) are not covered by the standard grep; documented as an accepted limitation.

### Disposition stamping (operational definition)

EVALUATE LOWS materializes its decisions in `CONSOLIDATED-REVIEW.md` so downstream sections (HANDLE DEFERRED ITEMS, FINAL SUMMARY) can read the result rather than guess. At the start of the step, append a new section to `CONSOLIDATED-REVIEW.md`:

```markdown
## Low Audit — in progress
```

Then, for each Low row, append a table row:

```markdown
| Row # | Disposition | Reason |
|-------|-------------|--------|
| 20 | upgraded-fixed | wrong exception type |
| 22 | bundled-fixed | <10 lines + file already in PR diff |
| 23 | twin-fixed | grep audit after #20 surfaced this twin in legacy module |
| 21 | genuinely-deferred | cosmetic typing without correctness impact |
```

On clean finish, replace `## Low Audit — in progress` with `## Low Audit — complete`. HANDLE DEFERRED ITEMS treats `in progress` as an unfinished step and refuses to run; treats `complete` as authoritative for which Lows still need a deferred-item decision (only `genuinely-deferred` rows flow to that section if they warrant tracking).

### Execution

- For each Low row in `CONSOLIDATED-REVIEW.md`, apply the upgrade and fix-while-cheap criteria.
- **Auto mode (`AUTO_CHAIN=true`)**: apply criteria heuristically; do not invoke `AskUserQuestion`. Items that pass become new todos with `[Low→High]`, `[Low→Twin]`, or `[Low+cheap]` prefixes and are implemented immediately (with TDD discipline for behavior changes — see Beck's rule 1).
- **Manual mode (`AUTO_CHAIN=false`)**: when the criteria clearly match, fix without asking. Only fire `AskUserQuestion` when a Low is genuinely borderline — i.e., the criteria match weakly (e.g., the bug shape is similar to a Critical but in a code path documented as unreachable). If criteria clearly match, fix; if they clearly don't, skip. Borderline ≠ "I want to be cautious." When asking, batch 2-4 items in one question, not one-by-one.
- For each upgraded fix, write the failing test first (red), then the fix (green). **If the new test fails after the fix lands (regression), `git revert` the audit commit before proceeding** — do not commit a red test or a fix the audit can't verify.
- After fixes, re-run `uv run pytest && uv run ruff check samantha_server/ tests/ && uv run mypy samantha_server/` and commit as `fix: address low-severity findings on audit pass`.
- Mark the Low Audit log `complete` in `CONSOLIDATED-REVIEW.md` as part of the same commit.
- If no Lows are upgraded or bundled, still write `## Low Audit — complete` (with no rows) and note "all Lows evaluated and deferred" in the FINAL SUMMARY before continuing to the simplification pass.

## SIMPLIFICATION PASS

After all fixes are validated, launch the `code-simplifier` agent on the files modified during this fix round. The agent should:

1. Read only the files changed by fix commits (use `git diff --name-only origin/main..HEAD` — this captures every commit on the PR branch, not just the last one; `HEAD~1` is wrong because EVALUATE LOWS can add an audit commit between the H/M fix commit and the simplifier, hiding H/M files from the diff)
2. Look for complexity introduced by the fixes: unnecessary nesting, redundant guards, overly verbose patterns
3. Suggest simplifications that preserve behavior
4. Apply simplifications that are clearly safe (no semantic change)
5. Skip this step if the only changes were documentation or test files

If the simplifier makes changes, run validation again before committing:

```bash
uv run ruff format samantha_server/ tests/
uv run ruff check samantha_server/ tests/
git add <simplified-files>
git commit -m "refactor: Simplify code after review fixes"
git push
```

## HANDLE DEFERRED ITEMS

Read `CONSOLIDATED-REVIEW.md` and locate the `## Low Audit — complete` section (written by EVALUATE LOW-SEVERITY FINDINGS). If you find `## Low Audit — in progress` instead, EVALUATE LOWS didn't finish cleanly — halt and surface the partial state to the user rather than re-running. If the section is absent entirely, EVALUATE LOWS hasn't run — go back and run it.

Skip this section if no items remain that need a tracking decision. Items that flow in: (a) rare High/Medium that IMPLEMENT couldn't land (commit-and-defer), (b) `genuinely-deferred` rows from the Low Audit log whose disposition reason indicates "track-but-don't-fix" (e.g., real-but-bigger refactor) — most `genuinely-deferred` cosmetic Lows skip this section entirely.

For deferred items requiring a decision, use AskUserQuestion with options:
- Fix now
- Add to existing issue (if related issue found)
- Create new issue
- Skip

**Before reaching for "Create new issue", apply the consolidation rule.** If the deferred item is a design constraint on an existing planned issue (e.g. "blocked on GH-15", "needs scaffolding from the kernel", "design depends on rules engine"), default to commenting on that issue rather than spawning a new one. The bar for a new issue: the work could be picked up by a different contributor, on a different timeline, with its own definition of done. If you can't honestly say that, it's a constraint, not a follow-up — `gh issue comment` on the parent.

**If "Create new issue"**: Create a GitHub issue with `gh issue create`, then add a row to the Follow-ups table in `docs/project/implementation-todo.md` with the new issue's `GH-<N>` ID, `Open` status, the description, the source (e.g. `PR #N review`), and any currently-open blocking issues in `Depends on`. Insert it in execution-readiness order among the Open rows (most-pickable first). The column schema and eligibility rules live in `docs/project/implementation-todo-schema.md` — read it first if you're unsure what goes in `Depends on` vs `Gate` or what the em-dash sentinel means. Run `markdownlint-cli2 --fix "docs/project/implementation-todo.md"` and stage the change in the same commit as any other follow-up bookkeeping.

**If commenting on an existing issue instead**: use `gh issue comment <N> --body "..."` and skip the implementation-todo row entirely.

## FINAL SUMMARY

Post to PR with:
- Issues Fixed table (Critical / High / Medium)
- Low-severity audit results — four disposition categories: **Upgraded** (Low → fixed, with reason: "wrong exception type" / "same shape as #N" / "latent crash" / "missing test for production-only crash path"), **Twin** (bug surfaced by audit-pressure on an upgraded Low, not in the original review), **Bundled** (Low fixed via fix-while-cheap criteria — under 10 lines, file already in PR diff — without an upgrade), and **Genuinely deferred** (evaluated against criteria, decided to skip — record the criterion that excluded it).
- Deferred Items table (with decisions/outcomes)
- Validation results

Use a single markdown table for the Low audit rather than four separate sections — readers scan one block. Concrete example:

```markdown
### Low audit (from EVALUATE LOW-SEVERITY FINDINGS)

| Row # | Disposition | Reason |
|-------|-------------|--------|
| 20 | Upgraded | wrong exception type — same shape as Critical #1 |
| 23 | Twin | grep audit after #20 surfaced this in legacy module |
| 22 | Bundled | <10 lines + file already in PR diff |
| 21 | Genuinely deferred | cosmetic typing without correctness impact |
```

```bash
gh pr comment $ARGUMENTS --body "$(cat <<'EOF'
## Code Review Fixes Applied
[summary tables]
EOF
)"
```

**Reminders:**
- Build complete matrix BEFORE implementing
- New behavior gets a failing test first (delegate to `test-first-implementer`)
- Reply to all @claude comments
- Create GitHub issues for deferred items as needed

## AUTO-CHAIN (only when `AUTO_CHAIN=true`)

Skip this section entirely if `auto` was not in `$ARGUMENTS`.

**Preconditions for chaining** (all must hold):

- **No `AskUserQuestion` was invoked anywhere in this command.** Make this machine-checkable: any section that may invoke `AskUserQuestion` (EVALUATE LOW-SEVERITY FINDINGS for borderline Lows, HANDLE DEFERRED ITEMS for rare H/M deferrals) writes a sentinel file `code_reviews/PR<N>-<title>/.aqu_invoked` (touch-create) when it asks. AUTO-CHAIN's check is `test ! -f code_reviews/PR<N>-<title>/.aqu_invoked`. The sentinel is per-PR and isn't cleaned up between runs — a follow-up `/fix-review` invocation against the same PR inherits the prior "asked at least once" state, which is the correct conservative behavior.
- This precondition is **transitive across sub-agents.** If `test-first-implementer`, `code-simplifier`, or any other sub-agent invokes `AskUserQuestion`, the sub-agent's parent must propagate the sentinel write before returning.
- All actionable fixes were committed and pushed without error.
- VALIDATE AND COMMIT, EVALUATE LOW-SEVERITY FINDINGS (if any items fixed), and the optional SIMPLIFICATION PASS all succeeded.

If any precondition fails, stop after FINAL SUMMARY and tell the user why the chain didn't fire.

If all hold:

1. Build the chain args: `{PR#} auto` if `FULL` is unset, or `{PR#} auto full` if `FULL=true`.
2. Print: `Auto-chain: invoking /merge-pr {chain-args} — interrupt to stop.`
3. Invoke the Skill tool with `skill="merge-pr"`, `args="{chain-args}"`.
