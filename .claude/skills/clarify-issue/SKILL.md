---
name: clarify-issue
description: Use this skill when the user wants to work through open questions / clarifications on a GitHub issue before implementation — phrases like "walk me through GH-105", "let's clarify issue 109", "help me think through the open questions on this issue", or pointing at multiple issues that need refinement (e.g., "GH-105, 109, 110, 111"). The skill drives one question at a time via `AskUserQuestion`, presents a recommendation per question, and posts the resolutions back to the issue as a comment. Do NOT use this skill for issues the user is ready to implement — that's `/issue`.
version: 0.1.0
---

# Clarify a GitHub issue

This skill exists because the user's hard preference is **one question at a time, with a recommendation, never a wall of text**. If you find yourself about to dump a multi-question list into chat, stop — that's the failure mode this skill prevents.

## When this fires

The user is in *triage / refinement* mode on a specific GitHub issue: open questions need answers before implementation can start. They want to think through each decision, not pre-commit to a plan.

If the user is ready to **implement**, route to `/issue` instead. If they want a high-level summary of an issue, just summarize — don't invoke the question-walk machinery.

## The shape of the work

### 1. Load the issue

`gh issue view <N> --json number,title,body,state,comments,labels`. State the issue number + title in one line so the user can interrupt if you've grabbed the wrong one.

- **If the call fails** (404, auth, network, rate limit, non-zero exit): surface the stderr to the user verbatim and stop — do not proceed with an empty issue body. Ask via `AskUserQuestion` (`Retry` / `Switch issue` / `Abort`) before doing anything else.
- **If the issue is closed:** ask via `AskUserQuestion` (`Continue anyway` / `Abort`). Sometimes follow-ups on a closed issue are still worth resolving, but the closed state warrants explicit confirmation.

If the user named several issues (e.g. "GH-105, 109, 110, 111"), confirm which one to start with via `AskUserQuestion`. Do **not** silently batch them — finish one issue end-to-end before opening the next.

### 2. Enumerate the questions (parse, then infer)

**Parse first.** Look for an explicit section in the body — `## Open questions`, `## Questions`, `## Clarifications needed`, or near-variants. Use the author's wording when found.

**Infer when absent.** Read body + comments and pull out the genuinely unresolved decisions:

- TBDs, "we should figure out", explicit tradeoffs without a chosen side.
- Ambiguities that would block implementation (interface shape, error semantics, naming, scope cuts, where code lives).
- Questions raised in prior comments that never got answered.

**Filter aggressively.** Drop anything already resolved in a later comment, anything an implementer should decide at the keyboard, or anything out of scope of *this* issue. If the filtered list is empty, say so plainly and stop — don't invent questions to justify the invocation. If it's >6, merge near-duplicates so the user isn't dragged through redundant rounds.

### 3. Walk through them one at a time

For each question, in order:

- **Form a recommendation grounded in the code, not memory.** Use `Grep` / `Read` enough to anchor your suggestion in current reality. If the question genuinely has no defensible default, say so — a fake recommendation is worse than no recommendation.

- **One `AskUserQuestion` call per question.** Structure:
  - First option: your recommendation, label suffixed with `(Recommended)`. Description carries the reasoning + the tradeoff.
  - Other options: realistic alternatives, each described by what argues *for* it.
  - Add a `Defer` option when the question is genuinely deferrable (description: "leave open in the issue; revisit before implementation").

- **Wait for the answer before forming the next question.** The user may pick "Other" with custom text or course-correct mid-stream — adapt. **Record an "Other" answer using the user's wording verbatim** (the same rule § 4 applies to the posted comment); paraphrase only when summarizing one of your own recommendations that the user accepted. Don't preview the next question, don't summarize progress, don't restate what they just said. A one-line acknowledgment is the most you should write between rounds.

- **Never batch.** Even if two questions feel related, keep them as separate `AskUserQuestion` calls. The whole point of the skill is per-question control.

### 4. Post the resolutions back

After every question is resolved (including any deferred), compose a comment:

```markdown
## Clarifications resolved YYYY-MM-DD

### Resolved

1. **<question>** — <answer>
   <one-line reasoning if non-obvious>

### Deferred

- **<question>** — <why it's deferred / what's needed to resolve it>
```

- Use today's date from conversation context — don't shell out to `date`.
- Omit `### Deferred` if nothing was deferred.
- Use the user's wording for "Other" answers; paraphrase only when summarizing your own recommendation that the user accepted.

Show the user the comment body and confirm via `AskUserQuestion` with options `Post`, `Edit then post`, `Skip posting`. Posting is visible to others — confirm before sending.

- **`Post`:** invoke `gh issue comment <N> --body-file -` and pipe the comment body to its stdin. The mechanism is "pipe the body to stdin" (a Bash heredoc into the command works; so does any other stdin pipe — what matters is `--body-file -`, not the wrapping shell construct). Do **not** pass the body inline via `--body "..."` — quoting and newlines mangle. **If the command exits non-zero**, surface the stderr to the user and report that the comment did **not** post; do not claim success on a failed post. The user can then choose to retry, edit, or abandon.
- **`Edit then post`:** ask the user what to change (free-form text reply, or a follow-up `AskUserQuestion` if the edits naturally split into discrete choices), apply the change to the draft, then re-show the body and re-confirm with the same three-option `AskUserQuestion`. Loop until the user picks `Post` (which then runs the success-and-error flow above) or `Skip posting`.
- **`Skip posting`:** stop without posting. Resolutions remain in the conversation transcript only.

Report the comment URL on a successful post.

**End of session.** After posting (or skipping) on the chosen issue, **stop** — do not auto-advance to the next issue in a multi-issue invocation. The user re-invokes the skill (or names the next issue) when they're ready. This rule applies between issues as well as at the end of a single-issue session, and is consistent with the "no auto-chain into `/issue`" rule below.

## What this skill does not do

- **No implementation.** No branch, no code, no tests. That's `/issue`.
- **No edits to the issue body.** Resolutions go in a comment so the original framing stays intact and the audit trail is clean.
- **No auto-chain into `/issue`.** After posting, stop. The user decides whether to invoke `/issue` next.
