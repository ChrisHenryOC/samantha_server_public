# Per-decision trace schema

The canonical shape that every dispatched event produces in Langfuse.
The schema is the durable contract behind:

- the dashboards (Step 11),
- the drift alarm (Step 12),
- the eval-harness replay tags (Step 13), and
- the post-market-monitoring story (Step 14).

The serializer lives at
[`samantha_server/observability/trace.py`](../../samantha_server/observability/trace.py)
(`to_trace_dict`). Tests in
[`tests/observability/test_trace_schema.py`](../../tests/observability/test_trace_schema.py)
pin the shape and the PHI-absence invariant.

## Shape

```yaml
trace:
  trace_id: <otel_trace_id>            # 32-char lowercase hex (128-bit OTel id); validated at the boundary
  session_id: <str>                    # caller-supplied; RBAC-bound, not PHI
  routing_path: deterministic | llm    # which layer produced the decision
  agreement: deterministic_only | llm_only | both_agree | both_disagree | unknown
  event_input_hash: <hex>               # SHA-256 of canonical-JSON input ctx
  applied_rule_id: <str|null>           # null on dispatch_empty / refusals / queries
  dispatched_rule_ids: [<str>, ...]
  also_matched: [<str>, ...]
  next_state: <str>
  outcome: <str>                        # outcome enum incl. refused_* variants
  latency_us:
    engine: <int|null>                  # deterministic engine wall time; null on LLM path
    llm_call: <int|null>                # LLM call wall time; null on deterministic path
    queue_wait: <int>                   # time between enqueue and dequeue
  receipt_id: <str>
  receipt_signature_key_id: <str>       # which signing key was used
  model_id: <str|null>                  # null on deterministic path
  tool_calls: [{ name, latency_us, succeeded }]
  decision_traces: [<discriminated-union per Phase 2 — kind: query/clarification/llm_review/refusal/canonicalization_warning>]
  skill_doc_hash: <hex|null>            # which SKILL.md body was loaded
```

## Field semantics

### `agreement`

Records whether the deterministic engine and the LLM path produced
the same decision when the routing crossed both. v0 emits **only**
`deterministic_only` or `llm_only`:

- `deterministic_only` — the deterministic engine produced the
  decision; no LLM was consulted.
- `llm_only` — the LLM path produced the decision (clinical query,
  specimen review, clarification, or refusal).
- `both_agree` / `both_disagree` — **reserved** for a future
  dual-route flow; **never emitted by the v0 serializer**.
- `unknown` — a fail-soft sentinel emitted when the serializer
  encounters an unrecognised `routing_path` value (e.g., a future
  enum addition not yet wired into the serializer). The receipt is
  the contract; observability fails soft so a Phase-4 enum addition
  doesn't lose traces while keeping the receipt. The serializer
  also emits a WARN log when this path fires.

### Why `both_*` is reserved

The three-way disposition is a deterministic→LLM *handoff*: the
engine routes ambiguous specimen types into the
`PENDING_LLM_REVIEW` state, where the LLM handler then produces the
decision. Both layers are involved sequentially, but only one of them
*decides* — the engine routes, the LLM dispositions. There is no
event where both layers independently decide on the same input.

Until a true dual-route flow lands, every event's `agreement` is
exactly one of `deterministic_only` or `llm_only`. The dashboard
panels (Step 11) include both `both_*` bins in their schema for
forward compatibility but treat them as 0 throughout v0; the
regression test
`tests/observability/test_trace_schema.py::test_agreement_only_emits_deterministic_or_llm_only`
asserts the serializer never emits the reserved values against any
input.

### `tool_calls`

Each tool invocation is recorded as
`{name: <str>, latency_us: <int>, succeeded: <bool>}`. No payloads.
The `lookup_skill` body is **not** included; only the `skill_doc_hash`
field above carries the SHA-256 of the loaded skill.

The v0 LLM path does not yet thread tool invocations through
`EngineDecision`, so the field is currently always `[]`. Phase 4's
tool-catalog landing populates it.

### `skill_doc_hash`

SHA-256 of the SKILL.md body that was loaded for this decision. Lets
the dashboard correlate accuracy regressions against skill-body edits
("did accuracy drop on the day skill X was edited?").

`null` on the deterministic path (no skill is loaded).

### `gen_ai.system` is operator-configured

The OTel `gen_ai.system` attribute (set in Step 8 from
`config.LLM_PROVIDER`) carries whichever transport is active in the
operator's `.env` — `"omlx"` (the post-Amendment-A1 default,
2026-05-09) or `"mlx"` (the pre-amendment in-process MLX path), or a
future provider name. OTel semantic conventions define a controlled
vocabulary for this field that does not list either, so a strict
semconv validator emits a warning.

We preserve the literal value deliberately — the spec admits
free-form values for unlisted systems, and "fixing" it to `"custom"`
would break the dashboard's per-provider filter. A reviewer hunting
a dashboard glitch needs to know the value comes from the operator's
configured provider, not from a hardcoded literal.

See [`infra/langfuse/dashboards/README.md`](../../infra/langfuse/dashboards/README.md)
for the Step 11 panel catalog and the operator setup that consumes
this section.

## Span attributes (OTel)

The fields above land as Langfuse trace-level attributes via the
serializer at `samantha_server/observability/trace.py`. Separately,
the parent `samantha_server.event` span carries OTel-level attributes
that Langfuse exposes as filterable dimensions in its UI. The
allowlist lives in `samantha_server/observability/otel.py`; these
names are also valid targets for dashboard filters.

| Span attribute | Source / status |
|---|---|
| `samantha.event_input_hash` | parent span; from `EngineDecision.event_input_hash` |
| `samantha.session_id` | parent span; from request body |
| `samantha.priority` | parent span; `STAT` / `ROUTINE` / `BACKGROUND` |
| `samantha.applied_rule_id` | parent span; nullable |
| `samantha.latency_us` | parent span |
| `samantha.receipt_id` | parent span |
| `gen_ai.system` | LLM child span; operator-configured (cfg.LLM_PROVIDER) |
| `gen_ai.request.model` / `gen_ai.response.model` | LLM child span |
| `gen_ai.usage.input_tokens` / `gen_ai.usage.output_tokens` | LLM child span |
| `gen_ai.request.temperature` / `gen_ai.request.max_tokens` | LLM child span |
| `gen_ai.response.finish_reasons` | LLM child span; omitted when absent |
| `langfuse.trace.metadata.environment` | parent span; `production` / `replay` / `parity`. Canonical dashboard-filter surface for environment discrimination. (This was moved off `samantha.environment` to the consolidated `langfuse.trace.metadata.*` surface.) |
| `langfuse.trace.metadata.routing_path` | parent span; `deterministic` / `llm` |
| `langfuse.trace.metadata.next_state` | parent span |
| `langfuse.trace.metadata.outcome` | parent span |
| `langfuse.trace.metadata.scenario_id` | parent span (replay + sweep surfaces); from scenario or session id |
| `langfuse.trace.metadata.scenario_category` | parent span (replay + sweep surfaces); enum |
| `langfuse.trace.metadata.sweep_run_id` | parent span (sweep surface); UUID |

## PHI safety

Every value the serializer emits is **one of**:

- a hex digest (`event_input_hash`, `skill_doc_hash`, `query_text_hash`,
  `response_text_hash`),
- an enum from a controlled vocabulary (`outcome`, `routing_path`,
  `agreement`, `refusal_reason`, `disposition`),
- a numeric latency (`latency_us.*`), or
- a structural identifier (`applied_rule_id`, `receipt_id`,
  `receipt_signature_key_id`, `session_id`, `model_id`, `trace_id`).

Patient-bearing fields — name, DOB, age, order_id, anatomic_site
free-text — never appear. The PHI-absence regression test in
`tests/observability/test_trace_schema.py` seeds known PHI sentinels
into a `SpecimenContext` and asserts none surface in the serialized
trace.

## Nullability summary

| Field | When `null` |
|---|---|
| `applied_rule_id` | dispatch_empty; queries; clarifications; refusals |
| `latency_us.engine` | LLM path (`decision.latency_us` carries the LLM-call wall time, not engine wall time) |
| `latency_us.llm_call` | deterministic path |
| `model_id` | deterministic path; refusals before any LLM call; clarifications where the LLM was unavailable (`ClarificationTrace.model_id` defaulted to empty) |
| `skill_doc_hash` | deterministic path; clarifications (`ClarificationTrace` doesn't carry the field); refusals before skill load |

Every other field is required.

### Consistency check

When `routing_path == "llm"` and `outcome` is non-refusal (i.e., not
prefixed `refused_`) but `model_id` is `null`, the serializer emits a
`logging.WARNING` so the contract gap is visible. The trace itself is
still produced — observability fails soft.

## Cross-links

- OTel parent-span attrs (Step 8): [`samantha_server/observability/otel.py`](../../samantha_server/observability/otel.py)
- Receipt contract: [`samantha_server/receipts/signing.py`](../../samantha_server/receipts/signing.py)
- Receipt-to-trace join key: `receipt_id` (this schema) ↔ `receipt_id`
  (DB primary key in `samantha_server/receipts/schema.sql`).
- (Forward reference) Step 11 `infra/langfuse/dashboards/README.md` —
  not yet shipped; will cross-reference the `gen_ai.system` rationale
  when it lands.

## When to update this document

- A new `outcome` value is added → list it in the
  `outcome` enum if downstream dashboards filter on it.
- A new `decision_trace` kind is added → enumerate in the
  `decision_traces[].kind` enum.
- A field is added, removed, or renamed → update both the YAML shape
  and the field-semantics section, and update
  `tests/observability/test_trace_schema.py`.
- The `agreement` enum gains real `both_*` semantics (a true
  dual-route flow lands) → update this doc, the serializer, and the
  regression test in the same PR.
