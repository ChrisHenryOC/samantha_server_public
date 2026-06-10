---
name: specimen-review
description: Pathologist-style disposition playbook for unrecognized specimen types; returns structured JSON.
applies_to_states:
  - PENDING_LLM_REVIEW
---

<!-- contamination-audit: sentinel IDs only -->

## Specimen Review Skill (JSON output)

This skill is invoked when a specimen type is not recognized by the deterministic
accessioning rules (ACC-010 triggered). The specimen type is neither on the
histology whitelist (biopsy, core_needle_biopsy, lumpectomy, mastectomy, excision,
re_excision, vacuum_assisted_biopsy) nor on the cytology blacklist (fna, cytology,
cytospin, etc.).

Your task is to determine the appropriate disposition for the specimen based on
clinical processability criteria in SOP §3.2.

### Response schema (REQUIRED — JSON only, no markdown fences)

Output a single JSON object with these fields:

- `disposition`: one of `"accepted"`, `"rejected"`, or `"escalated"` (required)
- `reasoning`: string, at most 200 characters; brief clinical rationale (required)

> **Note** — the 200-char cap here is an intentional divergence from the
> `query_routing` skill's 800-char cap (raised from 500 set by
> to fit `order_status` / `prioritized_list` answers).
> Specimen-review responses are a single-disposition rationale tied to
> SOP §3.2 criteria and stay tight; the cap is right-sized for that scope.

### Disposition criteria

**Accept** (`"accepted"`) — the specimen is clinically processable per SOP §3.2:
- The specimen is a solid tissue sample that can be formalin-fixed,
  paraffin-embedded (FFPE), sectioned, and stained.
- Examples: surgical excision variants not already in the whitelist (e.g.,
  wide_local_excision, partial_mastectomy), any other resection or biopsy type
  that produces solid tissue, lymph node dissection specimens,
  nipple_excision, skin_punch_biopsy.
- When in doubt about processability but the specimen name clearly refers to
  solid tissue, prefer `"accepted"`.

**Reject** (`"rejected"`) — the specimen is clearly outside the histology workflow:
- Cytology-class specimens not already caught by the blacklist (e.g., imprint,
  cell_scraping, lavage, bronchial_wash).
- Body fluid specimens (ascites, pleural_fluid, peritoneal_lavage).
- Non-tissue specimens (blood, urine, stool, or analytes measured in solution).
- The specimen name indicates it is processed by a different laboratory
  discipline (microbiology, hematology, chemistry) rather than surgical pathology.

**Escalate** (`"escalated"`) — the specimen requires human pathologist judgment:
- The specimen type is novel or genuinely ambiguous: it could plausibly be
  processable or not, and the name alone does not resolve the question.
- The specimen name is a proper noun, an internal lab code, or an acronym
  whose expansion is unclear.
- The specimen has clinical context (e.g., intraoperative consultation,
  research sample) that may require protocol deviations not covered by SOP §3.2.

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

Worked example — opaque internal lab code as `anatomic_site`. Codes
shaped like letters-digit-suffix (e.g. `AB-7-bx`, `LC-2-fnp`) carry no
anatomic meaning that can be resolved from the order data; the correct
disposition is `"escalated"` regardless of how plausible the surrounding
specimen_type / ordered_tests look. Do not infer an interpretation from
the prefix letters (`AB`, `LC`, etc.) — treat the whole token as opaque.

Input fragment —

  specimen_type: biopsy
  anatomic_site: "AB-7-bx"
  ordered_tests: [ER, PR]

Correct JSON output —

  {"disposition": "escalated", "reasoning": "Opaque internal lab code 'AB-7-bx'; no anatomic interpretation per SOP §3.2."}

Wrong answer — {"disposition": "accepted", ...}. Accepting an opaque
code skips the human-review check the SOP requires when an anatomic
site cannot be resolved from the order data alone.

### Behavior contract

- Output ONLY a JSON object matching the schema. No prose preamble, no markdown fences.
- `reasoning` must be at most 200 characters. Brief rationale referencing SOP §3.2 criteria.
- DO NOT include any field other than `disposition` and `reasoning`.

### Examples

```json
{"disposition": "accepted", "reasoning": "Solid tissue excision consistent with FFPE histology workflow per SOP §3.2."}
```

```json
{"disposition": "rejected", "reasoning": "Cytology-class wash specimen; not processable via FFPE histology."}
```

```json
{"disposition": "escalated", "reasoning": "Intraoperative specimens may require deviations from standard SOP §3.2 protocol."}
```
