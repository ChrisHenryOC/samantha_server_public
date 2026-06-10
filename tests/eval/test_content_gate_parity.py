"""Parity-report mapping for new content-gate StepVerdict statuses."""

from __future__ import annotations

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
    latency_us: int | None = 1_000_000,
    routing_path: str | None = "llm",
) -> StepVerdict:
    return StepVerdict(
        scenario_id=scenario_id,
        step_index=step_index,
        status=status,  # type: ignore[arg-type]
        expected=ExpectedOutput(next_state=next_state, applied_rules=(), flags=()),
        predicted=PredictedOutput(next_state=next_state, applied_rules=(), flags=()),
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


class TestContentGateParityMapping:
    """New status values produce expected failure_counts buckets."""

    def test_mismatch_query_response_maps_to_bucket(self) -> None:
        """mismatch_query_response → 'mismatch_query_response' bucket."""
        from samantha_server.eval.parity_report import compose_parity_metrics

        verdicts = (
            _verdict(
                "QRY-001",
                "query",
                "fail",
                (_step("QRY-001", 1, "mismatch_query_response"),),
            ),
        )
        metrics = compose_parity_metrics("test-model", _report(verdicts))
        assert metrics.failure_counts.get("mismatch_query_response", 0) == 1

    def test_mismatch_disposition_maps_to_bucket(self) -> None:
        """mismatch_disposition → 'mismatch_disposition' bucket."""
        from samantha_server.eval.parity_report import compose_parity_metrics

        verdicts = (
            _verdict(
                "LLM-001",
                "llm_review",
                "fail",
                (_step("LLM-001", 1, "mismatch_disposition"),),
            ),
        )
        metrics = compose_parity_metrics("test-model", _report(verdicts))
        assert metrics.failure_counts.get("mismatch_disposition", 0) == 1

    def test_invalid_json_maps_to_bucket(self) -> None:
        """invalid_json → 'invalid_json' bucket."""
        from samantha_server.eval.parity_report import compose_parity_metrics

        verdicts = (
            _verdict(
                "QRY-002",
                "query",
                "fail",
                (_step("QRY-002", 1, "invalid_json"),),
            ),
        )
        metrics = compose_parity_metrics("test-model", _report(verdicts))
        assert metrics.failure_counts.get("invalid_json", 0) == 1

    def test_empty_response_maps_to_bucket(self) -> None:
        """empty_response → 'empty_response' bucket (same bucket as dispatch_empty)."""
        from samantha_server.eval.parity_report import compose_parity_metrics

        verdicts = (
            _verdict(
                "QRY-003",
                "query",
                "fail",
                (_step("QRY-003", 1, "empty_response"),),
            ),
        )
        metrics = compose_parity_metrics("test-model", _report(verdicts))
        assert metrics.failure_counts.get("empty_response", 0) == 1

    def test_hallucinated_state_maps_to_bucket(self) -> None:
        """hallucinated_state → 'hallucinated_state' bucket with real count (not zero-fill)."""
        from samantha_server.eval.parity_report import compose_parity_metrics

        verdicts = (
            _verdict(
                "SC-001",
                "hallucination",
                "fail",
                (_step("SC-001", 1, "hallucinated_state"),),
            ),
        )
        metrics = compose_parity_metrics("test-model", _report(verdicts))
        assert metrics.failure_counts.get("hallucinated_state", 0) == 1

    def test_hallucinated_rule_maps_to_bucket(self) -> None:
        """hallucinated_rule → 'hallucinated_rule' bucket with real count."""
        from samantha_server.eval.parity_report import compose_parity_metrics

        verdicts = (
            _verdict(
                "SC-002",
                "hallucination",
                "fail",
                (_step("SC-002", 1, "hallucinated_rule"),),
            ),
        )
        metrics = compose_parity_metrics("test-model", _report(verdicts))
        assert metrics.failure_counts.get("hallucinated_rule", 0) == 1

    def test_hallucinated_flag_maps_to_bucket(self) -> None:
        """hallucinated_flag → 'hallucinated_flag' bucket with real count."""
        from samantha_server.eval.parity_report import compose_parity_metrics

        verdicts = (
            _verdict(
                "SC-003",
                "hallucination",
                "fail",
                (_step("SC-003", 1, "hallucinated_flag"),),
            ),
        )
        metrics = compose_parity_metrics("test-model", _report(verdicts))
        assert metrics.failure_counts.get("hallucinated_flag", 0) == 1

    def test_hallucination_buckets_no_longer_zero_filled_when_real_counts_exist(self) -> None:
        """After Slice 6: hallucinated_* buckets reflect real failure counts, not zero-fill."""
        from samantha_server.eval.parity_report import compose_parity_metrics

        # Two hallucinated_state failures
        verdicts = (
            _verdict(
                "SC-010",
                "hallucination",
                "fail",
                (
                    _step("SC-010", 1, "hallucinated_state"),
                    _step("SC-010", 2, "hallucinated_rule"),
                ),
            ),
        )
        metrics = compose_parity_metrics("test-model", _report(verdicts))
        assert metrics.failure_counts["hallucinated_state"] == 1
        assert metrics.failure_counts["hallucinated_rule"] == 1
        assert metrics.failure_counts.get("hallucinated_flag", 0) == 0
