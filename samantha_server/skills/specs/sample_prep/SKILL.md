---
name: sample-prep-routing
description: Route sample-prep events (processing/embedding/sectioning/QC) to the next state by first-match priority on event.outcome
applies_to_states:
  - SAMPLE_PREP_PROCESSING
  - SAMPLE_PREP_EMBEDDING
  - SAMPLE_PREP_SECTIONING
  - SAMPLE_PREP_QC
---

## Sample Prep Routing Skill

Sample prep follows a fixed sequence. Identify the event type and outcome,
then apply the first matching rule.

### State Sequence

When a step completes successfully, advance to the next state in this order:

ACCEPTED → SAMPLE_PREP_PROCESSING → SAMPLE_PREP_EMBEDDING →
SAMPLE_PREP_SECTIONING → SAMPLE_PREP_QC → HE_STAINING

MISSING_INFO_PROCEED also advances to SAMPLE_PREP_PROCESSING (same as ACCEPTED).

### Rules (first match wins)

**For grossing_complete, processing_complete, embedding_complete, sectioning_complete:**

| Outcome | Precondition | Rule | Next State | Flag Effect |
|---------|--------------|------|------------|-------------|
| success | sectioning_complete AND RECUT_REQUESTED is set | SP-007 | Advance (SAMPLE_PREP_SECTIONING → SAMPLE_PREP_QC) | Clear RECUT_REQUESTED |
| success | (otherwise) | SP-001 | Advance to next state in the sequence above | — |
| failure (tissue available) | — | SP-002 | Stay at current state (RETRY) | — |
| failure (no tissue) | — | SP-003 | ORDER_TERMINATED_QNS | — |

SP-007 has higher priority than SP-001, so it preempts on the recut path
only (sectioning_complete with RECUT_REQUESTED already in flags). SP-001
still wins on first-pass sectioning where the flag isn't set.

"RETRY current step" means output the CURRENT state name, not the word "RETRY".

**For sample_prep_qc (at SAMPLE_PREP_QC state):**

| Outcome | Rule | Next State |
|---------|------|-----------|
| pass | SP-004 | HE_STAINING |
| fail (tissue available) | SP-005 | SAMPLE_PREP_SECTIONING |
| fail (no tissue / fail_qns) | SP-006 | ORDER_TERMINATED_QNS |

### Flag Clearing

RECUT_REQUESTED is set by HE-009 (pathologist requests recuts) and cleared
by SP-007 when the recut sectioning completes successfully. Per the canonical
SOP (workflow_states.yaml flags.RECUT_REQUESTED.cleared_by: "Recut completed"),
"recut completed" means the sectioning_complete event after HE-009 — not any
later event in the post-recut HE staining/QC path.

### Example

Event: grossing_complete with outcome "success" on an order in ACCEPTED state.
Result: next_state = "SAMPLE_PREP_PROCESSING", applied_rules = ["SP-001"], flags = []
