# Design: Route ambiguous anatomic_site values to PENDING_LLM_REVIEW

**Status:** Draft — open questions resolved, ready for implementation
**Audit reference:** F-5 in `docs/audit/comprehensive-scenario-review-2026-05-13.md`
**Pattern reference:** ACC-010 + `specimen_review` skill (the specimen_type
analog of this proposal)
**Resolution log:** see "Resolved decisions" section at the end of this doc

## Problem

Today `ACC-003` is a **strict whitelist** rule: anatomic_site must be one
of `{breast, left breast, right breast}` or the order is REJECTed to
`DO_NOT_PROCESS`. This loses two clinically meaningful cases:

1. **Sites that are clearly outside the breast workflow** (lung, liver,
   colon, brain). These should reject — current behavior is correct.
2. **Sites that are ambiguous or breast-adjacent** (skin overlying
   breast, chest wall, axillary lymph node). These are not obviously
   accept-or-reject; clinical judgment is required. Today they all
   reject as "not whitelisted".

F-5 (audit) caught this through SC-102: the fixture documents
`"skin overlying breast"` as ambiguous (acceptable alternative:
ACCEPTED) but the gate forces a single DO_NOT_PROCESS verdict. The
fixture is artificially strict because the rule doesn't model the
ambiguity that exists in reality.

## Proposal

Mirror the `ACC-010 + specimen_review` pattern that handles ambiguous
`specimen_type` values, with an explicit null branch:

1. Re-author `ACC-003` from a strict whitelist to an explicit
   **blacklist** of definitively-out-of-scope sites.
2. Add `ACC-011` (new) with `severity: PROCEED` (as originally designed;
   re-classed to `REVIEW_HOLD`) and a "neither
   whitelist nor blacklist, non-null" predicate that routes to
   `PENDING_LLM_REVIEW` with the `LLM_REVIEW_REQUESTED` flag.
3. Add `ACC-012` (new) with `severity: HOLD` and a `is_null(anatomic_site)`
   predicate that routes null anatomic_site to `MISSING_INFO_HOLD` —
   matching ACC-001 / ACC-002's null-required-field semantics.
4. Extend the existing `specimen_review` skill with an "Anatomic site
   disposition" section; the dispatcher threads the trigger `rule_id`
   into the prompt template so the skill body's appropriate branch is
   emphasized.
5. The whitelist and blacklist live inline in the rule YAMLs (mirroring
   ACC-010's style). `canonical-fields.json` remains the parity surface
   for value canonicalization.

### Resulting four-way routing for anatomic_site

| Anatomic site | Rule | Severity | Next state |
|---|---|---|---|
| `breast`, `left breast`, `right breast`, `axillary lymph node` | ACC-008 (fall-through) | ACCEPT | ACCEPTED |
| `lung`, `liver`, `colon`, `brain`, `prostate` (blacklist) | ACC-003 (rewritten) | REJECT | DO_NOT_PROCESS |
| `null` | ACC-012 (new) | HOLD | MISSING_INFO_HOLD |
| `skin overlying breast`, `chest wall`, anything else non-null | ACC-011 (new) | PROCEED | PENDING_LLM_REVIEW + LLM_REVIEW_REQUESTED |

### Vocabulary source of truth

`samantha_server/data/canonical-fields.json` already enumerates the
anatomic_site values the system recognizes:

```json
"anatomic_site": [
  "breast", "left breast", "right breast",
  "axillary lymph node", "chest wall", "lung", "skin overlying breast"
]
```

Resolved partition (Q2 + Q3):

- **Whitelist** (proceed to ACCEPTED via ACC-008 fall-through):
  `breast`, `left breast`, `right breast`, `axillary lymph node`.
- **Blacklist** (REJECT via ACC-003): `lung`, `liver`, `colon`, `brain`,
  `prostate` — medium-scope list per Q2 resolution. Common rejections
  are deterministic; novel out-of-scope organs (kidney, pancreas, etc.)
  route through ACC-011 → LLM rejects.
- **LLM-review band** (PROCEED via ACC-011): `chest wall`,
  `skin overlying breast`, and any other non-null value not on the
  other two lists.
- **Null** (HOLD via ACC-012): null anatomic_site → MISSING_INFO_HOLD.

The whitelist + blacklist live **in the rule YAML** (mirroring ACC-010's
style — both lists are inline in the spec). `canonical-fields.json`
remains the parity surface for value canonicalization; it does not
gain a "list type" column. A new test
`TestAnatomicSiteNoDrift` enforces that every value in
`canonical-fields.json` appears in exactly one of: ACC-003's blacklist,
the whitelist inline in ACC-011, or the implicit middle band (values
in canonical-fields.json but listed in neither rule — these fall into
the LLM-review band by construction and must be reachable).

## Rewritten ACC-003 (final draft)

```yaml
rule_id: ACC-003
step: ACCESSIONING
applies_at: null
event_type: order_received
severity: REJECT
priority: null
when:
  # Blacklist of definitively-out-of-scope anatomic sites for the
  # breast histology workflow. Sites here are unambiguously not
  # processable for breast-cancer pathology and produce an immediate
  # DO_NOT_PROCESS verdict.
  #
  # Non-null guard is implicit: in_enum returns False on null
  # (Equals primitive fail-safe semantics), so null routes via
  # ACC-012 (HOLD) rather than ACC-003.
  #
  # Values must be lowercase canonical.
  # Casefolding happens at comparison time.
  in_enum:
    field: anatomic_site
    values:
      - lung
      - liver
      - colon
      - brain
      - prostate
action:
  transition: DO_NOT_PROCESS
  set_flags: []
  clear_flags: []
  outcome: rejected_invalid_anatomic_site
  panel: null
  branch: null
source: knowledge_base/sops/accessioning.md#3.2
```

## New rule spec — ACC-011 (final draft)

```yaml
rule_id: ACC-011
step: ACCESSIONING
applies_at: null
event_type: order_received
severity: REVIEW_HOLD
priority: null
when:
  # Fall-through predicate: fires when anatomic_site is non-null and
  # is neither on ACC-003's blacklist (definitively out-of-scope organs)
  # NOR on the whitelist (well-known breast-cancer-relevant sites).
  # Routes to LLM review for clinical disposition.
  #
  # Disjointness with ACC-003 is guaranteed by the first Not(in_enum)
  # clause: a value on the blacklist makes ACC-003 (REJECT) win;
  # ACC-011 cannot also fire (SEVERITY_ORDER enforces REJECT > REVIEW_HOLD).
  #
  # Disjointness with ACC-012 (the null branch) is guaranteed by the
  # Not(is_null) clause: null routes via ACC-012 (HOLD), not ACC-011.
  #
  # Disjointness with ACC-008 (the implicit accept fall-through): a
  # whitelisted value satisfies the second clause's negation, so
  # ACC-011 does NOT fire and ACC-008 picks up the accept.
  boolean_and:
    - not:
        is_null:
          field: anatomic_site
    - not:
        in_enum:
          field: anatomic_site
          values:
            # ACC-003 blacklist — definitively out-of-scope organs.
            # Must stay in sync with ACC-003.yaml.
            - lung
            - liver
            - colon
            - brain
            - prostate
    - not:
        in_enum:
          field: anatomic_site
          values:
            # Whitelist v1 — breast-cancer-relevant sites known to ACC
            # as explicitly acceptable. Values here bypass LLM review
            # and proceed to ACCEPTED via ACC-008.
            # Invariant: every value here must also appear in
            # canonical-fields.json under anatomic_site. Run
            # TestAnatomicSiteNoDrift to verify no drift.
            - breast
            - left breast
            - right breast
            - axillary lymph node
action:
  transition: PENDING_LLM_REVIEW
  set_flags:
    - LLM_REVIEW_REQUESTED
  clear_flags: []
  outcome: proceeding_pending_anatomic_site_review
  panel: null
  branch: null
source: docs/design/anatomic-site-llm-review-design.md
```

## New rule spec — ACC-012 (final draft)

```yaml
rule_id: ACC-012
step: ACCESSIONING
applies_at: null
event_type: order_received
severity: HOLD
priority: null
when:
  # Null anatomic_site is missing required clinical info. Mirrors
  # ACC-001 (null patient_name) and ACC-002 (null sex) — the order
  # is held until accessioning staff supply the value.
  #
  # Disjointness with ACC-003 / ACC-011 / ACC-008: this rule fires
  # only when anatomic_site is null; all other anatomic_site rules
  # require non-null values (in_enum is fail-safe on null; ACC-011
  # has an explicit Not(is_null) guard).
  is_null:
    field: anatomic_site
action:
  transition: MISSING_INFO_HOLD
  set_flags: []
  clear_flags: []
  outcome: held_missing_anatomic_site
  panel: null
  branch: null
source: knowledge_base/sops/accessioning.md#3.1
```

**Behavioral diff vs. today:**

| Input anatomic_site | Today | After this design |
|---|---|---|
| `breast` | ACC-008 → ACCEPTED | ACC-008 → ACCEPTED |
| `axillary lymph node` | ACC-003 → DO_NOT_PROCESS | ACC-008 → ACCEPTED |
| `lung` | ACC-003 → DO_NOT_PROCESS | ACC-003 → DO_NOT_PROCESS |
| `liver` / `colon` / `brain` / `prostate` | ACC-003 → DO_NOT_PROCESS | ACC-003 → DO_NOT_PROCESS |
| `skin overlying breast` | ACC-003 → DO_NOT_PROCESS | ACC-011 → PENDING_LLM_REVIEW |
| `chest wall` | ACC-003 → DO_NOT_PROCESS | ACC-011 → PENDING_LLM_REVIEW |
| `kidney` / `pancreas` / off-vocab | ACC-003 → DO_NOT_PROCESS | ACC-011 → PENDING_LLM_REVIEW → LLM rejects |
| `null` | ACC-003 → DO_NOT_PROCESS | ACC-012 → MISSING_INFO_HOLD |

## Fixture impact

### SC-102 rewrite

Move `tests/fixtures/scenarios/unknown_input/sc-102.json` to
`tests/fixtures/scenarios/llm_review/lr-004.json`. Two-step shape
mirroring LR-001 / LR-002 / LR-003:

```json
{
  "scenario_id": "LR-004",
  "category": "llm_review",
  "description": "Ambiguous anatomic site 'skin overlying breast' -> ACC-011 routes to PENDING_LLM_REVIEW; LLM decides disposition",
  "events": [
    {
      "step": 1,
      "event_type": "order_received",
      "event_data": { /* unchanged from SC-102 */ },
      "expected_output": {
        "next_state": "PENDING_LLM_REVIEW",
        "applied_rules": ["ACC-011"],
        "flags": ["LLM_REVIEW_REQUESTED"],
        "routing_path": "deterministic"
      }
    },
    {
      "step": 2,
      "event_type": "order_received",
      "event_data": { /* unchanged from SC-102 */ },
      "expected_output": {
        "next_state": "ACCEPTED",
        "applied_rules": [],
        "flags": [],
        "routing_path": "llm",
        "llm_disposition": "accepted"
      }
    }
  ]
}
```

Drop SC-102 from the unknown_input directory.

### Existing fixtures that need verification

Fixtures using `anatomic_site: "lung"` to test ACC-003 (verified clean
under the new design — `lung` stays on the blacklist):

- `tests/fixtures/scenarios/rule_coverage/sc-007.json` step 1
- `tests/fixtures/scenarios/rule_coverage/sc-008.json` step 1
- `tests/fixtures/scenarios/multi_rule/sc-080.json` step 1
- `tests/fixtures/scenarios/multi_rule/sc-082.json` step 1

These pass-through with no edits — `lung` remains in ACC-003's
predicate after the rewrite.

Fixtures using `anatomic_site: null`:

- `tests/fixtures/scenarios/unknown_input/sc-104.json`: description says
  "ACC-003 (REJECT: null anatomic_site treated as invalid site)". Under
  Q4 resolution (null → ACC-012 → HOLD), null routes to MISSING_INFO_HOLD
  instead of DO_NOT_PROCESS. SC-104's expected_output changes:
  - applied_rules: `[ACC-001, ACC-002, ACC-003, ACC-007, ACC-010]` →
    `[ACC-001, ACC-002, ACC-012, ACC-007, ACC-010]` (winner first per
    F-13). All four HOLD-severity rules (ACC-001/002/012) tie; ACC-001
    wins by glob order.
  - next_state: `DO_NOT_PROCESS` → `MISSING_INFO_HOLD`.
  - description: rewritten to reflect new severity hierarchy (HOLD wins
    over PROCEED; REJECT no longer fires on the null-everything input).
  - Fixture remains in `unknown_input/` category — it still tests
    "completely empty order data" routing, just with a different outcome.

### New positive-coverage fixtures (recommended)

Add to `rule_coverage/`:

- **SC-114** — ACC-011 fires on `skin overlying breast` → PENDING_LLM_REVIEW (1-step deterministic).
- **SC-115** — ACC-011 fires on a value not in canonical-fields.json (e.g., `tibia`) → PENDING_LLM_REVIEW (1-step deterministic).

Add to `llm_review/`:

- **LR-004** (the SC-102 rewrite above; LLM disposition accepted).
- **LR-005** — `chest wall` → ACC-011 → LLM rejects → DO_NOT_PROCESS.
- **LR-006** — Genuinely novel value → ACC-011 → LLM escalates → PENDING_HUMAN_REVIEW.

## Skill design (Q6 resolved → extend `specimen_review`)

The dispatcher routes PENDING_LLM_REVIEW to the `specimen_review`
skill today. Per Q6 resolution, we extend that skill rather than
adding a new one. The dispatch logic threads the trigger `rule_id`
into the prompt template; the skill body carries an "Anatomic site
disposition" section that the prompt template emphasizes when
ACC-011 fired.

### Skill body sketch

Add a new section to `samantha_server/skills/specs/specimen_review/SKILL.md`:

```markdown
## Anatomic site disposition (triggered by ACC-011)

When the trigger context indicates ACC-011 fired, you are
disambiguating an anatomic_site value rather than a specimen_type.

**Accept** (`"accepted"`) — the site is breast-cancer-relevant tissue:
- Sites that are part of breast cancer staging or pathology workup,
  e.g. axillary lymph node (sentinel/dissection), nipple, areolar
  complex, chest wall (in recurrence context), skin overlying breast.
- When in doubt about a site that is breast-adjacent and the order
  context (specimen_type + ordered_tests) supports breast workflow,
  prefer `"accepted"`.

**Reject** (`"rejected"`) — the site is clearly outside breast workflow:
- Sites in unrelated organ systems not on the ACC-003 blacklist (rare;
  ACC-003 should catch most of these explicitly).
- Sites that indicate the order was routed to the wrong lab.

**Escalate** (`"escalated"`) — requires human pathologist judgment:
- Novel anatomic site names (proper nouns, internal codes, unclear
  acronyms).
- Sites where the appropriate disposition depends on clinical context
  beyond what's in the order data.
```

## Engine / dispatch changes

The router-shim at `samantha_server/llm/handlers.py` detects
`PENDING_LLM_REVIEW` and dispatches to `handle_pending_llm_review`.
The handler reads the most recent transition trace to determine which
rule fired (ACC-010 vs ACC-011) and threads a `trigger_rule_id` field
into the prompt template. The skill body describes both branches; the
prompt template selects which section to emphasize.

Implementation note: the exact API for reading the prior trace from
within `handle_pending_llm_review` needs verification against the
current handler structure — confirm during implementation.

## Receipt / trace implications

The `LLMReviewTrace` already records `model_id`, `parsed_disposition`,
`response_text_hash`, etc. No new fields required. The `outcome`
string on the deterministic-step trace becomes
`proceeding_pending_anatomic_site_review` (distinct from
`proceeding_pending_llm_review` for specimen_type) so traces are
distinguishable post-hoc.

## Test coverage

- Unit tests for ACC-011 evaluation across whitelist / blacklist /
  middle-band inputs. Mirrors `test_acc_010_*` in
  `tests/rules/test_acc_010.py`.
- New `TestAnatomicSiteNoDrift` in `tests/test_canonical_fields.py`
  enforcing that every `anatomic_site` value in
  `canonical-fields.json` appears in exactly one of ACC-003's blacklist
  or ACC-011's whitelist (the middle-band is the implicit complement;
  values explicitly listed in neither rule but present in
  canonical-fields.json fall into the middle band by construction,
  but must be reachable — i.e., no orphan canonical values).
- Updated `test_acc_003_*` tests reflecting the blacklist semantics.
- Updated SC-104 fixture per Q4 resolution.
- LR-004/005/006 corpus fixtures executed under live LLM during the
  next harness sweep.

## Resolved decisions

| # | Question | Resolution |
|---|---|---|
| Q1 | Rule IDs for new rules | ACC-011 (LLM-review band, PROCEED), ACC-012 (null branch, HOLD). |
| Q2 | Blacklist contents | Medium scope: `{lung, liver, colon, brain, prostate}`. Off-list organs route through ACC-011 → LLM rejects. |
| Q3 | Whitelist contents | Expanded to `{breast, left breast, right breast, axillary lymph node}`. Sentinel/dissected axillary LN is unambiguous breast-pathology workflow. |
| Q4 | Null anatomic_site | Explicit null branch → ACC-012 (HOLD severity) → MISSING_INFO_HOLD. Mirrors ACC-001 / ACC-002's null-required-field semantic. |
| Q5 | SC-104 fixture update | Single-fixture update. New applied_rules = `[ACC-001, ACC-002, ACC-012, ACC-007, ACC-010]` (winner first per F-13); new next_state = MISSING_INFO_HOLD. Description rewritten. |
| Q6 | Skill design | Extend `specimen_review`; dispatcher threads `trigger_rule_id` into prompt template. Single skill, two sections. |
| Q7 | Action handler boundary | No new action-handler work. ACC-011 / ACC-012 / rewritten ACC-003 all use direct transitions. |
| Q8 | Knowledge-base citation | SOP source citation deferred to implementation. Both ACC-011 and ACC-012 likely cite `knowledge_base/sops/accessioning.md`; the SOP itself may need a brief update describing the four-way disposition. Coordinate with SOP owner during implementation. |

## Implementation order (post-spec-approval)

1. Update SOP / knowledge_base citation source (Q8 dependency).
2. Author ACC-011.yaml and ACC-012.yaml; rewrite ACC-003.yaml; add
   `TestAnatomicSiteNoDrift`.
3. Extend `samantha_server/skills/specs/specimen_review/SKILL.md` with
   the "Anatomic site disposition" section.
4. Update dispatch logic to thread `trigger_rule_id` into the prompt
   template.
5. Update SC-104 fixture per Q5 resolution (single-fixture rewrite).
6. Add new fixtures (SC-114, SC-115, LR-004, LR-005, LR-006).
7. Validation pass per CLAUDE.md (uv lock, ruff, mypy, pytest with coverage).
8. Replay-sweep verification before merge.

## Out of scope

- Adding new primitive types (the existing `in_enum`, `not`,
  `is_null`, `boolean_and` are sufficient).
- Changing the schema or canonical-fields format.
- Reworking ACC-003's REJECT semantics for other fields (the change is
  scoped to anatomic_site only).
- Backporting to samantha-public's v1 corpus.
