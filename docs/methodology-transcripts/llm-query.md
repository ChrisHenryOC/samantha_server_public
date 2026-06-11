# Worked-example transcript — LLM `query/` path (QR-001)

**Phase.** 2 — LLM-bound runtime path.
**Category.** `query/` — free-text clinical lookup against historical
scenarios; no rule fires, no state transitions, no flags change.
**Methodology.** [`rule-authoring-methodology.md`](../rule-authoring-methodology.md)
§ 10 (LLM-path methodology examples).

**Date.** 2026-05-08. **Session.** Replay against
`tests/fixtures/scenarios/query/qr-001.json` with the in-process LLM
fake substituted for the production MLX model; receipts signed under
a dummy key.

> **Phase 1 vs. Phase 2 transcript shape.** Phase 1 picked one
> canonical scenario per *complexity tier* (trivial / numeric /
> compositional) because the artefact under study was a YAML rule and
> the gradient that mattered was predicate complexity. Phase 2 picks
> one canonical scenario per *category* — there is no tier hierarchy
> across the LLM-bound categories (`query/`, `unknown_input/`,
> `hallucination/`); each category exercises a different part of the
> runtime (free-text retrieval, preflight clarification, Stage B
> grounded-check). This transcript and its two siblings
> ([`llm-unknown.md`](llm-unknown.md), [`llm-hallucination.md`](llm-hallucination.md))
> are the canonical methodology examples for the LLM path.

---

## 1. Setup — `SpecimenContext` and the inbound event

The scenario fixture
([`tests/fixtures/scenarios/query/qr-001.json`](../../tests/fixtures/scenarios/query/qr-001.json))
ships a `database_state` payload (seven illustrative orders) plus a
free-text `query` string. Query-category fixtures are not event-driven
in the same shape as workflow scenarios — the `database_state` is
illustrative of the corpus the LLM is reasoning over; the engine still
constructs a `SpecimenContext` whose `Event` carries the query string
in `event_data`.

For the worked example, the assistant constructed the context against
the order pinned by the question ("orders ready for grossing", anchored
on `ORD-101`):

```python
from samantha_server.models.context import Order, Event, SpecimenContext

order = Order(
    order_id="ORD-101",
    patient_name=None,         # query path — no PHI in the bound order
    patient_sex=None,
    age=None,
    specimen_type="biopsy",
    anatomic_site="breast",
    fixative="formalin",
    fixation_time_hours=24.0,
    ordered_tests=(),
    priority="routine",
    billing_info_present=True,
)
event = Event(
    event_type="clinical_query",
    event_data={"query": "What orders are ready for grossing?"},
    step_index=0,
)
ctx = SpecimenContext(
    order=order,
    current_state="ACCEPTED",
    flags=frozenset(),
    event=event,
)
```

`event.event_type == "clinical_query"` is the dispatcher key the
router-shim reads first.
[`router.route()`](../../samantha_server/llm/router.py) routes this
context to `handle_clinical_query` after the state-precedence check
(`current_state` is not `PENDING_LLM_REVIEW`) and after preflight
returns `PreflightOk()` (no missing required fields, no unknown
canonical values).

---

## 2. Prompt construction — `phi_safe(ctx)` then `build_query_prompt`

Every LLM-payload path routes its context through `phi_safe(ctx)`
([`samantha_server/llm/phi.py`](../../samantha_server/llm/phi.py))
**before** any prompt body is assembled. The transform is the only
boundary between raw `SpecimenContext` and the LLM call:

- Fields in `_ORDER_PASS_THROUGH` (`order_id`, `specimen_type`,
  `anatomic_site`, `fixative`, `fixation_time_hours`, `ordered_tests`,
  `priority`, `billing_info_present`, `age`) pass verbatim into
  `SafeOrder`. Note: `order_id` is now pass-through (see below).
- `patient_name` and `patient_sex` are stripped entirely; no
  representation reaches the prompt.
- `event.event_data` is HMAC-hashed into `event_data_hash`; the raw
  payload is never serialised into `SafeContext`.
- `age > 89` raises `PHIBoundaryError` (G18). `age is None` for this
  scenario, so the guard is a no-op here — the boundary check is
  exercised in [`llm-unknown.md`](llm-unknown.md).

**order_id trust-boundary note:** `order_id` is a synthetic LIS
identifier (not a HIPAA Safe Harbor element). The model runs on oMLX
inside the local trust boundary. The raw
`order_id` now passes through verbatim to `SafeOrder.order_id`; no
HMAC hash is required within this deployment. `patient_name` and
`patient_sex` remain STRIPPED; `event_data_hash` remains HASHED.

The `SafeContext` JSON the assistant fed into the prompt for QR-001:

```json
{
  "order": {
    "order_id": "ORD-101",
    "specimen_type": "biopsy",
    "anatomic_site": "breast",
    "fixative": "formalin",
    "fixation_time_hours": 24.0,
    "ordered_tests": [],
    "priority": "routine",
    "billing_info_present": true,
    "age": null
  },
  "current_state": "ACCEPTED",
  "flags": [],
  "event_type": "clinical_query",
  "event_data_hash": "c837ad328ea8fb4cde3378d06caaeceee9a3fc02ef1a1fdb8cb65e5c3730ba6f"
}
```

`build_query_prompt`
([`samantha_server/llm/handlers.py`](../../samantha_server/llm/handlers.py))
then assembles two blocks in deterministic order: `<query>` (the
literal query text, XML-escaped) followed by `<skill>` (the verbatim
`query-routing` skill body). An optional `<orders>` block (canonical
JSON of `database_state.orders`) appends when present. The
`<similar_scenarios>` and `<safe_context>` blocks are no longer
emitted: the production `scenarios_index` is empty (so similar
scenarios always rendered `(none available)`) and `ctx.order` is a
harness artifact for query events whose audit role is already served
by signed receipts and Langfuse span metadata.

The full prompt body (verbatim from `build_query_prompt`):

````text
<query>What orders are ready for grossing?</query>

<skill>
## Query Routing Skill

You are answering a free-text clinical query from a laboratory professional.
Your task is to produce a clear, accurate free-text response to the question.

### Behavior contract

- **Do NOT** suggest any workflow state change or transition.
- **Do NOT** include rule IDs, outcome codes, or transition-style fields in your response.
- **DO** cite similar historical scenarios by their scenario ID (e.g., QR-001) to
  support your answer. Citing scenarios improves auditability and allows the
  laboratory to verify your reasoning against known cases.
- **DO** return a concise free-text response that directly answers the query.
- **DO** base your answer on the provided current context (order state, flags,
  event data) and the similar scenarios supplied.

### Response format

Provide:
1. A direct answer to the query (1–3 sentences).
2. Supporting reasoning that references the similar scenarios by ID.
3. Any relevant caveats or edge cases the questioner should be aware of.

Example response structure:

> **Answer:** Orders ORD-101 and ORD-103 are ready for grossing.
>
> **Reasoning:** Both orders are in the `ACCEPTED` state, which indicates
> accessioning is complete and the specimen is cleared for the next step.
> This matches the pattern shown in scenarios QR-001 and QR-003.
>
> **Caveats:** Rush-priority orders should be grossed first.

### Grounding

You must ground every factual claim in the provided context or the cited
scenarios. Do not speculate beyond what the data supports. If the query
cannot be answered from the available information, say so explicitly.

</skill>
````

---

## 3. LLM output — raw response text

**Model.** `mlx-community/Qwen2.5-14B-Instruct-1M-4bit` is the
configured local model at this transcript's authoring time (see
[`config.py`](../../samantha_server/config.py) — `LLM_MODEL_PATH`).
The runtime always reads the model identifier from config; the
literal string above and the `model_id` value baked into § 4's
receipt are illustrative and would shift on a different build.
**Parameters.** `temperature` left at the client default
(`handle_clinical_query` does not pin temperature for queries because
the response is free-text and a small amount of variation is
acceptable; deterministic verdicts are required only for the Stage B
judge in [`llm-hallucination.md`](llm-hallucination.md) and the
clarification path in [`llm-unknown.md`](llm-unknown.md)).

The assistant emitted:

```text
**Answer:** Orders ORD-101, ORD-103, and ORD-105 are ready for grossing.

**Reasoning:** All three orders are in the `ACCEPTED` state, indicating
accessioning is complete and the specimens are cleared for the grossing step.
This pattern matches scenario QR-001, which lists ACCEPTED-state orders
as the canonical "ready for grossing" cohort.

**Caveats:** ORD-103 carries `priority: rush`; queue it ahead of the
routine orders.
```

Note the response cites a scenario ID (`QR-001`) and stays inside the
behaviour contract — no rule IDs, no outcome codes, no transition-style
fields. The skill's grounding clause held.

---

## 4. Receipt — `SignedReceipt` JSON and verification

`handle_clinical_query` returns an `EngineDecision` with
`outcome="query_response"`, `applied_rule_id=None`, and
`next_state=ctx.current_state` (the engine never proposes a transition
from a query). The router-shim's
[`_finalize`](../../samantha_server/llm/router.py) helper signs the
decision via `sign_decision` and persists the resulting `SignedReceipt`
to the configured store. The receipt below was generated under the
dummy signing key (`bytes(32)` — 32 zero bytes) for transcript
purposes; do not use this key in any real environment.

```json
{
  "receipt_id": "01KR4M64S17HAPSD6ET0RN7WY1",
  "decision": {
    "applied_rule_id": null,
    "next_state": "ACCEPTED",
    "flags_added": [],
    "flags_cleared": [],
    "outcome": "query_response",
    "also_matched": [],
    "dispatched_rule_ids": [],
    "event_input_hash": "6a49b63e8bbb3e6c9b1b3cdea2bcbf19996f4da4ce8c7688d1c9bea1610bbc42",
    "primitive_traces": {},
    "latency_us": 412000,
    "decision_traces": [
      {
        "kind": "query",
        "query_text_hash": "c837ad328ea8fb4cde3378d06caaeceee9a3fc02ef1a1fdb8cb65e5c3730ba6f",
        "skill_doc_hash": "4b893412eae76467740d733badeb51138db4210d531c84c4a9631827c5b100ee",
        "scenarios_cited": [],
        "model_id": "mlx-community/Qwen2.5-14B-Instruct-1M-4bit",
        "response_text_hash": "4428f73075417b6826881abf83939fafa047ef57fc6bb14e2e4f6ffb94f49c65"
      }
    ],
    "session_id": null
  },
  "signer_key_id": "v1",
  "signature": "4abe8af5dc64f1bc9254c3916e822573259ef5374450ae378690d9549168995913a94efdf7ce00e86a1c0176db886af509f5e2526da7be0c52f143a82c7f9f0d",
  "signed_at_utc": "2026-05-15T04:41:08.338481+00:00"
}
```

`query_text_hash` is HMAC-SHA256 over the canonical JSON encoding of
`event.event_data` (G17 — keyed HMAC, not bare SHA-256), and equals
`safe_ctx.event_data_hash` by construction (both run through
`_canonical_event_data` + `_hmac_hex`). `skill_doc_hash` and
`response_text_hash` are unkeyed SHA-256 over the skill body and the
LLM response text respectively — those payloads are not PHI-bearing
(the response is a free-text answer about order IDs, all of which are
themselves hashed in their own audit trail).

### 4.1 Signature verification

```python
from samantha_server.receipts.signing import verify_signature, VerificationResult

result = verify_signature(
    receipt,
    current_key=bytes(32),       # the dummy 32-byte all-zeros key
    current_key_id="v1",
)
assert result is VerificationResult.VALID
```

With the same dummy key used for both signing and verification, the
verdict is `VerificationResult.VALID`. In production the key bytes
come from `RECEIPT_SIGNING_KEY` (32 hex bytes loaded by
[`config.py`](../../samantha_server/config.py)); receipts signed under
the dummy key and verified against the production key would produce
`VerificationResult.INVALID_SIGNATURE`. This separation is what makes
the dummy key safe to commit to a docs file.

---

## 5. Verdict — expected vs. actual

The fixture's `expected_output` block:

```json
{
  "answer_type": "order_list",
  "order_ids": ["ORD-101", "ORD-103", "ORD-105"],
  "reasoning": "Orders in ACCEPTED state have completed accessioning and are ready for grossing. ORD-101, ORD-103, and ORD-105 are all in ACCEPTED state."
}
```

The receipt records `outcome="query_response"` and an empty
`applied_rules` (rendered as `applied_rule_id=null`,
`also_matched=[]`); no rule fires on the query path by construction.
The expected and actual `next_state` both stay at `"ACCEPTED"` — no
transition is proposed, and `flags` stays empty on both sides.

The free-text answer is what the fixture's `order_ids` and `reasoning`
fields anchor against. The accuracy assertion happens at the
`/replay-scenarios` boundary (developer-side, against the loaded
local model), not in CI; see
`docs/plans/ci-implementation.md` § 5.6
for the LLM-determinism rationale. For this transcript the
fake-substituted response matches the expected order list exactly.

---

## 6. PHI absence audit

The transcript above is searchable and the audit list returns clean:

| Category                                          | In transcript?                                                          | Status |
|---------------------------------------------------|-------------------------------------------------------------------------|--------|
| `age` (HIPAA Safe Harbor age ≤ 89)                | `null` in `SafeContext`                                                 | OK     |
| Raw `patient_name` string outside `_ORDER_PASS_THROUGH` documentation | absent — `Order.patient_name=None` at construction              | OK     |
| Raw `order_id` string in prompt body              | present (pass-through, synthetic LIS id, local trust boundary) | OK     |
| Field outside `_ORDER_PASS_THROUGH` in prompt body | absent — `<safe_context>` carries only allowlisted fields              | OK     |

The query path is the cleanest of the three LLM-bound categories on
the PHI surface: the bound order does not need to carry patient
identity to answer "which orders are ready for grossing?", so the
strip is mostly a no-op. The
[`llm-unknown.md`](llm-unknown.md) sibling exercises the strip with a
populated `patient_name` and demonstrates the visible disappearance.

> **`database_state` order IDs are intentionally present.** The skill
> body example, the LLM response text, and the fixture's
> `expected_output` reference `ORD-101`, `ORD-103`, and `ORD-105`.
> These are the bound order's identifier and sibling order identifiers
> in the hypothetical `database_state` payload. The
> bound `order_id` passes through verbatim into `SafeOrder.order_id`
> (synthetic LIS id, local trust boundary). The fixture-side IDs are
> part of a hypothetical `database_state` payload describing the lab's
> order inventory; in production the LLM constructs the response from
> queried data and the answer text is covered by `response_text_hash`
> in the receipt, not by `phi_safe`. (Production deployments must
> still keep this query path's *response text* free of raw PHI; the
> receipt records only the hash, but the live response goes back to
> the operator.)

---

## 7. Cross-references

- Canonical fixture:
  [`tests/fixtures/scenarios/query/qr-001.json`](../../tests/fixtures/scenarios/query/qr-001.json).
- Handler:
  [`samantha_server/llm/handlers.py:handle_clinical_query`](../../samantha_server/llm/handlers.py).
- Skill body:
  [`samantha_server/skills/specs/query_routing/SKILL.md`](../../samantha_server/skills/specs/query_routing/SKILL.md).
- Sibling category transcripts:
  [`llm-unknown.md`](llm-unknown.md) (preflight clarification path),
  [`llm-hallucination.md`](llm-hallucination.md) (Stage B grounded-check
  refusal).
- Methodology backfill:
  [`rule-authoring-methodology.md` § 10](../rule-authoring-methodology.md#10-llm-path-methodology-examples-phase-2).
