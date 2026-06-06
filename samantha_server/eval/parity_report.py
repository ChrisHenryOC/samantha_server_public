"""GH-156: parity-replay composer.

Walks one or more `AccuracyReport` instances (multi-model sweep) and
emits the published-shape JSON + markdown rollup that the parity CLI
ships against the upstream POC's `results/model_selection_phase1/
summary.json`.

Failure-mode mapping follows the parity-discovery memo's table
(PR #167, updated GH-194):

- `mismatch_state` → `wrong_state`
- `mismatch_rules` → `wrong_rules`
- `mismatch_flags` → `wrong_flags`
- `dispatch_empty` → `empty_response`
- `error` → not bucketed (caller may add finer attribution)
- `mismatch_query_response` → `mismatch_query_response` (GH-194)
- `mismatch_disposition` → `mismatch_disposition` (GH-194)
- `invalid_json` → `invalid_json` (GH-194)
- `empty_response` → `empty_response` (GH-194; same bucket as dispatch_empty)
- `hallucinated_state` → `hallucinated_state` (GH-194; real counts)
- `hallucinated_rule` → `hallucinated_rule` (GH-194; real counts)
- `hallucinated_flag` → `hallucinated_flag` (GH-194; real counts)
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Final, TypedDict

from samantha_server.scenarios.replay import (
    AccuracyReport,
    ScenarioVerdict,
    StepVerdict,
)

_STATUS_TO_FAILURE_TYPE: Final[dict[str, str]] = {
    # Structural mismatches (original set)
    "mismatch_state": "wrong_state",
    "mismatch_rules": "wrong_rules",
    "mismatch_flags": "wrong_flags",
    "mismatch_routing_path": "wrong_routing_path",
    "dispatch_empty": "empty_response",
    # GH-194: content-gate statuses — samantha vocabulary passthrough.
    # mismatch_query_response: LLM returned different order_ids than expected.
    "mismatch_query_response": "mismatch_query_response",
    # mismatch_disposition: LLM returned wrong review verdict (accept/reject/escalate).
    "mismatch_disposition": "mismatch_disposition",
    # invalid_json: LLM response could not be parsed as structured JSON.
    "invalid_json": "invalid_json",
    # empty_response: LLM returned an empty string (content gate).
    # Note: "dispatch_empty" (structural, engine never produced a rule match)
    # and "empty_response" (content gate, LLM returned empty text) both map
    # to the "empty_response" bucket — they are the same samantha failure mode
    # with different root causes. The field name "empty_response" is the
    # samantha-public FailureType vocabulary entry.
    "empty_response": "empty_response",
    # Hallucination buckets: samantha's FailureType has dedicated entries.
    # GH-194: these now populate from real StepVerdict counts; the old
    # _HALLUCINATION_KEYS zero-fill block is no longer needed.
    "hallucinated_state": "hallucinated_state",
    "hallucinated_rule": "hallucinated_rule",
    "hallucinated_flag": "hallucinated_flag",
}

# Statuses that are intentionally not bucketed in `failure_counts`:
#   `pass`  — a successful step is not a failure.
#   `error` — engine error is "not bucketed; caller may add finer attribution"
#             per the module docstring.
# Any other status seen at runtime indicates a new `StepVerdict.status`
# literal was added to `replay.py` without updating `_STATUS_TO_FAILURE_TYPE`,
# which would silently drop the failure mode from dashboards.
_NON_BUCKETED_STATUSES: Final[frozenset[str]] = frozenset({"pass", "error"})

# Statuses excluded from the rule/flag-match denominator: the engine never
# produced a comparable applied_rules / flags result for these steps, so
# counting them as mismatches against `()` would double-count failures
# already attributed via `failure_counts`.
_EXCLUDED_FROM_MATCH_RATE: Final[frozenset[str]] = frozenset({"error", "dispatch_empty"})

# GH-170: rule-decomposition equivalence classes (composer-only).
# samantha_server's deterministic engine fires SP-007 (priority 0) on the
# recut-clear path where the samantha POC emits SP-001. Both rules produce
# the same semantic outcome; the equivalence is intentional and documented
# in SP-007.yaml. This map teaches the parity *composer* to treat them as
# equivalent for scoring without touching the engine's strict comparison.
# Symmetry is guaranteed by construction — every member of a class maps to
# the same frozenset value. Add a new divergence by appending one frozenset
# to `_RULE_EQUIVALENCE_CLASS_LIST`; the inverted map updates automatically.
_RULE_EQUIVALENCE_CLASS_LIST: Final[tuple[frozenset[str], ...]] = (frozenset({"SP-001", "SP-007"}),)
_RULE_EQUIVALENCE_CLASSES: Final[dict[str, frozenset[str]]] = {
    member: equiv_class for equiv_class in _RULE_EQUIVALENCE_CLASS_LIST for member in equiv_class
}


def _canonicalize_rule_set(rules: tuple[str, ...]) -> frozenset[str]:
    """Map each rule in `rules` to a per-rule canonical representative and
    return the resulting frozenset.

    For each rule:
    - if the rule is in `_RULE_EQUIVALENCE_CLASSES`, replace it with the
      lex-smallest member of its class;
    - otherwise, keep the rule as-is.

    Two single-rule single-class steps that differ only by equivalence
    (e.g. ``("SP-001",)`` vs ``("SP-007",)``) thus produce identical
    frozensets. For multi-rule steps that span multiple classes, the
    output is a frozenset of per-rule representatives — useful for
    set-equality comparison but not a single "canonical name" for the
    rule combination as a whole.

    The lex-smallest choice is an implementation detail; equivalence-class
    authors don't need to think about it. What matters: equal classes
    produce equal output.
    """
    return frozenset(min(_RULE_EQUIVALENCE_CLASSES.get(rule, frozenset({rule}))) for rule in rules)


class StepRoutingCounts(TypedDict):
    """Per-step routing attribution. Keys are exhaustive — every step lands
    in exactly one bucket. The composer pre-seeds all five keys so dashboard
    schemas stay stable across runs.

    Buckets (PR #173 review H1):
      - `deterministic`: evaluate() ran and returned a non-error verdict.
      - `llm`: dispatch_event reached an LLM-invoking handler and returned
        a non-error verdict.
      - `errored_deterministic`: routing was decided as deterministic, then
        evaluate() / resolve_transition() / a flag-handler raised. Real
        engine bug surfaced when the parity CLI's
        `re_raise_on_deterministic_error=False` softens the exception.
      - `errored_llm`: routing was decided as LLM, then dispatch_event
        raised after committing to the LLM path.
      - `errored_pre_routing`: step errored before routing was determined
        (refusal, NotImplementedError at dispatch, pre-routing exception).
        This bucket — and only this bucket — fires the "model never called"
        banner in the Markdown rollup.
    """

    deterministic: int
    llm: int
    errored_deterministic: int
    errored_llm: int
    errored_pre_routing: int


@dataclass(frozen=True)
class ParityModelMetrics:
    """Composer output for one model — mirrors samantha's `ModelMetrics`
    shape (without token columns, per user direction)."""

    model_id: str
    accuracy: float
    accuracy_by_category: dict[str, float]
    rule_accuracy: float
    flag_accuracy: float
    scenario_reliability: float
    latency_mean_ms: float
    latency_p50_ms: float
    latency_p95_ms: float
    failure_counts: dict[str, int] = field(default_factory=dict)
    # GH-172: model_calls makes "did the model actually run?" inspectable.
    # `model_id` is `cfg.LLM_MODEL_NAME` echoed from config and tells you
    # *which* model was configured, not whether any completion call fired.
    # `model_calls` counts non-errored step_verdicts whose
    # `routing_path == "llm"` — i.e. steps where dispatch_event reached an
    # LLM-invoking handler and returned a verdict. A 0 means every LLM-path
    # step either errored (errored_llm / errored_pre_routing) or no LLM-path
    # step ran at all (deterministic-only corpus).
    model_calls: int = 0
    # Full step-routing breakdown so a reader can attribute every step.
    # See StepRoutingCounts docstring for bucket semantics.
    step_routing_counts: StepRoutingCounts = field(
        default_factory=lambda: StepRoutingCounts(
            deterministic=0,
            llm=0,
            errored_deterministic=0,
            errored_llm=0,
            errored_pre_routing=0,
        )
    )


def _step_is_parity_equivalent_mismatch_rules(sv: StepVerdict) -> bool:
    """Return True iff `sv.status == "mismatch_rules"` is rescued by equivalence.

    The literal rule sets must differ (otherwise the engine wouldn't have set
    `mismatch_rules` in the first place — guarding against pathological engine
    output that fires `mismatch_rules` with identical rule ids on both sides,
    which would otherwise inflate `rule_accuracy` while still incrementing
    `wrong_rules`; preserves the `test_failure_counts_mapping_per_memo`
    behaviour).

    When the literals do differ, canonicalize each side and compare. Equal
    canonical sets mean the divergence is purely rule-id attribution within
    a documented equivalence class (e.g. SP-001 ↔ SP-007).
    """
    expected_literals = frozenset(sv.expected.applied_rules)
    predicted_literals = frozenset(sv.predicted.applied_rules)
    if expected_literals == predicted_literals:
        return False
    return _canonicalize_rule_set(sv.expected.applied_rules) == _canonicalize_rule_set(
        sv.predicted.applied_rules
    )


def _scenario_parity_passed(v: ScenarioVerdict) -> bool:
    """Return True iff every step in this scenario is a parity pass.

    A step is a parity pass when:
    - Its status is ``"pass"`` (the engine agreed exactly), or
    - Its status is ``"mismatch_rules"`` AND the literal rule sets differ but
      canonicalize to the same equivalence-class representative (GH-170:
      SP-001 vs SP-007 on the recut-clear path).

    All other statuses (``"mismatch_state"``, ``"mismatch_flags"``,
    ``"dispatch_empty"``, ``"error"``) are not parity passes.

    A scenario with no step_verdicts (empty / malformed) is NOT a parity
    pass — vacuous truth would silently inflate `accuracy` for any scenario
    the loader produced with an empty steps list (PR #174 review H1).
    """
    if not v.step_verdicts:
        return False
    for sv in v.step_verdicts:
        if sv.status == "pass":
            continue
        if sv.status == "mismatch_rules" and _step_is_parity_equivalent_mismatch_rules(sv):
            continue
        return False
    return True


def compose_parity_metrics(model_id: str, report: AccuracyReport) -> ParityModelMetrics:
    """Derive per-model parity metrics from one `AccuracyReport`.

    The report's bucket-aggregate fields (`included_accuracy`, etc.) are
    not enough — samantha publishes per-category accuracy, rule/flag
    accuracy at step granularity, and percentile latency. The composer
    walks `scenario_verdicts[*].step_verdicts[*]` directly.
    """
    verdicts = report.scenario_verdicts
    overall_total = len(verdicts)
    # `accuracy` here uses _scenario_parity_passed (equivalence-class rescue
    # applies) and may exceed `report.included_accuracy` (which uses the
    # engine's strict v.status == "pass") on corpora where SP-001/SP-007 or
    # other equivalence-class divergences appear. The two values measuring
    # different things — `included_accuracy` is samantha_server's own gate;
    # `accuracy` is the parity-comparison number against the samantha POC.
    overall_pass = sum(1 for v in verdicts if _scenario_parity_passed(v))
    accuracy = (overall_pass / overall_total * 100.0) if overall_total else 0.0

    accuracy_by_category = _accuracy_by_category(verdicts)
    rule_accuracy = _step_rate(verdicts, _step_rule_match)
    flag_accuracy = _step_rate(verdicts, _step_flag_match)
    scenario_reliability = _scenario_reliability(verdicts)
    latency_mean_ms, latency_p50_ms, latency_p95_ms = _latency_stats_ms(verdicts)
    failure_counts = _failure_counts(verdicts)
    step_routing_counts = _step_routing_counts(verdicts)
    model_calls = step_routing_counts["llm"]

    return ParityModelMetrics(
        model_id=model_id,
        accuracy=accuracy,
        accuracy_by_category=accuracy_by_category,
        rule_accuracy=rule_accuracy,
        flag_accuracy=flag_accuracy,
        scenario_reliability=scenario_reliability,
        latency_mean_ms=latency_mean_ms,
        latency_p50_ms=latency_p50_ms,
        latency_p95_ms=latency_p95_ms,
        failure_counts=failure_counts,
        model_calls=model_calls,
        step_routing_counts=step_routing_counts,
    )


def write_parity_report(
    reports: dict[str, AccuracyReport],
    output_dir: Path,
    *,
    run_id: str,
) -> Path:
    """Compose per-model metrics and emit `<output_dir>/<run_id>.json`
    plus `<output_dir>/<run_id>.md`. Returns the JSON path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    metrics = [compose_parity_metrics(mid, rep) for mid, rep in reports.items()]

    payload = {
        "run_id": run_id,
        "models": [asdict(m) for m in metrics],
    }
    json_path = output_dir / f"{run_id}.json"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True))

    md_path = output_dir / f"{run_id}.md"
    md_path.write_text(_render_markdown(run_id, metrics))

    return json_path


def _accuracy_by_category(verdicts: tuple[ScenarioVerdict, ...]) -> dict[str, float]:
    by_cat: dict[str, list[ScenarioVerdict]] = {}
    for v in verdicts:
        by_cat.setdefault(v.category, []).append(v)
    return {
        cat: sum(1 for v in vs if _scenario_parity_passed(v)) / len(vs) * 100.0
        for cat, vs in by_cat.items()
    }


def _step_rate(
    verdicts: tuple[ScenarioVerdict, ...],
    predicate: Callable[[StepVerdict], bool | None],
) -> float:
    """Aggregate predicate-match rate over every non-`None` step result."""
    matches = 0
    total = 0
    for v in verdicts:
        for sv in v.step_verdicts:
            result = predicate(sv)
            if result is None:
                continue
            total += 1
            if result:
                matches += 1
    return (matches / total * 100.0) if total else 0.0


def _step_rule_match(sv: StepVerdict) -> bool | None:
    """Per-step rule-set match for `rule_accuracy`.

    `rule_accuracy` measures rule attribution **independently of state
    correctness** — a samantha-POC convention this composer mirrors. A
    `mismatch_state` step still flows through here because its rule
    attribution is a separate signal: the engine can fire the right rule
    and end up in the wrong state (handler bug) or fire the wrong rule
    and end up in the right state (lucky branch). Each layer is reported
    in its own column.

    Steps in `_EXCLUDED_FROM_MATCH_RATE` (`error`, `dispatch_empty`) are
    dropped from the denominator because no comparable rule attribution
    exists (the engine never produced an `applied_rules` to compare).

    GH-170: use equivalence-class canonicalization so SP-001 and SP-007
    (same semantic outcome, different rule ids) score as a match in the
    composer. The engine layer (_verdict_for_step in replay.py) remains
    strict — this canonicalization is composer-only.
    """
    if sv.status in _EXCLUDED_FROM_MATCH_RATE:
        return None
    return _canonicalize_rule_set(sv.expected.applied_rules) == _canonicalize_rule_set(
        sv.predicted.applied_rules
    )


def _step_flag_match(sv: StepVerdict) -> bool | None:
    if sv.status in _EXCLUDED_FROM_MATCH_RATE:
        return None
    return set(sv.expected.flags) == set(sv.predicted.flags)


def _scenario_reliability(verdicts: tuple[ScenarioVerdict, ...]) -> float:
    if not verdicts:
        return 0.0
    error_scenarios = sum(
        1 for v in verdicts if any(sv.status == "error" for sv in v.step_verdicts)
    )
    return (1.0 - error_scenarios / len(verdicts)) * 100.0


def _latency_stats_ms(
    verdicts: tuple[ScenarioVerdict, ...],
) -> tuple[float, float, float]:
    samples = [
        sv.latency_us / 1000.0
        for v in verdicts
        for sv in v.step_verdicts
        if sv.latency_us is not None
    ]
    if not samples:
        return 0.0, 0.0, 0.0
    samples_sorted = sorted(samples)
    n = len(samples_sorted)
    mean = sum(samples_sorted) / n
    p50 = samples_sorted[max(0, _ceil_index(n, 0.50) - 1)]
    p95 = samples_sorted[max(0, _ceil_index(n, 0.95) - 1)]
    return mean, p50, p95


def _step_routing_counts(verdicts: tuple[ScenarioVerdict, ...]) -> StepRoutingCounts:
    """Count steps by (routing_path, status) so a reader can attribute every
    step exactly once. See `StepRoutingCounts` for bucket semantics.

    The routing_path × status cross-product distinguishes:
      - "engine ran fine" (deterministic / llm),
      - "engine ran but errored after routing was decided"
        (errored_deterministic / errored_llm — surfaces the GH-156
        re_raise=False path's deterministic gaps without conflating them
        with orchestration refusals),
      - "errored before routing was decided" (errored_pre_routing — the
        only bucket that fires the "model never called" banner).
    """
    counts = StepRoutingCounts(
        deterministic=0,
        llm=0,
        errored_deterministic=0,
        errored_llm=0,
        errored_pre_routing=0,
    )
    for v in verdicts:
        for sv in v.step_verdicts:
            errored = sv.status == "error"
            if sv.routing_path == "deterministic":
                if errored:
                    counts["errored_deterministic"] += 1
                else:
                    counts["deterministic"] += 1
            elif sv.routing_path == "llm":
                if errored:
                    counts["errored_llm"] += 1
                else:
                    counts["llm"] += 1
            else:
                # routing_path is None — step errored before any routing decision.
                counts["errored_pre_routing"] += 1
    return counts


def _ceil_index(n: int, q: float) -> int:
    """Inclusive nearest-rank ceiling index for percentile q (0..1)."""
    return int(math.ceil(q * n))


def _failure_counts(verdicts: tuple[ScenarioVerdict, ...]) -> dict[str, int]:
    # Pre-seed every expected key so the JSON shape is stable regardless of
    # which failure modes appeared. All failure types (including hallucination
    # buckets) now live in _STATUS_TO_FAILURE_TYPE — no separate zero-fill
    # needed. GH-194 removed _HALLUCINATION_KEYS because real counts populate.
    counts: dict[str, int] = dict.fromkeys(_STATUS_TO_FAILURE_TYPE.values(), 0)
    for v in verdicts:
        for sv in v.step_verdicts:
            mapped = _STATUS_TO_FAILURE_TYPE.get(sv.status)
            if mapped is not None:
                # GH-170: a mismatch_rules step that is rescued by
                # equivalence-class canonicalization does not increment
                # wrong_rules — it is a parity pass for scoring purposes.
                if sv.status == "mismatch_rules" and _step_is_parity_equivalent_mismatch_rules(sv):
                    continue
                counts[mapped] += 1
                continue
            if sv.status in _NON_BUCKETED_STATUSES:
                continue
            # A new StepVerdict.status literal was added to replay.py without
            # being mapped here. Surface it instead of silently dropping the
            # failure mode from dashboards.
            raise ValueError(
                f"Unknown StepVerdict.status {sv.status!r} (scenario "
                f"{sv.scenario_id!r}, step {sv.step_index}). Add a mapping "
                "to _STATUS_TO_FAILURE_TYPE or _NON_BUCKETED_STATUSES."
            )
    return counts


def _escape_md_cell(text: str) -> str:
    """Escape pipe and backtick characters that would corrupt a Markdown table.

    `model_id` flows in from configuration (LLM_MODEL_NAME / LLM_MODEL_PATH);
    a stray `|` would break the column count and a backtick run could open
    an unbalanced inline-code span.
    """
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("`", "\\`")


def _render_markdown(run_id: str, metrics: list[ParityModelMetrics]) -> str:
    lines = [
        f"# Parity replay {run_id}",
        "",
        "Comparison shape mirrors the POC's `results/model_selection_phase1/summary.json`.",
        "Token columns intentionally omitted (per GH-156 discovery memo).",
        "",
    ]

    # GH-172 / PR #173 H1: emit a banner only when model_calls == 0 due to an
    # error condition; suppress the banner for deterministic-only runs (expected).
    for m in metrics:
        if m.model_calls != 0:
            continue
        counts = m.step_routing_counts
        total_steps = (
            counts["deterministic"]
            + counts["llm"]
            + counts["errored_deterministic"]
            + counts["errored_llm"]
            + counts["errored_pre_routing"]
        )
        if counts["errored_pre_routing"] > 0:
            lines.append(
                f"> **Note:** model `{m.model_id}` was configured but never called. "
                f"{counts['errored_pre_routing']} step(s) errored before routing "
                "was decided (pre-routing exception or orchestration refusal). "
                "Where LLM-path categories appear in the table below, their "
                "accuracy / failure_counts reflect those errors rather than any "
                "model output."
            )
            lines.append("")
        elif total_steps == 0:
            lines.append(
                f"> **Note:** model `{m.model_id}` had zero scored steps "
                "(empty corpus or fully-filtered include_scenario_ids). The "
                "table below is the empty-input shape, not a real comparison."
            )
            lines.append("")
        # else: deterministic-only run — `LLM calls = 0` is honest and
        # expected; no banner needed.

    lines.extend(
        [
            "| Model | Accuracy % | Rule % | Flag % | Reliability % | "
            "Latency mean ms | p50 ms | p95 ms | LLM calls |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
    )
    for m in metrics:
        lines.append(
            f"| {_escape_md_cell(m.model_id)} | {m.accuracy:.2f} | "
            f"{m.rule_accuracy:.2f} | {m.flag_accuracy:.2f} | "
            f"{m.scenario_reliability:.2f} | {m.latency_mean_ms:.1f} | "
            f"{m.latency_p50_ms:.1f} | {m.latency_p95_ms:.1f} | "
            f"{m.model_calls} |"
        )
    lines.append("")
    return "\n".join(lines)


__all__ = [
    "ParityModelMetrics",
    "compose_parity_metrics",
    "write_parity_report",
]
