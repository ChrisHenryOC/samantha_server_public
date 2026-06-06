# Rule primitive categorization (W2.1.b)

Per-rule decomposition of the 41 deterministic rules from
[`inventory.md`](inventory.md) into the locked primitive vocabulary defined in
[`decision-gate.md`](decision-gate.md). See
[`rules-vs-skills.md`](rules-vs-skills.md) for when a decision belongs in
this rule lane versus an LLM-consultable `SKILL.md` playbook.

## Conventions

- **Dispatch contract** (load-bearing): Predicates assume the kernel has
  already filtered by `step`, `applies_at`, **and `event_type`** before the
  predicate runs. No predicate below tests `current_state` or `event_type`.
  See [`decision-gate.md` § "Dispatch contract"](decision-gate.md#dispatch-contract)
  for the full filter order and the predicate-collision risks this contract
  prevents.
- `'field'` denotes a field on the order/event payload (bare name = order
  field, `event.<x>` = event payload key, `flags` = order-accumulated flags
  list). Field names match the inventory.
- All field-reading primitives are **fail-closed on null fields** —
  `Equals`/`Threshold*`/`InEnum`/`Contains` return false when the named
  field is absent. `IsNull` is the only way to assert absence. See
  [`decision-gate.md` § "Null semantics rationale"](decision-gate.md#null-semantics-rationale)
  for why this matters at IHC-001.
- The candidate `LengthGTE` primitive was **dropped** — no rule needed it.
- One new primitive — `Contains(collection_field, value)` — was added because
  several rules read membership in a collection field (`'HER2' in
  ordered_tests`, `'MISSING_INFO_PROCEED' in flags`, etc.). This is distinct
  from `InEnum(field, set)` which tests whether a scalar field's value lies
  in a literal set.
- The candidate `IsNotNull` was renamed to `IsNull` (its semantic dual). The
  "missing field" framing dominates the corpus — three rules become
  single-primitive `IsNull(field)` instead of double-negated
  `Not(IsNotNull(field))`. The two rules that need a present-check are
  expressed as `Not(IsNull(field))`.
- `ThresholdGT` and `ThresholdLT` from the candidate set are not used as
  atomic primitives; they are expressed as `Not(ThresholdLTE)` and
  `Not(ThresholdGTE)` respectively. This keeps the threshold family at two
  atomic forms.
- For accessioning rules using the `all_match` severity ladder, the predicate
  is the **trigger condition** only; severity-based dispatch is the kernel's
  job.

## Per-rule expressions

| rule_id | primitive_expression | primitives_used | notes |
|---------|----------------------|-----------------|-------|
| ACC-001 | `IsNull('patient_name')` | IsNull | "Missing" treated as null. Empty-string semantics deferred to the predicate's input normalization layer. |
| ACC-002 | `IsNull('sex')` | IsNull | Same null/empty caveat as ACC-001. |
| ACC-003 | `Not(InEnum('anatomic_site', BREAST_RELEVANT_SITES))` | Not, InEnum | `BREAST_RELEVANT_SITES` is a literal whitelist (e.g. `{"breast", "left breast", "right breast"}`), defined alongside the predicate. |
| ACC-004 | `Not(InEnum('specimen_type', HISTOLOGY_COMPATIBLE_TYPES))` | Not, InEnum | Whitelist similar to ACC-003 (excludes FNA, etc.). |
| ACC-005 | `BooleanAnd(Contains('ordered_tests', 'HER2'), Not(Equals('fixative', 'formalin')))` | BooleanAnd, Contains, Not, Equals | First rule that needs `Contains` for the "HER2 in ordered_tests" check. |
| ACC-006 | `BooleanAnd(Contains('ordered_tests', 'HER2'), BooleanOr(Not(ThresholdGTE('fixation_time_hours', 6.0)), Not(ThresholdLTE('fixation_time_hours', 72.0))))` | BooleanAnd, Contains, BooleanOr, Not, ThresholdGTE, ThresholdLTE | Numeric range expressed as the disjunction of negated bounds. The ThresholdGT/LT shorthand from the candidate set isn't needed. |
| ACC-007 | `Equals('billing_info_present', false)` | Equals | Single boolean check. |
| ACC-008 | `Not(BooleanOr(<ACC-001>, <ACC-002>, <ACC-003>, <ACC-004>, <ACC-005>, <ACC-006>, <ACC-007>, <ACC-009>))` | Not, BooleanOr | Meta-predicate: the negation of the disjunction of the other accessioning predicates. The kernel can compute this from the rule registry rather than re-encoding. |
| ACC-009 | `BooleanAnd(Contains('ordered_tests', 'HER2'), IsNull('fixation_time_hours'))` | BooleanAnd, Contains, IsNull | Distinguished from ACC-006 by the null check vs. range check on the same field. |
| SP-001 | `Equals('event.outcome', 'success')` | Equals | Step-completion signal. State filter (router) selects the right SAMPLE_PREP_* state. |
| SP-002 | `Equals('event.outcome', 'fail_retry')` | Equals | One of multiple failure outcomes; priority ordering disambiguates. |
| SP-003 | `Equals('event.outcome', 'fail_qns')` | Equals | QNS verdict encoded in the outcome enum. |
| SP-004 | `Equals('event.outcome', 'success')` | Equals | Same shape as SP-001; distinguished by `applies_at` (SAMPLE_PREP_QC). |
| SP-005 | `InEnum('event.outcome', {'fail_retry', 'fail_recut'})` | InEnum | QC failure with recut/retry options. |
| SP-006 | `Equals('event.outcome', 'fail_qns')` | Equals | Same shape as SP-003; distinguished by state. |
| SP-007 | `BooleanAnd(Equals('event.outcome', 'success'), Contains('flags', 'RECUT_REQUESTED'))` | BooleanAnd, Equals, Contains | Priority 0 — preempts SP-001 on the recut path (sectioning_complete with RECUT_REQUESTED set). Action clears RECUT_REQUESTED. Implements the canonical SOP `cleared_by: "Recut completed"` semantic. |
| HE-001 | `Equals('event.outcome', 'success')` | Equals | H&E QC pass. |
| HE-002 | `Equals('event.outcome', 'fail_retry')` | Equals | Restain. |
| HE-003 | `Equals('event.outcome', 'fail_recut')` | Equals | Recut path. |
| HE-004 | `Equals('event.outcome', 'fail_qns')` | Equals | QNS path. |
| HE-005 | `Equals('event.diagnosis', 'invasive_carcinoma')` | Equals | Diagnosis-driven routing. |
| HE-006 | `Equals('event.diagnosis', 'DCIS')` | Equals | The HER2-conditional panel construction is in the **action handler**, not the predicate — predicate just matches diagnosis. This is the canonical example of the action-handler boundary (see decision-gate.md § "Action handler boundary"): the action reads `ordered_tests` and constructs the panel, and the receipt records the constructed panel. The HE-006 row is **not** split into HE-006a/HE-006b because the `step+event_type+diagnosis` triple already uniquely dispatches the rule; the panel choice is downstream of the trigger. |
| HE-007 | `InEnum('event.diagnosis', {'suspicious', 'atypical'})` | InEnum | Two diagnoses share the routing path. |
| HE-008 | `Equals('event.diagnosis', 'benign')` | Equals | |
| HE-009 | `Equals('event.diagnosis', 'recut_requested')` | Equals | The recut request is encoded as a diagnosis sentinel. |
| IHC-001 | `BooleanAnd(Contains('event.added_markers', 'HER2'), BooleanOr(Not(Equals('fixative', 'formalin')), Not(ThresholdGTE('fixation_time_hours', 6.0)), Not(ThresholdLTE('fixation_time_hours', 72.0))))` | BooleanAnd, Contains, BooleanOr, Not, Equals, ThresholdGTE, ThresholdLTE | Most compositional rule in the corpus. Combines the ACC-005 and ACC-006 patterns at IHC time. |
| IHC-002 | `Equals('event.all_slides_complete', true)` | Equals | Safety relies on the **no-co-occurrence contract** (see decision-gate.md § "Dispatch contract"): `ihc_qc` events carry either `all_slides_complete` OR `staining_failure`/`tissue_available`, never both — verified across `tests/fixtures/scenarios/rule_coverage/`. If a future event source ever emits both keys on one event, IHC-002 needs `BooleanAnd(..., Not(Equals('event.staining_failure', true)))`. |
| IHC-003 | `Equals('event.all_slides_complete', false)` | Equals | Same no-co-occurrence contract as IHC-002 — IHC-003 events do not carry `staining_failure`. |
| IHC-004 | `BooleanAnd(Equals('event.staining_failure', true), Equals('event.tissue_available', true))` | BooleanAnd, Equals | |
| IHC-005 | `BooleanAnd(Equals('event.staining_failure', true), Equals('event.tissue_available', false))` | BooleanAnd, Equals | |
| IHC-006 | `BooleanAnd(Equals('event.all_scores_complete', true), Equals('event.any_equivocal', false))` | BooleanAnd, Equals | `event.any_equivocal` is a **mandatory derived field** computed by the event producer (simulator today, production adapter tomorrow) per the simulator-helper contract (see decision-gate.md § "Simulator-helper contract"). Predicates may not read `event.scores` directly until an `AnyMatch` primitive is added. |
| IHC-007 | `BooleanAnd(Equals('event.all_scores_complete', true), Equals('event.any_equivocal', true))` | BooleanAnd, Equals | Same simulator-helper contract as IHC-006. |
| IHC-008 | `Equals('event.approved', true)` | Equals | |
| IHC-009 | `Equals('event.approved', false)` | Equals | |
| IHC-010 | `BooleanAnd(Equals('event.status', 'received'), Not(Equals('event.result', 'qns')))` | BooleanAnd, Equals, Not | Excludes the QNS result so the priority-11 IHC-011 catches that case. |
| IHC-011 | `Equals('event.result', 'qns')` | Equals | |
| RES-001 | `Contains('flags', 'MISSING_INFO_PROCEED')` | Contains | Reads the order-accumulated `flags` list. **Event-type dispatch is what keeps RES-001 (priority 1) from preempting RES-002 on info-received events** — RES-001 binds to `resulting_review` events; RES-002 binds to `missing_info_received` events. Without that filter (see decision-gate.md § "Dispatch contract"), every info-received event would re-fire RES-001 and the order would never clear the flag. |
| RES-002 | `Not(IsNull('event.info_type'))` | Not, IsNull | Predicate fires whenever an info-received event arrives; the proceed-vs-stay-on-hold branch is in the action handler. **The receipt must record which branch the action took** (`outcome: cleared` vs `outcome: still_held`) so audits can distinguish — see decision-gate.md § "Action handler boundary". |
| RES-003 | `BooleanAnd(Equals('event.outcome', 'complete'), Not(Contains('flags', 'MISSING_INFO_PROCEED')))` | BooleanAnd, Equals, Not, Contains | Outcome value name pending confirmation against scenario JSON; the shape of the predicate is what matters. |
| RES-004 | `Not(IsNull('event.reportable_tests'))` | Not, IsNull | Pathologist-supplies-reportables event. |
| RES-005 | `Equals('event.outcome', 'report_generated')` | Equals | Final state transition. |

## Per-primitive coverage

| primitive | rules using it (count) | rule_ids |
|-----------|------------------------|----------|
| `Equals` | 29 | ACC-005, ACC-007, SP-001..004, SP-006, SP-007, HE-001..006, HE-008, HE-009, IHC-001..011, RES-003, RES-005 |
| `Not` | 10 | ACC-003, ACC-004, ACC-005, ACC-006, ACC-008, IHC-001, IHC-010, RES-002, RES-003, RES-004 |
| `BooleanAnd` | 11 | ACC-005, ACC-006, ACC-009, IHC-001, IHC-004, IHC-005, IHC-006, IHC-007, IHC-010, RES-003, SP-007 |
| `Contains` | 7 | ACC-005, ACC-006, ACC-009, IHC-001, RES-001, RES-003, SP-007 |
| `IsNull` | 5 | ACC-001, ACC-002, ACC-009, RES-002, RES-004 |
| `InEnum` | 4 | ACC-003, ACC-004, HE-007, SP-005 |
| `BooleanOr` | 3 | ACC-006, ACC-008, IHC-001 |
| `ThresholdGTE` | 2 | ACC-006, IHC-001 |
| `ThresholdLTE` | 2 | ACC-006, IHC-001 |

Sum of per-primitive counts = 73; mean primitives per rule = 1.78 (counts
exceed 41 because most rules use ≥2 primitives).
