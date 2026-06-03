# Drift-alarm webhook

`samantha_server`'s drift alarm fires a webhook when the LLM-call rate
exceeds a configured threshold over a rolling window. The alarm is
the first-line signal for deterministic-path regressions: if the
share of decisions taking the LLM fallback grows, something in the
deterministic path has stopped covering cases it used to, and the
model is silently absorbing the slack.

This document describes the rolling buffer, the webhook contract, the
default threshold and its rationale (G12 empirical-tuning), and the
operational knobs.

## Where it lives

- **Module**: [`samantha_server/observability/drift.py`](../../samantha_server/observability/drift.py)
- **Tests**: [`tests/observability/test_drift_monitor.py`](../../tests/observability/test_drift_monitor.py)
  and [`tests/api/test_drift_integration.py`](../../tests/api/test_drift_integration.py)
- **Lifecycle**: started by `samantha_server/api/app.py::lifespan`,
  cancelled in `AppState.aclose()`. The recording call is in
  `_consume` after a successful dispatch.

## Decoupled from Langfuse

The rolling buffer is owned by the orchestrator, not the Langfuse
trace store. This keeps the alarm available in:

- CI runs (no Langfuse instance running).
- Air-gapped lab deployments (no outbound trace export).
- Langfuse outages (the trace store may be down without the engine
  noticing — drift signal must keep working).

The EU AI Act post-market-monitoring story is therefore
self-contained: the alarm holds even when external observability
is unavailable.

## Rolling buffer

A single `collections.deque[tuple[int, str]]` holds
`(monotonic_ns, routing_path)` for every dispatched decision. Two
bounds apply together:

1. **Time-bound eviction** at compute time. Entries with
   `age > DRIFT_ALARM_WINDOW_SEC` are popped before each rate
   computation. The strict `>` predicate pins the boundary
   behaviour: an entry whose age is exactly `WINDOW_SEC` survives;
   an entry older by 1 ns is evicted.
2. **Defensive count cap** via `deque(maxlen=DRIFT_DEQUE_MAXLEN)`.
   At sustained high QPS, a slow `check_interval_sec` could let
   the deque grow until it stresses memory. The cap drops the
   oldest entry on overflow so the resident count stays bounded.

Per-priority bucketing was considered and dropped — the alarm
fires on the combined rate. Per-priority breakdowns belong on
the dashboard, not in the alarm-firing path.

## Webhook contract

When `compute_rate(...) > DRIFT_ALARM_THRESHOLD`, the monitor
POSTs JSON to `DRIFT_ALARM_WEBHOOK_URL` with exactly four keys:

```json
{
  "window_sec": 3600,
  "rate": 0.082,
  "threshold": 0.05,
  "fired_at_unix": 1730000000.0
}
```

- `window_sec` (int): the rolling window the rate was computed over.
- `rate` (float): `count(routing_path == "llm") / count(*)` over the
  window.
- `threshold` (float): the configured threshold, included so the
  receiver can correlate the firing with its expected setting.
- `fired_at_unix` (float): wall-clock unix time at firing
  (`time.time()`). The buffer uses `time.monotonic_ns()` for
  bookkeeping; this field is for human-readable correlation only.

No other keys are included. No PHI, no session IDs, no per-event-type
breakdown. The dashboard owns breakdowns; the alarm is a
notification.

## No retry on failure (G21)

Any failure path increments `counters.drift_webhook_failures` and
returns:

- Connection errors (`httpx.ConnectError`, `OSError`).
- Read/connect timeouts (`httpx.TimeoutException`, both bounded by
  `_HTTPX_TIMEOUT_SEC = 5.0`).
- TLS errors (`httpx.RequestError` subclasses).
- Non-2xx responses.

The next interval's rate computation will fire again if drift
persists. A misconfigured target should not hold the drift task in
retry loops; the failure counter on `/readyz` is the operator's cue
to investigate.

## Default threshold (G12 empirical-tuning)

`DRIFT_ALARM_THRESHOLD` defaults to `0.05` (5%). Rationale:

- The deterministic path is the baseline; LLM consultation is the
  exception. A long-running steady-state north of 5% LLM-call rate
  means either (a) a new event class is bypassing rule coverage, or
  (b) an existing rule has regressed and is no longer matching its
  cases.
- The threshold is intentionally below the noise floor of any
  individual operator's expected variation. G12 specifies that
  alarm thresholds should be chosen to fire on regressions, not
  on routine variance — operators raise it to taste once they
  have a baseline.
- The `fail_under = 100` coverage gate on `samantha_server/primitives/`
  is the structural guarantee that primitive logic is covered;
  drift-alarm is the runtime guarantee that the primitives' coverage
  is still meaningful in production.

Operators tuning the threshold for their deployment should:

1. Run with the default for a one-week observation window.
2. Read the empirical 95th-percentile rate from the dashboard
   `LLM-call rate` panel.
3. Set the threshold to `max(observed_p95 + 0.02, 0.05)`. The
   `+0.02` buffer is a 2-percentage-point cushion above the
   measured 95th-percentile so routine variance never trips the
   alarm — empirically, the rate's coefficient of variation in a
   stable deployment sits well below this. The lower-bound floor
   of 5% catches structural regressions even when the empirical
   baseline is suspiciously low. Re-tune at the next phase
   closeout (G12).

## Operational knobs

All knobs live in `samantha_server/config.py` and are read at
startup:

| Variable | Default | Meaning |
|----------|---------|---------|
| `DRIFT_ALARM_WEBHOOK_URL` | `None` | Target URL. `None` disables firing (buffer still records). |
| `DRIFT_ALARM_THRESHOLD` | `0.05` | LLM-call rate above which the webhook fires. |
| `DRIFT_ALARM_WINDOW_SEC` | `3600` | Rolling window for rate computation. |
| `DRIFT_ALARM_CHECK_INTERVAL_SEC` | `60` | How often the lifespan task wakes to compute + maybe fire. |
| `DRIFT_DEQUE_MAXLEN` | `100000` | Defensive count cap on the rolling buffer. |

## Disabling

Set `DRIFT_ALARM_WEBHOOK_URL=` (empty) or unset it. The buffer
still records on every dispatch and time-eviction still runs (so
`/readyz` could surface the current rate via degraded JSON in the
future) but no httpx client ever opens.
