---
description: Merge a PR and clean up branches
allowed-tools: Bash(gh pr view:*),Bash(gh pr merge:*),Bash(gh issue view:*),Bash(git checkout:*),Bash(git pull:*),Bash(git push:*),Bash(git fetch:*),Bash(git branch:*),Bash(git add:*),Bash(git commit:*),Bash(git status:*),Bash(git rev-parse:*),Bash(git rev-list:*),Bash(markdownlint-cli2:*),Read,Edit,Grep
---

Merge PR $ARGUMENTS with automatic implementation-todo.md updates.

**Argument parsing.** Tokenize `$ARGUMENTS` by splitting on whitespace, then compare each token by **exact equality** (never substring) against the keywords `auto` and `full`. Strip both if present. Set `AUTO_CHAIN=true` if a token equal to `auto` was present and `FULL=true` if a token equal to `full` was present. Defaults: both flags are `false` if their keyword token is absent. Duplicate occurrences are idempotent — `auto auto` is treated the same as a single `auto`. The remaining tokens form the PR-number argument.

`auto` is parsed only for symmetry with the upstream chain commands; **`AUTO_CHAIN` is not consumed anywhere in `/merge-pr`** (this command is the terminal step within a single issue's chain, so there is no in-issue chain to gate). `FULL=true` is the sole activation gate for Step 5 (the cross-issue chain), and `full` works whether or not `auto` is also present.

If a token's match is ambiguous (e.g., a hypothetical aspect keyword starting with `auto…`), default to **not** setting the flag and warn — never substring-match.

## Step 1: Resolve PR and linked issues

```bash
gh pr view $ARGUMENTS --json number,headRefName,closingIssuesReferences \
  -q '{number, branch: .headRefName, issues: [.closingIssuesReferences[].number | "GH-" + tostring]}'
```

Save from this output:

- `branch` — the PR's head branch name.
- `JUST_CLOSED` — the list of `GH-<N>` **strings** the PR will close on merge (each formed by prepending `GH-` to the integer issue number). May be empty. This set is used as a runtime loop guard in Step 5; the values must be `GH-<N>` strings (not bare integers) so they compare correctly to row IDs in the todo file.

## Step 2: Update implementation-todo.md

**If the PR closes one or more GitHub issues:**

1. Check out the PR's branch: `git checkout <branch>`
2. Read `docs/project/implementation-todo.md`
3. For each closing issue number, find the table row containing `GH-<number>`
   and change its Status column from `Open` to `Done` using the Edit tool.
   If a `GH-<number>` is not found in the file, skip it silently — Step 5's
   `JUST_CLOSED` loop guard and `gh issue view` cross-check together cover
   the resulting stale-Open case.
4. Run: `markdownlint-cli2 --fix "docs/project/implementation-todo.md"`
5. Commit and push:

```bash
git add docs/project/implementation-todo.md
git commit -m "docs: Mark GH-<numbers> as Done in implementation todo"
git push
```

**If the PR closes no issues**, skip this step entirely.

## Step 3: Merge and clean up

Run each command in sequence. **Halt the entire command** (no Step 4, no Step 5) if any returns a non-zero exit code; print the failing command and its stderr to the user before stopping.

```bash
gh pr merge $ARGUMENTS --merge --delete-branch
git checkout main
git pull origin main
git fetch --prune origin
```

Partial completion (e.g., merge succeeded but `git pull` failed) leaves the local repo in an undefined state for the cross-issue chain — halting here is required to keep Step 5's pre-chain checks meaningful.

## Step 4: Report

Report:

- Which issues (if any) were marked Done in implementation-todo.md
- "PR #$ARGUMENTS merged. On branch main."

## Step 5: Full-auto cross-issue chain (only when `FULL=true`)

Skip this section entirely if `FULL` is not set.

### Pre-chain checks

All of the following must hold; **treat any non-zero exit code as a check failure** (not just an unmet stdout assertion). On any failure, print the failing command and its stderr to the user, then stop without chaining.

```bash
git status --porcelain                        # exit 0, empty stdout (clean working tree)
git rev-parse --abbrev-ref HEAD               # exit 0, stdout == "main"
git rev-list --count HEAD..origin/main        # exit 0, stdout == "0" (not behind origin)
git rev-list --count origin/main..HEAD        # exit 0, stdout == "0" (not ahead of origin — guards against silent push failures in Steps 2/3)
```

The "ahead" check is symmetric to the "behind" check on purpose: a silent push failure in Step 2's `git push` (or any earlier step that committed locally without pushing) leaves local `main` ahead of `origin/main`. Without this check, the chain would proceed and eventually try to push from a branch that diverges from origin.

### Select next eligible issue

> **Schema source of truth:** the rules in this section are an inline copy of the canonical schema at [`docs/project/implementation-todo-schema.md`](../../docs/project/implementation-todo-schema.md). The duplication is deliberate — `/merge-pr` runs against its own context window and can't depend on having loaded another doc — but if you change one, change both. CI doesn't enforce this; reviewers do.

Read `docs/project/implementation-todo.md` and walk the **active** tables in this order: **Follow-ups → Phase 2**. This walk order is authoritative for selection — it takes precedence over the Follow-ups table's "execution-readiness order" comment (the comment is a cosmetic ordering preference; if Follow-ups has any Open row, that row is picked first regardless of cosmetic ordering).

Closed issues are not retained in this file: when a row transitions to `Done` (Step 2 above flips the cell), the row is deleted in the same PR or in a subsequent cleanup pass, and any active row that depended on it has its `Depends on` cell trimmed to drop the now-resolved token. Dependency resolution is therefore over present rows only — if a `Depends on` token references a `GH-<N>` that does not appear in the file, treat the row as ineligible **and** print a warning (the editor likely forgot to trim a resolved-Done dep at deletion time).

A row is **eligible** iff **all** of:

1. The `GH Issue ID` cell matches the pattern `GH-<digits>` exactly. Rows with non-conforming IDs (e.g., `(TBD)`, blank, or any other format) are never eligible.
2. The `Status` cell equals `Open` exactly (case-sensitive; `OPEN` / `open` are schema violations and should never appear, but if they do, treat the row as ineligible and warn).
3. The row's `GH-<N>` ID is **not** in `JUST_CLOSED` (single-step loop guard against the row we just merged, in case Step 2 silently skipped it; this is **not** a multi-hop cycle detector).
4. Every `GH-<N>` token in the `Depends on` cell resolves to a row in this file whose `Status` is `Done`. A cell of `—` (em-dash, not regular hyphen) trivially satisfies this. Tokens pointing at rows not present in the file are schema violations (per the cleanup-pass discipline above) and disqualify the row with a warning. The check is **non-transitive** — a stale `Done` flag on an intermediate dependency is the human's responsibility to maintain.
5. The `Gate` cell is exactly `—` (em-dash). Any non-empty gate text disqualifies the row.

Pick the first eligible row in walk order. If none is eligible, print `Full-auto: no eligible Open issues remain. Chain complete.` and stop.

**Tokenizing `Depends on`.** Split the cell on commas, trim whitespace from each token, then require each non-empty token to match `GH-<digits>` exactly. If any token does not match, the cell is malformed — treat the row as ineligible **and** print a warning naming the row ID and the offending token. Visible warnings beat silent disqualification: editors must be able to see their schema mistakes.

### Cross-check against GitHub

For the chosen `GH-<N>`:

```bash
gh issue view <N> --json state,title -q '{state, title}'
```

The `state` field must equal `OPEN`. If the call fails, returns non-zero, or returns any other state, print the discrepancy and stop without chaining. Also save the `title` field as `<title>` for the Invoke step.

This catches stale todo state, manually-closed issues, and Step 2 silent-skips. There is a benign TOCTOU race between this check and the `/issue` invocation — `/issue` § 1 re-checks the chosen issue's state before doing real work, so a state change in that narrow window halts cleanly via the existing § 1 path (second-line defense).

### Notify (optional, deferred)

**This subsection is not yet implemented — skip it entirely.** Future work: when an iMessage handle is configured (e.g., in `~/.claude/settings.json`), send a notification on chain stop with reason in `{human-needed, error, complete}`.

### Invoke

Print one line: `Full-auto: chaining to <chosen-id> (<title>) — interrupt to stop.` (where `<chosen-id>` is the `GH-<N>` from selection and `<title>` came from the GH cross-check).

Then build the chain args (`FULL=true` always at this point, since Step 5 is gated on it): `args="<N> auto full"` where `<N>` is the bare integer from the chosen `GH-<N>`.

Invoke the Skill tool with `skill="issue"`, `args="<N> auto full"`.

### Halt-and-notify summary

The chain stops without invoking `/issue` if any of: pre-chain checks fail, no eligible row, GH cross-check fails, or schema violation in todo file. In each case print the reason — the user can resume manually after inspecting.
