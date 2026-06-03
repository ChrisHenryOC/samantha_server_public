---
name: pathologist-he-review-routing
description: Route pathologist H&E diagnosis events (invasive_carcinoma, DCIS, suspicious, atypical, benign, recut_requested) to IHC, RESULTING, or recut paths
applies_to_states:
  - PATHOLOGIST_HE_REVIEW
---

## Pathologist H&E Review Routing Skill

Evaluate the diagnosis field in the pathologist_he_review event and apply
the first matching rule.

### Rules (first match wins)

| Diagnosis | Rule | Next State | Flags |
|-----------|------|-----------|-------|
| invasive_carcinoma | HE-005 | IHC_STAINING | [] |
| DCIS or dcis | HE-006 | IHC_STAINING | [] |
| suspicious_atypical | HE-007 | IHC_STAINING | [] |
| lobular_carcinoma_in_situ | HE-007 | IHC_STAINING | [] |
| benign | HE-008 | RESULTING | [] |
| recut_requested | HE-009 | SAMPLE_PREP_SECTIONING | ["RECUT_REQUESTED"] |

Invasive carcinoma and DCIS proceed to IHC_STAINING.
LCIS and suspicious/atypical findings also proceed to IHC_STAINING (HE-007).
Benign skips IHC entirely and goes to RESULTING.
