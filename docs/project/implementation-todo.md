# Implementation TODO

> **Before reading or editing rows, load
> [`implementation-todo-schema.md`](implementation-todo-schema.md)** —
> column schema, eligibility rules, closed-issue cleanup convention,
> and schema-violation handling.

Active issue queue. This public mirror ships the queue as an empty
template: the live rows track the maintainer's private issue tracker
and are not mirrored. The structure below is what the
[`/merge-pr`](../../.claude/commands/merge-pr.md) automation reads —
it auto-updates the **Status** column from `Open` to `Done` when a PR
closes an issue (matched by the `GH-<number>` pattern). Manual edits
for other status transitions (`In progress`, `Blocked`, etc.) are
welcome. Closed (`Done`) rows are deleted in a cleanup pass.

## Follow-ups (review-driven)

Issues opened from PR-review feedback that don't map to numbered plan
steps. Listed in execution-readiness order (most-pickable first).

| GH Issue ID | Status | Description | Source | Depends on | Gate |
|-------------|--------|-------------|--------|------------|------|

<!-- Template row (replace <n> with a real issue number to activate):
| GH-<n> | Open | One-line summary of the work. | PR #<n> review | — | — |
-->
