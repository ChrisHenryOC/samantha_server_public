# Implementation TODO — schema and operational rules

Reference doc for [`implementation-todo.md`](implementation-todo.md). The
queue file itself stays minimal so it doesn't reload these rules into
every Claude Code session that opens it. Anything load-bearing for the
`/merge-pr` automation lives here.

## Closed-issue retention

**Closed issues are not retained in [`implementation-todo.md`](implementation-todo.md).**
When a row transitions to `Done`, `/merge-pr`'s auto-update flips its
status; the row is then deleted from the queue file in the same PR (or
a subsequent cleanup pass), and any active row that depended on it has
its `Depends on` cell trimmed to drop the now-resolved token. The PR
description, the GitHub issue itself, and `git log` are the historical
record for closed work — not the queue file. Phase-level closure
narratives live in per-phase closeout docs in the maintainer's
planning tree (not mirrored here).

## Column schema

Every active table in the queue file shares these columns:

- **GH Issue ID** — `GH-<N>` for tracked issues, `(TBD)` for placeholders.
  Only rows whose ID matches the literal pattern `GH-<digits>` are
  eligible for full-auto pickup; `(TBD)` rows are intentionally never
  eligible until converted.
- **Status** — `Open` | `In progress` | `Done`. The full-auto selector
  matches `Open` exactly (case-sensitive). `Blocked` is acceptable as a
  manually-curated transient state, but rows in that state are not
  eligible for pickup. Merge automation only flips `Open` → `Done`;
  the manual cleanup pass deletes the row.
- **Description** — free text. Human-readable summary; not parsed.
- **Plan step** *or* **Source** — provenance, free text. Not parsed.
- **Depends on** — `GH-<N>` tokens only, **comma-separated** (split on
  `,`, trim each token, each non-empty token must match `GH-<digits>`),
  or `—` (em-dash, not regular hyphen) when there are no GitHub-issue
  dependencies. **Free-text gates do not go here** — they belong in
  `Gate`.
- **Gate** — free-text gates that block pickup beyond GH-issue
  dependencies (e.g., `phase-2-entry`, `needs-llm-handler-scaffolding`),
  or `—` (em-dash) when no such gate applies. Multiple gates are
  semicolon-separated. The selector treats `Gate` as a single-string
  comparison: any non-`—` value disqualifies the row.

## Eligibility for full-auto pickup

A row is **eligible** for full-auto pickup iff **all** of these
schema-level conditions hold:

1. `GH Issue ID` matches `GH-<digits>` exactly.
2. `Status` equals `Open` (case-sensitive).
3. Every `GH-<N>` token in `Depends on` resolves to a row in the queue
   file whose `Status` is `Done`. The check is non-transitive — only
   direct dependencies are checked. Tokens that don't resolve to a
   present row are treated as a schema violation (see below), so
   editors must trim resolved-Done deps as part of the deletion
   cleanup pass.
4. `Gate` is exactly `—` (em-dash).

The full-auto selector additionally applies a runtime loop guard
(excluding the just-merged row from pickup) — that is operational, not
a schema property, and is documented in `/merge-pr` Step 5.

## Schema-violation handling

If a `Depends on` cell contains tokens that don't match `GH-<digits>`
*or* point at a row that does not exist in the queue file, the
selector treats the row as ineligible **and** prints a warning naming
the row and the offending token, so editors see their mistake. Visible
warnings beat silent disqualification.

## Dependency resolution scope

`GH-<N>` tokens in `Depends on` may reference rows in any active table
(Follow-ups, Phase 2). Tokens for closed issues that have been deleted
from the queue file will warn (per above) — the cleanup pass at
deletion time is responsible for trimming resolved-Done deps in
dependents.

## Walk order

Open rows in the Follow-ups table are listed in execution-readiness
order (most-pickable first) as a cosmetic preference for human
readers. The selector's walk order — Follow-ups → Phase 2 — takes
precedence: if Follow-ups has any eligible row, that row is picked
first regardless of any cosmetic ordering.
