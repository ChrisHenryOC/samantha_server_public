# Deployment — `samantha_server` Phase 3 (POC)

This is the operator-facing companion to
`docs/plans/phase-3-implementation.md`.
Every step in the implementation plan that names a "deployment doc
names …" or "release-checklist item" reference contributes one
bullet here. The plan is the *why*; this doc is the *what to do*.

> **POC scope.** This doc covers the lab-network single-worker
> deployment posture documented in the Phase 3 plan. Production
> hardening (multi-worker, OIDC, TLS termination at the
> orchestrator, networked receipt store) is out of scope. The known
> production-deployment gaps are listed in
> [`docs/compliance/post-market-monitoring.md` § 5](../compliance/post-market-monitoring.md).

---

## 1. Pre-deploy checklist

Run before bringing the orchestrator up against any real traffic.

- [ ] **Single-worker invariant** — confirm `WEB_CONCURRENCY=1` in
      the deployment env. The orchestrator hard-fails at startup
      when `> 1` (G2). Any opt-in surface for multi-worker is a
      future change tied to the SQLite swap, not a deploy-time
      config knob.
- [ ] **`.env` populated from
      [`.env.example`](../../.env.example).** All required env vars
      have defaults or fail-loud guards in
      [`samantha_server/config.py`](../../samantha_server/config.py);
      any `MisconfiguredEnvironmentError` at startup names the
      missing/invalid variable.
- [ ] **Receipt-signing key.** `RECEIPT_SIGNING_KEY` is a 64-char hex
      seed produced by
      `python -m samantha_server.receipts.signing --gen-key`. Do
      **not** redirect the output with `>` against an existing key
      file. The rotation playbook lives in
      [`CLAUDE.md`](../../CLAUDE.md) § "Receipt-signing key rotation".
- [ ] **RBAC HMAC key.** `RBAC_HMAC_KEY` is a 64-char hex secret;
      issue capability tokens via
      `python -m samantha_server.api.rbac --issue <capability>`
      (capabilities: `events:submit`, `receipts:read`,
      `health:read`). Tokens are 24h by default
      (`RBAC_TOKEN_TTL_SEC`); rotation kills *every* outstanding
      token (no per-token revocation in v0).
- [ ] **Loopback exposure only.** v0 runs on the lab network with
      no TLS at the orchestrator and no CORS middleware. Production
      deployment requires a reverse proxy (nginx / Caddy / k8s
      ingress) terminating TLS in front of the orchestrator.
- [ ] **Langfuse instance up** (when `LANGFUSE_ENABLED=true`).
      `docker compose up` in the lab Langfuse directory; verify the
      OTLP endpoint resolves before starting the orchestrator. The
      Langfuse Docker bind address must remain `127.0.0.1:<port>`
      per Step 9 G10 — production deployments rebinding to a
      lab-internal address must update **both** the Docker bind and
      the orchestrator's `LANGFUSE_BASE_URL` together.
- [ ] **Loopback smoke + Langfuse end-to-end test.** Run
      `pytest -m langfuse` after `docker compose up` and **before**
      the orchestrator goes live. The marked test exercises the
      full trace-emission round-trip — without this, the loopback
      contract is documented but not actually defended in any
      automated form. Skip when `LANGFUSE_ENABLED=false`: the
      tagged test requires a reachable Langfuse instance, and a
      `LANGFUSE_ENABLED=false` deployment has no observability
      surface to defend.

---

## 2. Post-deploy checks

Run immediately after the orchestrator starts serving.

- [ ] **`/healthz`** returns 200. Process liveness only; no
      dependency check.
- [ ] **`/readyz`** returns 200. The 200 payload represents the
      load-bearing readiness contract (engine, signing keys,
      SQLite connection cache, queue, RBAC) — these surface only
      via the **503** path when any one fails (`failed_components`
      array). The 200 body carries a `components` block reporting
      the **non-blocking** dependencies (`langfuse`,
      `drift_webhook`); these can be degraded without gating
      traffic.
- [ ] **`/readyz` non-blocking component fields.** When Langfuse
      is enabled the post-deploy probe should show
      `components.langfuse.reachable: true`. The other Langfuse
      fields are `components.langfuse.enabled` (a config mirror)
      and `components.langfuse.last_probe_age_sec` (cached probe
      freshness). The drift webhook's field set is
      `components.drift_webhook.configured` (env-var presence
      mirror) and `components.drift_webhook.last_failure_age_sec`
      (null until the first webhook failure). A degraded
      `langfuse.reachable` means the trace store is unreachable
      from the orchestrator; receipts continue (they are not gated
      on Langfuse), but the compliance trace surface is not
      populated.
- [ ] **`/version`** returns the deployed commit SHA. Cross-check
      against the deploy artifact's git ref.

---

## 3. Operational runbooks

### 3.1 STAT-503 incident response

A 503 to a STAT-priority request is **not** a normal back-pressure
signal — it is an **incident**. STAT carries clinical-safety
weight; rejecting one means the queue is fully saturated even of
its preempt slot.

**Operator rule:** any non-zero count of STAT 503s in a rolling
window pages the on-call operator. The v0 surface is `/readyz`
(degraded JSON `counters` block) plus the access-log JSON; the
production-hardening seam is a per-priority 503 counter exposed via
a metrics endpoint.

### 3.2 Drift-alarm webhook

The drift monitor fires
`{window_sec, rate, threshold, fired_at_unix}` to the configured
webhook target whenever the rolling-hour LLM-call rate is **strictly
greater than** `DRIFT_ALARM_THRESHOLD` (default 0.05; the comparison
is `>`, not `>=`, so a rate exactly at threshold does not fire). The
webhook re-fires every `DRIFT_ALARM_CHECK_INTERVAL_SEC` (default 60 s)
while threshold is exceeded — there is no acknowledge / silence /
clear path in v0.

Operator response procedure:
[`docs/compliance/post-market-monitoring.md` § 3](../compliance/post-market-monitoring.md).
Webhook payload contract:
[`docs/observability/drift-alarm.md`](../observability/drift-alarm.md).

### 3.3 Receipt-signing key rotation

Two-key rolling rotation per [`CLAUDE.md`](../../CLAUDE.md)
§ "Receipt-signing key rotation". Summary:

1. Generate new key:
   `python -m samantha_server.receipts.signing --gen-key`.
2. Move current `RECEIPT_SIGNING_KEY` →
   `RECEIPT_SIGNING_KEY_PREVIOUS`; set new key as
   `RECEIPT_SIGNING_KEY`.
3. Restart engine. **Do not bump `RECEIPT_SIGNING_KEY_ID` yet.**
4. After clean restart, increment `RECEIPT_SIGNING_KEY_ID` and
   restart again so new receipts carry the new key id.
5. After two rotations, receipts under the oldest key fail
   verification with `KEY_EXPIRED` (by design).

### 3.4 Graceful shutdown

The orchestrator's lifespan task drains the priority queue on
SIGTERM and resolves in-flight `POST /events` futures with
`ShutdownError`. The drain is bounded by `SHUTDOWN_DRAIN_TIMEOUT_SEC`
(default 30 s).

**Sizing rule** (per the canonical statement in
`phase-3-implementation.md` § 4.2): the deployment platform's
SIGTERM-to-SIGKILL window must be **≥ `SHUTDOWN_DRAIN_TIMEOUT_SEC`
+ 5 s margin**, i.e. the **platform window is larger than the
drain**. On k8s set `terminationGracePeriodSeconds` ≥ 35; on
systemd set `TimeoutStopSec` ≥ 35. A platform window smaller than
the drain risks abrupt termination of mid-dispatch decisions
before the orchestrator finishes draining; the +5 s margin covers
the lifespan's cleanup path after drain (closing SQLite write
connections, awaiting the drift task) and any platform-side signal
delivery latency.

### 3.5 Dual-stack rebind override

Hosts that resolve `localhost` → `::1` (rather than `127.0.0.1`)
are not a v0 deployment target. If they become one, override the
Langfuse client URL to use the resolved IPv6 literal explicitly;
do **not** rely on `localhost` as the bind/connect host. The
`127.0.0.1`-only invariant on the **Langfuse Docker bind** (Step 9
G10) is a separate concern (server-side bind interface, not
client-side connect URL) and is unaffected.

---

## 4. Audit handoff

The end-to-end audit handoff procedure is documented in
[`docs/compliance/post-market-monitoring.md` § 4](../compliance/post-market-monitoring.md):
authenticate with a `receipts:read` token →
`GET /receipts/{receipt_id}` → verify Ed25519 signature locally →
search Langfuse by `samantha.event_input_hash` → reproduce via
`replay_to_langfuse` with `samantha.environment="replay"`.

---

## 5. Cross-references

- `docs/plans/phase-3-implementation.md`
  — the *why* behind every checklist item.
- `docs/plans/phase-3-closeout.md`
  — final state at Phase 3 close (gate measurements, deltas,
  process gaps).
- [`docs/compliance/post-market-monitoring.md`](../compliance/post-market-monitoring.md)
  — EU AI Act Article 17 audit-handoff and operator playbook.
- [`docs/observability/drift-alarm.md`](../observability/drift-alarm.md)
  — drift-alarm webhook payload + threshold semantics.
- [`docs/observability/replay-traces.md`](../observability/replay-traces.md)
  — `replay_to_langfuse` workflow.
- [`CLAUDE.md`](../../CLAUDE.md) § "Receipt-signing key rotation"
  — rotation playbook (with key-id-bump ordering rule).
