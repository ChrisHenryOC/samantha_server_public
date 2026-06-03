---
name: accessioning-routing
description: Evaluate every accessioning rule against an incoming order; collect all matches and apply severity hierarchy (REJECT > HOLD > PROCEED > ACCEPT)
applies_to_states:
  - ACCESSIONING
  - ACCEPTED
  - MISSING_INFO_HOLD
  - MISSING_INFO_PROCEED
  - DO_NOT_PROCESS
---

## Accessioning Evaluation Skill

Evaluate EVERY rule below. Do NOT stop after finding the first match — even if
you find a REJECT rule, KEEP CHECKING all remaining rules. ACCESSIONING
requires ALL matching rules reported in applied_rules.

IMPORTANT: Use the order data under "Current Order State" for your evaluation,
NOT the event data. The order's ordered_tests field contains the expanded test
names (e.g., "ER", "PR", "HER2", "Ki-67"). Panel names like "Breast IHC Panel"
appear in the event but are already expanded in the order.

### Rule Checklist

Evaluate each condition. MATCH = the condition is true.

```text
ACC-001: patient_name is null or missing?
ACC-002: patient_sex is null or missing?
ACC-003: anatomic_site not in {"breast","left breast","right breast","axillary lymph node","chest wall"}?
ACC-004: specimen_type in cytology blacklist (fna, cytology, cytospin, ...)?
ACC-010: specimen_type NOT in blacklist AND NOT in whitelist (unrecognized → PENDING_LLM_REVIEW)?
```

HER2 fixation checks — FIRST check if HER2 is ordered:

```text
if "HER2" not in ordered_tests:
    ACC-005 = NO MATCH
    ACC-006 = NO MATCH
    ACC-009 = NO MATCH
    (skip to ACC-007 — fixation rules only apply to HER2 orders)
    Example: ordered_tests = ["ER", "PR", "Ki-67"] → no HER2 → skip all three

if "HER2" in ordered_tests:
    ACC-005: fixative != "formalin"?
    ACC-006: fixation_time_hours < 6.0 or fixation_time_hours > 72.0?
             (5.0 < 6.0 is TRUE. 6.0 < 6.0 is FALSE. Skip if null.)
    ACC-009: fixation_time_hours is null?
             (A number like 5.0 is NOT null.)
```

Remaining checks:

```text
ACC-007: billing_info_present == false?
ACC-008: ALL above conditions are false? (catch-all: all validations pass)
       If ACC-008 matches, applied_rules = ["ACC-008"].
```

### Determine Outcome

Collect ALL rules that matched, then look up the severity:

```text
Severity table:
  REJECT  → ACC-003, ACC-004, ACC-005, ACC-006  → next_state = "DO_NOT_PROCESS"
  HOLD    → ACC-001, ACC-002, ACC-009            → next_state = "MISSING_INFO_HOLD"
  PROCEED → ACC-007                              → next_state = "MISSING_INFO_PROCEED"
  PROCEED → ACC-010                              → next_state = "PENDING_LLM_REVIEW" (LLM review)
  ACCEPT  → ACC-008                              → next_state = "ACCEPTED"
```

Apply the HIGHEST severity among matched rules (REJECT > HOLD > PROCEED > ACCEPT):

```text
if any of {ACC-003, ACC-004, ACC-005, ACC-006} matched:
    next_state = "DO_NOT_PROCESS"       # REJECT — flags = [] always
elif any of {ACC-001, ACC-002, ACC-009} matched:
    next_state = "MISSING_INFO_HOLD"    # HOLD — flags = []
elif ACC-010 matched:
    next_state = "PENDING_LLM_REVIEW"   # PROCEED (LLM review)
    flags = ["LLM_REVIEW_REQUESTED"]
elif ACC-007 matched:
    next_state = "MISSING_INFO_PROCEED" # PROCEED
    flags = ["MISSING_INFO_PROCEED"]
else:
    next_state = "ACCEPTED"             # ACC-008
    flags = []

applied_rules = sorted list of ALL matching rule IDs
```

IMPORTANT: ACC-009 is HOLD severity, NOT REJECT. Null fixation time means
missing data — the order is held for info, not rejected.

Fixation time is enforced as a hard pass/fail by ACC-006: orders with
`fixation_time_hours` outside the closed interval `[6.0, 72.0]` are
rejected. There is no borderline-warning band — the sample is either
within the range, or it isn't.

### Example 1: Single defect

Order: patient_name="Jane", specimen_type="biopsy", anatomic_site="breast",
fixative="formalin", fixation_time_hours=5.0,
ordered_tests=["ER","PR","HER2","Ki-67"], billing_info_present=true

```text
ACC-001: "Jane" is not null           → NO MATCH
ACC-002: sex is "F" (not null)        → NO MATCH
ACC-003: "breast" in valid set        → NO MATCH
ACC-004: "biopsy" in valid set        → NO MATCH
ACC-005: fixative is "formalin"       → NO MATCH
ACC-006: 5.0 < 6.0? YES              → MATCH
ACC-009: 5.0 is not null             → NO MATCH
ACC-007: billing_info_present is true → NO MATCH
```

Result: applied_rules=["ACC-006"], next_state="DO_NOT_PROCESS", flags=[]

### Example 2: Multiple defects

Order: patient_name=null, specimen_type="cytospin", anatomic_site="breast",
fixative="alcohol", fixation_time_hours=5.0,
ordered_tests=["ER","PR","HER2","Ki-67"], billing_info_present=false

```text
ACC-001: null                         → MATCH
ACC-002: "F" is not null              → NO MATCH
ACC-003: "breast" in valid set        → NO MATCH
ACC-004: "cytospin" not in valid set  → MATCH
ACC-005: "alcohol" != "formalin"      → MATCH
ACC-006: 5.0 < 6.0? YES              → MATCH
ACC-009: 5.0 is not null             → NO MATCH
ACC-007: false                        → MATCH
```

Result: applied_rules=["ACC-001","ACC-004","ACC-005","ACC-006","ACC-007"],
next_state="DO_NOT_PROCESS", flags=[]

### Example 3: HOLD severity (null fixation time)

Order: patient_name="Betty", specimen_type="biopsy", anatomic_site="breast",
fixative="formalin", fixation_time_hours=null,
ordered_tests=["ER","PR","HER2","Ki-67"], billing_info_present=true

```text
ACC-001: "Betty" is not null          → NO MATCH
ACC-002: "F" is not null              → NO MATCH
ACC-003: "breast" in valid set        → NO MATCH
ACC-004: "biopsy" in valid set        → NO MATCH
ACC-005: fixative is "formalin"       → NO MATCH
ACC-006: fixation_time_hours is null  → SKIP (ACC-009 handles null)
ACC-009: fixation_time_hours is null  → MATCH
ACC-007: billing_info_present is true → NO MATCH
```

Result: applied_rules=["ACC-009"],
next_state="MISSING_INFO_HOLD", flags=[]
(ACC-009 is HOLD severity, not REJECT — null means missing data, not rejection)
