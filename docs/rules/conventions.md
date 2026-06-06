# Rule-spec authoring conventions

Conventions that the YAML rule specs in `samantha_server/rules/specs/` follow.
The loader (`samantha_server/rules/loader.py`) does not enforce these — they
exist to keep the corpus readable and to prevent silent collisions across the
40 rules authored across Steps 6–10 of the phase-1 plan.

## `outcome` verb-prefix convention

The `action.outcome` field is a snake_case string used in receipts and
matched against scenarios' `expected_output.outcome`. Prefix the outcome
with a verb that mirrors the rule's `severity` so reviewers can read the
rule's verdict from the outcome alone.

For ACCESSIONING rules (severity-based):

| `severity` | Outcome prefix      | Example                                |
| ---------- | ------------------- | -------------------------------------- |
| `HOLD`     | `held_*`            | `held_missing_patient_name`            |
| `REJECT`   | `rejected_*`        | `rejected_fixation_out_of_tolerance`   |
| `PROCEED`  | `proceeding_*`      | `proceeding_missing_billing`           |
| `ACCEPT`   | `accessioning_*`    | `accessioning_validations_passed`      |

> **Note: `ACCEPT` is the deliberate exception.** The other three prefixes are
> verdict verbs (`held`, `rejected`, `proceeding`). `ACCEPT`'s prefix is the
> *workflow step name* (`accessioning_`), not a verdict verb — using `accepted_*`
> would collide once the SP/HE/IHC/RES "step succeeded" rules land, since each
> phase has its own ACCEPT-equivalent terminal. Naming the step makes the
> outcome unambiguous in receipts. Don't extrapolate `accessioning_*` to other
> phases — see the non-ACCESSIONING table below.

For non-ACCESSIONING rules (priority-based, no severity), prefer a verb in
the past tense that names the action just performed: `restained_*`,
`recut_*`, `reported_*`, `advanced_*`. Avoid mixing destination-form
prefixes (`advanced_to_*`) with action-form prefixes — pick one shape per
phase. Future steps will lock these once the SP/HE/IHC/RES specs land.

### Why a prefix and not the field name

ACC-006 (`rejected_fixation_out_of_tolerance`) and ACC-009
(`held_missing_fixation_time`) both touch `fixation_time_hours`. Without
the verb prefix, two rules with different verdicts could collide on the
same outcome string and silently swap in receipts.

## Field naming alignment

A rule's `when:` predicate must reference fields that exist on the
`Order` model (`samantha_server/models/context.py`) or via `event.<key>`
namespacing on `Event.event_data`. The `SpecimenContext.field()` accessor
returns `None` for any unknown name via a `getattr` fallback, which means
typos are silent at evaluate time. Review caught one such case (ACC-002
referenced `sex` instead of `patient_sex`); the round-trip behavioral test
in `tests/rules/test_acc_specs.py` is the runtime guard.

When in doubt, `grep` the `Order` model fields before authoring a new
predicate.
