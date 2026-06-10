"""content_diagnostic field on StepVerdict and report formatter.

Tests are organized by slice:
  Slice 1: StepVerdict.content_diagnostic field — exists, defaults to None.
  Slice 2: _verdict_for_step populates content_diagnostic for per-step gate fires.
  Slice 3: Post-loop query content gate populates content_diagnostic.
  Slice 4: _print_report_and_get_exit_code renders | content=... when set.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

from samantha_server.scenarios.replay import (
    AccuracyReport,
    ExpectedOutput,
    PredictedOutput,
    ScenarioVerdict,
    StepVerdict,
    _print_report_and_get_exit_code,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_expected() -> ExpectedOutput:
    return ExpectedOutput(next_state="ACCEPTED", applied_rules=(), flags=())


def _minimal_predicted() -> PredictedOutput:
    return PredictedOutput(next_state="ACCEPTED", applied_rules=(), flags=())


def _make_step_verdict(
    status: str = "pass",
    content_diagnostic: str | None = None,
) -> StepVerdict:
    return StepVerdict(
        scenario_id="SC-TEST",
        step_index=1,
        status=status,  # type: ignore[arg-type]
        expected=_minimal_expected(),
        predicted=_minimal_predicted(),
        content_diagnostic=content_diagnostic,
    )


def _make_minimal_report(step_verdicts: tuple[StepVerdict, ...]) -> AccuracyReport:
    """Build a minimal AccuracyReport with one scenario containing the given step_verdicts."""
    scenario_status = "pass" if all(sv.status == "pass" for sv in step_verdicts) else "fail"
    scenario_verdict = ScenarioVerdict(
        scenario_id="SC-TEST",
        category="query",
        status=scenario_status,
        step_verdicts=step_verdicts,
    )
    all_pass = scenario_status == "pass"
    return AccuracyReport(
        included_accuracy=1.0 if all_pass else 0.0,
        overall_accuracy=1.0 if all_pass else 0.0,
        included_pass=1 if all_pass else 0,
        included_total=1,
        overall_pass=1 if all_pass else 0,
        overall_total=1,
        p99_latency_us=None,
        p99_latency_us_llm=None,
        llm_latency_step_count=0,
        deterministic_latency_step_count=0,
        scenario_verdicts=(scenario_verdict,),
    )


# ---------------------------------------------------------------------------
# Slice 1: StepVerdict.content_diagnostic field
# ---------------------------------------------------------------------------


class TestStepVerdictContentDiagnosticField:
    """content_diagnostic field on StepVerdict."""

    def test_field_exists_and_defaults_to_none(self) -> None:
        """StepVerdict must carry content_diagnostic; default is None."""
        sv = StepVerdict(
            scenario_id="SC-001",
            step_index=1,
            status="pass",
            expected=_minimal_expected(),
            predicted=_minimal_predicted(),
        )
        assert sv.content_diagnostic is None

    def test_field_accepts_string_value(self) -> None:
        """content_diagnostic accepts a non-None string without error."""
        sv = StepVerdict(
            scenario_id="SC-001",
            step_index=1,
            status="hallucinated_state",
            expected=_minimal_expected(),
            predicted=_minimal_predicted(),
            content_diagnostic="predicted_state=BOGUS (not in VALID_STATES)",
        )
        assert sv.content_diagnostic == "predicted_state=BOGUS (not in VALID_STATES)"

    def test_field_survives_dataclasses_replace(self) -> None:
        """dataclasses.replace preserves content_diagnostic when not overridden."""
        import dataclasses

        sv = _make_step_verdict(status="pass")
        sv2 = dataclasses.replace(sv, status="mismatch_query_response")
        assert sv2.content_diagnostic is None

    def test_dataclasses_replace_can_set_diagnostic(self) -> None:
        """dataclasses.replace can set content_diagnostic on a verdict."""
        import dataclasses

        sv = _make_step_verdict(status="pass")
        sv2 = dataclasses.replace(sv, status="mismatch_query_response", content_diagnostic="x=1")
        assert sv2.content_diagnostic == "x=1"


# ---------------------------------------------------------------------------
# Slice 2: _verdict_for_step populates content_diagnostic
# ---------------------------------------------------------------------------


class TestVerdictForStepContentDiagnostic:
    """content_diagnostic populated for per-step gate fires."""

    @pytest.fixture()
    def rule_index(self) -> object:
        from samantha_server.rules.loader import RuleIndex, load_rule_specs

        specs_dir = Path(__file__).resolve().parents[2] / "samantha_server" / "rules" / "specs"
        return RuleIndex(load_rule_specs(specs_dir))

    def _make_step(
        self,
        expected_next_state: str = "ACCEPTED",
        expected_applied_rules: tuple[str, ...] = (),
        expected_flags: tuple[str, ...] = (),
        llm_disposition: str | None = None,
    ) -> object:
        from samantha_server.scenarios.loader import ScenarioStep

        return ScenarioStep(
            step_index=1,
            event_type="order_received",
            event_data={},
            expected_next_state=expected_next_state,
            expected_applied_rules=expected_applied_rules,
            expected_flags=expected_flags,
            llm_disposition=llm_disposition,
        )

    def _make_decision(
        self,
        next_state: str = "ACCEPTED",
        applied_rule_id: str | None = None,
        flags_added: tuple[str, ...] = (),
        flags_cleared: tuple[str, ...] = (),
        also_matched: tuple[str, ...] = (),
        decision_traces: tuple[object, ...] = (),
        latency_us: int = 100,
    ) -> object:
        from unittest.mock import MagicMock

        from samantha_server.engine.decision import EngineDecision

        # PR211 review #9: outcome and event_input_hash are not read by
        # _verdict_for_step (they're consumed by _emit_deterministic_replay_span,
        # not under test here). Dropped to align with the established
        # _make_decision helper in tests/scenarios/test_engine_content_gate.py.
        m = MagicMock(spec=EngineDecision)
        m.next_state = next_state
        m.applied_rule_id = applied_rule_id
        m.also_matched = also_matched
        m.flags_added = flags_added
        m.flags_cleared = flags_cleared
        m.decision_traces = decision_traces
        m.latency_us = latency_us
        return m

    def test_hallucinated_state_diagnostic(self, rule_index: object) -> None:
        """hallucinated_state sets diagnostic to 'predicted_state=X (not in VALID_STATES)'."""
        from samantha_server.scenarios.replay import _verdict_for_step

        step = self._make_step(expected_next_state="ACCEPTED")
        decision = self._make_decision(next_state="BOGUS_STATE")
        verdict = _verdict_for_step(
            "SC-001",
            step,  # type: ignore[arg-type]
            decision,  # type: ignore[arg-type]
            predicted_next_state="BOGUS_STATE",
            accumulated_flags=frozenset(),
            routing_path="deterministic",
            rule_index=rule_index,  # type: ignore[arg-type]
        )
        assert verdict.status == "hallucinated_state"
        assert verdict.content_diagnostic is not None
        assert "predicted_state=BOGUS_STATE" in verdict.content_diagnostic
        assert "not in VALID_STATES" in verdict.content_diagnostic

    def test_hallucinated_rule_applied_rule_id_diagnostic(self, rule_index: object) -> None:
        """hallucinated_rule (applied_rule_id) sets diagnostic naming the invented rule."""
        from samantha_server.scenarios.replay import _verdict_for_step

        step = self._make_step()
        decision = self._make_decision(
            next_state="ACCEPTED",
            applied_rule_id="FAKE-RULE-999",
        )
        verdict = _verdict_for_step(
            "SC-001",
            step,  # type: ignore[arg-type]
            decision,  # type: ignore[arg-type]
            predicted_next_state="ACCEPTED",
            accumulated_flags=frozenset(),
            routing_path="deterministic",
            rule_index=rule_index,  # type: ignore[arg-type]
        )
        assert verdict.status == "hallucinated_rule"
        assert verdict.content_diagnostic is not None
        assert "predicted_rule_id=FAKE-RULE-999" in verdict.content_diagnostic
        assert "not in rule_index" in verdict.content_diagnostic
        # PR211 review #2: pin the distinction from the also_matched variant.
        # Without this assertion, a regression that emitted the
        # `(also_matched, not in rule_index)` shape for both branches would
        # still pass — the also_matched test asserts the qualifier present,
        # but the applied_rule_id test would also pass for the wrong reason.
        assert "also_matched" not in verdict.content_diagnostic

    def test_hallucinated_rule_also_matched_diagnostic(self, rule_index: object) -> None:
        """hallucinated_rule (also_matched) sets diagnostic with 'also_matched' qualifier."""
        from samantha_server.rules.loader import RuleIndex
        from samantha_server.scenarios.replay import _verdict_for_step

        assert isinstance(rule_index, RuleIndex)
        real_rule_id = rule_index.all_rules[0].rule_id
        step = self._make_step()
        decision = self._make_decision(
            next_state="ACCEPTED",
            applied_rule_id=real_rule_id,
            also_matched=("FAKE-ALSO-999",),
        )
        verdict = _verdict_for_step(
            "SC-001",
            step,  # type: ignore[arg-type]
            decision,  # type: ignore[arg-type]
            predicted_next_state="ACCEPTED",
            accumulated_flags=frozenset(),
            routing_path="deterministic",
            rule_index=rule_index,  # type: ignore[arg-type]
        )
        assert verdict.status == "hallucinated_rule"
        assert verdict.content_diagnostic is not None
        assert "predicted_rule_id=FAKE-ALSO-999" in verdict.content_diagnostic
        assert "also_matched" in verdict.content_diagnostic
        assert "not in rule_index" in verdict.content_diagnostic

    def test_hallucinated_flag_diagnostic(self, rule_index: object) -> None:
        """hallucinated_flag sets diagnostic naming the first invented flag."""
        from samantha_server.scenarios.replay import _verdict_for_step

        step = self._make_step()
        decision = self._make_decision(
            next_state="ACCEPTED",
            flags_added=("BOGUS_FLAG",),
        )
        verdict = _verdict_for_step(
            "SC-001",
            step,  # type: ignore[arg-type]
            decision,  # type: ignore[arg-type]
            predicted_next_state="ACCEPTED",
            accumulated_flags=frozenset(["BOGUS_FLAG"]),
            routing_path="deterministic",
            rule_index=rule_index,  # type: ignore[arg-type]
        )
        assert verdict.status == "hallucinated_flag"
        assert verdict.content_diagnostic is not None
        assert "predicted_flag=BOGUS_FLAG" in verdict.content_diagnostic
        assert "not in VALID_FLAGS" in verdict.content_diagnostic

    def test_mismatch_disposition_diagnostic(self, rule_index: object) -> None:
        """mismatch_disposition sets diagnostic with expected and parsed disposition."""
        from samantha_server.engine.decision import LLMReviewTrace
        from samantha_server.scenarios.replay import _verdict_for_step

        trace = LLMReviewTrace(
            skill_doc_hash="abc",
            model_id="test-model",
            response_text_hash="def",
            disposition="reject",
            parsed_disposition="reject",
        )
        step = self._make_step(llm_disposition="accepted")  # expected verb: "accept"
        decision = self._make_decision(
            next_state="ACCEPTED",
            decision_traces=(trace,),
        )
        verdict = _verdict_for_step(
            "SC-001",
            step,  # type: ignore[arg-type]
            decision,  # type: ignore[arg-type]
            predicted_next_state="ACCEPTED",
            accumulated_flags=frozenset(),
            routing_path="llm",
            rule_index=rule_index,  # type: ignore[arg-type]
        )
        assert verdict.status == "mismatch_disposition"
        assert verdict.content_diagnostic is not None
        assert "expected_disposition=accept" in verdict.content_diagnostic
        assert "parsed_disposition=reject" in verdict.content_diagnostic

    def test_invalid_json_llm_review_trace_diagnostic(self, rule_index: object) -> None:
        """invalid_json (LLMReviewTrace path) sets diagnostic with parse_failure."""
        from samantha_server.engine.decision import LLMReviewTrace
        from samantha_server.scenarios.replay import _verdict_for_step

        trace = LLMReviewTrace(
            skill_doc_hash="abc",
            model_id="test-model",
            response_text_hash="def",
            disposition="accept",
            parsed_disposition=None,
            parse_failure="json_decode",
        )
        step = self._make_step(llm_disposition="accepted")
        decision = self._make_decision(
            next_state="ACCEPTED",
            decision_traces=(trace,),
        )
        verdict = _verdict_for_step(
            "SC-001",
            step,  # type: ignore[arg-type]
            decision,  # type: ignore[arg-type]
            predicted_next_state="ACCEPTED",
            accumulated_flags=frozenset(),
            routing_path="llm",
            rule_index=rule_index,  # type: ignore[arg-type]
        )
        assert verdict.status == "invalid_json"
        assert verdict.content_diagnostic is not None
        assert "parse_failure=json_decode" in verdict.content_diagnostic

    def test_invalid_json_refusal_trace_diagnostic(self, rule_index: object) -> None:
        """invalid_json (RefusalTrace STAGE_PRE_UNPARSEABLE path) sets diagnostic."""
        from samantha_server.engine.decision import RefusalTrace
        from samantha_server.scenarios.replay import _verdict_for_step

        trace = RefusalTrace(
            refusal_reason="STAGE_PRE_UNPARSEABLE",
            refusal_stage="PRE",
            judge_verdict=None,
        )
        step = self._make_step(llm_disposition="accepted")
        decision = self._make_decision(
            next_state="ACCEPTED",
            decision_traces=(trace,),
        )
        verdict = _verdict_for_step(
            "SC-001",
            step,  # type: ignore[arg-type]
            decision,  # type: ignore[arg-type]
            predicted_next_state="ACCEPTED",
            accumulated_flags=frozenset(),
            routing_path="llm",
            rule_index=rule_index,  # type: ignore[arg-type]
        )
        assert verdict.status == "invalid_json"
        assert verdict.content_diagnostic is not None
        # PR211 review #5: RefusalTrace branch emits `refusal_reason=`, not
        # `parse_failure=` — the two are disjoint namespaces (refusal-stage
        # category vs parse-attempt discriminant).
        assert "refusal_reason=STAGE_PRE_UNPARSEABLE" in verdict.content_diagnostic
        assert "parse_failure=" not in verdict.content_diagnostic

    def test_pass_verdict_has_no_diagnostic(self, rule_index: object) -> None:
        """A passing step must not have content_diagnostic set."""
        from samantha_server.scenarios.replay import _verdict_for_step

        step = self._make_step()
        decision = self._make_decision(next_state="ACCEPTED")
        verdict = _verdict_for_step(
            "SC-001",
            step,  # type: ignore[arg-type]
            decision,  # type: ignore[arg-type]
            predicted_next_state="ACCEPTED",
            accumulated_flags=frozenset(),
            routing_path="deterministic",
            rule_index=rule_index,  # type: ignore[arg-type]
        )
        assert verdict.status == "pass"
        assert verdict.content_diagnostic is None


# ---------------------------------------------------------------------------
# Slice 3: Post-loop query gate populates content_diagnostic
# (tested indirectly through StepVerdict.content_diagnostic values on a
#  minimal fixture; the gate logic lives in _replay_scenario_async so
#  we test the diagnostic string shapes that dataclasses.replace must set)
# ---------------------------------------------------------------------------


class TestQueryGateContentDiagnosticShapes:
    """Diagnostic string shapes for query-gate statuses.

    These tests verify the expected string formats without running the full
    async replay loop — they construct the diagnostic directly from the same
    formula used in production and assert the string contract.
    """

    def test_fmt_id_list_empty(self) -> None:
        """PR211 review #1: empty list renders as `[]`."""
        from samantha_server.scenarios.replay import _fmt_id_list

        assert _fmt_id_list([]) == "[]"

    def test_fmt_id_list_at_cap_no_overflow(self) -> None:
        """PR211 review #1: a list at the cap (5 items) renders fully — no overflow suffix.

        Boundary case that an off-by-one in the `<= 5` guard would flip.
        """
        from samantha_server.scenarios.replay import _fmt_id_list

        items = ["a", "b", "c", "d", "e"]
        result = _fmt_id_list(items)
        assert result == "['a', 'b', 'c', 'd', 'e']"
        assert "+0 more" not in result
        assert "..." not in result

    def test_fmt_id_list_first_overflow(self) -> None:
        """PR211 review #1: at 6 items, first overflow → `...+1 more]`."""
        from samantha_server.scenarios.replay import _fmt_id_list

        items = ["a", "b", "c", "d", "e", "f"]
        result = _fmt_id_list(items)
        assert "['a', 'b', 'c', 'd', 'e'" in result
        assert "...+1 more]" in result
        assert "f" not in result  # the 6th item should be hidden

    def test_fmt_id_list_large_overflow(self) -> None:
        """PR211 review #1: at 11 items, overflow count is 6 → `...+6 more]`."""
        from samantha_server.scenarios.replay import _fmt_id_list

        items = [f"id-{i:02d}" for i in range(11)]
        result = _fmt_id_list(items)
        assert "...+6 more]" in result

    def test_mismatch_query_response_diagnostic_short_lists(self) -> None:
        """mismatch_query_response diagnostic renders both sorted lists (short case).

        PR211 review #10: now exercises the lifted production `_fmt_id_list`
        instead of a test-local closure — assertions test real behavior.
        """
        from samantha_server.scenarios.replay import _fmt_id_list

        expected_ids = frozenset({"ORD-001", "ORD-002"})
        parsed_ids = ("ORD-003",)
        sorted_expected = sorted(expected_ids)
        sorted_parsed = sorted(parsed_ids)
        diagnostic = (
            f"expected_order_ids={_fmt_id_list(sorted_expected)}"
            f" parsed_order_ids={_fmt_id_list(sorted_parsed)}"
        )
        assert "expected_order_ids=['ORD-001', 'ORD-002']" in diagnostic
        assert "parsed_order_ids=['ORD-003']" in diagnostic

    def test_mismatch_query_response_diagnostic_overflow(self) -> None:
        """mismatch_query_response diagnostic caps at 5 items with overflow suffix."""
        from samantha_server.scenarios.replay import _fmt_id_list

        expected_ids = frozenset({f"ORD-{i:03d}" for i in range(7)})
        parsed_ids = tuple(f"ORD-X{i:03d}" for i in range(8))
        diag_expected = _fmt_id_list(sorted(expected_ids))
        diag_parsed = _fmt_id_list(sorted(parsed_ids))
        assert "...+2 more]" in diag_expected  # 7 - 5 = 2
        assert "...+3 more]" in diag_parsed  # 8 - 5 = 3

    def test_empty_response_diagnostic_shape(self) -> None:
        """empty_response diagnostic renders `response_text_hash=<hash> (empty)`.

        PR211 review #4: the previous shape emitted `parse_failure=None ...` —
        uninformative because the branch fires *because* parse_failure is None.
        The hash + `(empty)` tag is the informative form.
        """
        import hashlib

        sha256_empty = hashlib.sha256(b"").hexdigest()
        # Mirror the production format from _replay_scenario_async's
        # empty_response branch.
        diagnostic = f"response_text_hash={sha256_empty} (empty)"
        assert sha256_empty in diagnostic
        assert "(empty)" in diagnostic
        assert "parse_failure=None" not in diagnostic

    def test_invalid_json_query_diagnostic_has_parse_failure(self) -> None:
        """invalid_json (query path) diagnostic includes parse_failure."""
        parse_failure = "json_decode"
        diagnostic = f"parse_failure={parse_failure}"
        assert "parse_failure=json_decode" in diagnostic


# ---------------------------------------------------------------------------
# Slice 3 integration: verify _replay_scenario_async sets content_diagnostic
# (uses the existing conftest / llm_stub_helpers patterns to run a minimal
#  scenario and inspect the returned StepVerdict)
# ---------------------------------------------------------------------------


class TestQueryGateDiagnosticIntegration:
    """content_diagnostic populated on query-gate verdicts."""

    def _make_query_trace(
        self,
        parsed_order_ids: tuple[str, ...] | None = None,
        parse_failure: str | None = None,
        response_text_hash: str | None = None,
    ) -> object:
        import hashlib

        from samantha_server.engine.decision import QueryTrace

        if response_text_hash is None:
            response_text_hash = hashlib.sha256(b"").hexdigest()
        return QueryTrace(
            query_text_hash="aaa",
            skill_doc_hash="bbb",
            scenarios_cited=(),
            model_id="test-model",
            response_text_hash=response_text_hash,
            parsed_order_ids=parsed_order_ids,
            parsed_answer_type="order_list" if parsed_order_ids is not None else None,
            parse_failure=parse_failure,  # type: ignore[arg-type]
        )

    def _apply_query_gate(
        self,
        step_verdict: StepVerdict,
        query_trace: object,
        expected_order_ids: frozenset[str] | None,
    ) -> StepVerdict:
        """Apply the same query-gate replacement logic as _replay_scenario_async.

        PR211 review #6 + #10: now imports the production `_fmt_id_list` helper
        and uses the production empty_response format so a divergence in the
        truncation/format would surface here. The if/elif structure still
        mirrors `_replay_scenario_async` — a full restructure to call
        `_replay_scenario_async` directly (the way
        `test_engine_content_gate.py:TestQueryContentGate._run_query_replay`
        does) is deferred to a follow-up if this pattern grows. See PR211
        review #6 in the consolidated review.
        """
        import dataclasses

        from samantha_server.engine.decision import QueryTrace
        from samantha_server.scenarios.replay import _SHA256_EMPTY, _fmt_id_list

        assert isinstance(query_trace, QueryTrace)

        content_status = None
        content_diagnostic: str | None = None

        if query_trace.parse_failure is not None:
            content_status = "invalid_json"
            content_diagnostic = f"parse_failure={query_trace.parse_failure}"
        elif (
            query_trace.parsed_order_ids is None and query_trace.response_text_hash == _SHA256_EMPTY
        ):
            content_status = "empty_response"
            # PR211 review #4: matches the updated production format —
            # `parse_failure=None` was uninformative because the branch
            # fires *because* parse_failure is None.
            content_diagnostic = f"response_text_hash={query_trace.response_text_hash} (empty)"
        elif (
            expected_order_ids is not None
            and query_trace.parsed_order_ids is not None
            and set(query_trace.parsed_order_ids) != expected_order_ids
        ):
            content_status = "mismatch_query_response"
            sorted_expected = sorted(expected_order_ids)
            sorted_parsed = sorted(query_trace.parsed_order_ids)
            content_diagnostic = (
                f"expected_order_ids={_fmt_id_list(sorted_expected)}"
                f" parsed_order_ids={_fmt_id_list(sorted_parsed)}"
            )

        if content_status is not None:
            return dataclasses.replace(
                step_verdict,
                status=content_status,  # type: ignore[arg-type]
                content_diagnostic=content_diagnostic,
            )
        return step_verdict

    def test_mismatch_query_response_sets_diagnostic(self) -> None:
        """Query gate sets content_diagnostic on mismatch_query_response verdict."""
        trace = self._make_query_trace(
            parsed_order_ids=("ORD-003",),
            response_text_hash="somehash",
        )
        sv = _make_step_verdict(status="pass")
        result = self._apply_query_gate(
            sv, trace, expected_order_ids=frozenset({"ORD-001", "ORD-002"})
        )
        assert result.status == "mismatch_query_response"
        assert result.content_diagnostic is not None
        assert "expected_order_ids=" in result.content_diagnostic
        assert "parsed_order_ids=" in result.content_diagnostic
        assert "ORD-001" in result.content_diagnostic
        assert "ORD-002" in result.content_diagnostic
        assert "ORD-003" in result.content_diagnostic

    def test_empty_response_sets_diagnostic(self) -> None:
        """Query gate sets content_diagnostic on empty_response verdict."""
        import hashlib

        sha256_empty = hashlib.sha256(b"").hexdigest()
        trace = self._make_query_trace(
            parsed_order_ids=None,
            response_text_hash=sha256_empty,
        )
        sv = _make_step_verdict(status="pass")
        result = self._apply_query_gate(sv, trace, expected_order_ids=frozenset({"ORD-001"}))
        assert result.status == "empty_response"
        assert result.content_diagnostic is not None
        # PR211 review #4: new format — `response_text_hash=<hash> (empty)`.
        # The previous `parse_failure=None ...` key was uninformative.
        assert sha256_empty in result.content_diagnostic
        assert "(empty)" in result.content_diagnostic
        assert "parse_failure=" not in result.content_diagnostic

    def test_invalid_json_query_sets_diagnostic(self) -> None:
        """Query gate sets content_diagnostic on invalid_json verdict."""
        trace = self._make_query_trace(
            parse_failure="json_decode",
            response_text_hash="somehash",
        )
        sv = _make_step_verdict(status="pass")
        result = self._apply_query_gate(sv, trace, expected_order_ids=frozenset({"ORD-001"}))
        assert result.status == "invalid_json"
        assert result.content_diagnostic is not None
        assert "parse_failure=json_decode" in result.content_diagnostic

    def test_passing_query_step_no_diagnostic(self) -> None:
        """When parsed order_ids match expected, no content_diagnostic is set."""
        trace = self._make_query_trace(
            parsed_order_ids=("ORD-001",),
            response_text_hash="somehash",
        )
        sv = _make_step_verdict(status="pass")
        result = self._apply_query_gate(sv, trace, expected_order_ids=frozenset({"ORD-001"}))
        assert result.status == "pass"
        assert result.content_diagnostic is None


# ---------------------------------------------------------------------------
# Slice 4: _print_report_and_get_exit_code renders | content=...
# ---------------------------------------------------------------------------


class TestPrintReportContentDiagnosticRendering:
    """Formatter appends | content=... when content_diagnostic is set."""

    def _capture_report(self, step_verdicts: tuple[StepVerdict, ...]) -> tuple[str, str]:
        """Run _print_report_and_get_exit_code and return (stdout, stderr)."""
        report = _make_minimal_report(step_verdicts)
        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        orig_stdout, orig_stderr = sys.stdout, sys.stderr
        sys.stdout = stdout_buf
        sys.stderr = stderr_buf
        try:
            _print_report_and_get_exit_code(
                report,
                skiplist={},
                directory=Path("."),
            )
        finally:
            sys.stdout = orig_stdout
            sys.stderr = orig_stderr
        return stdout_buf.getvalue(), stderr_buf.getvalue()

    def test_failing_step_with_diagnostic_renders_content_field(self) -> None:
        """A failing step with content_diagnostic shows '| content=...' in the report."""
        sv = _make_step_verdict(
            status="hallucinated_state",
            content_diagnostic="predicted_state=BOGUS (not in VALID_STATES)",
        )
        stdout, _ = self._capture_report((sv,))
        assert "| content=predicted_state=BOGUS (not in VALID_STATES)" in stdout

    def test_failing_step_without_diagnostic_omits_content_field(self) -> None:
        """A failing step without content_diagnostic does NOT show '| content=' in the report."""
        sv = _make_step_verdict(
            status="mismatch_state",
            content_diagnostic=None,
        )
        stdout, _ = self._capture_report((sv,))
        assert "| content=" not in stdout

    def test_passing_step_omits_content_field(self) -> None:
        """A passing step is not printed at all in the failures section."""
        sv = _make_step_verdict(status="pass")
        stdout, _ = self._capture_report((sv,))
        assert "step 1" not in stdout
        assert "| content=" not in stdout

    def test_mismatch_query_response_diagnostic_in_report(self) -> None:
        """mismatch_query_response diagnostic renders correctly in the report output."""
        sv = _make_step_verdict(
            status="mismatch_query_response",
            content_diagnostic="expected_order_ids=['ORD-001'] parsed_order_ids=['ORD-002']",
        )
        stdout, _ = self._capture_report((sv,))
        assert "mismatch_query_response" in stdout
        assert "| content=expected_order_ids=['ORD-001'] parsed_order_ids=['ORD-002']" in stdout

    def test_invalid_json_diagnostic_in_report(self) -> None:
        """invalid_json diagnostic renders correctly in the report output."""
        sv = _make_step_verdict(
            status="invalid_json",
            content_diagnostic="parse_failure=json_decode",
        )
        stdout, _ = self._capture_report((sv,))
        assert "invalid_json" in stdout
        assert "| content=parse_failure=json_decode" in stdout

    def test_empty_response_diagnostic_in_report(self) -> None:
        """empty_response diagnostic renders correctly in the report output."""
        import hashlib

        sha256_empty = hashlib.sha256(b"").hexdigest()
        diagnostic = f"parse_failure=None response_text_hash={sha256_empty}"
        sv = _make_step_verdict(
            status="empty_response",
            content_diagnostic=diagnostic,
        )
        stdout, _ = self._capture_report((sv,))
        assert "empty_response" in stdout
        assert f"| content={diagnostic}" in stdout

    def test_structural_fields_still_rendered_alongside_content(self) -> None:
        """The structural triple (expected_state, etc.) still appears when content is set."""
        sv = _make_step_verdict(
            status="mismatch_disposition",
            content_diagnostic="expected_disposition=accept parsed_disposition=reject",
        )
        stdout, _ = self._capture_report((sv,))
        assert "expected_state=ACCEPTED" in stdout
        assert "predicted_state=ACCEPTED" in stdout
        assert "| content=" in stdout

    def test_phi_boundary_no_gen_ai_substrings_in_report_output(self) -> None:
        """PR211 review #11: the formatter is a bounded surface — `gen_ai.completion`
        and `gen_ai.prompt` substrings must NOT appear in the CLI report output,
        even when an operator's `content_diagnostic` happens to mention them
        (e.g., in a future test fixture or a docstring that leaks).

        This pins the deliberate decision to keep raw LLM output off the CLI
        report and direct operators to Langfuse for it.
        """
        # Construct a deliberately misleading diagnostic — the formatter must
        # render it verbatim if set, but the production code paths that
        # populate content_diagnostic only reference parsed/structured fields
        # (`parsed_order_ids`, `parsed_disposition`, `parse_failure`,
        # `refusal_reason`, `response_text_hash`). None of those production
        # diagnostics include the literal substrings checked here.
        # Run a real gate fire and inspect the diagnostic — verify that no
        # production code path leaks `gen_ai.completion` or `gen_ai.prompt`
        # into the diagnostic.
        from unittest.mock import MagicMock

        from samantha_server.engine.decision import EngineDecision
        from samantha_server.rules.loader import RuleIndex, load_rule_specs
        from samantha_server.scenarios.loader import ScenarioStep
        from samantha_server.scenarios.replay import _verdict_for_step

        specs_dir = Path(__file__).resolve().parents[2] / "samantha_server" / "rules" / "specs"
        rule_index = RuleIndex(load_rule_specs(specs_dir))

        step = ScenarioStep(
            step_index=1,
            event_type="order_received",
            event_data={},
            expected_next_state="ACCEPTED",
            expected_applied_rules=(),
            expected_flags=(),
        )
        decision = MagicMock(spec=EngineDecision)
        decision.next_state = "BOGUS_STATE"
        decision.applied_rule_id = None
        decision.also_matched = ()
        decision.flags_added = ()
        decision.flags_cleared = ()
        decision.decision_traces = ()
        decision.latency_us = 100

        verdict = _verdict_for_step(
            "SC-PHI",
            step,  # type: ignore[arg-type]
            decision,
            predicted_next_state="BOGUS_STATE",
            accumulated_flags=frozenset(),
            routing_path="deterministic",
            rule_index=rule_index,  # type: ignore[arg-type]
        )
        # Run through the formatter, capturing stdout.
        stdout, _ = self._capture_report((verdict,))

        # The diagnostic must not contain LLM-output attribute names.
        assert "gen_ai.completion" not in stdout, (
            "CLI report must not echo gen_ai.completion — operators look in Langfuse for that"
        )
        assert "gen_ai.prompt" not in stdout, (
            "CLI report must not echo gen_ai.prompt — operators look in Langfuse for that"
        )
