# Decision-gate recommendation: rule-synthesis methodology (W2.1.b)

> **See also:** [`rules-vs-skills.md`](rules-vs-skills.md) — when a
> decision belongs in this primitive vocabulary versus an LLM-consultable
> `SKILL.md` playbook.

## Verdict: **PROCEED**

The full 42-rule corpus from [`inventory.md`](inventory.md) is expressible
with **9 atomic primitives**, only **one** of which (`Contains`) is a new
addition versus the candidate set in the issue.
(Count updated from 40 → 41, then 41 → 42 (ACC-010).) The original `LengthGTE`
candidate was dropped (no rule needed it); `ThresholdGT` / `ThresholdLT`
were collapsed into negations of `ThresholdLTE` / `ThresholdGTE`; and
`IsNotNull` was inverted to `IsNull` so the dominant "missing X" rules
read as a single primitive instead of a double negation. The methodology
framing — "deterministic rules synthesizable from a tiny primitive
vocabulary" — holds. W2 worked-example selection can begin.

## Final primitive set

| primitive | semantics |
|-----------|-----------|
| `IsNull(field)` | true iff the named field is absent or null on the order/event (use `Not(IsNull(field))` for the present-check) |
| `Equals(field, value)` | true iff `field == value` (scalar compare). On null `field`, returns false unless `value is None`. |
| `ThresholdGTE(field, value)` | true iff `field >= value` (numeric). **On null `field`, returns false (fail-closed).** |
| `ThresholdLTE(field, value)` | true iff `field <= value` (numeric). **On null `field`, returns false (fail-closed).** |
| `InEnum(field, set)` | true iff the scalar `field`'s value lies in the literal `set`. On null `field`, returns false. |
| `Contains(collection_field, value)` | true iff `value` is an element of the collection-typed `collection_field` (lists, sets, frozensets). On null `collection_field`, returns false (treat as empty collection). |
| `BooleanAnd(p1, p2, …)` | true iff every sub-predicate is true. Short-circuits on first false. |
| `BooleanOr(p1, p2, …)` | true iff at least one sub-predicate is true. Short-circuits on first true. |
| `Not(p)` | true iff `p` is false. (Predicates are total over the field domain — they never return null/exception, so `Not` is a clean Boolean negation.) |

### Null semantics rationale

All field-reading primitives treat a missing/null field as **predicate-false**
(not as exception, not as null-propagating). This keeps every predicate total
over the field domain, so `Not(p)` is a clean Boolean negation and
`BooleanAnd`/`BooleanOr` short-circuit predictably. The `IsNull` primitive
remains the **only** way to assert "field is absent" — every other primitive
fails-closed on a null field, and the rule author must guard explicitly with
`IsNull` or the kernel's dispatch contract (see below).

Concrete consequence for IHC-001: if a HER2-added-at-IHC-review event arrives
on an order whose `fixation_time_hours` is null (an edge case not blocked by
ACC-009 because the order was already accepted by ACC-008 before IHC), the
`BooleanOr(Not(ThresholdGTE(null, 6.0)), Not(ThresholdLTE(null, 72.0)))`
evaluates to `BooleanOr(Not(false), Not(false))` = `BooleanOr(true, true)` =
true. IHC-001 fires (HER2 rejected). This is the safe direction — refuse to
process HER2 with unknown fixation time.

## Dispatch contract

The primitive vocabulary expresses **trigger conditions only**. The rules
engine selects which rules to evaluate against an incoming event using the
following filters in order, applied **before** any primitive predicate runs:

1. **`step` filter** — every rule carries `step: <ACCESSIONING|SAMPLE_PREP|HE_QC|PATHOLOGIST_HE_REVIEW|IHC|RESULTING>`. Rules outside the current step never evaluate.
2. **`applies_at` filter** — IHC rules carry an explicit `applies_at: <state>` key (IHC_STAINING, IHC_QC, IHC_SCORING, SUGGEST_FISH_REFLEX, FISH_SEND_OUT). Rules whose `applies_at` doesn't match the current state are skipped. Other phases do not use `applies_at` because their state-to-rule mapping is one-to-one (or the event-type filter below is sufficient).
3. **`event_type` filter** — every event carries an `event_type` (`order_received`, `processing_complete`, `embedding_complete`, `sectioning_complete`, `qc_complete`, `pathologist_he_review`, `ihc_staining_complete`, `ihc_qc`, `ihc_scoring_complete`, `pathologist_decision`, `fish_received`, `resulting_review`, `missing_info_received`, `pathologist_signout`, `report_generated`). Each rule binds to one event_type implicitly via its `step` and `trigger` text. The kernel must dispatch on `event_type` to keep predicates from accidentally firing on the wrong event shape.

The categorization assumes all three filters have already been applied, so
predicates do not test `current_state` or `event_type`. **The W2.1.c port
must enforce this contract** — either as registry metadata on each rule, or
as code in the dispatcher. Without it, the following predicate-collision
risks materialize:

- **SP rules**: SP-001 / SP-004 share `Equals('event.outcome', 'success')` and
  the same `step: SAMPLE_PREP`. Without event_type dispatch (SP-001 fires on
  `processing_complete`/`embedding_complete`/`sectioning_complete`; SP-004
  fires on `qc_complete`), they would both match. SP-003 / SP-006 share
  `fail_qns`; SP-002 collides with SP-005's `InEnum`. The simulator separates
  them by event_type — verified — but that contract must travel with the
  rules.
- **IHC-002 / IHC-003 vs IHC-004 / IHC-005**: events carry
  `all_slides_complete` OR `staining_failure`/`tissue_available`, never both
  — verified across `scenarios/rule_coverage/`. The categorization's
  predicate-only form is safe given this no-co-occurrence contract; document
  it on the IHC event_type spec.
- **RES-001 vs RES-002**: RES-001 fires on `resulting_review`; RES-002 fires
  on `missing_info_received`. Different event types — verified. Without
  event_type dispatch RES-001 (priority 1, `Contains('flags',
  'MISSING_INFO_PROCEED')`) would preempt RES-002 on every info-received
  event, sending the order back to RESULTING_HOLD instead of clearing the
  flag.

### Action handler boundary

Predicates capture **trigger** conditions. Actions may carry their own
conditional logic (e.g., HE-006 routes every DCIS diagnosis to PROCEED_IHC,
but the panel constructor inside the action handler reads `ordered_tests` to
decide whether HER2 is included). This is a deliberate split:

- **Inside primitives**: anything the rules engine evaluates to decide
  *whether* a rule fires.
- **Inside actions**: anything the action computes to decide *what state to
  transition to* or *what flags to set*, given that the predicate matched.

Action-handler conditionals do **not** need to be expressible in the
primitive vocabulary, but they **do** need to be deterministic, side-effect
free over the order/event payload, and covered by tests. The receipt
emitted for each rule firing must record the action's chosen branch (e.g.,
RES-002 records `outcome: cleared` vs `outcome: still_held`; HE-006 records
the constructed panel) so audit trails can reconstruct the decision.

### Simulator-helper contract (IHC-006 / IHC-007)

Two predicates lean on `event.any_equivocal`, which is **computed by the
event producer** (the simulator today; the production event adapter
tomorrow) from `event.scores`. The contract:

- The event adapter must populate `event.any_equivocal` whenever it emits an
  `ihc_scoring_complete` event. This is a mandatory derived field, not
  optional.
- The W2.1.c port may **not** read `event.scores` directly inside an
  `Equals(...)` primitive — that would require an `AnyMatch` primitive, which
  is deferred (see "What this verdict does not commit to" below).
- If a future rule needs to interrogate the score list shape (e.g., "any
  marker scored 3+"), `AnyMatch(collection_field, predicate)` becomes
  necessary; revisit the primitive lock then.

## Coverage summary

| primitive | rules using it | role |
|-----------|----------------|------|
| `Equals` | 29 / 42 | **load-bearing** — most outcome-driven and diagnosis-driven rules collapse to this. |
| `Not` | 12 / 42 | **load-bearing** — drives every "X out of tolerance", "exclude QNS", "field-present" check, and ACC-010's fall-through negations (+2: ACC-010 has 2 Not nodes). |
| `BooleanAnd` | 12 / 42 | **load-bearing** for accessioning compound rules, IHC QC distinction, SP-007's flag-gated preemption, and ACC-010's conjunction (+1). |
| `Contains` | 7 / 42 | **load-bearing** — every HER2-conditional accessioning rule and order-flag check needs it (including SP-007's recut-clear gate on `flags`). The single primitive added vs. the candidate set. |
| `IsNull` | 5 / 42 | **moderate** — 3 missing-field rules at accessioning (ACC-001, ACC-002, ACC-009) + 2 event-presence rules at resulting (RES-002, RES-004 use `Not(IsNull(...))`). |
| `InEnum` | 6 / 42 | **moderate** — anatomic-site / specimen-type whitelists + a couple of multi-outcome triggers. ACC-010 adds 2 new InEnum nodes (blacklist + whitelist). |
| `BooleanOr` | 3 / 42 | **rare but irreducible** — needed for ACC-006's "outside 6–72 h" range, IHC-001's tolerance disjunction, and ACC-008's negation-over-disjunction. |
| `ThresholdGTE` | 2 / 42 | **rare but irreducible** — only the fixation-time rules use numeric ranges. |
| `ThresholdLTE` | 2 / 42 | same as `ThresholdGTE`. |

The "rare but irreducible" set (`BooleanOr`, `ThresholdGTE`, `ThresholdLTE`)
is small but cannot be dropped without losing the fixation-tolerance and
all-pass-defines-accept logic. The threshold pair is the only numeric
comparison in the corpus and lives entirely in the four fixation rules
(ACC-005, ACC-006, ACC-009, IHC-001).

ACC-010 increased the `Not` and `BooleanAnd` counts by 1 each and
the `InEnum` count by 2 — the fall-through predicate requires two negated
`InEnum` nodes joined by `BooleanAnd` to express "not on blacklist AND not
on whitelist".

## Rationale

What the categorization showed:

1. **Most rules are extremely simple.** 24 of 41 rules collapse to a single
   `Equals(event.X, literal)` check. The deterministic routing surface is
   dominated by event-outcome-to-state mappings, not complex predicates.
2. **Accessioning is the predicate-complexity hot spot.** Five of the nine
   rules using `BooleanAnd` are accessioning rules. The HER2/fixation
   interaction (ACC-005, ACC-006, ACC-009 + IHC-001) is the entire numeric
   range surface area in the corpus.
3. **`Contains` was the only addition forced by the corpus.** Three different
   patterns required it: HER2 in `ordered_tests` (ACC-005/006/009), HER2 in
   `event.added_markers` (IHC-001), and `MISSING_INFO_PROCEED` in `flags`
   (RES-001, RES-003). Composition could not paper this over: the candidate
   `InEnum(field, set)` checks a scalar against a literal set, which is the
   inverse direction.
4. **`LengthGTE` was genuinely unused.** No rule reads collection size
   directly; the closest cases (IHC-002/003 "all slides complete") use a
   pre-computed boolean that the simulator emits, and the underlying
   semantics could be expressed with `Equals(len(field), expected)` if ever
   needed — but `Equals` already covers it.
5. **The threshold family compresses to 2 forms, not 4.** GT and LT are
   negations of LTE and GTE; keeping them as primitives doubles the surface
   area for no expressive gain.
6. **No rule required `AnyMatch` / quantification over collection elements.**
   IHC-006 and IHC-007 lean on the simulator's `event.any_equivocal` boolean.
   If a future rule needed quantification (e.g., "any score with
   value=='2+'"), `AnyMatch(collection_field, predicate)` would be the
   natural addition. **Defer until a rule demands it**, per the
   "extend only if forced" rule.

What surprised:

- The accessioning compound rules required less primitive variety than
  expected. ACC-005, ACC-006, ACC-009 all reduce to the same shape:
  `BooleanAnd(Contains(ordered_tests, 'HER2'), <fixation_check>)`. This
  suggests a "HER2 + fixation" rule family that could be factored if
  authoring volume grows.
- ACC-008 ("all validations pass") is best expressed as
  `Not(BooleanOr(<other ACC predicates>))`. It's a **meta-predicate**:
  the kernel can compute it from the rule registry rather than re-encoding.
  This is a free win for the methodology — one fewer hand-authored rule.

What didn't surprise:

- IHC-001 is the most compositional rule (3 levels of nesting, 6 distinct
  primitives). Already flagged in the inventory as the unusual case.
- Diagnosis-driven HE rules (HE-005..HE-009) are nearly identical in shape;
  five rules, one shape, distinguished only by literal value. Strong
  candidate for a `dispatch-by-enum-value` macro if the methodology grows
  syntactic sugar.

## Worked-example candidates

For the methodology document transcripts, I propose three rules spanning the
difficulty range. Surfacing for sign-off in the PR description.

| tier | rule_id | why this rule | primitive_expression |
|------|---------|---------------|----------------------|
| 1 — trivial single-primitive | **ACC-001** | One field check, one primitive. The minimum viable rule — proves the LLM-author transcript can write a rule from a one-sentence SOP fragment in one shot. | `IsNull('patient_name')` |
| 2 — numeric threshold | **ACC-006** | Combines the new `Contains` primitive with the threshold family and a disjunction over a numeric range. Tests whether the methodology handles compound predicates with numeric tolerances — the most common "real" rule shape outside trivial dispatch. | `BooleanAnd(Contains('ordered_tests', 'HER2'), BooleanOr(Not(ThresholdGTE('fixation_time_hours', 6.0)), Not(ThresholdLTE('fixation_time_hours', 72.0))))` |
| 3 — compositional with ≥2 primitives | **IHC-001** | The most nested predicate in the corpus: 3 levels deep, 6 distinct primitives, two cross-cutting concerns (HER2 add at IHC time + fixation-out-of-tolerance check that mirrors ACC-005/006). If the methodology survives this rule, it survives the corpus. | `BooleanAnd(Contains('event.added_markers', 'HER2'), BooleanOr(Not(Equals('fixative', 'formalin')), Not(ThresholdGTE('fixation_time_hours', 6.0)), Not(ThresholdLTE('fixation_time_hours', 72.0))))` |

Alternative tier-2 candidate: ACC-005 (simpler — single `Not(Equals)` instead
of a numeric range). Use as fallback if ACC-006 proves too long for a
transcript fixture.

Alternative tier-3 candidate: ACC-008 (the meta-predicate). Different
flavor of compositional — compositional **over the rule registry**, not over
field tests. Worth an additional transcript to demonstrate the kernel-level
shortcut.

## What this verdict does not commit to

- The actual Python/dataclass shape of the primitives (W2.2). The
  categorization is form-only.
- Whether predicates are authored by hand or generated from SOPs by an LLM.
  Both paths are supported by this primitive set.
- An `AnyMatch(collection_field, predicate)` primitive. Deferred — IHC-006
  and IHC-007 use the simulator-helper `event.any_equivocal` boolean per
  the contract above. Revisit only when a rule needs to interrogate the
  shape of a collection element (e.g., "any marker scored 3+").
- The HE-006 untested HER2-ordered branch flagged in the inventory.
  Resolved in favour of keeping the predicate as `Equals(diagnosis, 'DCIS')`
  and pushing HER2-conditional panel construction into the action handler
  per the action-handler boundary above. If the port later decides to push
  the conditional into the predicate, the primitive set is unchanged
  (already supports `BooleanAnd`).
