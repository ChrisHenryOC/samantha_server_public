# Worked-example transcript — LLM `unknown_input/` path (preflight clarification)

**Phase.** 2 — LLM-bound runtime path.
**Category.** `unknown_input/` — preflight detects a value outside the
canonical pick list; router routes to the clarification handler instead
of letting the deterministic engine evaluate against an unknown
canonical field.
**Methodology.** [`rule-authoring-methodology.md`](../rule-authoring-methodology.md)
§ 10 (LLM-path methodology examples).

**Date.** 2026-05-08. **Session.** Synthetic but representative
context (`fixative="ethanol"`); receipts signed under a dummy key.

> **Phase 1 vs. Phase 2 transcript shape.** Phase 2 picks one canonical
> scenario per *category* — there is no tier hierarchy across the three
> LLM-bound categories. This transcript covers the
> *preflight-then-clarify* flow; its siblings are
> [`llm-query.md`](llm-query.md) (free-text clinical lookup) and
> [`llm-hallucination.md`](llm-hallucination.md) (Stage B grounded-check
> refusal).

The fixture corpus under `tests/fixtures/scenarios/unknown_input/`
ships scenarios that exercise rule-authored unknown-handling paths
(SC-100 routes via ACC-004; ACC-010 owns the `specimen_type` unknown
path). For this methodology transcript the canonical example is the
*orthogonal* preflight path — a field that is **not** owned by a rule
and therefore short-circuits before dispatch — because that is the
shape the router-shim's clarification handler is built around.
`fixative` is the chosen field: it lives in `CANONICALIZED_ORDER_FIELDS`
([`samantha_server/llm/preflight.py`](../../samantha_server/llm/preflight.py))
but `specimen_type` does not (ACC-010 owns that path; preflight would
collide with it). `ethanol` is plausibly a real LIS misspelling for
"ethanol-based fixation" but not in the canonical pick list, so
`canonicalize("fixative", "ethanol")` returns `was_unknown=True`.

---

## 1. Setup — `SpecimenContext` and the inbound event

The raw inbound order carries a populated `patient_name` ("Jane Doe")
to demonstrate the PHI strip on a path where the LIS *did* supply
patient identity. The fixative comes in canonicalisation-unfriendly:

```python
from samantha_server.models.context import Order, Event, SpecimenContext

order = Order(
    order_id="ORD-200",
    patient_name="Jane Doe",       # PHI — stripped by phi_safe before any prompt
    patient_sex="F",                # PHI — stripped
    age=42,                         # Safe Harbor allowlisted (≤ 89)
    specimen_type="biopsy",
    anatomic_site="breast",
    fixative="ethanol",            # NOT in the canonical pick list
    fixation_time_hours=24.0,
    ordered_tests=("Breast IHC Panel",),
    priority="routine",
    billing_info_present=True,
)
event = Event(
    event_type="order_received",
    event_data={
        "patient_name": "Jane Doe",  # PHI in event_data — hashed into event_data_hash
        "age": 42,
        "sex": "F",
        "specimen_type": "biopsy",
        "anatomic_site": "breast",
        "fixative": "ethanol",
        "fixation_time_hours": 24.0,
        "ordered_tests": ["Breast IHC Panel"],
        "priority": "routine",
        "billing_info_present": True,
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

The router-shim's `route()`
runs in fixed order:

1. **State check.** `current_state != "PENDING_LLM_REVIEW"`, so the
   specimen-review handler does not preempt.
2. **Preflight.** `preflight(ctx)` walks `REQUIRED_ORDER_FIELDS`
   (currently empty — every required-field null already has a
   deterministic rule that handles it) and the
   `CANONICALIZED_ORDER_FIELDS` set (`anatomic_site`, `fixative`,
   `priority`). For each, it calls `canonicalize(field, raw_value)` and
   checks `was_unknown`.
3. **Branch.** `canonicalize("fixative", "ethanol")` returns
   `was_unknown=True`, so preflight constructs:

   ```python
   PreflightMissing(
       kind="missing",
       missing_fields=(),
       unknown_canonical_fields=("fixative",),
   )
   ```

   The router routes to `handle_clarification` *before* the
   deterministic engine runs evaluate(). Bypassing dispatch is
   deliberate: a downstream rule that reads `fixative` would otherwise
   evaluate against an unknown value and silently fail-closed (per the
   null-discipline contract), losing the audit-trail signal that the
   value was *unknown* rather than merely *missing*.

---

## 2. Prompt construction — `phi_safe(ctx)` then `build_clarification_prompt`

`phi_safe(ctx)` runs first and is the load-bearing PHI guard.
[`samantha_server/llm/phi.py`](../../samantha_server/llm/phi.py)
implements the strip:

- `_ORDER_PASS_THROUGH` (`order_id`, `specimen_type`, `anatomic_site`,
  `fixative`, `fixation_time_hours`, `ordered_tests`, `priority`,
  `billing_info_present`, `age`) survives verbatim. Note: `order_id`
  is now pass-through (GH-367: synthetic LIS id, local trust boundary).
- `_HASHED_PHI_FIELDS` is now empty by design (GH-367: `order_id` was
  the only member and is now pass-through).
- `_STRIPPED_PHI_FIELDS` (`patient_name`, `patient_sex`) is dropped
  entirely — `SafeOrder` has no field for either.
- `event.event_data` (which carries the raw `patient_name` "Jane Doe"
  on the inbound) is HMAC-hashed into `event_data_hash`; the raw
  payload never serialises into `SafeContext`.
- `age=42` ≤ 89, so the G18 PHI-boundary guard passes. (An `age=92`
  context would raise `PHIBoundaryError` here, which
  `handle_clarification` catches and converts to a
  `RefusalTrace(refusal_reason="STAGE_PRE_PHI_BOUNDARY",
  refusal_stage="PRE")` receipt — never an unaudited drop.)

The `SafeContext` JSON the assistant fed into the clarification
prompt:

```json
{
  "order": {
    "order_id": "ORD-200",
    "specimen_type": "biopsy",
    "anatomic_site": "breast",
    "fixative": "ethanol",
    "fixation_time_hours": 24.0,
    "ordered_tests": ["Breast IHC Panel"],
    "priority": "routine",
    "billing_info_present": true,
    "age": 42
  },
  "current_state": "ACCESSIONING",
  "flags": [],
  "event_type": "order_received",
  "event_data_hash": "e4db16ed45abc856b814a470baa83aab9e1a4612b9924b38903e03532661bc65"
}
```

Note what is **not** there: no `patient_name` field at any level (the
"Jane Doe" string from the raw `Order` and from `event.event_data` is
nowhere in the JSON), no `patient_sex` field. The `order_id` now
appears verbatim (GH-367: synthetic LIS id, local oMLX trust boundary).

`build_clarification_prompt`
([`handlers.py`](../../samantha_server/llm/handlers.py)) then
assembles three blocks:

```text
<fields_to_clarify>
  - fixative
</fields_to_clarify>

<safe_context>
{"order":{"order_id":"ORD-200","specimen_type":"biopsy","anatomic_site":"breast","fixative":"ethanol","fixation_time_hours":24.0,"ordered_tests":["Breast IHC Panel"],"priority":"routine","billing_info_present":true,"age":42},"current_state":"ACCESSIONING","flags":[],"event_type":"order_received","event_data_hash":"e4db16ed45abc856b814a470baa83aab9e1a4612b9924b38903e03532661bc65"}
</safe_context>

The above order has missing or unrecognized values for the fields listed above. For each field, provide the canonical form of the value.

Respond with one line per field in the format:
  field_name=canonical_value

Respond only with field=value lines. One line per field.
```

---

## 3. LLM output — raw response text

**Model.** `mlx-community/Qwen2.5-14B-Instruct-1M-4bit`.
**Parameters.** `temperature=0.0` (`handle_clarification` pins it for
determinism — the receipt records `suggested_values`, and a
non-deterministic suggestion would invalidate replay).

The assistant emitted:

```text
fixative=formalin
```

`_parse_clarification_response`
([`handlers.py`](../../samantha_server/llm/handlers.py)) parses the
single line, drops anything outside the `fields_to_clarify` set
(none here), length-caps each value at 256 chars, and re-canonicalises
via `canonicalize("fixative", "formalin")`. Because `formalin` *is* in
the canonical pick list (`was_unknown=False`), the suggestion survives
and lands in `suggested_values={"fixative": "formalin"}`.

---

## 4. Receipt — `SignedReceipt` JSON and verification

The clarification path emits `outcome="needs_clarification"`,
`applied_rule_id=None`, and `next_state=ctx.current_state` (the
clarification handler does **not** auto-apply suggestions —
`suggested_values` is advisory; a future Phase 4 orchestrator will
decide whether to re-issue the order with the suggested fix).

```json
{
  "receipt_id": "01KR4M9ERPTRY0HZWPKK3W2BXQ",
  "decision": {
    "applied_rule_id": null,
    "next_state": "ACCESSIONING",
    "flags_added": [],
    "flags_cleared": [],
    "outcome": "needs_clarification",
    "also_matched": [],
    "dispatched_rule_ids": [],
    "event_input_hash": "ecc18efad09c0cb40fc0c11afb65aa2d05a11b64846988ef174044edab5922d9",
    "primitive_traces": {},
    "latency_us": 287000,
    "decision_traces": [
      {
        "kind": "clarification",
        "missing_fields": [],
        "unknown_canonical_fields": ["fixative"],
        "suggested_values": {"fixative": "formalin"},
        "model_id": "mlx-community/Qwen2.5-14B-Instruct-1M-4bit"
      }
    ],
    "session_id": null
  },
  "signer_key_id": "v1",
  "signature": "2246c4120ac3e183bf11106e09bea87d89904bec64c28b4052ed22436b7f785f6b459e83ee4ea818f275db3d60445642f7c9d2f352201c247e2d016232d0cf04",
  "signed_at_utc": "2026-05-08T20:25:59.830494+00:00"
}
```

`missing_fields` and `unknown_canonical_fields` are recorded
**separately** (not merged) so audit-trail replay can reconstruct
why each field was flagged: a null fixative would land in
`missing_fields`, while an unparseable value lands in
`unknown_canonical_fields`. The shape mirrors the discriminated union
in [`preflight.py`](../../samantha_server/llm/preflight.py) so the
receipt is the source-of-truth replay artefact.

### 4.1 LLMClientError fallback

The receipt above is the happy-path shape. If the local LLM is
unavailable (timeout, model-load failure, inference crash —
`LLMClientError` and its subclasses) `handle_clarification` catches
the exception, logs WARNING + `_logger.debug(..., exc_info=exc)`, and
**still emits a `ClarificationTrace` receipt** rather than degrading
to a `RefusalTrace`. The fallback receipt has the same shape as
above with three sentinel values:

- `decision_traces[0].suggested_values` is `{}` (the load-bearing
  data is `missing_fields` + `unknown_canonical_fields`; the LLM
  suggestion is advisory).
- `decision_traces[0].model_id` is `""` — the LLM-unavailability
  sentinel for downstream consumers (a future Phase 4 orchestrator
  must treat this as "no suggestion produced", not as "the empty
  string is a valid model identifier").
- `decision.latency_us` is `0`.

The `outcome` stays `"needs_clarification"`. See
[`handle_clarification`](../../samantha_server/llm/handlers.py)'s
docstring for the contract.

### 4.2 Signature verification

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
`VerificationResult.VALID`. Any production receipt verified against
this dummy key would yield `VerificationResult.INVALID_SIGNATURE`
because the canonical-payload bytes were signed under a different
seed.

---

## 5. Verdict — expected vs. actual

There is no fixture-side `expected_output` for this synthetic context;
the contract under test is the runtime-shape one:

| Field                                                | Expected                       | Actual (receipt)               | Match |
|------------------------------------------------------|--------------------------------|--------------------------------|-------|
| `outcome`                                            | `needs_clarification`          | `needs_clarification`          | OK    |
| `applied_rule_id`                                    | `None`                         | `None`                         | OK    |
| `next_state`                                         | `ACCESSIONING` (unchanged)     | `ACCESSIONING`                 | OK    |
| `flags_added` / `flags_cleared`                      | `()`                           | `()`                           | OK    |
| `decision_traces[0].kind`                            | `clarification`                | `clarification`                | OK    |
| `decision_traces[0].unknown_canonical_fields`        | `("fixative",)`                | `("fixative",)`                | OK    |
| `decision_traces[0].suggested_values["fixative"]`    | `"formalin"` (canonical pick)  | `"formalin"`                   | OK    |

The path emits a complete audit trail without proposing a transition.
A Phase 4 orchestrator inspecting the receipt would either resubmit
the order with `fixative="formalin"` (if a human approves the
suggestion) or hold it pending operator review.

---

## 6. PHI absence audit

The `Order` and `event.event_data` constructed above carry
`patient_name="Jane Doe"` and `patient_sex="F"`. After `phi_safe`,
both are gone. Auditing the transcript bodies:

| Category                                                                    | In transcript?                                                                                                                              | Status |
|-----------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------|--------|
| `age` (HIPAA Safe Harbor age ≤ 89)                                          | `42` in `SafeContext` — allowlisted under `_ORDER_PASS_THROUGH`                                                                             | OK     |
| Raw `patient_name` string in prompt body or receipt                         | absent — `"Jane Doe"` appears only in the *raw* `SpecimenContext` setup section to demonstrate the strip                                    | OK     |
| Raw `patient_sex` string in prompt body or receipt                          | absent — `"F"` appears only in the raw setup section                                                                                        | OK     |
| Raw `order_id` string (`"ORD-200"`) in prompt body                          | present (GH-367: pass-through, synthetic LIS id, local trust boundary)                                                                      | OK     |
| Raw `event.event_data` payload (carries `patient_name`) in prompt body      | absent — only `event_data_hash` (HMAC) appears                                                                                              | OK     |
| Field outside `_ORDER_PASS_THROUGH` in prompt body                          | absent — the `<safe_context>` JSON carries only allowlisted fields                                                                          | OK     |

The visible disappearance of "Jane Doe" between § 1 (raw setup) and
§ 2 (`SafeContext` JSON) is the load-bearing demonstration of the PHI
boundary. Nothing in the prompt body or receipt round-trips back to
the raw patient-identity strings.

---

## 7. Cross-references

- Preflight implementation:
  [`samantha_server/llm/preflight.py`](../../samantha_server/llm/preflight.py)
  — `CANONICALIZED_ORDER_FIELDS`, `PreflightMissing`, `preflight()`.
- Handler:
  [`samantha_server/llm/handlers.py:handle_clarification`](../../samantha_server/llm/handlers.py).
- PHI transform:
  [`samantha_server/llm/phi.py`](../../samantha_server/llm/phi.py) —
  `_ORDER_PASS_THROUGH`, `_STRIPPED_PHI_FIELDS`, `_HASHED_PHI_FIELDS`,
  `phi_safe()`, the G18 boundary guard.
- Sibling category transcripts:
  [`llm-query.md`](llm-query.md) (free-text clinical lookup),
  [`llm-hallucination.md`](llm-hallucination.md) (Stage B grounded-check
  refusal).
- Methodology backfill:
  [`rule-authoring-methodology.md` § 10](../rule-authoring-methodology.md#10-llm-path-methodology-examples-phase-2).
