# Worked-example transcript — LLM `hallucination/` path (Stage B grounded-check refusal)

**Phase.** 2 — LLM-bound runtime path.
**Category.** `hallucination/` — Stage B grounded-check verdict catches
an LLM proposal that is not supported by the skill body; router emits
a `RefusalTrace` receipt with `STAGE_B_UNGROUNDED`, no transition, no
flag changes.
**Methodology.** [`rule-authoring-methodology.md`](../rule-authoring-methodology.md)
§ 10 (LLM-path methodology examples).

**Date.** 2026-05-08. **Session.** Replay against
`tests/fixtures/scenarios/hallucination/sc-106.json` (BRCA1 mutation,
prior mastectomy, biopsy/breast, in-tolerance formalin) with the
in-process LLM fake substituted for the production MLX model;
receipts signed under a dummy key.

> **Phase 1 vs. Phase 2 transcript shape.** Phase 2 picks one canonical
> scenario per *category*. This transcript covers the *Stage B refusal*
> flow, the most defence-in-depth-flavoured of the three: the LLM has
> already proposed a rule pick, but the judge — re-prompted with the
> verbatim skill body, the same safe-context, and the alternatives —
> rejects the proposal as ungrounded. Siblings:
> [`llm-query.md`](llm-query.md) (free-text clinical lookup),
> [`llm-unknown.md`](llm-unknown.md) (preflight clarification).
>
> **Note on the source fixture.** SC-106 is documented as a
> *deterministic-pass* scenario: the extra clinical context (BRCA1
> mutation, family history, prior mastectomy) should be ignored
> because no accessioning rule reads `clinical_notes`, and the
> deterministic engine routes via ACC-008 to ACCEPTED. This transcript
> uses SC-106 as the *context* but pivots the worked example onto the
> Stage B path: it imagines an LLM that *did* let the extra context
> sway it, mis-picked ACC-006 (HER2 fixation-out-of-tolerance, which
> does not fire here because `fixation_time_hours=24.0` is well
> inside `[6, 72]`), and then was caught by the judge. The pivot is
> the methodology illustration; SC-106's authoritative deterministic
> verdict still anchors § 5 below.

---

## 1. Setup — `SpecimenContext` and the inbound event

```python
from samantha_server.models.context import Order, Event, SpecimenContext

order = Order(
    order_id="ORD-0106",
    patient_name="TESTPATIENT-0106, Maria",   # PHI — stripped by phi_safe
    patient_sex="F",                            # PHI — stripped
    age=47,                                     # Safe Harbor allowlisted (≤ 89)
    specimen_type="biopsy",
    anatomic_site="breast",
    fixative="formalin",
    fixation_time_hours=24.0,
    ordered_tests=("Breast IHC Panel",),
    priority="routine",
    billing_info_present=True,
)
event = Event(
    event_type="order_received",
    event_data={
        "patient_name": "TESTPATIENT-0106, Maria",  # PHI — hashed into event_data_hash
        "age": 47,
        "sex": "F",
        "specimen_type": "biopsy",
        "anatomic_site": "breast",
        "fixative": "formalin",
        "fixation_time_hours": 24.0,
        "ordered_tests": ["Breast IHC Panel"],
        "priority": "routine",
        "billing_info_present": True,
        "clinical_notes": (
            "BRCA1 mutation carrier. Prior contralateral mastectomy 2023. "
            "Family history: mother and maternal aunt with breast cancer."
        ),
    },
    step_index=0,
)
ctx = SpecimenContext(
    order=order,
    current_state="ACCESSIONING",
    flags=frozenset(),
    event=event,
)
```

Routing:

1. **State check.** `current_state != "PENDING_LLM_REVIEW"`.
2. **Preflight.** All canonicalised fields (`anatomic_site`,
   `fixative`, `priority`) are in their pick lists, no required field
   is null, so `preflight(ctx)` returns `PreflightOk()`.
3. **LLM rule-pick step.** The deterministic dispatcher returns
   `dispatched_rule_ids=("ACC-006", "ACC-008")` (illustrative; the
   actual production list depends on the engine's per-event
   `list_applicable_rules` enumeration). The LLM is prompted with the
   accessioning skill body and asked to pick the rule that fires.
   Stage A — the mechanical session-state gate in `propose_transition`
   that asks "did the LLM call `lookup_skill('accessioning-routing')`
   first?" — passes silently in this scenario because the LLM did
   invoke `lookup_skill` before proposing.
4. **LLM mis-pick.** The model returns `ACC-006`. This is a
   hallucination: ACC-006 fires only when HER2 (or the Breast IHC
   Panel) is ordered **and** `fixation_time_hours` is outside
   `[6, 72]`. The order has `fixation_time_hours=24.0` (in tolerance);
   the predicate would evaluate false at the deterministic gate.
5. **Stage B grounded check.** Before the engine acts on the LLM's
   pick, `propose_transition` invokes `handle_grounded_check` with
   the *picked* rule plus the *alternatives* tuple. The judge
   re-reads the skill body and the safe-context and votes on whether
   the pick is grounded. (Stage B lives in
   `samantha_server/tools/propose_transition.py`,
   not in the router-shim's
   `route()`.)

This transcript captures step 5.

---

## 2. Prompt construction — `phi_safe(ctx)` then `build_grounded_judge_prompt`

`phi_safe(ctx)` strips/hashes PHI. The visible PHI in § 1
(`"TESTPATIENT-0106, Maria"`, `"F"`, the entire raw `event.event_data`)
does not survive into the safe context. `order_id` now passes through
verbatim (GH-367: synthetic LIS id, local trust boundary):

```json
{
  "order": {
    "order_id": "ORD-0106",
    "specimen_type": "biopsy",
    "anatomic_site": "breast",
    "fixative": "formalin",
    "fixation_time_hours": 24.0,
    "ordered_tests": ["Breast IHC Panel"],
    "priority": "routine",
    "billing_info_present": true,
    "age": 47
  },
  "current_state": "ACCESSIONING",
  "flags": [],
  "event_type": "order_received",
  "event_data_hash": "7dc5782b35a9d6be3e1dbbda4b3c81f1626378f6b3bd467589d1a1a0cc1f46a0"
}
```

The `clinical_notes` text from the raw event_data — the very thing
the LLM was tempted to over-weight in step 4 — is now only a
HMAC-SHA256 digest. The judge cannot see "BRCA1" or "mastectomy". The
PHI strip is also a hallucination guard: the judge is forced to
reason against the canonical-pick fields plus the skill body, not
against free-text clinical narrative.

`build_grounded_judge_prompt`
([`samantha_server/llm/handlers.py`](../../samantha_server/llm/handlers.py))
assembles five blocks: `<skill>` (the verbatim accessioning skill
body), `<safe_context>`, `<picked_rule>`, `<alternatives>`, and a
final instruction.

The judge prompt template (with the long skill body abbreviated for
readability — the runtime sends it verbatim):

````text
<skill>
## Accessioning Evaluation Skill

Evaluate EVERY rule below. Do NOT stop after finding the first match — even if
you find a REJECT rule, KEEP CHECKING all remaining rules. ACCESSIONING
requires ALL matching rules reported in applied_rules.

IMPORTANT: Use the order data under "Current Order State" for your evaluation,
NOT the event data. The order's ordered_tests field contains the expanded test
names (e.g., "ER", "PR", "HER2", "Ki-67"). Panel names like "Breast IHC Panel"
appear in the event bu[…verbatim skill body, ~5,600 characters total…]
</skill>

<safe_context>
{"order":{"order_id":"ORD-0106","specimen_type":"biopsy","anatomic_site":"breast","fixative":"formalin","fixation_time_hours":24.0,"ordered_tests":["Breast IHC Panel"],"priority":"routine","billing_info_present":true,"age":47},"current_state":"ACCESSIONING","flags":[],"event_type":"order_received","event_data_hash":"7dc5782b35a9d6be3e1dbbda4b3c81f1626378f6b3bd467589d1a1a0cc1f46a0"}
</safe_context>

<picked_rule>
ACC-006
</picked_rule>

<alternatives>
  - ACC-008
</alternatives>

Given the skill definition, the clinical context, the picked rule, and the alternative rules that were also available, determine whether the picked rule is the correct choice.

Respond with exactly one word on the first line: 'grounded', 'ungrounded', or 'uncertain'.
````

Per the
[`build_grounded_judge_prompt`](../../samantha_server/llm/handlers.py)
docstring, the `alternatives` tuple is load-bearing (G9): the judge
weighs the picked rule against what was *also* available, not in
isolation. An empty alternatives tuple degrades the judge to a unary
yes/no verdict.

---

## 3. LLM output — raw response text

**Model.** `mlx-community/Qwen2.5-14B-Instruct-1M-4bit` is the
configured judge model at this transcript's authoring time. The judge
runs on the same model as the proposal step in this build; a future
split into proposal/judge models is the canonical follow-up. The
runtime reads `LLM_JUDGE_MODEL_PATH` (with `LLM_MODEL_PATH` as the
fallback when unset) — the literal model string here is illustrative
and would shift on a different build.
**Parameters.** `temperature=0.0` is **non-negotiable** for the judge
([`handle_grounded_check`](../../samantha_server/llm/handlers.py)
pins it). Non-zero temperature would produce different verdicts on
replay, invalidating receipt-replay determinism (G15 + G20).

The judge emitted:

```text
ungrounded
ACC-006 fires only when HER2 (or the Breast IHC Panel) is ordered AND
fixation_time_hours falls outside [6, 72]. The safe_context shows
fixation_time_hours=24.0, which is well inside tolerance, so the
ACC-006 trigger is not satisfied. ACC-008 is the correct alternative
for an in-tolerance breast biopsy with the Breast IHC Panel ordered.
```

`parse_grounded_verdict`
([`handlers.py`](../../samantha_server/llm/handlers.py)) reads the
first non-whitespace word, normalises it case-insensitively, and
returns `GroundedVerdict(kind="ungrounded")`. The trailing prose is
not consumed by the parser — the contract is "exactly one word on the
first line", and the rest is human-readable narrative that may end up
in operator dashboards but does not flow into the receipt.

**Unparseable verdicts are tagged, not raised.** If the judge emitted
something the parser couldn't classify (no recognised keyword on the
first line, or a non-string response), `parse_grounded_verdict`
returns `GroundedVerdict(kind="unparseable")` and `propose_transition`
converts it to a `RefusalTrace(refusal_reason="STAGE_B_UNPARSEABLE",
refusal_stage="B")` receipt. The unparseable verdict never silently
drops; it surfaces in the audit trail like any other refusal reason.

---

## 4. Receipt — `SignedReceipt` JSON and verification

`propose_transition` catches `GroundedVerdict(kind="ungrounded")` and
emits a `RefusalTrace` decision via `_make_refusal_decision`.
Critically: the LLM's mis-pick (`ACC-006`) is **not** applied —
`applied_rule_id` stays `None`, `next_state` stays at
`ctx.current_state`, no flags change, no transition is proposed. The
Stage B verdict's job is to refuse, not to fix.

`latency_us` on the receipt is hardcoded to `0`. Refusal decisions
are synthetic — `_make_refusal_decision`
(`propose_transition.py`)
doesn't preserve the judge-call wall time on the receipt; the timing
signal lives in observability backends (e.g. Langfuse), not in the
signed audit trail.

```json
{
  "receipt_id": "01KR4MCTFKFX28KPJAPMS6NH6K",
  "decision": {
    "applied_rule_id": null,
    "next_state": "ACCESSIONING",
    "flags_added": [],
    "flags_cleared": [],
    "outcome": "refused_ungrounded_stage_b",
    "also_matched": [],
    "dispatched_rule_ids": ["ACC-006", "ACC-008"],
    "event_input_hash": "4bd59193f776bf8a363daf538ecf4406533c4579686c290b4c49d495882f1d3f",
    "primitive_traces": {},
    "latency_us": 0,
    "decision_traces": [
      {
        "kind": "refusal",
        "refusal_reason": "STAGE_B_UNGROUNDED",
        "refusal_stage": "B",
        "judge_verdict": "ungrounded",
        "alternatives": ["ACC-008"]
      }
    ],
    "session_id": null
  },
  "signer_key_id": "v1",
  "signature": "9cacbb2d3d1754328da46755418c6dd4e4ac00b44d0aec43812326ea72cd2299920fa5da06c78ab7eae7f2358962d778c8b45c1da1c4e1a41514ba3b20760604",
  "signed_at_utc": "2026-05-08T20:27:50.131356+00:00"
}
```

The receipt records exactly enough to reproduce the refusal:
`refusal_stage="B"` (the
[`RefusalTrace._stage_invariants`](../../samantha_server/engine/decision.py)
validator enforces that `STAGE_B_*` reasons require stage `"B"`),
`judge_verdict="ungrounded"`, and `alternatives=("ACC-008",)` — the
rule the judge implicitly preferred. `dispatched_rule_ids` carries
both candidates so a downstream auditor can see the LLM had the right
answer in the alternatives tuple all along.

### 4.1 Signature verification

```python
from samantha_server.receipts.signing import verify_signature, VerificationResult

result = verify_signature(
    receipt,
    current_key=bytes(32),       # dummy 32-byte all-zeros key
    current_key_id="v1",
)
assert result is VerificationResult.VALID
```

With the dummy key on both sides, the verdict is
`VerificationResult.VALID`. A receipt signed under the dummy key but
verified against the production key would yield
`VerificationResult.INVALID_SIGNATURE`; a receipt whose
`signer_key_id` matched neither the current nor the previous key
would yield `VerificationResult.KEY_EXPIRED`.

---

## 5. Verdict — expected vs. actual

SC-106's authoritative `expected_output` lives in the fixture and
records the *deterministic-pass* outcome (no LLM in the loop):

```json
{
  "next_state": "ACCEPTED",
  "applied_rules": ["ACC-008"],
  "flags": [],
  "routing_path": "deterministic"
}
```

This worked example imagines a build where the LLM path is engaged
and proposes `ACC-006` instead. The judge catches it. The expected
**runtime-shape** verdict for this Stage B refusal:

| Field                                 | Expected                                                  | Actual (receipt)                                          | Match |
|---------------------------------------|-----------------------------------------------------------|-----------------------------------------------------------|-------|
| `outcome`                             | `refused_ungrounded_stage_b`                              | `refused_ungrounded_stage_b`                              | OK    |
| `applied_rule_id`                     | `None`                                                    | `None`                                                    | OK    |
| `next_state`                          | `ACCESSIONING` (unchanged — no transition on refusal)     | `ACCESSIONING`                                            | OK    |
| `flags_added` / `flags_cleared`       | `()`                                                      | `()`                                                      | OK    |
| `decision_traces[0].kind`             | `refusal`                                                 | `refusal`                                                 | OK    |
| `decision_traces[0].refusal_stage`    | `"B"`                                                     | `"B"`                                                     | OK    |
| `decision_traces[0].refusal_reason`   | `"STAGE_B_UNGROUNDED"`                                    | `"STAGE_B_UNGROUNDED"`                                    | OK    |
| `decision_traces[0].judge_verdict`    | `"ungrounded"`                                            | `"ungrounded"`                                            | OK    |
| `decision_traces[0].alternatives`     | `("ACC-008",)`                                            | `("ACC-008",)`                                            | OK    |

The mis-pick (`ACC-006`) does **not** appear in any decision-shape
field. The receipt is the audit-trail record that the judge fired
and refused; an operator inspecting the receipt would re-route the
order through the deterministic engine (which would correctly pick
`ACC-008`, matching the fixture's authoritative deterministic verdict
above) or escalate it to a human pathologist.

---

## 6. PHI absence audit

`SC-106`'s raw inbound carries `patient_name="TESTPATIENT-0106,
Maria"`, `patient_sex="F"`, and a `clinical_notes` string with
"BRCA1", "mastectomy", and family-history detail. After `phi_safe`,
none of those reach the prompt or the receipt. Auditing the
transcript bodies:

| Category                                                              | In transcript?                                                                                                                              | Status |
|-----------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------|--------|
| `age` (HIPAA Safe Harbor age ≤ 89)                                    | `47` in `SafeContext` — allowlisted under `_ORDER_PASS_THROUGH`                                                                             | OK     |
| Raw `patient_name` string in prompt body or receipt                   | absent — the literal name appears only in the *raw* `SpecimenContext` setup section (§ 1) to demonstrate the strip                          | OK     |
| Raw `patient_sex` string in prompt body or receipt                    | absent — `"F"` appears only in the raw setup section                                                                                        | OK     |
| Raw `order_id` string (`"ORD-0106"`) in prompt body                   | present (GH-367: pass-through, synthetic LIS id, local trust boundary)                                                                      | OK     |
| Raw `clinical_notes` text (BRCA1, mastectomy, family history)         | absent — only `event_data_hash` (HMAC) appears, exactly as designed for hallucination defence                                               | OK     |
| Field outside `_ORDER_PASS_THROUGH` in prompt body                    | absent — the `<safe_context>` JSON carries only allowlisted fields                                                                          | OK     |

The hallucination-defence story and the PHI-defence story are the
same mechanism: the judge cannot be swayed by clinical narrative it
cannot read. Stripping `clinical_notes` is what makes the
`STAGE_B_UNGROUNDED` verdict reproducible — a judge with access to
the raw notes might over-weight the BRCA1 mention and confirm an
incorrect rule pick out of "abundance of caution".

---

## 7. Cross-references

- Source fixture:
  [`tests/fixtures/scenarios/hallucination/sc-106.json`](../../tests/fixtures/scenarios/hallucination/sc-106.json).
- Handler:
  [`samantha_server/llm/handlers.py:handle_grounded_check`](../../samantha_server/llm/handlers.py)
  and `build_grounded_judge_prompt`.
- Skill body:
  [`samantha_server/skills/specs/accessioning/SKILL.md`](../../samantha_server/skills/specs/accessioning/SKILL.md).
- `RefusalTrace` schema and stage-prefix invariant:
  [`samantha_server/engine/decision.py`](../../samantha_server/engine/decision.py).
- Sibling category transcripts:
  [`llm-query.md`](llm-query.md) (free-text clinical lookup),
  [`llm-unknown.md`](llm-unknown.md) (preflight clarification).
- Methodology backfill:
  [`rule-authoring-methodology.md` § 10](../rule-authoring-methodology.md#10-llm-path-methodology-examples-phase-2).
