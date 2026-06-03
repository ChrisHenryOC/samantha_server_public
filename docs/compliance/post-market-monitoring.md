# Post-market Monitoring — EU AI Act Article 17

This document explains how the `samantha_server` observability layer
satisfies Article 17 of the EU Artificial Intelligence Act ("post-market
monitoring system" obligations) for high-risk AI systems. It is the
auditor- and operator-facing companion to the implementation chain
shipped in Phase 3 Steps 5, 5.5, 10, 11, 12, and 13.

> **POC framing (G14).** The system described here is **evidentially
> complete but not production-deployed**. The Langfuse instance ships
> in a lab network; receipts are signed and persisted; an auditor can
> reproduce any decision. The production-deployment gap (multi-worker
> session-state, programmatic skill-body PHI scan, RBAC identity
> model) is documented in § 5 below — it is a known gap, not a hidden
> one.

---

## 1. Article 17 obligations

Article 17 of the EU AI Act requires providers of high-risk AI systems
to maintain a post-market monitoring system that:

- **Continuously collects** data on system behaviour after deployment.
- Allows **root-cause analysis** of incidents and quality regressions.
- **Reports to the operator** when monitored signals indicate risk
  drift (degraded accuracy, increased rate of human-review handoff,
  new failure modes).
- Produces **independently verifiable** evidence of individual
  decisions for audit and review.
- **Reproduces** the system's behaviour against a controlled corpus
  on demand, so an auditor can confirm the deployed engine matches
  its documented specification.

The remainder of this doc names the `samantha_server` artifact that
discharges each obligation, in the order an auditor would meet them.

---

## 2. How `samantha_server` satisfies each obligation

| Article 17 obligation | `samantha_server` artifact | Source |
|---|---|---|
| Continuous data collection | Langfuse trace store (one trace per dispatched event) | Step 9 (Langfuse instance) + Step 10 (per-decision trace schema) |
| Per-decision verifiable artifact | Ed25519-signed receipt persisted in `receipts.db` | Step 5 / Step 5.5 |
| Risk-drift surface | Aggregate dashboards (per-skill accuracy, LLM-call rate, latency p99) | Step 11 |
| Operator notification on drift | Drift-alarm webhook (>5% LLM-call rate over rolling hour) | Step 12 |
| Reproducibility of evidence | `replay_to_langfuse()` against the vendored corpus | Step 13 |
| Pre-decision PHI hygiene | `phi_safe` transform on every LLM-payload construction; allowlisted span attributes | G18 (PHI boundary invariant) |

### 2.1 Langfuse trace store (continuous data)

Every event dispatched through `POST /events` emits exactly one
`samantha_server.event` parent span carrying the schema documented in
[`docs/observability/trace-schema.md`](../observability/trace-schema.md).
The schema includes the routing path, the canonical
`event_input_hash`, the receipt id, the latency breakdown, the model
id (LLM path), and the skill-doc hash. No prompt or completion text is
attached — only hashes. The Langfuse instance is the durable
continuous-data system; queries against it are the post-market
analysis surface.

### 2.2 Signed receipts (independently verifiable artifact)

Every `EngineDecision` flows through the orchestrator's
`emit_receipt` chokepoint, which wraps the canonical receipt body in
an Ed25519 signature using the configured signing key. The receipt is
persisted in `receipts.db` and addressable via
`GET /receipts/{receipt_id}` (Step 5.5). The signing key is rotated
via a two-key rolling procedure; receipts carry the signer key id so
an auditor can verify against the published key catalogue without
trusting the runtime.

### 2.3 Aggregate dashboards (risk-drift surface)

The Step 11 dashboards (`infra/langfuse/dashboards/*.yaml`) include:

- Per-skill accuracy panels (one row per LLM-consultable skill).
- LLM-call rate over time (the drift-alarm signal).
- Decision-latency p99 (deterministic vs. LLM, separated to keep the
  deterministic anchor clean).

Every panel filters `samantha.environment == "production"` by default
so replay traces (§ 2.5) do not pollute live-traffic accuracy
estimates. Replay is closed-set; production is open-set. Mixing them
without filtering would inflate apparent accuracy.

### 2.4 Drift-alarm webhook (operator notification)

The drift monitor (`samantha_server.observability.drift`) polls the
LLM-call rate over a rolling hour and fires a webhook when it exceeds
the configured threshold (5% of decisions). The webhook payload and
the per-tick re-fire behavior are documented in
[`docs/observability/drift-alarm.md`](../observability/drift-alarm.md).

There is no operator-side acknowledge / silence / clear path in v0:
the monitor recomputes the rate every
`DRIFT_ALARM_CHECK_INTERVAL_SEC` (60 s by default) and re-fires the
webhook on every interval the threshold remains exceeded. "Clearing"
the alarm means driving the underlying rate back below threshold —
either by intervening on the regression (rolling back a rule change,
re-tuning a skill) or by waiting out a workload burst the engine
correctly handed to the LLM. The drift alarm is the **automated**
half of the operator-notification obligation; the audit handoff in
§ 4 is the **on-demand** half.

### 2.5 Replay harness (reproducibility of evidence)

`replay_to_langfuse(corpus_dir, *, langfuse_base_url,
langfuse_public_key, langfuse_secret_key)` runs the vendored corpus
through the current engine version and exports one OTel trace per
scenario step to the same trace store the dashboards query. Replay
spans carry `samantha.environment="replay"` so dashboards can
discriminate them from live traffic.

### 2.6 PHI hygiene (pre-decision boundary)

LLM-payload construction passes through `phi_safe`
(`samantha_server/llm/phi.py`), which:

- **HMAC-hashes** patient-identifying tokens (`patient_name`,
  `order_id`, the canonical `event_data` blob) using
  `PHI_HASH_SALT` so the hashes are not recomputable without the
  salt.
- **Passes through verbatim** the clinically-relevant order fields
  the LLM needs to reason (`anatomic_site`, `specimen_type`,
  `fixative`, `fixation_time_hours`, `ordered_tests`, `priority`,
  and `age` for `age ≤ 89`). These are documented as
  `_ORDER_PASS_THROUGH` in `phi.py`.
- **Hard-fails (G18)** when `ctx.order.age > 89`: `phi_safe`
  raises `PHIBoundaryError` rather than emit a payload that could
  carry an HIPAA Safe Harbor reidentification risk.

OTel span attributes are gated by an allowlist
(`samantha_server/observability/otel.py::_SAMANTHA_ATTRS`). As of GH-196
the allowlist includes `gen_ai.completion` (stamped unconditionally) and
`gen_ai.prompt`; GH-363 makes prompt stamping default-on (suppressed only
by `SAMANTHA_STAMP_PROMPT=0/false/no/off`). Both the prompt and the
completion are therefore sent to the self-hosted Langfuse instance by
default. This is bounded by topology, not redaction: inference is
local-only and Langfuse runs inside the trust and compliance boundary, so
the text never leaves the host. The signed receipt still carries only
hashes (e.g. `query_text_hash`), so the audit record remains
de-identified even though the observability layer holds full text.

> **Auditor note.** The "PHI surface to the LLM" is the
> `_ORDER_PASS_THROUGH` set above (clinical context the model needs),
> not the empty set. The discipline is *de-identified*, not
> *content-free*: identifiers are hashed, age is bounded, and the
> remaining clinical context is what the LLM consumes.

---

## 3. Operator playbook — drift alarm fired

When the drift-alarm webhook fires (LLM-call rate > 5% over a rolling
hour), the on-call operator works the following sequence. The goal is
to distinguish a **real regression** (the engine is handing more
decisions to the LLM because a deterministic rule is misfiring) from a
**workload shift** (operator volume changed; the rate moved but the
per-skill accuracy didn't).

1. **Open the per-skill accuracy panel** (Step 11 dashboard). If
   accuracy held flat across the alarm window, the LLM-call rate moved
   because workload composition shifted — log the incident as a
   workload-shift event. The webhook will continue re-firing every
   `DRIFT_ALARM_CHECK_INTERVAL_SEC` until the rate falls back under
   threshold; that's the intended behavior in v0 (no
   operator-clearable state). Suppress duplicate downstream
   notifications at the webhook receiver if the noise is operationally
   prohibitive.
2. **If accuracy regressed**, drill into the per-skill panel that
   regressed. The dashboard filters on `samantha.environment ==
   "production"` by default — confirm you are not looking at a replay
   panel.
3. **Pull the cohort of LLM-routed decisions** from Langfuse for the
   regressed skill across the alarm window. Each trace carries
   `event_input_hash`, `applied_rule_id` (often null on the LLM path),
   and the `decision_traces` block.
4. **Correlate with the deterministic engine.** For each
   `event_input_hash`, locate the matching scenario fixture in
   `corpus/`. If the fixture is in the deterministic-bucket
   (`rule_coverage` / `multi_rule` / `accumulated_state`), a real
   regression is likely — a deterministic rule should have fired but
   didn't. If the fixture is LLM-bucket (`query` / `unknown_input` /
   `hallucination`), the LLM hand-off was expected — the regression is
   in the model output, not the rule engine.
5. **Reproduce against the lab Langfuse instance.** Run
   `replay_to_langfuse` over the affected scenario subset and confirm
   the regression reproduces on the lab instance. Replay spans carry
   `samantha.environment="replay"` so the lab traces can be queried
   side-by-side with production without conflation.
6. **File the incident** with the receipt ids of the regressed
   decisions, the trace ids of the matching Langfuse spans, and the
   replay accuracy report. Receipt + trace + replay together are the
   "root-cause analysis" record Article 17 requires.

The drift alarm threshold (5%) is tuned for the POC's expected
workload mix. Calibrating it for production deployment is a Phase-4
follow-up; the current value is documented at
[`docs/observability/drift-alarm.md`](../observability/drift-alarm.md).

---

## 4. Audit handoff (manual procedure)

This is the end-to-end procedure for an auditor verifying an
individual decision. It uses no new tooling — every link in the chain
already exists in Steps 5, 5.5, 10, and 13. The doc's contribution is
naming the procedure end-to-end so an auditor does not need to
reverse-engineer it from per-step plans.

### 4.1 Step 1 — Authenticate

Authenticate with a **`receipts:read`** capability token issued
out-of-band. The token-issuance flow is documented in
[`samantha_server/api/rbac.py`](../../samantha_server/api/rbac.py)
(see also the RBAC payload format at module head). Token issuance is
manual in v0; the production identity model is the gap identified in
§ 5.

### 4.2 Step 2 — Fetch the signed receipt

`GET /receipts/{receipt_id}` returns the **PHI-safe surface** of the
signed receipt body — the canonical fields, the signature, and
`signer_key_id`. The wire shape excludes `decision_traces` and
`primitive_traces` by design
(`samantha_server/api/receipts.py::_to_wire`); those blocks live in
the persisted `receipts.db` and are accessible only to operators with
direct database access (an internal control, not an HTTP-exposed
artifact). The auditor surface is the canonical fields plus the
signature, which is sufficient for both signature verification and
the trace pivot in § 4.3.

The auditor verifies the Ed25519 signature locally against the
signer key id named in the receipt (`signer_key_id`); the published
key catalogue maps key ids to public keys. Local verification is
deliberately auditor-side and Langfuse-independent: the receipt chain
works fully air-gapped.

> **Naming.** The signing-key identifier appears as `signer_key_id`
> on the receipt and as `receipt_signature_key_id` on the trace
> (`docs/observability/trace-schema.md`). They are the **same value
> reached by two paths**, not two separate keys; the trace's longer
> name disambiguates it from any future trace-level signing.

### 4.3 Step 3 — Locate the matching trace

Read `event_input_hash` and the receipt's emission timestamp from the
receipt body. In Langfuse, search traces with the filter
`samantha.event_input_hash = <hex>` and time-bound to the emission
window. Each match is a candidate trace; the auditor picks the one
whose `receipt_id` attribute matches the receipt's id. (Receipts are
1:1 with traces by construction; the time-bound filter is a
tractability optimization, not a correctness requirement.)

**Air-gapped fallback.** When Langfuse is unreachable (deployment
running with `LANGFUSE_ENABLED=false`, network-isolated audit, or an
older receipt whose trace has aged out of the trace store), the same
pivot is available offline via the `receipts.db` index on
`event_input_hash`: the auditor queries the receipts store directly
to confirm which receipts share the input hash. The receipt-side
chain (signature, canonical fields, `event_input_hash`) is the
durable record; the trace is the convenience surface.

The trace surfaces the engine's decision-time view: routing path,
rule ids dispatched and applied, model id (LLM path), skill-doc hash,
and the latency breakdown. Verbatim prompt and completion text are
NOT on the span by design (G8). Server-side, the receipt's
`decision_traces` block (persisted in `receipts.db`, not exposed via
HTTP) carries the LLM-call detail (skill-doc hash, model id, latency
us); the auditor's HTTP-side view is the trace + canonical receipt
fields.

### 4.4 Step 4 — Reproduce deterministically

To confirm the engine reproduces the decision against a controlled
corpus:

1. Locate the scenario fixture whose hash matches `event_input_hash`
   in the vendored corpus (`corpus/`). The construction is bare
   `SHA-256(canonical-JSON(SpecimenContext))` — see
   `samantha_server.engine.decision.compute_event_input_hash`. No
   salt; the auditor can recompute the hash independently from the
   fixture.
2. Run `replay_to_langfuse` against the lab Langfuse instance with
   the matching corpus subset.
3. Replay spans carry `samantha.environment="replay"`; the auditor
   queries the lab instance with that filter and confirms the
   replayed decision matches the production decision (same routing
   path, same applied rule id, same outcome).

> **Deterministic-bucket caveat.** Replay spans for deterministic
> categories (`rule_coverage` / `multi_rule` / `accumulated_state`)
> do **not** carry `samantha.receipt_id` because the deterministic
> replay path bypasses `dispatch_event` / `emit_receipt`.
> The auditor's pivot for these spans is `event_input_hash` only
> (still unique per scenario step). LLM-bucket replay spans carry
> `samantha.receipt_id` as production does.

Reproducibility against the corpus is the closing link in the chain:
the auditor sees the engine emit the same decision, with the same
trace shape, against a fixed input — independent of the production
runtime.

### 4.5 Air-gapped audit modality (cut for v0)

An ephemeral-Langfuse audit modality (spinning up Langfuse via
`docker compose up`/`down` for occasions when no long-running instance
is available) was considered and explicitly cut for POC scope. The
receipt chain in § 4.1 – § 4.2 above is already independent of
Langfuse and works fully air-gapped; the only thing the ephemeral
modality added was the dashboard surface, which a v0 auditor does not
need. **Production deployments running `LANGFUSE_ENABLED=true` against
a long-running instance is the only documented audit path in v0.**

---

## 5. POC ↔ production gap (G14)

The system above is **evidentially complete** for a POC audit: the
signed receipt is present, the trace shape is fixed, the corpus replay
is reproducible. It is **not yet production-deployed**. The known
gap-list is:

| Gap | Tracked at | Disposition |
|---|---|---|
| Multi-worker `_session_state` SQLite swap | [GH-111](https://github.com/ChrisHenryOC/samantha_server/issues/111) (closed, NOT_PLANNED — re-open trigger: multi-worker deployment) | v0 hard-fails on `WEB_CONCURRENCY > 1` (G2). Single-worker FastAPI is the documented v0 deployment shape. |
| Programmatic skill-body PHI scan at discover-time | [GH-110](https://github.com/ChrisHenryOC/samantha_server/issues/110) (closed, NOT_PLANNED — re-open trigger: skill-doc PHI incident surfaced via observability) | The `phi_safe` transform handles per-decision payloads; skill bodies are author-vetted today. The discover-time scan is a defense-in-depth follow-up, not a v0 blocker. |
| RBAC identity model | Not separately tracked; v0 token issuance is manual | Tokens carry capabilities, not identities. Production deployment requires a per-operator identity model so audit logs attribute decisions to operators, not to "the bearer of a `receipts:read` token". |
| Unscoped `GET /receipts` enumeration | Not separately tracked; v0 receipts API has no per-token scoping | Any valid `receipts:read` token can enumerate the **full receipt corpus** via offset/limit pagination. There is no per-token receipt-scope or owner filter. v0 mitigates by treating `receipts:read` token issuance as a privileged manual operation; production deployment requires either a scoped capability (e.g. `receipts:read:owner=<id>`) or an identity-aware filter on the list endpoint. |

The gap list is published, not hidden, on purpose: a production
deployment matrix that depended on un-shipped work would not satisfy
Article 17's documentation obligations. POC framing (evidentially
complete, not production-deployed) is the honest description.

---

## Cross-references

- [`docs/observability/trace-schema.md`](../observability/trace-schema.md)
  — per-decision trace schema (the durable contract).
- [`docs/observability/drift-alarm.md`](../observability/drift-alarm.md)
  — drift-alarm webhook payload, threshold + check-interval
  configuration, and per-tick re-fire behavior.
