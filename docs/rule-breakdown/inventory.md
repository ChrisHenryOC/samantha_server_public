# Rule inventory — `samantha-public` deterministic rules (W2.1.a)

## Provenance & method

This inventory was built against the upstream `samantha-public` repo at the snapshot taken
on 2026-04-25. The canonical source-of-truth for rule definitions is
`knowledge_base/workflow_states.yaml`; rules are loaded into Python by
`src/workflow/state_machine.py` (`class Rule`, line 80) and surfaced into LLM
prompts by `src/prediction/prompt_template.py:_format_rules`. **There are no
per-rule Python predicate functions in `samantha-public`** — the LLM evaluates
each rule's `trigger` string against the order/event payload at routing time.
The "implementation path" column therefore points at the YAML row that defines
the rule, since that is the closest thing to executable predicate code.

For rules authored **locally in `samantha_server`** (no upstream
`samantha-public/workflow_states.yaml` row — currently SP-007 only), the
"implementation path" column points at the local YAML spec under
`samantha_server/rules/specs/`. The "source_SOP_section" column for these
rules cites the canonical SOP section that *motivates* the rule
(e.g., `workflow_states.yaml#flags.RECUT_REQUESTED`), not a samantha-public
rule row.

The `fields_referenced` column was derived by:

1. Reading the rule's `trigger` text in `workflow_states.yaml`.
2. Cross-checking against the actual `event_data` keys present in scenarios
   that list the rule under `expected_output.applied_rules` (parsed from every
   JSON file under `tests/fixtures/scenarios/`).
3. Using the simulator (`src/simulator/order_generator.py`) only as a secondary
   reference — many of its `target_rules=(...)` templates encode order-level
   shortcuts that don't survive into the scenario events.

### Field namespaces in `fields_referenced`

The column mixes two namespaces. Read each cell with this in mind:

- **Bare names** (e.g., `patient_name`, `fixative`, `flags`) are fields on the
  order/context object. They persist for the order's lifetime and are
  accumulated (e.g., `flags` may carry `MISSING_INFO_PROCEED` set at ACC-007
  and read again at RES-001).
- **`event.<name>`** entries are keys on the per-event `event_data` dict that
  drives the transition. The exact key names below match what appears in the
  scenario JSON under `events[].event_data`.
- **`current_state`** is the workflow state immediately before the event is
  applied; transition-only rules (most SP/HE/IHC priorities) depend on this
  plus a single event field.

### Important caveat for the porter

Many rules in scenarios receive **pre-computed booleans** on the event payload
(e.g., IHC-001's event includes `fixation_out_of_tolerance: true`,
`her2_added_at_review: true`, `her2_rejected: true`). These are simulator
conveniences. A faithful Python predicate written from scratch must derive the
same booleans from the raw order/event fields (e.g., `fixative != "formalin"`
or `fixation_time_hours not in [6.0, 72.0]`). Where this matters, the cell
lists the simulator-supplied booleans so the implementer can match scenarios
verbatim, with the underlying raw fields where they differ.

The `covering_scenarios` column was generated programmatically by parsing
every JSON file under `tests/fixtures/scenarios/` and collecting
scenarios whose `events[].expected_output.applied_rules` contains the rule_id
(across all subdirs: `rule_coverage/`, `multi_rule/`, `accumulated_state/`,
`hallucination/`, `query/`, `unknown_input/`, `unknown_inputs/`).

## Summary

- Total rules: **41** (W2.1.a baseline 40 from `samantha-public`; +1 SP-007
  authored locally to implement the canonical `RECUT_REQUESTED.cleared_by:
  "Recut completed"` semantic that the upstream corpus did not pin).
- Deterministic: **41** (`true`). No ambiguous (`?`) or non-deterministic (`false`) rules found.
- Rules with at least one covering scenario: **41 / 41**.
- Rules with no Python predicate function in `samantha-public`: **40 / 41** —
  see "Architectural observations" below. SP-007 originates as a YAML spec in
  this repo (no `samantha-public` workflow_states.yaml row) and is documented
  via the SOP coverage map's "(authored in `samantha_server`)" note.

## Architectural observations (for downstream W2.1.b work)

1. **No code-grounded predicates.** Every rule lives only in YAML; matching is
   done by the LLM. To satisfy the `samantha_server` v2 invariant that the
   deterministic routing path does not import the LLM client, each rule will
   need a Python predicate (`fn(context) -> bool`) authored as part of the
   port. The `trigger` strings in this inventory are the specs to convert.
2. **No formal `SpecimenContext` type.** Field shape is implicit in the JSON
   event payloads and the simulator's order generators. The port should
   introduce a typed dataclass and use it as the predicate signature. Note
   that order-level state (e.g., accumulated `flags`) and per-event payloads
   are distinct dicts in scenarios — the dataclass needs both.
3. **Two evaluation modes coexist.** Accessioning uses `all_match` with a
   severity hierarchy (`REJECT > HOLD > PROCEED > ACCEPT`); every other phase
   uses priority-based `first_match`. Both modes need to be reproduced by the
   kernel. The `severity_or_priority` column below captures the dispatch fact.
4. **`applies_at`-scoped rules.** IHC rules carry an `applies_at` key
   restricting them to a specific state (e.g., `IHC_QC`, `IHC_SCORING`,
   `SUGGEST_FISH_REFLEX`, `FISH_SEND_OUT`). `step` alone is not sufficient to
   index rule applicability for the IHC phase.
5. **IHC-001 is unusual.** Although its `applies_at` is `IHC_STAINING`, the
   rule fires on the `ihc_staining_complete` event, not at state-entry. The
   other IHC rules (`IHC_QC`, `IHC_SCORING`) do behave like state-entry
   evaluators. Watch this when porting.
6. **HE-006 has an untested branch.** The "DCIS + HER2 originally ordered"
   path is referenced in the SOP and depends on `ordered_tests`, but no
   scenario in the corpus exercises it (SC-040, SC-041, SC-065 only cover the
   standard DCIS panel). The port should add coverage when the predicate is
   written.

## Inventory

| rule_id | description | current_implementation_path | source_SOP_section | fields_referenced | severity_or_priority | is_deterministic | covering_scenarios |
|---------|-------------|-----------------------------|--------------------|-------------------|----------------------|------------------|--------------------|
| ACC-001 | Patient name missing → hold order, request name | `knowledge_base/workflow_states.yaml:rules[ACC-001]` | `knowledge_base/sops/accessioning.md` § 3.1 | `patient_name` | severity=HOLD | true | SC-003, SC-004, SC-006, SC-008, SC-080, SC-081, SC-082, SC-104 (8) |
| ACC-002 | Patient sex missing → hold order, request sex | `knowledge_base/workflow_states.yaml:rules[ACC-002]` | `knowledge_base/sops/accessioning.md` § 3.1 | `sex` | severity=HOLD | true | SC-005, SC-006, SC-082, SC-083, SC-104 (5) |
| ACC-003 | Anatomic site not breast-cancer-relevant → DO_NOT_PROCESS | `knowledge_base/workflow_states.yaml:rules[ACC-003]` | `knowledge_base/sops/accessioning.md` § 3.2 | `anatomic_site` | severity=REJECT | true | SC-007, SC-008, SC-080, SC-082, SC-102, SC-104 (6) |
| ACC-004 | Specimen type incompatible with histology workflow → DO_NOT_PROCESS | `knowledge_base/workflow_states.yaml:rules[ACC-004]` | `knowledge_base/sops/accessioning.md` § 3.2 | `specimen_type` | severity=REJECT | true | SC-009, SC-010, SC-100, SC-101, SC-104 (5) |
| ACC-005 | HER2 ordered and fixative is not formalin → DO_NOT_PROCESS | `knowledge_base/workflow_states.yaml:rules[ACC-005]` | `knowledge_base/rules/fixation_requirements.md` "ACC-005 — Wrong Fixative" | `fixative`, `ordered_tests` | severity=REJECT | true | SC-010, SC-011, SC-012 (3) |
| ACC-006 | HER2 ordered and fixation time outside 6–72 h → DO_NOT_PROCESS | `knowledge_base/workflow_states.yaml:rules[ACC-006]` | `knowledge_base/rules/fixation_requirements.md` "ACC-006 — Fixation Time Out of Tolerance" | `fixation_time_hours`, `ordered_tests` | severity=REJECT | true | SC-013, SC-014, SC-081, SC-082 (4) |
| ACC-007 | Billing info missing → MISSING_INFO_PROCEED with flag | `knowledge_base/workflow_states.yaml:rules[ACC-007]` | `knowledge_base/sops/accessioning.md` § 3.1 | `billing_info_present` | severity=PROCEED | true | SC-004, SC-015, SC-016, SC-070, SC-071, SC-072, SC-073, SC-079, SC-082, SC-083, SC-090, SC-091, SC-097, SC-098, SC-104, SC-105 (16) |
| ACC-008 | All accessioning validations pass → ACCEPTED | `knowledge_base/workflow_states.yaml:rules[ACC-008]` | `knowledge_base/sops/accessioning.md` § 3.4 | `patient_name`, `sex`, `specimen_type`, `anatomic_site`, `fixative`, `fixation_time_hours`, `ordered_tests`, `billing_info_present` (defined as: no other ACC rule fires) | severity=ACCEPT | true | 80 scenarios — SC-001, SC-002, SC-017–SC-069, SC-074–SC-078, SC-084–SC-089, SC-092–SC-096, SC-099, SC-106–SC-113 |
| ACC-009 | HER2 ordered and fixation time is null → MISSING_INFO_HOLD | `knowledge_base/workflow_states.yaml:rules[ACC-009]` | `knowledge_base/rules/fixation_requirements.md` "ACC-009 — Fixation Time Missing (Null)" | `fixation_time_hours`, `ordered_tests` | severity=HOLD | true | SC-103, SC-105 (2) |
| ACC-010 | Specimen type unrecognized (not whitelist or blacklist) → PENDING_LLM_REVIEW | `samantha_server/rules/specs/ACC-010.yaml` | Phase 2 spec, Step 8 | `specimen_type` | severity=PROCEED | true | LR-001, LR-002, LR-003 (3) |
| SP-001 | Sample-prep step completed successfully → advance | `knowledge_base/workflow_states.yaml:rules[SP-001]` | `knowledge_base/sops/sample_prep.md` § 2.1 | `current_state`, `event.outcome` (== `"success"`) | priority=1 | true | 85 scenarios across `rule_coverage/`, `multi_rule/`, `accumulated_state/` |
| SP-002 | Sample-prep step failed, tissue available → RETRY current | `knowledge_base/workflow_states.yaml:rules[SP-002]` | `knowledge_base/sops/sample_prep.md` § 2.2 | `current_state`, `event.outcome` (encodes `"fail_retry"` / similar) | priority=2 | true | SC-019, SC-020, SC-024, SC-028, SC-029 (5) |
| SP-003 | Sample-prep step failed, insufficient tissue → ABORT (QNS) | `knowledge_base/workflow_states.yaml:rules[SP-003]` | `knowledge_base/sops/sample_prep.md` § 2.3 | `current_state`, `event.outcome` (== `"fail_qns"`) | priority=3 | true | SC-021, SC-022 (2) |
| SP-004 | Sample-prep QC passes → advance to HE_STAINING | `knowledge_base/workflow_states.yaml:rules[SP-004]` | `knowledge_base/sops/sample_prep.md` § 3.1 | `current_state`, `event.outcome` (== `"success"`) | priority=4 | true | 68 scenarios — SC-023–SC-079 (subset), SC-084–SC-099, SC-109–SC-111 |
| SP-005 | Sample-prep QC fails, tissue available → RETRY (back to SECTIONING) | `knowledge_base/workflow_states.yaml:rules[SP-005]` | `knowledge_base/sops/sample_prep.md` § 3.2 | `current_state`, `event.outcome` (encodes `"fail_retry"` / `"fail_recut"`) | priority=5 | true | SC-025, SC-026 (2) |
| SP-006 | Sample-prep QC fails, insufficient tissue → ABORT (QNS) | `knowledge_base/workflow_states.yaml:rules[SP-006]` | `knowledge_base/sops/sample_prep.md` § 3.3 | `current_state`, `event.outcome` (== `"fail_qns"`) | priority=6 | true | SC-027, SC-028 (2) |
| SP-007 | Sectioning completes with RECUT_REQUESTED set → clear flag and advance to SAMPLE_PREP_QC | `samantha_server/rules/specs/SP-007.yaml` | `knowledge_base/workflow_states.yaml` `flags.RECUT_REQUESTED.cleared_by` | `current_state`, `event.outcome` (== `"success"`), `flags` (contains `"RECUT_REQUESTED"`) | priority=0 | true | SC-093, SC-099 (2) |
| HE-001 | H&E QC passes → route to PATHOLOGIST_HE_REVIEW | `knowledge_base/workflow_states.yaml:rules[HE-001]` | `knowledge_base/sops/he_staining.md` § 4.1 | `current_state`, `event.outcome` (== `"success"`) | priority=1 | true | 58 scenarios — SC-030–SC-079 (subset), SC-087, SC-090–SC-099, SC-109–SC-111 |
| HE-002 | H&E QC fails, restain possible → RETRY (back to HE_STAINING) | `knowledge_base/workflow_states.yaml:rules[HE-002]` | `knowledge_base/sops/he_staining.md` § 4.2 | `current_state`, `event.outcome` (encodes `"fail_retry"`) | priority=2 | true | SC-031, SC-032, SC-033, SC-084 (4) |
| HE-003 | H&E QC fails, recut needed, tissue available → RETRY (back to SECTIONING) | `knowledge_base/workflow_states.yaml:rules[HE-003]` | `knowledge_base/sops/he_staining.md` § 4.3 | `current_state`, `event.outcome` (encodes `"fail_recut"`), optional `event.backup_slides_available` | priority=3 | true | SC-034, SC-035, SC-085 (3) |
| HE-004 | H&E QC fails, insufficient tissue → ABORT (QNS) | `knowledge_base/workflow_states.yaml:rules[HE-004]` | `knowledge_base/sops/he_staining.md` § 4.4 | `current_state`, `event.outcome` (== `"fail_qns"`); some scenarios additionally include `event.backup_slides_available`, `event.tissue_remaining` | priority=4 | true | SC-036, SC-037 (2) |
| HE-005 | Pathologist diagnosis: invasive carcinoma → PROCEED_IHC (ER, PR, HER2, Ki-67) | `knowledge_base/workflow_states.yaml:rules[HE-005]` | `knowledge_base/rules/breast_ihc_panels.md` "Diagnosis-to-Panel Mappings" table (HE-005 row) | `current_state`, `event.diagnosis`; some scenarios include `event.added_markers`, `event.ihc_panel` | priority=1 | true | 42 scenarios — SC-038, SC-039, SC-048–SC-079 (subset), SC-090–SC-099, SC-110 |
| HE-006 | Pathologist diagnosis: DCIS → PROCEED_IHC (ER, PR; HER2 only if originally ordered) | `knowledge_base/workflow_states.yaml:rules[HE-006]` | `knowledge_base/rules/breast_ihc_panels.md` "Diagnosis-to-Panel Mappings" table (HE-006 row) | `current_state`, `event.diagnosis`, `ordered_tests` (HER2 branch); optional `event.ihc_panel`, `event.added_markers` | priority=2 | true | SC-040, SC-041, SC-065 (3) — **HER2-ordered branch is untested** |
| HE-007 | Pathologist diagnosis: suspicious/atypical → PROCEED_IHC (custom panel) | `knowledge_base/workflow_states.yaml:rules[HE-007]` | `knowledge_base/rules/breast_ihc_panels.md` "Diagnosis-to-Panel Mappings" table (HE-007 row) | `current_state`, `event.diagnosis`; some scenarios include `event.ihc_panel`, `event.added_markers` (output, not predicate input) | priority=3 | true | SC-042, SC-043, SC-069, SC-109 (4) |
| HE-008 | Pathologist diagnosis: benign → CANCEL_IHC, route to RESULTING | `knowledge_base/workflow_states.yaml:rules[HE-008]` | `knowledge_base/rules/breast_ihc_panels.md` "Diagnosis-to-Panel Mappings" table (HE-008 row) | `current_state`, `event.diagnosis`; downstream signals `event.ihc_cancelled`, `event.cancelled_tests` | priority=4 | true | SC-044, SC-045, SC-087, SC-111 (4) |
| HE-009 | Pathologist requests recuts → REQUEST_RECUTS (back to SECTIONING, set RECUT_REQUESTED on flags) | `knowledge_base/workflow_states.yaml:rules[HE-009]` | `knowledge_base/rules/breast_ihc_panels.md` "Diagnosis-to-Panel Mappings" table (HE-009 row) | `current_state`, `event.diagnosis` (== `"recut_requested"`); optional `event.tissue_limited` | priority=5 | true | SC-046, SC-047, SC-093, SC-099 (4) |
| IHC-001 | HER2 added by pathologist + fixation out of tolerance → reject HER2, set HER2_FIXATION_REJECT (fires on `ihc_staining_complete`, not state-entry) | `knowledge_base/workflow_states.yaml:rules[IHC-001]` (`applies_at: IHC_STAINING`) | `knowledge_base/rules/fixation_requirements.md` "IHC-001 — HER2 Added with Fixation Out of Tolerance" | `current_state`, `fixative`, `fixation_time_hours` (raw inputs); scenarios provide pre-computed `event.her2_added_at_review`, `event.fixation_out_of_tolerance`, `event.her2_rejected`. Pathologist H&E-review event uses `event.added_markers` to flag the HER2 add | priority=1 | true | SC-048, SC-094 (2) |
| IHC-002 | All slides stained, IHC QC passed → route to IHC_SCORING | `knowledge_base/workflow_states.yaml:rules[IHC-002]` (`applies_at: IHC_QC`) | `knowledge_base/sops/ihc_staining.md` § 6.2 | `current_state`, `event.slides`, `event.all_slides_complete` (== `true`) | priority=2 | true | 33 scenarios — SC-050, SC-051, SC-058–SC-079 (subset), SC-090–SC-098, SC-110 |
| IHC-003 | Some slides still pending → HOLD at IHC_QC | `knowledge_base/workflow_states.yaml:rules[IHC-003]` (`applies_at: IHC_QC`) | `knowledge_base/sops/ihc_staining.md` § 6.3 | `current_state`, `event.slides`, `event.all_slides_complete` (== `false`); some scenarios include `event.slides_complete`, `event.slides_pending` | priority=3 | true | SC-052, SC-053 (2) |
| IHC-004 | IHC staining failed, retry possible → RETRY staining | `knowledge_base/workflow_states.yaml:rules[IHC-004]` (`applies_at: IHC_QC`) | `knowledge_base/sops/ihc_staining.md` § 6.4 | `current_state`, `event.slides`, `event.staining_failure` (== `true`), `event.tissue_available` (== `true`); optional `event.failed_marker` | priority=4 | true | SC-051, SC-054, SC-055, SC-057 (4) |
| IHC-005 | IHC staining failed, insufficient tissue → ABORT (QNS) | `knowledge_base/workflow_states.yaml:rules[IHC-005]` (`applies_at: IHC_QC`) | `knowledge_base/sops/ihc_staining.md` § 6.5 | `current_state`, `event.slides`, `event.staining_failure` (== `true`), `event.tissue_available` (== `false`) | priority=5 | true | SC-056, SC-057 (2) |
| IHC-006 | IHC scoring complete, no equivocal results → route to RESULTING | `knowledge_base/workflow_states.yaml:rules[IHC-006]` (`applies_at: IHC_SCORING`) | `knowledge_base/sops/ihc_staining.md` § 7.1 | `current_state`, `event.scores` (list), `event.all_scores_complete` (== `true`), `event.any_equivocal` (== `false`) | priority=6 | true | 17 scenarios — SC-058, SC-059, SC-070–SC-079 (subset), SC-090–SC-098, SC-110 |
| IHC-007 | HER2 equivocal → SUGGEST_FISH_REFLEX (set FISH_SUGGESTED) | `knowledge_base/workflow_states.yaml:rules[IHC-007]` (`applies_at: IHC_SCORING`) | `knowledge_base/sops/ihc_staining.md` § 7.2 | `current_state`, `event.scores` (list of `{test, value, equivocal}`), `event.all_scores_complete`, `event.any_equivocal` (== `true`); HER2 row identified by `test == "HER2"` and `value == "2+"` / `equivocal == true` | priority=7 | true | 14 scenarios — SC-060–SC-069, SC-071, SC-075, SC-095, SC-096 |
| IHC-008 | Pathologist approves FISH reflex → FISH_SEND_OUT | `knowledge_base/workflow_states.yaml:rules[IHC-008]` (`applies_at: SUGGEST_FISH_REFLEX`) | `knowledge_base/sops/ihc_staining.md` § 7.3 | `current_state`, `event.approved` (== `true`) | priority=8 | true | SC-062, SC-063, SC-066–SC-069, SC-071, SC-075 (8) |
| IHC-009 | Pathologist declines FISH reflex → route to RESULTING | `knowledge_base/workflow_states.yaml:rules[IHC-009]` (`applies_at: SUGGEST_FISH_REFLEX`) | `knowledge_base/sops/ihc_staining.md` § 7.4 | `current_state`, `event.approved` (== `false`); optional `event.reason` | priority=9 | true | SC-064, SC-065, SC-096 (3) |
| IHC-010 | FISH result received from external lab → route to RESULTING | `knowledge_base/workflow_states.yaml:rules[IHC-010]` (`applies_at: FISH_SEND_OUT`) | `knowledge_base/sops/ihc_staining.md` § 7.5 | `current_state`, `event.result`, `event.status`; optional `event.her2_gene_copy_number`, `event.her2_cep17_ratio` | priority=10 | true | SC-066, SC-067, SC-071, SC-075, SC-095 (5) |
| IHC-011 | FISH external lab returns QNS → ABORT (QNS) | `knowledge_base/workflow_states.yaml:rules[IHC-011]` (`applies_at: FISH_SEND_OUT`) | `knowledge_base/sops/ihc_staining.md` § 7.6 | `current_state`, `event.result` (== `"qns"`), `event.status`; optional `event.extended_processing`, `event.reason` | priority=11 | true | SC-068, SC-069 (2) |
| RES-001 | MISSING_INFO_PROCEED flag present on order → RESULTING_HOLD | `knowledge_base/workflow_states.yaml:rules[RES-001]` | `knowledge_base/sops/resulting.md` § 8.1 | `current_state`, `flags` (order-level accumulated; predicate checks `MISSING_INFO_PROCEED` is set), `event.outcome` | priority=1 | true | SC-070, SC-071, SC-072, SC-073, SC-079, SC-090, SC-091, SC-097, SC-098 (9) |
| RES-002 | Missing info received, re-evaluate flags → proceed or remain on hold | `knowledge_base/workflow_states.yaml:rules[RES-002]` | `knowledge_base/sops/resulting.md` § 8.2 | `current_state`, `flags`, `event.info_type`, `event.value` | priority=2 | true | SC-072, SC-073, SC-079, SC-091, SC-098 (5) |
| RES-003 | All scoring/testing complete, no blocking flags → PATHOLOGIST_SIGNOUT | `knowledge_base/workflow_states.yaml:rules[RES-003]` | `knowledge_base/sops/resulting.md` § 8.3 | `current_state`, `flags`, `event.outcome` | priority=3 | true | SC-074, SC-075, SC-076, SC-077, SC-078, SC-079, SC-091, SC-094, SC-098 (9) |
| RES-004 | Pathologist selects reportable tests → REPORT_GENERATION | `knowledge_base/workflow_states.yaml:rules[RES-004]` | `knowledge_base/sops/resulting.md` § 8.4 | `current_state`, `event.reportable_tests` | priority=4 | true | SC-076, SC-077, SC-078, SC-079, SC-094, SC-098 (6) |
| RES-005 | Report generated → ORDER_COMPLETE | `knowledge_base/workflow_states.yaml:rules[RES-005]` | `knowledge_base/sops/resulting.md` § 8.5 | `current_state`, `event.outcome` | priority=5 | true | SC-078, SC-079, SC-094, SC-098 (4) |

## Pending flag emit sites

Flags present in `samantha_server/models/context.py`'s `VALID_FLAGS`
that have no rule-row emit site in the inventory above. Listed so a
reader of `models/context.py` doesn't infer the gap is an oversight.

| flag | tracking issue | predicate (from upstream SOP) |
|------|----------------|--------------------------------|
| `FIXATION_WARNING` | Deferred | HER2-bearing order, `next_state == "ACCEPTED"`, `fixation_time_hours` not null, AND fixation in 6.0–8.0 h or 68.0–72.0 h. Authoritative source: the POC knowledge base (`knowledge_base/skills/accessioning.md`). Vocabulary is defined; emitter rule design pending architectural decision (rule-spec schema extension vs. new co-firing rule vs. post-evaluate action handler). |

## SOP coverage map

| SOP file | Rules produced |
|----------|----------------|
| `knowledge_base/sops/accessioning.md` | ACC-001, ACC-002, ACC-003, ACC-004, ACC-005, ACC-006, ACC-007, ACC-008 |
| `knowledge_base/rules/fixation_requirements.md` | ACC-005, ACC-006, ACC-009, IHC-001 |
| `knowledge_base/sops/sample_prep.md` | SP-001, SP-002, SP-003, SP-004, SP-005, SP-006 |
| `knowledge_base/workflow_states.yaml#flags.RECUT_REQUESTED` | SP-007 (authored in `samantha_server`; clears the flag on recut sectioning_complete) |
| `knowledge_base/sops/he_staining.md` | HE-001, HE-002, HE-003, HE-004 |
| `knowledge_base/rules/breast_ihc_panels.md` | HE-005, HE-006, HE-007, HE-008, HE-009 |
| `knowledge_base/sops/ihc_staining.md` | IHC-001, IHC-002, IHC-003, IHC-004, IHC-005, IHC-006, IHC-007, IHC-008, IHC-009, IHC-010, IHC-011 |
| `knowledge_base/sops/resulting.md` | RES-001, RES-002, RES-003, RES-004, RES-005 |

Notes:

- ACC-005 and ACC-006 each have **two** SOP sources: `accessioning.md` § 3.3
  and `fixation_requirements.md`. The "source_SOP_section" column points to
  `fixation_requirements.md` because that file contains the more specific
  numeric thresholds. ACC-009 lives **only** in `fixation_requirements.md`
  (and the YAML); `accessioning.md` § 3.3 does not document it. IHC-001
  similarly lives in both `ihc_staining.md` and `fixation_requirements.md`.
- No SOP section was found that defines a rule lacking an implementation row
  in the YAML; no orphaned YAML rule was found that lacks an SOP section.
