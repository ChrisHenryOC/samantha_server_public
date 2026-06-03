---
name: resulting-routing
description: Route resulting events through MISSING_INFO_HOLD, signout, report-generation, and order-complete based on accumulated flags and event outcomes
applies_to_states:
  - RESULTING_HOLD
  - RESULTING
  - PATHOLOGIST_SIGNOUT
  - REPORT_GENERATION
---

## Resulting Routing Skill

Resulting handles the final workflow stages. Check the current state and
event type, then apply the first matching rule.

IMPORTANT: Check the order's flags from "Current Order State", not from
the event data. Flags are carried on the order object.

### At RESULTING (after resulting_review)

Check the order's flags FIRST:

| Condition | Rule | Next State | Flags |
|-----------|------|-----------|-------|
| MISSING_INFO_PROCEED flag is present | RES-001 | RESULTING_HOLD | (carry forward existing flags) |
| No blocking flags, all complete | RES-003 | PATHOLOGIST_SIGNOUT | (carry forward existing flags) |

### At RESULTING_HOLD (after missing_info_received)

| Condition | Rule | Next State | Flags |
|-----------|------|-----------|-------|
| Info received | RES-002 | RESULTING | REMOVE MISSING_INFO_PROCEED from flags |

When RES-002 applies, remove MISSING_INFO_PROCEED from the flags list.
The missing information has been received and the hold reason is resolved.

### At PATHOLOGIST_SIGNOUT (after pathologist_signout)

| Condition | Rule | Next State | Flags |
|-----------|------|-----------|-------|
| Pathologist selects reportable tests | RES-004 | REPORT_GENERATION | (carry forward) |

### At REPORT_GENERATION (after report_generated)

| Condition | Rule | Next State | Flags |
|-----------|------|-----------|-------|
| Report generated successfully | RES-005 | ORDER_COMPLETE | [] |
