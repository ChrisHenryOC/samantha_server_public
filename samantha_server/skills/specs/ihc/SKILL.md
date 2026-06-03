---
name: ihc-routing
description: Route IHC staining/QC/scoring/FISH events using applies_at-scoped rules; handles HER2 fixation rejection and FISH reflex suggestion
applies_to_states:
  - IHC_STAINING
  - IHC_QC
  - IHC_SCORING
  - SUGGEST_FISH_REFLEX
  - FISH_SEND_OUT
---

## IHC Routing Skill

IHC rules apply at specific states. Find your current state below and apply
the first matching rule.

### At IHC_STAINING (after ihc_staining_complete)

| Condition | Rule | Next State | Flags |
|-----------|------|-----------|-------|
| HER2 added by pathologist AND fixation out of tolerance | IHC-001 | IHC_STAINING (self-loop: HER2 rejected, other markers continue) | ["HER2_FIXATION_REJECT"] |
| Staining success (outcome = "success") | No rule applies — set applied_rules to [] | IHC_QC | [] |

### At IHC_QC (after ihc_qc)

Read the `all_slides_complete` field from the ihc_qc EVENT DATA (not order
data). Order flags like MISSING_INFO_PROCEED do NOT affect IHC QC evaluation —
only the event data fields determine which rule applies.

```text
if event_data.all_slides_complete == false:
    → IHC-003: stay at IHC_QC

if event_data.all_slides_complete == true:
    if all slides have qc_result == "pass":
        → IHC-002: advance to IHC_SCORING
    elif any slide has qc_result == "fail" and tissue_available:
        → IHC-004: retry at IHC_STAINING
    else:
        → IHC-005: ORDER_TERMINATED_QNS
```

Example: event has `all_slides_complete: true` and all slides show
`qc_result: "pass"` → IHC-002 applies, advance to IHC_SCORING.
This is true even if the order has flags like MISSING_INFO_PROCEED.

### At IHC_SCORING (after ihc_scoring)

| Condition | Rule | Next State | Flags |
|-----------|------|-----------|-------|
| All scores complete, NO equivocal results | IHC-006 | RESULTING | [] |
| HER2 equivocal (any_equivocal = true) | IHC-007 | SUGGEST_FISH_REFLEX | Add FISH_SUGGESTED |

### At SUGGEST_FISH_REFLEX (after fish_decision)

| Condition | Rule | Next State | Flags |
|-----------|------|-----------|-------|
| Pathologist approves FISH (approved = true) | IHC-008 | FISH_SEND_OUT | REMOVE FISH_SUGGESTED |
| Pathologist declines FISH (approved = false) | IHC-009 | RESULTING | REMOVE FISH_SUGGESTED |

When IHC-008 or IHC-009 applies, remove FISH_SUGGESTED from the flags list.

### At FISH_SEND_OUT (after fish_result)

| Condition | Rule | Next State | Flags |
|-----------|------|-----------|-------|
| FISH result received | IHC-010 | RESULTING | [] |
| FISH lab returns QNS | IHC-011 | ORDER_TERMINATED_QNS | [] |
