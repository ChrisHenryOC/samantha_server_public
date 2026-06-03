"""GH-156: parity_report composer.

Walks one or more `AccuracyReport` instances (multi-model sweep) and
emits the published-shape JSON + markdown rollup that the parity CLI
ships against `~/source/samantha/results/model_selection_phase1/
summary.json`.

Per the discovery memo (PR #167):
- Latency mean/p50/p95: derived from `step_verdicts[*].latency_us`.
- Per-category accuracy split: group by `ScenarioVerdict.category`.
- Rule/flag accuracy: per-step rule-match / flag-set match rate.
- Reliability: `1 - error_count / overall_total`.
- Failure counts: map `StepVerdict.status` → samantha's `FailureType`
  enum (`mismatch_state` → `wrong_state`, `mismatch_rules` →
  `wrong_rules`, `mismatch_flags` → `wrong_flags`, `dispatch_empty`
  → `empty_response`; hallucination buckets read zero with a
  "not separately tracked" note).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from samantha_server.scenarios.replay import (
    AccuracyReport,
    ExpectedOutput,
    PredictedOutput,
    ScenarioVerdict,
    StepVerdict,
)


def _step(
    scenario_id: str,
    step_index: int,
    status: str,
    *,
    next_state: str = "ACCEPTED",
    expected_rules: tuple[str, ...] = ("ACC-008",),
    predicted_rules: tuple[str, ...] = ("ACC-008",),
    expected_flags: tuple[str, ...] = (),
    predicted_flags: tuple[str, ...] = (),
    latency_us: int | None = 1_000_000,
    routing_path: str | None = "deterministic",
) -> StepVerdict:
    return StepVerdict(
        scenario_id=scenario_id,
        step_index=step_index,
        status=status,  # type: ignore[arg-type]
        expected=ExpectedOutput(
            next_state=next_state, applied_rules=expected_rules, flags=expected_flags
        ),
        predicted=PredictedOutput(
            next_state=next_state, applied_rules=predicted_rules, flags=predicted_flags
        ),
        latency_us=latency_us,
        routing_path=routing_path,  # type: ignore[arg-type]
    )


def _verdict(
    scenario_id: str, category: str, status: str, step_verdicts: tuple[StepVerdict, ...]
) -> ScenarioVerdict:
    return ScenarioVerdict(
        scenario_id=scenario_id,
        category=category,
        status=status,  # type: ignore[arg-type]
        step_verdicts=step_verdicts,
    )


def _report(verdicts: tuple[ScenarioVerdict, ...]) -> AccuracyReport:
    """Build a minimal AccuracyReport with derived totals."""
    overall_total = len(verdicts)
    overall_pass = sum(1 for v in verdicts if v.status == "pass")
    return AccuracyReport(
        included_accuracy=overall_pass / overall_total if overall_total else 0.0,
        overall_accuracy=overall_pass / overall_total if overall_total else 0.0,
        included_total=overall_total,
        overall_total=overall_total,
        included_pass=overall_pass,
        overall_pass=overall_pass,
        scenario_verdicts=verdicts,
        p99_latency_us=None,
        p99_latency_us_llm=None,
        deterministic_latency_step_count=sum(len(v.step_verdicts) for v in verdicts),
        llm_latency_step_count=0,
    )


# ---------------------------------------------------------------------------
# compose_parity_metrics — single-model derivations
# ---------------------------------------------------------------------------


def test_accuracy_overall_percentage() -> None:
    """`accuracy` is the overall pass rate as a percentage (0–100)."""
    from samantha_server.eval.parity_report import compose_parity_metrics

    verdicts = (
        _verdict("SC-001", "rule_coverage", "pass", (_step("SC-001", 1, "pass"),)),
        _verdict("SC-002", "rule_coverage", "pass", (_step("SC-002", 1, "pass"),)),
        _verdict(
            "SC-003",
            "rule_coverage",
            "fail",
            (_step("SC-003", 1, "mismatch_state"),),
        ),
    )
    metrics = compose_parity_metrics("test-model", _report(verdicts))
    assert metrics.accuracy == pytest.approx(2 / 3 * 100, rel=1e-6)


def test_accuracy_by_category_split() -> None:
    """Per-category accuracy keyed by scenario_verdict.category."""
    from samantha_server.eval.parity_report import compose_parity_metrics

    verdicts = (
        _verdict("SC-001", "rule_coverage", "pass", (_step("SC-001", 1, "pass"),)),
        _verdict(
            "SC-002",
            "rule_coverage",
            "fail",
            (_step("SC-002", 1, "mismatch_state"),),
        ),
        _verdict("SC-100", "hallucination", "pass", (_step("SC-100", 1, "pass"),)),
    )
    metrics = compose_parity_metrics("test-model", _report(verdicts))
    assert metrics.accuracy_by_category["rule_coverage"] == pytest.approx(50.0)
    assert metrics.accuracy_by_category["hallucination"] == pytest.approx(100.0)


def test_rule_accuracy_step_level() -> None:
    """`rule_accuracy` = per-step rule-set match rate."""
    from samantha_server.eval.parity_report import compose_parity_metrics

    verdicts = (
        # 1 step rule-match, 1 step rule-mismatch
        _verdict(
            "SC-001",
            "rule_coverage",
            "pass",
            (
                _step(
                    "SC-001",
                    1,
                    "pass",
                    expected_rules=("ACC-008",),
                    predicted_rules=("ACC-008",),
                ),
                _step(
                    "SC-001",
                    2,
                    "mismatch_rules",
                    expected_rules=("SP-001",),
                    predicted_rules=("ACC-008",),
                ),
            ),
        ),
    )
    metrics = compose_parity_metrics("test-model", _report(verdicts))
    assert metrics.rule_accuracy == pytest.approx(50.0)
    # PR #174 L3: pin wrong_rules independently so the test doesn't quietly
    # flip if a future change adds SP-001 to the same equivalence class as
    # ACC-008.
    assert metrics.failure_counts["wrong_rules"] == 1


def test_flag_accuracy_step_level() -> None:
    """`flag_accuracy` = per-step flag-set match rate."""
    from samantha_server.eval.parity_report import compose_parity_metrics

    verdicts = (
        _verdict(
            "SC-001",
            "rule_coverage",
            "pass",
            (
                _step("SC-001", 1, "pass", expected_flags=(), predicted_flags=()),
                _step(
                    "SC-001",
                    2,
                    "mismatch_flags",
                    expected_flags=("MISSING_INFO_PROCEED",),
                    predicted_flags=(),
                ),
            ),
        ),
    )
    metrics = compose_parity_metrics("test-model", _report(verdicts))
    assert metrics.flag_accuracy == pytest.approx(50.0)


def test_scenario_reliability_excludes_error_status() -> None:
    """`scenario_reliability` = 1 − (error verdicts / total)."""
    from samantha_server.eval.parity_report import compose_parity_metrics

    verdicts = (
        _verdict("SC-001", "rule_coverage", "pass", (_step("SC-001", 1, "pass"),)),
        _verdict(
            "SC-002",
            "rule_coverage",
            "fail",
            (_step("SC-002", 1, "error", latency_us=None, routing_path=None),),
        ),
    )
    metrics = compose_parity_metrics("test-model", _report(verdicts))
    # 1 of 2 scenarios had no error → reliability = 50%
    assert metrics.scenario_reliability == pytest.approx(50.0)


def test_latency_mean_p50_p95_in_milliseconds() -> None:
    """Latency stats are in milliseconds (samantha's POC unit)."""
    from samantha_server.eval.parity_report import compose_parity_metrics

    verdicts = (
        _verdict(
            "SC-001",
            "rule_coverage",
            "pass",
            (
                _step("SC-001", 1, "pass", latency_us=1_000_000),  # 1000ms
                _step("SC-001", 2, "pass", latency_us=2_000_000),  # 2000ms
            ),
        ),
    )
    metrics = compose_parity_metrics("test-model", _report(verdicts))
    assert metrics.latency_mean_ms == pytest.approx(1500.0)
    assert metrics.latency_p50_ms == pytest.approx(1000.0)  # nearest-rank inclusive
    assert metrics.latency_p95_ms == pytest.approx(2000.0)


def test_failure_counts_mapping_per_memo() -> None:
    """StepVerdict.status maps to samantha's FailureType per the memo's table.

    `dispatch_empty` → `empty_response` (PR #167 review M1 fix). Hallucination
    buckets read zero with a "not separately tracked" note.
    """
    from samantha_server.eval.parity_report import compose_parity_metrics

    verdicts = (
        _verdict(
            "SC-001",
            "rule_coverage",
            "fail",
            (
                _step("SC-001", 1, "mismatch_state"),
                _step("SC-002", 1, "mismatch_rules"),
                _step("SC-003", 1, "mismatch_flags"),
                _step(
                    "SC-004",
                    1,
                    "dispatch_empty",
                    latency_us=None,
                    routing_path=None,
                ),
                _step("SC-005", 1, "error", latency_us=None, routing_path=None),
            ),
        ),
    )
    metrics = compose_parity_metrics("test-model", _report(verdicts))
    assert metrics.failure_counts["wrong_state"] == 1
    assert metrics.failure_counts["wrong_rules"] == 1
    assert metrics.failure_counts["wrong_flags"] == 1
    assert metrics.failure_counts["empty_response"] == 1
    # Hallucination buckets present but zero (not separately tracked).
    assert metrics.failure_counts["hallucinated_state"] == 0
    assert metrics.failure_counts["hallucinated_rule"] == 0
    assert metrics.failure_counts["hallucinated_flag"] == 0


# ---------------------------------------------------------------------------
# write_parity_report — JSON + markdown emission
# ---------------------------------------------------------------------------


def test_write_parity_report_emits_json(tmp_path: Path) -> None:
    """The composer writes a JSON artifact mirroring samantha's summary.json shape."""
    from samantha_server.eval.parity_report import write_parity_report

    verdicts = (_verdict("SC-001", "rule_coverage", "pass", (_step("SC-001", 1, "pass"),)),)
    reports = {"qwen-2.5-coder": _report(verdicts)}
    json_path = write_parity_report(reports, tmp_path, run_id="parity-001")

    assert json_path.exists()
    payload = json.loads(json_path.read_text())
    assert payload["run_id"] == "parity-001"
    assert "models" in payload
    assert payload["models"][0]["model_id"] == "qwen-2.5-coder"
    assert payload["models"][0]["accuracy"] == pytest.approx(100.0)


def test_write_parity_report_emits_markdown(tmp_path: Path) -> None:
    """Markdown rollup published alongside the JSON for human consumption."""
    from samantha_server.eval.parity_report import write_parity_report

    verdicts = (_verdict("SC-001", "rule_coverage", "pass", (_step("SC-001", 1, "pass"),)),)
    reports = {"qwen-2.5-coder": _report(verdicts)}
    write_parity_report(reports, tmp_path, run_id="parity-001")

    md_path = tmp_path / "parity-001.md"
    assert md_path.exists()
    body = md_path.read_text()
    assert "qwen-2.5-coder" in body
    # Look for the accuracy cell explicitly — `"100" in body` would also
    # match the "100" inside a percentile column or a future denominator.
    assert "| 100.00 |" in body


# ---------------------------------------------------------------------------
# Edge cases: empty corpus, degenerate latency sample sizes
# ---------------------------------------------------------------------------


def test_compose_parity_metrics_empty_verdicts() -> None:
    """Empty `scenario_verdicts` (e.g. fully filtered run) — every numeric
    field returns 0.0 and `accuracy_by_category` is empty."""
    from samantha_server.eval.parity_report import compose_parity_metrics

    metrics = compose_parity_metrics("test-model", _report(()))
    assert metrics.accuracy == 0.0
    assert metrics.accuracy_by_category == {}
    assert metrics.rule_accuracy == 0.0
    assert metrics.flag_accuracy == 0.0
    assert metrics.scenario_reliability == 0.0
    assert metrics.latency_mean_ms == 0.0
    assert metrics.latency_p50_ms == 0.0
    assert metrics.latency_p95_ms == 0.0
    # Failure-count keys are pre-seeded to zero so dashboard schemas stay stable.
    assert metrics.failure_counts["wrong_state"] == 0
    assert metrics.failure_counts["empty_response"] == 0
    assert metrics.failure_counts["hallucinated_state"] == 0
    # GH-172 + PR #173 H1: routing counts pre-seed all five keys so a 0
    # LLM call count is comparable across runs.
    assert metrics.model_calls == 0
    assert metrics.step_routing_counts == {
        "deterministic": 0,
        "llm": 0,
        "errored_deterministic": 0,
        "errored_llm": 0,
        "errored_pre_routing": 0,
    }


def test_latency_stats_single_sample() -> None:
    """`n == 1` — mean/p50/p95 all equal the lone sample (in ms)."""
    from samantha_server.eval.parity_report import compose_parity_metrics

    verdicts = (
        _verdict(
            "SC-001",
            "rule_coverage",
            "pass",
            (_step("SC-001", 1, "pass", latency_us=2_500_000),),
        ),
    )
    metrics = compose_parity_metrics("test-model", _report(verdicts))
    assert metrics.latency_mean_ms == pytest.approx(2500.0)
    assert metrics.latency_p50_ms == pytest.approx(2500.0)
    assert metrics.latency_p95_ms == pytest.approx(2500.0)


def test_latency_stats_all_none_returns_zeros() -> None:
    """All steps with `latency_us=None` (engine-error path) — stats degrade
    to 0.0 instead of raising."""
    from samantha_server.eval.parity_report import compose_parity_metrics

    verdicts = (
        _verdict(
            "SC-001",
            "rule_coverage",
            "fail",
            (
                _step("SC-001", 1, "error", latency_us=None, routing_path=None),
                _step("SC-001", 2, "error", latency_us=None, routing_path=None),
            ),
        ),
    )
    metrics = compose_parity_metrics("test-model", _report(verdicts))
    assert metrics.latency_mean_ms == 0.0
    assert metrics.latency_p50_ms == 0.0
    assert metrics.latency_p95_ms == 0.0


# ---------------------------------------------------------------------------
# Rule/flag-match denominator: dispatch_empty excluded like error
# ---------------------------------------------------------------------------


def test_dispatch_empty_excluded_from_rule_accuracy_denominator() -> None:
    """`dispatch_empty` steps don't run a comparable engine output, so they
    must be excluded from the rule/flag-match denominator like `error`. The
    failure is already attributed via `failure_counts['empty_response']`."""
    from samantha_server.eval.parity_report import compose_parity_metrics

    verdicts = (
        _verdict(
            "SC-001",
            "rule_coverage",
            "fail",
            (
                _step("SC-001", 1, "pass"),
                _step(
                    "SC-001",
                    2,
                    "dispatch_empty",
                    expected_rules=("SP-001",),
                    predicted_rules=(),
                    latency_us=None,
                    routing_path=None,
                ),
            ),
        ),
    )
    metrics = compose_parity_metrics("test-model", _report(verdicts))
    # The dispatch_empty step is excluded — denominator is 1, numerator 1.
    assert metrics.rule_accuracy == pytest.approx(100.0)
    assert metrics.flag_accuracy == pytest.approx(100.0)
    # And the failure is attributed exactly once via failure_counts.
    assert metrics.failure_counts["empty_response"] == 1


def test_rule_accuracy_set_equality_ignores_order() -> None:
    """`rule_accuracy` uses set equality so multi-rule steps that emit the
    same rules in different order count as a match (aligned with
    `_verdict_for_step`)."""
    from samantha_server.eval.parity_report import compose_parity_metrics

    verdicts = (
        _verdict(
            "SC-001",
            "multi_rule",
            "pass",
            (
                _step(
                    "SC-001",
                    1,
                    "pass",
                    expected_rules=("ACC-008", "SP-001"),
                    predicted_rules=("SP-001", "ACC-008"),
                ),
            ),
        ),
    )
    metrics = compose_parity_metrics("test-model", _report(verdicts))
    assert metrics.rule_accuracy == pytest.approx(100.0)


def test_failure_counts_unknown_status_raises() -> None:
    """Adding a new `StepVerdict.status` literal in `replay.py` without a
    `_STATUS_TO_FAILURE_TYPE` mapping must raise rather than silently drop."""
    from samantha_server.eval.parity_report import compose_parity_metrics

    verdicts = (
        _verdict(
            "SC-001",
            "rule_coverage",
            "fail",
            # Construct a synthetic StepVerdict bypassing the Literal check.
            (_step("SC-001", 1, "future_status_not_yet_mapped"),),
        ),
    )
    with pytest.raises(ValueError, match="Unknown StepVerdict.status"):
        compose_parity_metrics("test-model", _report(verdicts))


# ---------------------------------------------------------------------------
# Markdown safety: model_id with pipe / backtick characters
# ---------------------------------------------------------------------------


def test_render_markdown_escapes_model_id_pipe(tmp_path: Path) -> None:
    """A `|` or backtick in `model_id` (which originates in config) must
    not corrupt the Markdown table layout."""
    from samantha_server.eval.parity_report import write_parity_report

    verdicts = (_verdict("SC-001", "rule_coverage", "pass", (_step("SC-001", 1, "pass"),)),)
    reports = {"weird|model`name": _report(verdicts)}
    write_parity_report(reports, tmp_path, run_id="parity-md-escape")

    body = (tmp_path / "parity-md-escape.md").read_text()
    # The pipe / backtick must be escaped so the row still has 8 columns.
    data_rows = [line for line in body.splitlines() if line.startswith("| weird")]
    assert len(data_rows) == 1
    # 9 columns (8 metric columns + the GH-172 LLM-calls column) means 10
    # unescaped pipes; an unescaped `|` in model_id would push to 11.
    raw_pipes = data_rows[0].replace("\\|", "")
    assert raw_pipes.count("|") == 10


# ---------------------------------------------------------------------------
# Multi-model write_parity_report
# ---------------------------------------------------------------------------


def test_write_parity_report_multi_model_sweep(tmp_path: Path) -> None:
    """The composer's stated use case is multi-model sweeps — both models
    appear in the JSON `models` array and both rows in the Markdown table."""
    from samantha_server.eval.parity_report import write_parity_report

    verdicts_a = (_verdict("SC-001", "rule_coverage", "pass", (_step("SC-001", 1, "pass"),)),)
    verdicts_b = (
        _verdict("SC-002", "rule_coverage", "fail", (_step("SC-002", 1, "mismatch_state"),)),
    )
    reports = {
        "model-a": _report(verdicts_a),
        "model-b": _report(verdicts_b),
    }
    json_path = write_parity_report(reports, tmp_path, run_id="parity-multi")

    payload = json.loads(json_path.read_text())
    seen = {row["model_id"]: row["accuracy"] for row in payload["models"]}
    assert seen == {"model-a": pytest.approx(100.0), "model-b": pytest.approx(0.0)}

    body = (tmp_path / "parity-multi.md").read_text()
    assert "| model-a |" in body
    assert "| model-b |" in body


# ---------------------------------------------------------------------------
# GH-172: model_calls + step_routing_counts surfacing
# ---------------------------------------------------------------------------


def test_model_calls_counts_only_llm_routed_steps() -> None:
    """`model_calls` counts step_verdicts whose `routing_path == 'llm'`.

    A run with only deterministic steps must report `model_calls == 0`
    even though `model_id` is non-empty — the field tells you whether
    the model was actually called, separately from which model was
    configured."""
    from samantha_server.eval.parity_report import compose_parity_metrics

    verdicts = (
        _verdict(
            "SC-001",
            "rule_coverage",
            "pass",
            (_step("SC-001", 1, "pass", routing_path="deterministic"),),
        ),
        _verdict(
            "SC-002",
            "hallucination",
            "pass",
            (
                _step("SC-002", 1, "pass", routing_path="llm"),
                _step("SC-002", 2, "pass", routing_path="llm"),
            ),
        ),
    )
    metrics = compose_parity_metrics("Qwen2.5-Coder", _report(verdicts))
    assert metrics.model_calls == 2
    assert metrics.step_routing_counts == {
        "deterministic": 1,
        "llm": 2,
        "errored_deterministic": 0,
        "errored_llm": 0,
        "errored_pre_routing": 0,
    }


def test_step_routing_counts_attributes_pre_routing_errors() -> None:
    """A step that errored before routing was determined (routing_path=None)
    lands in `errored_pre_routing` so a 0 in `model_calls` can be
    cross-checked. This is the GH-171 dispatch-refusal shape."""
    from samantha_server.eval.parity_report import compose_parity_metrics

    verdicts = (
        _verdict(
            "SC-001",
            "hallucination",
            "fail",
            (_step("SC-001", 1, "error", latency_us=None, routing_path=None),),
        ),
        _verdict(
            "SC-002",
            "hallucination",
            "fail",
            (_step("SC-002", 1, "error", latency_us=None, routing_path=None),),
        ),
    )
    metrics = compose_parity_metrics("Qwen2.5-Coder", _report(verdicts))
    assert metrics.model_calls == 0
    # PR #173 M5: full-dict equality so a regression that bumped any
    # other key on the routing_path=None path would be caught.
    assert metrics.step_routing_counts == {
        "deterministic": 0,
        "llm": 0,
        "errored_deterministic": 0,
        "errored_llm": 0,
        "errored_pre_routing": 2,
    }


def test_step_routing_counts_attributes_post_routing_errors() -> None:
    """PR #173 H1: a step that errored *after* routing was decided lands in
    `errored_deterministic` or `errored_llm`, NOT `errored_pre_routing`. This
    is the GH-156 `re_raise_on_deterministic_error=False` shape — a real
    deterministic engine gap (e.g. SC-092's FIXATION_WARNING) must not
    trigger the GH-172 "model never called" banner."""
    from samantha_server.eval.parity_report import compose_parity_metrics

    verdicts = (
        _verdict(
            "SC-DET-ERR",
            "rule_coverage",
            "fail",
            (
                _step(
                    "SC-DET-ERR",
                    1,
                    "error",
                    latency_us=None,
                    routing_path="deterministic",
                ),
            ),
        ),
        _verdict(
            "SC-LLM-ERR",
            "hallucination",
            "fail",
            (
                _step(
                    "SC-LLM-ERR",
                    1,
                    "error",
                    latency_us=None,
                    routing_path="llm",
                ),
            ),
        ),
    )
    metrics = compose_parity_metrics("Qwen2.5-Coder", _report(verdicts))
    assert metrics.model_calls == 0  # errored_llm steps don't count
    assert metrics.step_routing_counts == {
        "deterministic": 0,
        "llm": 0,
        "errored_deterministic": 1,
        "errored_llm": 1,
        "errored_pre_routing": 0,
    }


def test_render_markdown_warns_on_silent_model(tmp_path: Path) -> None:
    """When `model_calls == 0` and pre-routing errors exist, the markdown
    rollup emits a banner above the table so the 0 in `LLM calls` cannot
    be misread as 'model performed badly'."""
    from samantha_server.eval.parity_report import write_parity_report

    verdicts = (
        _verdict(
            "SC-001",
            "hallucination",
            "fail",
            (_step("SC-001", 1, "error", latency_us=None, routing_path=None),),
        ),
    )
    reports = {"Qwen2.5-Coder": _report(verdicts)}
    write_parity_report(reports, tmp_path, run_id="parity-silent")

    body = (tmp_path / "parity-silent.md").read_text()
    # PR #173 L3: structural check on the banner so a prose reword doesn't
    # break the test. The banner is a Markdown block-quote starting with
    # `> **Note:**`.
    assert any(line.startswith("> **Note:**") for line in body.splitlines()), body
    assert "Qwen2.5-Coder" in body
    # The new column header must be present too.
    assert "LLM calls" in body
    # The data row must show the 0 in the LLM-calls column.
    rows = [line for line in body.splitlines() if line.startswith("| Qwen")]
    assert rows and rows[0].rstrip().endswith("| 0 |"), rows


def test_render_markdown_no_banner_when_model_was_called(tmp_path: Path) -> None:
    """A run with at least one successful LLM call must NOT emit the
    silent-model banner — the banner is for the false-positive trap, not a
    decoration."""
    from samantha_server.eval.parity_report import write_parity_report

    verdicts = (
        _verdict(
            "SC-001",
            "hallucination",
            "pass",
            (_step("SC-001", 1, "pass", routing_path="llm"),),
        ),
    )
    reports = {"Qwen2.5-Coder": _report(verdicts)}
    write_parity_report(reports, tmp_path, run_id="parity-real")

    body = (tmp_path / "parity-real.md").read_text()
    assert not any(line.startswith("> **Note:**") for line in body.splitlines()), body


def test_render_markdown_no_banner_on_deterministic_only_run(tmp_path: Path) -> None:
    """PR #173 M3: deterministic-only runs (e.g. accumulated_state subset)
    have `model_calls == 0` AND `errored_pre_routing == 0`. No banner — the
    "LLM calls: 0" column is honest and expected, not a warning sign."""
    from samantha_server.eval.parity_report import write_parity_report

    verdicts = (
        _verdict(
            "SC-001",
            "rule_coverage",
            "pass",
            (_step("SC-001", 1, "pass", routing_path="deterministic"),),
        ),
        _verdict(
            "SC-002",
            "rule_coverage",
            "pass",
            (_step("SC-002", 1, "pass", routing_path="deterministic"),),
        ),
    )
    reports = {"Qwen2.5-Coder": _report(verdicts)}
    write_parity_report(reports, tmp_path, run_id="parity-det")

    body = (tmp_path / "parity-det.md").read_text()
    assert not any(line.startswith("> **Note:**") for line in body.splitlines()), body
    # `LLM calls: 0` still shows in the data row — that's the honest report.
    rows = [line for line in body.splitlines() if line.startswith("| Qwen")]
    assert rows and rows[0].rstrip().endswith("| 0 |"), rows


def test_render_markdown_warns_on_empty_corpus(tmp_path: Path) -> None:
    """PR #173 M2: a composer call with zero verdicts (empty corpus or
    fully-filtered include_scenario_ids on a non-CLI caller) emits a
    distinct banner so the empty-input shape is impossible to misread as a
    real comparison.

    The CLI guards this case via an `overall_total == 0` raise, but the
    composer is library-callable and must be defensive."""
    from samantha_server.eval.parity_report import write_parity_report

    reports = {"Qwen2.5-Coder": _report(())}
    write_parity_report(reports, tmp_path, run_id="parity-empty")

    body = (tmp_path / "parity-empty.md").read_text()
    assert "zero scored steps" in body
    assert any(line.startswith("> **Note:**") for line in body.splitlines()), body


# ---------------------------------------------------------------------------
# GH-170: rule equivalence-class matching (composer-only)
# ---------------------------------------------------------------------------


def test_rule_equivalence_classes_constant() -> None:
    """_RULE_EQUIVALENCE_CLASSES maps SP-001 and SP-007 to the same frozenset."""
    from samantha_server.eval.parity_report import _RULE_EQUIVALENCE_CLASSES

    assert _RULE_EQUIVALENCE_CLASSES["SP-001"] == frozenset({"SP-001", "SP-007"})
    assert _RULE_EQUIVALENCE_CLASSES["SP-007"] == frozenset({"SP-001", "SP-007"})


def test_canonicalize_rule_set_equivalence() -> None:
    """_canonicalize_rule_set maps SP-001 and SP-007 to the same canonical frozenset."""
    from samantha_server.eval.parity_report import _canonicalize_rule_set

    assert _canonicalize_rule_set(("SP-001",)) == _canonicalize_rule_set(("SP-007",))


def test_canonicalize_rule_set_non_equivalent_rules_untouched() -> None:
    """Rules not in the equivalence map canonicalize to a singleton frozenset."""
    from samantha_server.eval.parity_report import _canonicalize_rule_set

    result = _canonicalize_rule_set(("ACC-008",))
    assert result == frozenset({"ACC-008"})


def test_step_rule_match_parity_equivalent_rules() -> None:
    """_step_rule_match returns True for SP-001 vs SP-007 (same equivalence class).

    The engine layer uses strict rule-id equality; the composer applies
    equivalence-class canonicalization so SC-093/SC-099 step 10 scores as
    a match even though samantha_server fires SP-007 where the POC emits SP-001.
    """
    from samantha_server.eval.parity_report import _step_rule_match
    from samantha_server.scenarios.replay import (
        ExpectedOutput,
        PredictedOutput,
        StepVerdict,
    )

    sv = StepVerdict(
        scenario_id="SC-093",
        step_index=10,
        status="mismatch_rules",  # type: ignore[arg-type]
        expected=ExpectedOutput(next_state="SAMPLE_PREP", applied_rules=("SP-001",), flags=()),
        predicted=PredictedOutput(next_state="SAMPLE_PREP", applied_rules=("SP-007",), flags=()),
        latency_us=1_000,
        routing_path="deterministic",  # type: ignore[arg-type]
    )
    assert _step_rule_match(sv) is True


def test_compose_parity_metrics_equivalence_promotes_accuracy() -> None:
    """A scenario with status='mismatch_rules' for SP-001 vs SP-007 scores
    accuracy=100.0 and failure_counts['wrong_rules']==0 after equivalence rescue.

    The scenario-level status from the engine is 'mismatch_rules' (strict
    comparison); the composer must re-evaluate it under parity equivalence and
    count the scenario as passing for accuracy and category-accuracy purposes.
    The wrong_rules failure bucket must also not be incremented.
    """
    from samantha_server.eval.parity_report import compose_parity_metrics

    sv = _step(
        "SC-093",
        10,
        "mismatch_rules",
        expected_rules=("SP-001",),
        predicted_rules=("SP-007",),
    )
    verdict = _verdict("SC-093", "rule_coverage", "mismatch_rules", (sv,))
    metrics = compose_parity_metrics("test-model", _report((verdict,)))
    assert metrics.accuracy == pytest.approx(100.0)
    assert metrics.accuracy_by_category["rule_coverage"] == pytest.approx(100.0)
    assert metrics.failure_counts["wrong_rules"] == 0
    # PR #174 M7: pin rule_accuracy so a regression that promoted at the
    # scenario layer but missed _step_rule_match's numerator wouldn't slip.
    assert metrics.rule_accuracy == pytest.approx(100.0)


def test_render_markdown_per_model_banner_is_per_model(tmp_path: Path) -> None:
    """PR #173 L4: the banner is per-model, not per-report. An asymmetric
    multi-model sweep (one silent, one healthy) shows the banner for the
    silent model only, not as a global header."""
    from samantha_server.eval.parity_report import write_parity_report

    silent_verdicts = (
        _verdict(
            "SC-S1",
            "hallucination",
            "fail",
            (_step("SC-S1", 1, "error", latency_us=None, routing_path=None),),
        ),
    )
    healthy_verdicts = (
        _verdict(
            "SC-H1",
            "hallucination",
            "pass",
            (_step("SC-H1", 1, "pass", routing_path="llm"),),
        ),
    )
    reports = {
        "silent-model": _report(silent_verdicts),
        "healthy-model": _report(healthy_verdicts),
    }
    write_parity_report(reports, tmp_path, run_id="parity-asymmetric")

    body = (tmp_path / "parity-asymmetric.md").read_text()
    notes = [line for line in body.splitlines() if line.startswith("> **Note:**")]
    assert len(notes) == 1, notes
    assert "silent-model" in notes[0]
    assert "healthy-model" not in notes[0]


# ---------------------------------------------------------------------------
# PR #174 review fixes — equivalence-class hardening
# ---------------------------------------------------------------------------


def test_scenario_parity_passed_rejects_empty_step_verdicts() -> None:
    """PR #174 H1: a scenario with no step_verdicts is NOT a parity pass.
    Vacuous truth in the for-loop would silently inflate accuracy for any
    malformed scenario the loader produced with an empty steps list."""
    from samantha_server.eval.parity_report import (
        _scenario_parity_passed,
        compose_parity_metrics,
    )

    empty_verdict = _verdict("SC-EMPTY", "rule_coverage", "fail", ())
    assert _scenario_parity_passed(empty_verdict) is False

    # Integration: empty-step scenarios reduce accuracy as expected.
    metrics = compose_parity_metrics("test-model", _report((empty_verdict,)))
    assert metrics.accuracy == pytest.approx(0.0)


def test_rule_equivalence_classes_symmetry_invariant() -> None:
    """PR #174 M2: every member of an equivalence class must map to the
    same frozenset value. The `_RULE_EQUIVALENCE_CLASSES` dict is built
    from `_RULE_EQUIVALENCE_CLASS_LIST` so this is true by construction —
    pin it so a future hand-written entry can't violate it."""
    from samantha_server.eval.parity_report import (
        _RULE_EQUIVALENCE_CLASS_LIST,
        _RULE_EQUIVALENCE_CLASSES,
    )

    for equiv_class in _RULE_EQUIVALENCE_CLASS_LIST:
        for member in equiv_class:
            assert member in _RULE_EQUIVALENCE_CLASSES, (
                f"Member {member!r} of class {equiv_class!r} is missing from the inverted index."
            )
            assert _RULE_EQUIVALENCE_CLASSES[member] == equiv_class, (
                f"Member {member!r} maps to "
                f"{_RULE_EQUIVALENCE_CLASSES[member]!r} but should map to "
                f"{equiv_class!r} (symmetry violation)."
            )


def test_rule_accuracy_counts_mismatch_state_step_with_equivalent_rules() -> None:
    """PR #174 M3 (orthogonality pin): a step that mismatches state but
    has equivalence-class-matching rules still contributes a rule-match
    point to `rule_accuracy`. The `rule_accuracy` metric measures rule
    attribution independently of state correctness — a samantha-POC
    convention this composer mirrors. A future change that conflates the
    two would flip this assertion."""
    from samantha_server.eval.parity_report import compose_parity_metrics

    verdicts = (
        _verdict(
            "SC-WS",
            "rule_coverage",
            "fail",
            (
                _step(
                    "SC-WS",
                    1,
                    "mismatch_state",
                    expected_rules=("SP-001",),
                    predicted_rules=("SP-007",),
                ),
            ),
        ),
    )
    metrics = compose_parity_metrics("test-model", _report(verdicts))
    # Rule attribution matched (under equivalence) — counts toward
    # rule_accuracy even though the state was wrong.
    assert metrics.rule_accuracy == pytest.approx(100.0)
    # State mismatch — scenario does NOT promote to a parity pass.
    assert metrics.accuracy == pytest.approx(0.0)
    # And the failure is attributed to wrong_state, not wrong_rules.
    assert metrics.failure_counts["wrong_state"] == 1
    assert metrics.failure_counts["wrong_rules"] == 0


def test_canonicalize_multi_rule_partial_equivalence() -> None:
    """PR #174 M4: a step where one rule is in the equivalence map and
    another is not — the canonicalization must map each rule
    independently. Expected ("SP-001","ACC-008") vs predicted
    ("SP-007","ACC-008") canonicalize equal."""
    from samantha_server.eval.parity_report import _canonicalize_rule_set

    expected = _canonicalize_rule_set(("SP-001", "ACC-008"))
    predicted = _canonicalize_rule_set(("SP-007", "ACC-008"))
    assert expected == predicted

    # And the integration: a multi-rule mismatch_rules step rescues correctly.
    from samantha_server.eval.parity_report import compose_parity_metrics

    verdicts = (
        _verdict(
            "SC-MULTI",
            "multi_rule",
            "mismatch_rules",
            (
                _step(
                    "SC-MULTI",
                    1,
                    "mismatch_rules",
                    expected_rules=("SP-001", "ACC-008"),
                    predicted_rules=("SP-007", "ACC-008"),
                ),
            ),
        ),
    )
    metrics = compose_parity_metrics("test-model", _report(verdicts))
    assert metrics.accuracy == pytest.approx(100.0)
    assert metrics.rule_accuracy == pytest.approx(100.0)
    assert metrics.failure_counts["wrong_rules"] == 0


def test_scenario_reliability_unchanged_by_equivalence_rescue() -> None:
    """PR #174 M5: a scenario rescued from mismatch_rules to a parity pass
    still counts toward reliability. `_scenario_reliability` examines step
    statuses directly (looking for `error`), so rescue is a no-op for
    reliability — a future refactor that routes rescued steps through
    reliability would break this assertion."""
    from samantha_server.eval.parity_report import compose_parity_metrics

    rescued = _verdict(
        "SC-RESCUED",
        "rule_coverage",
        "mismatch_rules",
        (
            _step(
                "SC-RESCUED",
                1,
                "mismatch_rules",
                expected_rules=("SP-001",),
                predicted_rules=("SP-007",),
            ),
        ),
    )
    metrics = compose_parity_metrics("test-model", _report((rescued,)))
    # Single scenario, no errors → 100% reliability regardless of rescue.
    assert metrics.scenario_reliability == pytest.approx(100.0)


def test_accuracy_by_category_two_categories_with_rescue_and_real_failure() -> None:
    """PR #174 M6: a two-category corpus with one rescued scenario and one
    genuine mismatch_state pins the per-category split under
    `_scenario_parity_passed`."""
    from samantha_server.eval.parity_report import compose_parity_metrics

    verdicts = (
        _verdict(
            "SC-RESC",
            "accumulated_state",
            "mismatch_rules",
            (
                _step(
                    "SC-RESC",
                    1,
                    "mismatch_rules",
                    expected_rules=("SP-001",),
                    predicted_rules=("SP-007",),
                ),
            ),
        ),
        _verdict(
            "SC-WS",
            "rule_coverage",
            "fail",
            (
                _step(
                    "SC-WS",
                    1,
                    "mismatch_state",
                    expected_rules=("ACC-008",),
                    predicted_rules=("ACC-008",),
                ),
            ),
        ),
    )
    metrics = compose_parity_metrics("test-model", _report(verdicts))
    assert metrics.accuracy_by_category["accumulated_state"] == pytest.approx(100.0)
    assert metrics.accuracy_by_category["rule_coverage"] == pytest.approx(0.0)
    # Overall: 1 of 2 passes under parity equivalence.
    assert metrics.accuracy == pytest.approx(50.0)


def test_canonicalize_empty_tuple() -> None:
    """PR #174 L1: pin `_canonicalize_rule_set(())` returns an empty
    frozenset. The current code reaches this case only via
    `_EXCLUDED_FROM_MATCH_RATE` (which excludes `dispatch_empty`), but
    the helper's contract on empty input should be observable so a
    future caller can rely on it."""
    from samantha_server.eval.parity_report import _canonicalize_rule_set

    assert _canonicalize_rule_set(()) == frozenset()


def test_step_is_parity_equivalent_mismatch_rules_returns_false_on_identical_literals() -> None:
    """PR #174 L2: pin the identical-literals early-out. The engine should
    not produce mismatch_rules with identical sets, but if it ever did, the
    composer must NOT rescue the step (preserves wrong_rules accounting on
    pathological engine output)."""
    from samantha_server.eval.parity_report import _step_is_parity_equivalent_mismatch_rules

    sv = _step(
        "SC-PATHO",
        1,
        "mismatch_rules",
        expected_rules=("SP-001",),
        predicted_rules=("SP-001",),  # identical literals — should NOT rescue
    )
    assert _step_is_parity_equivalent_mismatch_rules(sv) is False
