# Langfuse dashboards (Phase 3 Step 11)

Three v0 panels driving the post-market monitoring story:

| Panel | YAML | What it shows |
|---|---|---|
| Per-skill accuracy | `per-skill-accuracy.yaml` | Agreement rate per skill (`skill_doc_hash`) over rolling 24h / 7d / 30d windows. Headline production accuracy signal. |
| LLM-call rate | `llm-call-rate.yaml` | Fraction of decisions routed through the LLM path over rolling 1h / 24h / 7d windows. Project target ≤5%. |
| Latency histograms | `latency-histograms.yaml` | p50 / p95 / p99 of `latency_us.engine` (deterministic) and `latency_us.llm_call` (LLM) over 24h / 7d. |

## Why these are YAMLs, not Langfuse JSON exports

The plan's decision G11 calls these "committed JSON" — we ship them
as **YAML** instead. Reason: Langfuse v3 (the operator's running
build is v3.172.0) **has no public API for creating, importing, or
exporting dashboards.** Per Langfuse maintainer in
[discussion #8819](https://github.com/orgs/langfuse/discussions/8819):
*"the necessary API calls and schemata are quite complicated which
makes it hard to have them accessible via an API."*

So we have no canonical Langfuse JSON shape to emit. The panels are
committed here in a tool-agnostic YAML form ("dashboards as code"
per Plan G11 — file format is the implementation detail; PR-review
of dashboard changes is the load-bearing requirement). The CI gate
in `tests/observability/test_dashboard_schema.py` asserts every
referenced field name exists in
[`docs/observability/trace-schema.md`](../../../docs/observability/trace-schema.md)
— schema drift fails CI without having to stand up Langfuse.

When Langfuse ships a public dashboard import API, this directory
will gain a generator that converts the YAMLs into Langfuse's native
import format. Until then, the operator manually replicates each
panel in the UI on first deployment.

## First-time operator setup

For each YAML in this directory, replicate the panel in the Langfuse
v3 UI:

1. Open the operator's Langfuse instance (e.g., `http://localhost:3000`).
2. Navigate to **Dashboards** → **New Dashboard**.
3. Name the dashboard per the YAML's `name` field
   (e.g., `per_skill_accuracy`).
4. Add the panel-level filter:
   `langfuse.trace.metadata.environment` = `production` (G_environment_filter — keeps
   replay data out of production accuracy panels).
5. Configure rolling windows (the YAML's `windows` list).
6. Configure `group_by` and `metrics` per the YAML.
7. Save.

When updating a panel:

1. Edit the YAML in this directory; PR-review the diff.
2. After merge, an operator manually updates the corresponding panel
   in the UI to match.

The CI schema test catches the failure mode where a YAML references
a field that no longer exists in the trace shape. It does NOT catch
operator drift between a YAML and the live UI panel — that is on the
operator.

**Drift discipline (no automation gate yet):**

- After every PR that edits a YAML in this directory, the operator
  updates the corresponding UI panel before merging into the
  production deploy.
- Quarterly (or before each release-checklist run), spot-check one
  panel against its YAML to confirm filter / window / metric drift
  hasn't crept in. There is no automation enforcing this — when
  Langfuse ships a public dashboard API, this section gets a CI
  gate; until then, the discipline is procedural.

## Replay vs production

Per Step 13 (not yet shipped) the orchestrator will tag every
trace with `langfuse.trace.metadata.environment ∈ {"production", "replay"}`. Each
v0 panel's filter pins `production`. An auditor or demo operator who
wants to visualise replay data instead duplicates the dashboard in
Langfuse, swaps the filter to `replay`, and saves under a separate
name — no need to touch the production-default YAMLs.

### Preemptive filter caveat

Until Step 13 ships, the `langfuse.trace.metadata.environment` attribute is **unset
on every span**. The panel filter is preemptive — wired into the
YAMLs now so the Step 13 change is a single-attribute change rather than
an N-dashboards change.

**Caveat (operator-visible):** Langfuse v3.172.0's exact
equality-filter semantics for absent attributes are not documented.
There are two possible behaviours:

- **Strict equality** — filters on absent attributes match nothing.
  Every panel shows 0 events until Step 13 lands. **Workaround:** on
  first import, set the filter to "is set OR equals production"
  (or temporarily disable the filter) and remove the workaround when
  Step 13 ships.
- **Pass-through on missing** — filters on absent attributes match
  every event. Every panel shows production data correctly today; no
  workaround needed.

The release-checklist gate (`pytest -m langfuse`) does
not cover dashboard rendering. Verify which behaviour your Langfuse
build exhibits by importing one panel after a real corpus replay
and checking whether events appear.

## Cross-links

- Trace schema: [`docs/observability/trace-schema.md`](../../../docs/observability/trace-schema.md)
- Schema test: [`tests/observability/test_dashboard_schema.py`](../../../tests/observability/test_dashboard_schema.py)
- Step 13 (replay tagging): not yet shipped
