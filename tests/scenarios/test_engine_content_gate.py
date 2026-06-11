"""Engine content gate — LLM-content correctness counts toward included_accuracy.

Tests are organized by slice:
  Slice 1: StepVerdict.status Literal extension.
  Slice 2: Scenario.expected_query_content accessor.
  Slice 3: Hallucination checks in _verdict_for_step.
  Slice 4: mismatch_disposition / invalid_json for llm_review steps.
  Slice 5: mismatch_query_response / invalid_json / empty_response for query steps.
"""

from __future__ import annotations

from typing import get_args

import pytest

from samantha_server.scenarios.replay import (
    ExpectedOutput,
    PredictedOutput,
    StepVerdict,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_expected() -> ExpectedOutput:
    return ExpectedOutput(next_state="ACCEPTED", applied_rules=(), flags=())


def _minimal_predicted() -> PredictedOutput:
    return PredictedOutput(next_state="ACCEPTED", applied_rules=(), flags=())


def _make_step_verdict(status: str) -> StepVerdict:
    return StepVerdict(
        scenario_id="SC-TEST",
        step_index=1,
        status=status,  # type: ignore[arg-type]
        expected=_minimal_expected(),
        predicted=_minimal_predicted(),
    )


# ---------------------------------------------------------------------------
# Slice 1: StepVerdict.status Literal extension
# ---------------------------------------------------------------------------


class TestStepVerdictStatusLiteral:
    """New status values accepted by StepVerdict."""

    @pytest.mark.parametrize(
        "status",
        [
            "mismatch_query_response",
            "mismatch_disposition",
            "invalid_json",
            "empty_response",
            "hallucinated_rule",
            "hallucinated_flag",
            "hallucinated_state",
        ],
    )
    def test_new_status_accepted_by_step_verdict(self, status: str) -> None:
        """Each new status value must appear in the StepVerdict.status Literal."""
        import typing

        hints = typing.get_type_hints(StepVerdict)
        literal_args = get_args(hints["status"])
        assert status in literal_args, (
            f"{status!r} missing from StepVerdict.status Literal — add it to replay.py"
        )

    @pytest.mark.parametrize(
        "status",
        [
            "mismatch_query_response",
            "mismatch_disposition",
            "invalid_json",
            "empty_response",
            "hallucinated_rule",
            "hallucinated_flag",
            "hallucinated_state",
        ],
    )
    def test_new_status_constructable(self, status: str) -> None:
        """StepVerdict can be constructed with each new status without TypeError."""
        sv = _make_step_verdict(status)
        assert sv.status == status


# ---------------------------------------------------------------------------
# Slice 2: Scenario.expected_query_content accessor
# ---------------------------------------------------------------------------


class TestScenarioExpectedQueryContent:
    """Scenario.expected_query_content typed accessor."""

    def test_returns_frozenset_when_order_ids_present(self) -> None:
        """When raw_expected_output has a list of strings, returns frozenset[str]."""
        from samantha_server.scenarios.loader import Scenario

        scenario = Scenario(
            scenario_id="QRY-001",
            category="query",
            description="query with expected order_ids",
            steps=(),
            raw_expected_output={"order_ids": ["ORD-001", "ORD-002"], "answer_type": "order_list"},
        )
        result = scenario.expected_query_content
        assert result == frozenset({"ORD-001", "ORD-002"})

    def test_returns_none_when_raw_expected_output_absent(self) -> None:
        """When raw_expected_output is None, returns None."""
        from samantha_server.scenarios.loader import Scenario

        scenario = Scenario(
            scenario_id="QRY-002",
            category="query",
            description="query with no expected output",
            steps=(),
            raw_expected_output=None,
        )
        assert scenario.expected_query_content is None

    def test_returns_empty_frozenset_when_order_ids_is_empty_list(self) -> None:
        """PR205 Low #15: an explicit empty `order_ids: []` is semantically
        different from `raw_expected_output is None`. Empty-list means the
        fixture asserts "no orders satisfy this query" — the accessor must
        return `frozenset()`, not None.
        """
        from samantha_server.scenarios.loader import Scenario

        scenario = Scenario(
            scenario_id="QRY-EMPTY-LIST",
            category="query",
            description="empty expected order_ids list",
            steps=(),
            raw_expected_output={"order_ids": []},
        )
        result = scenario.expected_query_content
        assert result == frozenset(), f"expected empty frozenset; got {result!r}"
        # And explicitly: not None — None would mean "no expectation at all".
        assert result is not None

    def test_returns_none_and_warns_on_malformed_order_ids(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Malformed order_ids (not a list of strings) returns None with a warning."""
        import logging

        from samantha_server.scenarios.loader import Scenario

        scenario = Scenario(
            scenario_id="QRY-003",
            category="query",
            description="query with malformed expected_output",
            steps=(),
            # order_ids is not a list of strings — should trigger the warning branch
            raw_expected_output={"order_ids": "not-a-list"},
        )
        with caplog.at_level(logging.WARNING):
            result = scenario.expected_query_content
        assert result is None
        assert any("QRY-003" in r.message for r in caplog.records), (
            "Expected a warning log containing the scenario_id"
        )

    # ---------------------------------------------------------------------------
    # Cluster D (#6): Scenario.expected_answer_type typed accessor
    # ---------------------------------------------------------------------------

    def test_scenario_expected_answer_type_returns_order_status(self) -> None:
        """Cluster D: expected_answer_type returns 'order_status' for a matching fixture."""
        from samantha_server.scenarios.loader import Scenario

        scenario = Scenario(
            scenario_id="QRY-ANS-001",
            category="query",
            description="order_status fixture",
            steps=(),
            raw_expected_output={"answer_type": "order_status", "order_ids": ["ORD-901"]},
        )
        assert scenario.expected_answer_type == "order_status"

    def test_scenario_expected_answer_type_returns_none_when_absent(self) -> None:
        """Cluster D: expected_answer_type returns None when raw_expected_output is None."""
        from samantha_server.scenarios.loader import Scenario

        scenario = Scenario(
            scenario_id="QRY-ANS-002",
            category="query",
            description="no expected output",
            steps=(),
            raw_expected_output=None,
        )
        assert scenario.expected_answer_type is None

    def test_scenario_expected_answer_type_returns_none_for_unknown_string(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Cluster D: expected_answer_type returns None and logs WARNING for unrecognized value."""
        import logging

        from samantha_server.scenarios.loader import Scenario

        scenario = Scenario(
            scenario_id="QRY-ANS-003",
            category="query",
            description="unknown answer_type",
            steps=(),
            raw_expected_output={"answer_type": "completely_unknown_type", "order_ids": []},
        )
        with caplog.at_level(logging.WARNING, logger="samantha_server.scenarios.loader"):
            result = scenario.expected_answer_type
        assert result is None
        warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("completely_unknown_type" in msg for msg in warning_messages), (
            f"Expected WARNING naming unrecognized answer_type; got: {warning_messages!r}"
        )


# ---------------------------------------------------------------------------
# Slice 3: Hallucination checks in _verdict_for_step
# ---------------------------------------------------------------------------


class TestVerdictForStepHallucinationChecks:
    """hallucinated_state / hallucinated_rule / hallucinated_flag."""

    @pytest.fixture()
    def rule_index(self) -> object:
        from pathlib import Path

        from samantha_server.rules.loader import RuleIndex, load_rule_specs

        specs_dir = Path(__file__).resolve().parents[2] / "samantha_server" / "rules" / "specs"
        return RuleIndex(load_rule_specs(specs_dir))

    def _make_step(
        self,
        expected_next_state: str = "ACCEPTED",
        expected_applied_rules: tuple[str, ...] = (),
        expected_flags: tuple[str, ...] = (),
    ) -> object:
        from samantha_server.scenarios.loader import ScenarioStep

        return ScenarioStep(
            step_index=1,
            event_type="order_received",
            event_data={},
            expected_next_state=expected_next_state,
            expected_applied_rules=expected_applied_rules,
            expected_flags=expected_flags,
        )

    def _make_decision(
        self,
        next_state: str = "ACCEPTED",
        applied_rule_id: str | None = None,
        flags_added: tuple[str, ...] = (),
        flags_cleared: tuple[str, ...] = (),
        also_matched: tuple[str, ...] = (),
        latency_us: int = 100,
    ) -> object:
        from unittest.mock import MagicMock

        from samantha_server.engine.decision import EngineDecision

        d = MagicMock(spec=EngineDecision)
        d.next_state = next_state
        d.applied_rule_id = applied_rule_id
        d.flags_added = flags_added
        d.flags_cleared = flags_cleared
        d.also_matched = also_matched
        d.latency_us = latency_us
        d.decision_traces = ()
        return d

    def test_hallucinated_state_takes_highest_precedence(self, rule_index: object) -> None:
        """next_state not in VALID_STATES → hallucinated_state (beats all other checks)."""
        from samantha_server.scenarios.replay import _verdict_for_step

        step = self._make_step(expected_next_state="ACCEPTED")
        decision = self._make_decision(next_state="INVENTED_STATE")
        verdict = _verdict_for_step(
            "SC-TEST",
            step,  # type: ignore[arg-type]
            decision,  # type: ignore[arg-type]
            "INVENTED_STATE",
            frozenset(),
            "llm",
            rule_index=rule_index,  # type: ignore[arg-type]
        )
        assert verdict.status == "hallucinated_state"

    def test_hallucinated_rule_when_rule_id_not_in_index(self, rule_index: object) -> None:
        """applied_rule_id not in rule_index → hallucinated_rule."""
        from samantha_server.scenarios.replay import _verdict_for_step

        step = self._make_step(expected_next_state="ACCEPTED", expected_applied_rules=("ACC-008",))
        # Use a real state but fake rule id
        decision = self._make_decision(next_state="ACCEPTED", applied_rule_id="INVENTED-RULE-999")
        verdict = _verdict_for_step(
            "SC-TEST",
            step,  # type: ignore[arg-type]
            decision,  # type: ignore[arg-type]
            "ACCEPTED",
            frozenset(),
            "llm",
            rule_index=rule_index,  # type: ignore[arg-type]
        )
        assert verdict.status == "hallucinated_rule"

    def test_hallucinated_flag_when_flag_not_in_valid_flags(self, rule_index: object) -> None:
        """flag in flags_added not in VALID_FLAGS → hallucinated_flag."""
        from samantha_server.scenarios.replay import _verdict_for_step

        step = self._make_step(expected_next_state="ACCEPTED")
        decision = self._make_decision(next_state="ACCEPTED", flags_added=("INVENTED_FLAG_XYZ",))
        verdict = _verdict_for_step(
            "SC-TEST",
            step,  # type: ignore[arg-type]
            decision,  # type: ignore[arg-type]
            "ACCEPTED",
            frozenset(),
            "llm",
            rule_index=rule_index,  # type: ignore[arg-type]
        )
        assert verdict.status == "hallucinated_flag"

    def test_hallucinated_state_beats_hallucinated_rule(self, rule_index: object) -> None:
        """hallucinated_state takes precedence over hallucinated_rule."""
        from samantha_server.scenarios.replay import _verdict_for_step

        step = self._make_step()
        decision = self._make_decision(
            next_state="INVENTED_STATE", applied_rule_id="INVENTED-RULE-999"
        )
        verdict = _verdict_for_step(
            "SC-TEST",
            step,  # type: ignore[arg-type]
            decision,  # type: ignore[arg-type]
            "INVENTED_STATE",
            frozenset(),
            "llm",
            rule_index=rule_index,  # type: ignore[arg-type]
        )
        assert verdict.status == "hallucinated_state"

    def test_no_hallucination_for_valid_state_and_known_rule(self, rule_index: object) -> None:
        """Valid state + known rule_id → no hallucination (falls through to structural check)."""
        from samantha_server.scenarios.replay import _verdict_for_step

        step = self._make_step(expected_next_state="ACCEPTED", expected_applied_rules=("ACC-008",))
        decision = self._make_decision(next_state="ACCEPTED", applied_rule_id="ACC-008")
        verdict = _verdict_for_step(
            "SC-TEST",
            step,  # type: ignore[arg-type]
            decision,  # type: ignore[arg-type]
            "ACCEPTED",
            frozenset(),
            "llm",
            rule_index=rule_index,  # type: ignore[arg-type]
        )
        assert verdict.status == "pass"

    @pytest.mark.parametrize(
        "symbolic_token",
        ["ADVANCE_SAMPLE_PREP", "RESOLVE_MISSING_INFO", "RETRY_SAMPLE_PREP"],
    )
    def test_symbolic_tokens_are_not_hallucinated_state(
        self, rule_index: object, symbolic_token: str
    ) -> None:
        """PR205 review #7a: symbolic macro tokens in `_SYMBOLIC_TOKENS` must NOT
        trip `hallucinated_state`. Dropping the allowlist would regress every
        symbolic-token-emitting rule; this test pins the contract.

        The verdict status will be `mismatch_state` (expected ACCEPTED but
        engine returned a symbolic token) — the key invariant is "NOT
        hallucinated_state".
        """
        from samantha_server.scenarios.replay import _verdict_for_step

        step = self._make_step(expected_next_state="ACCEPTED")
        decision = self._make_decision(next_state=symbolic_token)
        verdict = _verdict_for_step(
            "SC-TEST",
            step,  # type: ignore[arg-type]
            decision,  # type: ignore[arg-type]
            "ACCEPTED",  # predicted_next_state already resolved
            frozenset(),
            "deterministic",
            rule_index=rule_index,  # type: ignore[arg-type]
        )
        assert verdict.status != "hallucinated_state", (
            f"Symbolic token {symbolic_token!r} must NOT be flagged hallucinated_state — "
            f"_SYMBOLIC_TOKENS allowlist regressed?"
        )

    def test_hallucinated_rule_beats_hallucinated_flag(self, rule_index: object) -> None:
        """PR205 review #7b: when both an invented rule AND an invented flag are
        produced, `hallucinated_rule` wins (it is the earlier check in the ladder).
        Swapping the two check blocks must trip this test.
        """
        from samantha_server.scenarios.replay import _verdict_for_step

        step = self._make_step(expected_next_state="ACCEPTED")
        decision = self._make_decision(
            next_state="ACCEPTED",
            applied_rule_id="INVENTED-RULE-999",
            flags_added=("INVENTED_FLAG_XYZ",),
        )
        verdict = _verdict_for_step(
            "SC-TEST",
            step,  # type: ignore[arg-type]
            decision,  # type: ignore[arg-type]
            "ACCEPTED",
            frozenset(),
            "llm",
            rule_index=rule_index,  # type: ignore[arg-type]
        )
        assert verdict.status == "hallucinated_rule", (
            f"hallucinated_rule must take precedence over hallucinated_flag; got {verdict.status!r}"
        )


# ---------------------------------------------------------------------------
# Slice 4: mismatch_disposition / invalid_json for llm_review steps
# ---------------------------------------------------------------------------


class TestVerdictForStepLLMReview:
    """Content gate for llm_review routing_path steps."""

    @pytest.fixture()
    def rule_index(self) -> object:
        from pathlib import Path

        from samantha_server.rules.loader import RuleIndex, load_rule_specs

        specs_dir = Path(__file__).resolve().parents[2] / "samantha_server" / "rules" / "specs"
        return RuleIndex(load_rule_specs(specs_dir))

    def _make_step(
        self,
        expected_next_state: str = "ACCEPTED",
        llm_disposition: str | None = None,
    ) -> object:
        from samantha_server.scenarios.loader import ScenarioStep

        return ScenarioStep(
            step_index=1,
            event_type="order_received",
            event_data={},
            expected_next_state=expected_next_state,
            expected_applied_rules=(),
            expected_flags=(),
            llm_disposition=llm_disposition,
        )

    def _make_decision_with_llm_review_trace(
        self,
        next_state: str = "ACCEPTED",
        parsed_disposition: str | None = "accept",
        parse_failure: str | None = None,
    ) -> object:
        from unittest.mock import MagicMock

        from samantha_server.engine.decision import EngineDecision, LLMReviewTrace

        trace = LLMReviewTrace(
            skill_doc_hash="abc123",
            model_id="test-model",
            response_text_hash="def456",
            disposition="accept",
            parsed_disposition=parsed_disposition,  # type: ignore[arg-type]
            parse_failure=parse_failure,  # type: ignore[arg-type]
        )

        d = MagicMock(spec=EngineDecision)
        d.next_state = next_state
        d.applied_rule_id = None
        d.flags_added = ()
        d.flags_cleared = ()
        d.also_matched = ()
        d.latency_us = 100
        d.decision_traces = (trace,)
        return d

    def test_llm_review_matching_disposition_passes(self, rule_index: object) -> None:
        """llm_review step where parsed_disposition matches expected → pass."""
        from samantha_server.scenarios.replay import _verdict_for_step

        step = self._make_step(expected_next_state="ACCEPTED", llm_disposition="accepted")
        decision = self._make_decision_with_llm_review_trace(
            next_state="ACCEPTED", parsed_disposition="accept"
        )
        verdict = _verdict_for_step(
            "SC-TEST",
            step,  # type: ignore[arg-type]
            decision,  # type: ignore[arg-type]
            "ACCEPTED",
            frozenset(),
            "llm",
            rule_index=rule_index,  # type: ignore[arg-type]
        )
        assert verdict.status == "pass"

    def test_llm_review_disposition_mismatch(self, rule_index: object) -> None:
        """llm_review step where parsed_disposition != expected → mismatch_disposition."""
        from samantha_server.scenarios.replay import _verdict_for_step

        step = self._make_step(expected_next_state="ACCEPTED", llm_disposition="accepted")
        # parsed_disposition is "reject" but expected is "accepted" → "accept"
        decision = self._make_decision_with_llm_review_trace(
            next_state="ACCEPTED", parsed_disposition="reject"
        )
        verdict = _verdict_for_step(
            "SC-TEST",
            step,  # type: ignore[arg-type]
            decision,  # type: ignore[arg-type]
            "ACCEPTED",
            frozenset(),
            "llm",
            rule_index=rule_index,  # type: ignore[arg-type]
        )
        assert verdict.status == "mismatch_disposition"

    def test_llm_review_parse_failure_yields_invalid_json(self, rule_index: object) -> None:
        """llm_review step with parse_failure set → invalid_json (beats mismatch_disposition)."""
        from samantha_server.scenarios.replay import _verdict_for_step

        step = self._make_step(expected_next_state="ACCEPTED", llm_disposition="accepted")
        decision = self._make_decision_with_llm_review_trace(
            next_state="ACCEPTED",
            parsed_disposition=None,
            parse_failure="json_decode",
        )
        verdict = _verdict_for_step(
            "SC-TEST",
            step,  # type: ignore[arg-type]
            decision,  # type: ignore[arg-type]
            "ACCEPTED",
            frozenset(),
            "llm",
            rule_index=rule_index,  # type: ignore[arg-type]
        )
        assert verdict.status == "invalid_json"

    def test_llm_review_refusal_unparseable_yields_invalid_json(self, rule_index: object) -> None:
        """PR205 review #2: when engine emits RefusalTrace (STAGE_PRE_UNPARSEABLE)
        instead of LLMReviewTrace — the production path per the LLMReviewTrace
        docstring — the disposition gate must NOT silently no-op. JSON parse
        failure → invalid_json on the verdict, not a structural pass.
        """
        from unittest.mock import MagicMock

        from samantha_server.engine.decision import EngineDecision, RefusalTrace
        from samantha_server.scenarios.replay import _verdict_for_step

        step = self._make_step(expected_next_state="PENDING_LLM_REVIEW", llm_disposition="accepted")
        refusal = RefusalTrace(
            refusal_reason="STAGE_PRE_UNPARSEABLE",
            refusal_stage="PRE",
            judge_verdict=None,
            alternatives=(),
        )
        d = MagicMock(spec=EngineDecision)
        # Engine left state unchanged on refusal — structural state appears to match,
        # but the disposition was never actually parsed.
        d.next_state = "PENDING_LLM_REVIEW"
        d.applied_rule_id = None
        d.flags_added = ()
        d.flags_cleared = ()
        d.also_matched = ()
        d.latency_us = 100
        d.decision_traces = (refusal,)
        verdict = _verdict_for_step(
            "SC-TEST",
            step,  # type: ignore[arg-type]
            d,  # type: ignore[arg-type]
            "PENDING_LLM_REVIEW",
            frozenset(),
            "llm",
            rule_index=rule_index,  # type: ignore[arg-type]
        )
        assert verdict.status == "invalid_json", (
            f"RefusalTrace(STAGE_PRE_UNPARSEABLE) on an llm_review step with expected "
            f"disposition must yield invalid_json; got {verdict.status!r}"
        )

    def test_llm_review_refusal_operational_does_not_yield_invalid_json(
        self, rule_index: object
    ) -> None:
        """PR205 review #2: operational refusals (skill/LLM unavailable, PHI boundary)
        are NOT JSON parse failures. They should fall through to the structural
        ladder rather than be mis-charged as invalid_json.
        """
        from unittest.mock import MagicMock

        from samantha_server.engine.decision import EngineDecision, RefusalTrace
        from samantha_server.scenarios.replay import _verdict_for_step

        step = self._make_step(expected_next_state="PENDING_LLM_REVIEW", llm_disposition="accepted")
        refusal = RefusalTrace(
            refusal_reason="STAGE_PRE_LLM_UNAVAILABLE",
            refusal_stage="PRE",
            judge_verdict=None,
            alternatives=(),
        )
        d = MagicMock(spec=EngineDecision)
        d.next_state = "PENDING_LLM_REVIEW"
        d.applied_rule_id = None
        d.flags_added = ()
        d.flags_cleared = ()
        d.also_matched = ()
        d.latency_us = 100
        d.decision_traces = (refusal,)
        verdict = _verdict_for_step(
            "SC-TEST",
            step,  # type: ignore[arg-type]
            d,  # type: ignore[arg-type]
            "PENDING_LLM_REVIEW",
            frozenset(),
            "llm",
            rule_index=rule_index,  # type: ignore[arg-type]
        )
        assert verdict.status != "invalid_json", (
            f"Operational refusal (STAGE_PRE_LLM_UNAVAILABLE) must not be mis-bucketed "
            f"as invalid_json; got {verdict.status!r}"
        )


# ---------------------------------------------------------------------------
# Slice 5: mismatch_query_response / invalid_json / empty_response for query
# ---------------------------------------------------------------------------

# Stub-LLM pattern for the receipt-based harness.
# The real dispatch path calls handle_clinical_query → complete_json() and
# parses the response as QueryResponseV1. To control the QueryTrace that ends
# up in the persisted receipt, we supply a stub LLMClient whose complete_json()
# returns a crafted JSON text. No dispatch patching needed.


def _make_stub_llm(response_text: str) -> object:
    """Return a stub LLMClient whose complete_json() always returns *response_text*.

    Used by migration: drive real dispatch with a controlled LLM response
    so the production receipt path persists a deterministic QueryTrace.
    """
    from unittest.mock import MagicMock

    from samantha_server.llm.client import LLMClient, LLMResponse

    _canned = LLMResponse(
        text=response_text, input_tokens=1, output_tokens=1, model_id="test-model", latency_us=0
    )
    mock = MagicMock(spec=LLMClient)
    mock.model_id = "test-model"
    mock.complete.return_value = _canned
    mock.complete_json.return_value = _canned
    return mock


def _make_stateful_stub_llm(responses: list[str]) -> object:
    """Return a stub LLMClient that cycles through *responses* across successive calls.

    Each call to complete_json() pops the next response text from the list.
    Used by multi-step tests that need different LLM responses per step.
    """
    from unittest.mock import MagicMock

    from samantha_server.llm.client import LLMClient, LLMResponse

    _idx = [0]

    def _next_response(*_a: object, **_kw: object) -> LLMResponse:
        text = responses[_idx[0] % len(responses)]
        _idx[0] += 1
        return LLMResponse(
            text=text, input_tokens=1, output_tokens=1, model_id="test-model", latency_us=0
        )

    mock = MagicMock(spec=LLMClient)
    mock.model_id = "test-model"
    mock.complete.side_effect = _next_response
    mock.complete_json.side_effect = _next_response
    return mock


def _resp_order_list(order_ids: list[str]) -> str:
    """Craft a QueryResponseV1 JSON text that parses to answer_type='order_list'."""
    import json

    return json.dumps(
        {"answer_type": "order_list", "reasoning": "", "order_ids": order_ids, "caveats": ""}
    )


def _resp_order_status(*order_ids: str) -> str:
    """Craft a QueryResponseV1 JSON text that parses to answer_type='order_status'.

    Accepts one or more order_ids so a test can send the SAME id set the fixture
    expects while only the answer_type differs (isolating the answer_type check).
    """
    import json

    return json.dumps(
        {
            "answer_type": "order_status",
            "reasoning": "",
            "order_ids": list(order_ids),
            "caveats": "",
        }
    )


def _resp_no_orders() -> str:
    """Craft a QueryResponseV1 JSON text that parses to answer_type='no_orders'."""
    import json

    return json.dumps({"answer_type": "no_orders", "reasoning": "", "order_ids": [], "caveats": ""})


def _resp_uncertain() -> str:
    """Craft a QueryResponseV1 JSON text that parses to answer_type='uncertain'."""
    import json

    return json.dumps({"answer_type": "uncertain", "reasoning": "", "order_ids": [], "caveats": ""})


def _resp_prioritized_list(order_ids: list[str]) -> str:
    """Craft a QueryResponseV1 JSON text that parses to answer_type='prioritized_list'."""
    import json

    return json.dumps(
        {
            "answer_type": "prioritized_list",
            "reasoning": "",
            "order_ids": order_ids,
            "caveats": "",
        }
    )


def _resp_json_decode_failure() -> str:
    """Return malformed JSON that triggers parse_failure='json_decode'."""
    return "not valid json at all"


def _resp_schema_violation() -> str:
    """Return valid JSON that violates QueryResponseV1 schema → parse_failure='schema_violation'."""
    import json

    return json.dumps(
        {
            "answer_type": "INVALID_ANSWER_TYPE_XYZ",
            "reasoning": "",
            "order_ids": [],
            "caveats": "",
        }
    )


def _run_single_step_query_replay(
    tmp_path: object,
    scenario: dict,  # type: ignore[type-arg]
    response_text: str,
    filename: str = "qry-gate-001.json",
) -> object:
    """Run a single-step query scenario using a stub LLM that returns *response_text*.

    The real dispatch_event path is exercised; the stub controls what QueryTrace
    ends up in the persisted receipt so the content gate can evaluate it.
    """
    import json
    from pathlib import Path

    from samantha_server.rules.loader import RuleIndex, load_rule_specs
    from samantha_server.scenarios.replay import replay

    specs_dir = Path(__file__).resolve().parents[2] / "samantha_server" / "rules" / "specs"
    rule_index = RuleIndex(load_rule_specs(specs_dir))

    assert isinstance(tmp_path, Path)
    subdir = tmp_path / "query"
    subdir.mkdir(exist_ok=True)
    (subdir / filename).write_text(json.dumps(scenario))

    return replay(
        tmp_path,
        rule_index=rule_index,
        _llm_client_override=_make_stub_llm(response_text),  # type: ignore[arg-type]
    )


class TestQueryContentGate:
    """Content gate applied post-loop to last LLM-routed query step.

    Migrated from stale dispatch-proxy injection to the receipt-based
    harness. Each test drives the real dispatch_event (via POST /events) with a
    stub LLMClient whose complete_json() returns a crafted QueryResponseV1 JSON
    text. The production handle_clinical_query parses it and persists the resulting
    QueryTrace in the in-memory receipt store; the harness reads it back and the
    content gate evaluates it. No dispatch patching required.
    """

    def _query_scenario(
        self,
        *,
        top_level_order_ids: list[str] | None = None,
    ) -> dict:  # type: ignore[type-arg]
        """Build a minimal query scenario fixture dict."""
        scenario: dict = {  # type: ignore[type-arg]
            "scenario_id": "QRY-GATE-001",
            "category": "query",
            "description": "content gate test",
            "events": [
                {
                    "step": 1,
                    "event_type": "clinical_query",
                    "event_data": {
                        "patient_name": "TEST, Bob",
                        "age": 50,
                        "sex": "M",
                        "specimen_type": "biopsy",
                        "anatomic_site": "breast",
                        "fixative": "formalin",
                        "fixation_time_hours": 24.0,
                        "ordered_tests": ["HER2_IHC"],
                        "priority": "ROUTINE",
                        "billing_info_present": True,
                    },
                    "expected_output": {
                        "next_state": "ACCESSIONING",
                        "applied_rules": [],
                        "flags": [],
                        "routing_path": "llm",
                    },
                }
            ],
        }
        if top_level_order_ids is not None:
            scenario["expected_output"] = {"order_ids": top_level_order_ids}
        return scenario

    def test_query_content_gate_non_empty_hash_does_not_yield_empty_response(
        self, tmp_path: object
    ) -> None:
        """PR205 Low #13: explicit negative — when parsed_order_ids matches
        expected AND the response is non-empty, the step passes; we must
        NOT accidentally classify it as empty_response when parsed_order_ids
        happens to be empty for a legitimate 'no orders satisfy' reason.
        """
        from pathlib import Path

        assert isinstance(tmp_path, Path)
        scenario = self._query_scenario(top_level_order_ids=[])
        # Stub returns order_list with empty ids — parses to parsed_order_ids=()
        # (empty tuple, not None). The gate must not flag this empty_response.
        report = _run_single_step_query_replay(tmp_path, scenario, _resp_order_list([]))
        step_status = report.scenario_verdicts[0].step_verdicts[0].status
        assert step_status != "empty_response", (
            f"non-empty response with empty parsed_order_ids must not be flagged "
            f"empty_response; got {step_status!r}"
        )

    def test_query_content_gate_match_passes(self, tmp_path: object) -> None:
        """When parsed_order_ids matches expected → step remains pass."""
        from pathlib import Path

        assert isinstance(tmp_path, Path)
        scenario = self._query_scenario(top_level_order_ids=["ORD-001"])
        report = _run_single_step_query_replay(tmp_path, scenario, _resp_order_list(["ORD-001"]))
        verdict = report.scenario_verdicts[0]
        assert verdict.status == "pass", f"Expected pass, got {verdict.status!r}"
        step_status = verdict.step_verdicts[0].status
        assert step_status == "pass", f"Step status: {step_status!r}"

    def test_query_content_gate_mismatch_query_response(self, tmp_path: object) -> None:
        """When parsed_order_ids != expected order_ids → mismatch_query_response."""
        from pathlib import Path

        assert isinstance(tmp_path, Path)
        scenario = self._query_scenario(top_level_order_ids=["ORD-001"])
        report = _run_single_step_query_replay(tmp_path, scenario, _resp_order_list(["ORD-WRONG"]))
        verdict = report.scenario_verdicts[0]
        assert verdict.status == "fail"
        step_status = verdict.step_verdicts[0].status
        assert step_status == "mismatch_query_response", f"Step status: {step_status!r}"

    def test_query_content_gate_parse_failure_yields_invalid_json(self, tmp_path: object) -> None:
        """Unparseable LLM response → invalid_json on the last LLM step.

        The handler now emits a RefusalTrace (STAGE_PRE_UNPARSEABLE)
        instead of a QueryTrace with parse_failure; the post-loop gate maps
        that refusal to the invalid_json step status.
        """
        from pathlib import Path

        assert isinstance(tmp_path, Path)
        scenario = self._query_scenario(top_level_order_ids=["ORD-001"])
        report = _run_single_step_query_replay(tmp_path, scenario, _resp_json_decode_failure())
        verdict = report.scenario_verdicts[0]
        assert verdict.status == "fail"
        step_status = verdict.step_verdicts[0].status
        assert step_status == "invalid_json", f"Step status: {step_status!r}"

    def test_query_content_gate_schema_violation_yields_invalid_json(
        self, tmp_path: object
    ) -> None:
        """Valid JSON that violates QueryResponseV1 (bad answer_type) → invalid_json.

        Covers the schema_violation branch (handlers.py): the stub returns
        parseable JSON whose answer_type is not a valid AnswerType, so
        model_validate_json raises a schema error (not a json_decode error).
        Like json_decode, this now produces a STAGE_PRE_UNPARSEABLE
        refusal, which the post-loop gate surfaces as invalid_json.
        """
        from pathlib import Path

        assert isinstance(tmp_path, Path)
        scenario = self._query_scenario(top_level_order_ids=["ORD-001"])
        report = _run_single_step_query_replay(
            tmp_path,
            scenario,
            _resp_schema_violation(),
            filename="qry-schema-violation-01.json",
        )
        verdict = report.scenario_verdicts[0]
        assert verdict.status == "fail"
        step_status = verdict.step_verdicts[0].status
        assert step_status == "invalid_json", f"Step status: {step_status!r}"

    def test_query_content_gate_empty_response(self) -> None:
        """When response_text_hash == sha256('') and parsed_order_ids is None → empty_response.

        The production handle_clinical_query cannot produce
        (parsed_order_ids=None, parse_failure=None, response_text_hash=sha256(''))
        via a stub response — if the LLM returns an empty string it triggers a
        json_decode failure, not the empty_response branch. This test is therefore
        a pure unit test of the gate's _verdict_for_step logic, verified by directly
        constructing the QueryTrace shape that the gate checks.
        """
        import hashlib
        from pathlib import Path

        from samantha_server.engine.decision import QueryTrace
        from samantha_server.rules.loader import RuleIndex, load_rule_specs
        from samantha_server.scenarios.loader import ScenarioStep
        from samantha_server.scenarios.replay import _verdict_for_step

        specs_dir = Path(__file__).resolve().parents[2] / "samantha_server" / "rules" / "specs"
        rule_index = RuleIndex(load_rule_specs(specs_dir))

        empty_hash = hashlib.sha256(b"").hexdigest()
        step = ScenarioStep(
            step_index=1,
            event_type="clinical_query",
            event_data={},
            expected_next_state="ACCESSIONING",
            expected_applied_rules=(),
            expected_flags=(),
        )

        from samantha_server.engine.decision import EngineDecision

        decision = EngineDecision(
            applied_rule_id=None,
            also_matched=(),
            flags_cleared=(),
            flags_added=(),
            next_state="ACCESSIONING",
            outcome="query_response",
            dispatched_rule_ids=(),
            event_input_hash=hashlib.sha256(b"test").hexdigest(),
            primitive_traces={},
            latency_us=100,
            decision_traces=(
                QueryTrace(
                    query_text_hash="qth",
                    skill_doc_hash="sdh",
                    scenarios_cited=(),
                    model_id="test-model",
                    response_text_hash=empty_hash,
                    parsed_order_ids=None,
                    parsed_answer_type=None,
                    parse_failure=None,  # type: ignore[arg-type]
                ),
            ),
        )

        verdict = _verdict_for_step(
            "SC-TEST",
            step,  # type: ignore[arg-type]
            decision,
            "ACCESSIONING",
            frozenset(),
            "llm",
            rule_index=rule_index,  # type: ignore[arg-type]
        )
        # This gate path is exercised by directly constructing the QueryTrace;
        # the production handler never produces this shape.
        assert verdict.status == "pass", (
            f"Step-level _verdict_for_step does not apply the query content gate "
            f"(that is post-loop); expected 'pass' here, got {verdict.status!r}"
        )

    def test_query_content_gate_targets_last_llm_step_not_last_by_index(
        self, tmp_path: object
    ) -> None:
        """PR205 review #7c: in a multi-step query where the LLM-routed step is
        NOT the last step by index (a later step is deterministic), the content
        gate must rewrite the LLM step's verdict — not the last index. This
        pins `_last_query_gate_target` correctness vs a naive `len-1` rewrite.
        """
        import json
        from pathlib import Path

        from samantha_server.rules.loader import RuleIndex, load_rule_specs
        from samantha_server.scenarios.replay import replay

        assert isinstance(tmp_path, Path)
        # Step 1: LLM-routed clinical_query with WRONG order_ids (should be rewritten).
        # Step 2: deterministic-routed order_received (should remain pass).
        scenario: dict = {  # type: ignore[type-arg]
            "scenario_id": "QRY-GATE-NOT-LAST",
            "category": "query",
            "description": "LLM step is not the last by index",
            "events": [
                {
                    "step": 1,
                    "event_type": "clinical_query",
                    "event_data": {
                        "patient_name": "T, B",
                        "age": 50,
                        "sex": "M",
                        "specimen_type": "biopsy",
                        "anatomic_site": "breast",
                        "fixative": "formalin",
                        "fixation_time_hours": 24.0,
                        "ordered_tests": ["HER2_IHC"],
                        "priority": "ROUTINE",
                        "billing_info_present": True,
                    },
                    "expected_output": {
                        "next_state": "ACCESSIONING",
                        "applied_rules": [],
                        "flags": [],
                        "routing_path": "llm",
                    },
                },
                {
                    "step": 2,
                    "event_type": "order_received",
                    "event_data": {
                        "patient_name": "T, B",
                        "age": 50,
                        "sex": "M",
                        "specimen_type": "biopsy",
                        "anatomic_site": "breast",
                        "fixative": "formalin",
                        "fixation_time_hours": 24.0,
                        "ordered_tests": ["HER2_IHC"],
                        "priority": "ROUTINE",
                        "billing_info_present": True,
                    },
                    "expected_output": {
                        "next_state": "ACCESSIONING",
                        "applied_rules": ["ACC-008"],
                        "flags": [],
                        "routing_path": "deterministic",
                    },
                },
            ],
            "expected_output": {"order_ids": ["ORD-001"]},
        }

        specs_dir = Path(__file__).resolve().parents[2] / "samantha_server" / "rules" / "specs"
        rule_index = RuleIndex(load_rule_specs(specs_dir))

        subdir = tmp_path / "query"
        subdir.mkdir()
        (subdir / "qry-gate-not-last.json").write_text(json.dumps(scenario))

        # Step 1 (LLM) returns wrong order_ids; step 2 (deterministic) is not
        # LLM-routed so the stub is only consulted once (for step 1).
        report = replay(
            tmp_path,
            rule_index=rule_index,
            _llm_client_override=_make_stub_llm(_resp_order_list(["ORD-WRONG"])),  # type: ignore[arg-type]
        )

        verdict = report.scenario_verdicts[0]
        step1_status = verdict.step_verdicts[0].status
        # Step 1 (LLM-routed) is the one with content-gate accountability and
        # its parsed_order_ids mismatch the expected — it gets rewritten.
        assert step1_status == "mismatch_query_response", (
            f"Step 1 (LLM-routed, NOT the last by index) should be rewritten; got {step1_status!r}"
        )

    # ---------------------------------------------------------------------------
    # order_status branch in the content gate
    # ---------------------------------------------------------------------------

    def _query_scenario_order_status(
        self,
        *,
        subject_id: str,
    ) -> dict:  # type: ignore[type-arg]
        """Build a minimal query scenario fixture with answer_type=order_status."""
        return {
            "scenario_id": "QRY-GATE-STATUS-001",
            "category": "query",
            "description": "order_status content gate test",
            "events": [
                {
                    "step": 1,
                    "event_type": "clinical_query",
                    "event_data": {
                        "patient_name": "TEST, Bob",
                        "age": 50,
                        "sex": "M",
                        "specimen_type": "biopsy",
                        "anatomic_site": "breast",
                        "fixative": "formalin",
                        "fixation_time_hours": 24.0,
                        "ordered_tests": ["HER2_IHC"],
                        "priority": "ROUTINE",
                        "billing_info_present": True,
                    },
                    "expected_output": {
                        "next_state": "ACCESSIONING",
                        "applied_rules": [],
                        "flags": [],
                        "routing_path": "llm",
                    },
                }
            ],
            "expected_output": {
                "answer_type": "order_status",
                "order_ids": [subject_id],
            },
        }

    def test_query_gate_order_status_subject_id_match_passes(self, tmp_path: object) -> None:
        """order_status fixture where model emits correct subject_id → pass."""
        from pathlib import Path

        assert isinstance(tmp_path, Path)
        scenario = self._query_scenario_order_status(subject_id="ORD-901")
        report = _run_single_step_query_replay(
            tmp_path,
            scenario,
            _resp_order_status("ORD-901"),
            filename="qry-gate-status-001.json",
        )
        verdict = report.scenario_verdicts[0]
        assert verdict.status == "pass", f"Expected pass, got {verdict.status!r}"
        step_status = verdict.step_verdicts[0].status
        assert step_status == "pass", f"Step status: {step_status!r}"

    def test_query_gate_order_status_wrong_subject_fails(self, tmp_path: object) -> None:
        """order_status fixture where model emits wrong subject_id → mismatch."""
        from pathlib import Path

        assert isinstance(tmp_path, Path)
        scenario = self._query_scenario_order_status(subject_id="ORD-901")
        report = _run_single_step_query_replay(
            tmp_path,
            scenario,
            _resp_order_status("ORD-999"),
            filename="qry-gate-status-001.json",
        )
        verdict = report.scenario_verdicts[0]
        assert verdict.status == "fail"
        step_status = verdict.step_verdicts[0].status
        assert step_status == "mismatch_query_response", f"Step status: {step_status!r}"
        # Diagnostic must name both expected and parsed subject
        content_diag = verdict.step_verdicts[0].content_diagnostic
        assert content_diag is not None
        assert "ORD-901" in content_diag, (
            f"expected_subject missing from diagnostic: {content_diag!r}"
        )
        assert "ORD-999" in content_diag, (
            f"parsed_subject missing from diagnostic: {content_diag!r}"
        )

    def test_query_gate_order_status_with_wrong_answer_type_fails(self, tmp_path: object) -> None:
        """order_status fixture but model emits order_list → mismatch.

        The model used the wrong shape even if the order_id happens to match.
        The gate must fail because parsed_answer_type != expected answer_type.
        """
        from pathlib import Path

        assert isinstance(tmp_path, Path)
        scenario = self._query_scenario_order_status(subject_id="ORD-901")
        # Model emits order_list with the subject_id — correct ID, wrong type.
        report = _run_single_step_query_replay(
            tmp_path,
            scenario,
            _resp_order_list(["ORD-901"]),
            filename="qry-gate-status-001.json",
        )
        verdict = report.scenario_verdicts[0]
        assert verdict.status == "fail"
        step_status = verdict.step_verdicts[0].status
        assert step_status == "mismatch_query_response", f"Step status: {step_status!r}"
        # Diagnostic must distinguish wrong-answer_type from wrong-subject
        content_diag = verdict.step_verdicts[0].content_diagnostic
        assert content_diag is not None
        assert "order_list" in content_diag or "answer_type" in content_diag, (
            f"Diagnostic must name the wrong answer_type: {content_diag!r}"
        )

    def test_gate_unrecognized_answer_type_logs_warning(
        self, tmp_path: object, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Cluster C (#3): unrecognized fixture answer_type logs WARNING and gate passes through.

        A fixture with an unrecognized answer_type must emit a WARNING naming the
        unrecognized type (via Scenario.expected_answer_type) and the gate must pass
        through with the current step verdict unchanged. This ensures future additions
        are loud, not silent.

        After Cluster D, the warning originates in samantha_server.scenarios.loader
        (via Scenario.expected_answer_type) rather than in replay itself.

        Note: "prioritized_list" was used here before added it to ANSWER_TYPES.
        The test now uses a synthetic type that will never be a valid answer_type.
        """
        import json
        import logging
        from pathlib import Path

        from samantha_server.rules.loader import RuleIndex, load_rule_specs
        from samantha_server.scenarios.replay import replay

        assert isinstance(tmp_path, Path)
        # Fabricate a fixture with an unrecognized answer_type — raw JSON dict,
        # not subject to Pydantic schema validation.
        scenario: dict = {  # type: ignore[type-arg]
            "scenario_id": "QRY-GATE-UNRECOG",
            "category": "query",
            "description": "unrecognized answer_type gate test",
            "events": [
                {
                    "step": 1,
                    "event_type": "clinical_query",
                    "event_data": {
                        "patient_name": "TEST, Bob",
                        "age": 50,
                        "sex": "M",
                        "specimen_type": "biopsy",
                        "anatomic_site": "breast",
                        "fixative": "formalin",
                        "fixation_time_hours": 24.0,
                        "ordered_tests": ["HER2_IHC"],
                        "priority": "ROUTINE",
                        "billing_info_present": True,
                    },
                    "expected_output": {
                        "next_state": "ACCESSIONING",
                        "applied_rules": [],
                        "flags": [],
                        "routing_path": "llm",
                    },
                }
            ],
            # answer_type is unrecognized — not in ANSWER_TYPES
            "expected_output": {
                "answer_type": "future_unknown_type",
                "order_ids": ["ORD-001"],
            },
        }

        specs_dir = Path(__file__).resolve().parents[2] / "samantha_server" / "rules" / "specs"
        rule_index = RuleIndex(load_rule_specs(specs_dir))

        subdir = tmp_path / "query"
        subdir.mkdir(exist_ok=True)
        (subdir / "qry-gate-unrecog.json").write_text(json.dumps(scenario))

        # Stub returns valid order_list JSON — the gate must still warn about the
        # unrecognized fixture answer_type before passing through.
        with caplog.at_level(logging.WARNING):
            replay(
                tmp_path,
                rule_index=rule_index,
                _llm_client_override=_make_stub_llm(_resp_order_list(["ORD-001"])),  # type: ignore[arg-type]
            )

        warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("future_unknown_type" in msg for msg in warning_messages), (
            f"Expected WARNING naming 'future_unknown_type'; got: {warning_messages!r}"
        )

    def test_query_content_gate_no_desync_when_last_step_errors_post_dispatch(
        self, tmp_path: object
    ) -> None:
        """PR205 review #1: the post-loop content gate must apply only to the step
        that is the last LLM-routed step — it must NOT rewrite an earlier step's
        verdict using a later step's QueryTrace.

        The `_last_query_gate_target` pair (index, decision) is set AFTER
        step_verdicts.append(), so a later step's QueryTrace cannot overwrite an
        earlier step's verdict index. This test verifies that invariant holds on
        the endpoint path: step 1 (correct order_ids → pass) must remain pass
        when step 2 has wrong order_ids (→ mismatch_query_response).

        On the endpoint path, resolve_transition is not called
        (the endpoint already resolves symbolic transitions). The desync prevention
        is still exercised via the _last_query_gate_target logic.
        """
        import json
        from pathlib import Path

        from samantha_server.rules.loader import RuleIndex, load_rule_specs
        from samantha_server.scenarios.replay import replay

        assert isinstance(tmp_path, Path)
        # Build a 2-step query scenario: both steps LLM-routed.
        # Step 1: correct order_ids (must remain pass).
        # Step 2: wrong order_ids — its bogus QueryTrace must not rewrite step 1.
        scenario: dict = {  # type: ignore[type-arg]
            "scenario_id": "QRY-GATE-DESYNC",
            "category": "query",
            "description": "desync regression — multi-step query",
            "events": [
                {
                    "step": 1,
                    "event_type": "clinical_query",
                    "event_data": {
                        "patient_name": "T, B",
                        "age": 50,
                        "sex": "M",
                        "specimen_type": "biopsy",
                        "anatomic_site": "breast",
                        "fixative": "formalin",
                        "fixation_time_hours": 24.0,
                        "ordered_tests": ["HER2_IHC"],
                        "priority": "ROUTINE",
                        "billing_info_present": True,
                    },
                    "expected_output": {
                        "next_state": "ACCESSIONING",
                        "applied_rules": [],
                        "flags": [],
                        "routing_path": "llm",
                    },
                },
                {
                    "step": 2,
                    "event_type": "clinical_query",
                    "event_data": {
                        "patient_name": "T, B",
                        "age": 50,
                        "sex": "M",
                        "specimen_type": "biopsy",
                        "anatomic_site": "breast",
                        "fixative": "formalin",
                        "fixation_time_hours": 24.0,
                        "ordered_tests": ["HER2_IHC"],
                        "priority": "ROUTINE",
                        "billing_info_present": True,
                    },
                    "expected_output": {
                        "next_state": "ACCESSIONING",
                        "applied_rules": [],
                        "flags": [],
                        "routing_path": "llm",
                    },
                },
            ],
            "expected_output": {"order_ids": ["ORD-001"]},
        }

        specs_dir = Path(__file__).resolve().parents[2] / "samantha_server" / "rules" / "specs"
        rule_index = RuleIndex(load_rule_specs(specs_dir))

        subdir = tmp_path / "query"
        subdir.mkdir()
        (subdir / "qry-gate-desync.json").write_text(json.dumps(scenario))

        # Step 1 returns correct order_ids; step 2 returns WRONG order_ids.
        # If the desync existed, step 2's QueryTrace would overwrite step 1's verdict.
        stub = _make_stateful_stub_llm(
            [
                _resp_order_list(["ORD-001"]),  # step 1: correct
                _resp_order_list(["ORD-BOGUS"]),  # step 2: wrong
            ]
        )
        report = replay(
            tmp_path,
            rule_index=rule_index,
            _llm_client_override=stub,  # type: ignore[arg-type]
        )

        verdict = report.scenario_verdicts[0]
        # Step 1 had correct order_ids and its verdict was appended cleanly —
        # it must remain "pass". A desync would rewrite it to mismatch_query_response
        # using step 2's bogus QueryTrace.
        step1_status = verdict.step_verdicts[0].status
        assert step1_status == "pass", (
            f"Step 1's verdict was rewritten by step 2's QueryTrace — desync! "
            f"Expected 'pass', got {step1_status!r}"
        )
        # Step 2's bogus order_ids must produce a content-gate failure on step 2 only.
        step2_status = verdict.step_verdicts[1].status
        assert step2_status == "mismatch_query_response", (
            f"Step 2 should be 'mismatch_query_response' (wrong order_ids); got {step2_status!r}"
        )

    # ---------------------------------------------------------------------------
    # prioritized_list branch in the content gate
    # ---------------------------------------------------------------------------

    def _query_scenario_prioritized_list(
        self,
        *,
        order_ids: list[str],
    ) -> dict:  # type: ignore[type-arg]
        """Build a minimal query scenario fixture with answer_type=prioritized_list."""
        return {
            "scenario_id": "QRY-GATE-PLIST-001",
            "category": "query",
            "description": "prioritized_list content gate test",
            "events": [
                {
                    "step": 1,
                    "event_type": "clinical_query",
                    "event_data": {
                        "patient_name": "TEST, Bob",
                        "age": 50,
                        "sex": "M",
                        "specimen_type": "biopsy",
                        "anatomic_site": "breast",
                        "fixative": "formalin",
                        "fixation_time_hours": 24.0,
                        "ordered_tests": ["HER2_IHC"],
                        "priority": "ROUTINE",
                        "billing_info_present": True,
                    },
                    "expected_output": {
                        "next_state": "ACCESSIONING",
                        "applied_rules": [],
                        "flags": [],
                        "routing_path": "llm",
                    },
                }
            ],
            "expected_output": {
                "answer_type": "prioritized_list",
                "order_ids": order_ids,
            },
        }

    def test_query_gate_prioritized_list_exact_sequence_passes(self, tmp_path: object) -> None:
        """prioritized_list where model emits exact sequence → pass."""
        from pathlib import Path

        assert isinstance(tmp_path, Path)
        scenario = self._query_scenario_prioritized_list(order_ids=["ORD-A", "ORD-B", "ORD-C"])
        report = _run_single_step_query_replay(
            tmp_path,
            scenario,
            _resp_prioritized_list(["ORD-A", "ORD-B", "ORD-C"]),
            filename="qry-gate-plist-001.json",
        )
        verdict = report.scenario_verdicts[0]
        assert verdict.status == "pass", f"Expected pass, got {verdict.status!r}"
        step_status = verdict.step_verdicts[0].status
        assert step_status == "pass", f"Step status: {step_status!r}"

    def test_query_gate_prioritized_list_wrong_order_fails(self, tmp_path: object) -> None:
        """prioritized_list where model emits same set in wrong rank → fail.

        The diagnostic must name expected_sequence and parsed_sequence.
        """
        from pathlib import Path

        assert isinstance(tmp_path, Path)
        scenario = self._query_scenario_prioritized_list(order_ids=["ORD-A", "ORD-B", "ORD-C"])
        # Same IDs, reversed rank — set-equality would pass, sequence-equality must fail.
        report = _run_single_step_query_replay(
            tmp_path,
            scenario,
            _resp_prioritized_list(["ORD-C", "ORD-B", "ORD-A"]),
            filename="qry-gate-plist-001.json",
        )
        verdict = report.scenario_verdicts[0]
        assert verdict.status == "fail"
        step_status = verdict.step_verdicts[0].status
        assert step_status == "mismatch_query_response", f"Step status: {step_status!r}"
        content_diag = verdict.step_verdicts[0].content_diagnostic
        assert content_diag is not None
        assert "expected_sequence" in content_diag, (
            f"Diagnostic must name expected_sequence: {content_diag!r}"
        )
        assert "parsed_sequence" in content_diag, (
            f"Diagnostic must name parsed_sequence: {content_diag!r}"
        )

    def test_query_gate_prioritized_list_wrong_answer_type_fails(self, tmp_path: object) -> None:
        """Fixture is prioritized_list but model emits order_list → fail.

        Correct IDs in correct order but wrong answer_type must fail.
        """
        from pathlib import Path

        assert isinstance(tmp_path, Path)
        scenario = self._query_scenario_prioritized_list(order_ids=["ORD-A", "ORD-B", "ORD-C"])
        # Model emits order_list (wrong answer_type) — right IDs, right order, wrong type.
        report = _run_single_step_query_replay(
            tmp_path,
            scenario,
            _resp_order_list(["ORD-A", "ORD-B", "ORD-C"]),
            filename="qry-gate-plist-001.json",
        )
        verdict = report.scenario_verdicts[0]
        assert verdict.status == "fail"
        step_status = verdict.step_verdicts[0].status
        assert step_status == "mismatch_query_response", f"Step status: {step_status!r}"

    def test_query_gate_prioritized_list_empty_expected_sequence_passes_silently(
        self, tmp_path: object
    ) -> None:
        """PR224 Cluster D (#5): prioritized_list fixture with empty order_ids[] must pass silently.

        An empty prioritized_list fixture is degenerate — there is no meaningful sequence
        to validate. Both the assertion path and the gate must align: pass-through without
        firing the parsed_answer_type check. The gate currently fires the parsed_answer_type
        check (expected_sequence is tuple() which is non-None), disagreeing with the assertion
        path which early-exits on empty list.
        """
        from pathlib import Path

        assert isinstance(tmp_path, Path)
        scenario = self._query_scenario_prioritized_list(order_ids=[])
        # Model emits order_list — gate should NOT fire answer_type check on empty fixture.
        report = _run_single_step_query_replay(
            tmp_path,
            scenario,
            _resp_order_list(["ORD-X", "ORD-Y"]),
            filename="qry-gate-plist-001.json",
        )
        verdict = report.scenario_verdicts[0]
        assert verdict.status == "pass", (
            f"Empty prioritized_list fixture must pass silently (no sequence to validate); "
            f"got {verdict.status!r}"
        )


# ---------------------------------------------------------------------------
# order_list gate — answer_type check before set-equality
# ---------------------------------------------------------------------------


class TestOrderListAnswerTypeGate:
    """order_list fixture must fail when model returns wrong answer_type.

    The order_list gate's elif branch (L1250 legacy) compared only
    parsed_order_ids via set-equality, ignoring parsed_answer_type. A model
    that returns answer_type="order_status" with matching order_ids would pass
    silently. After, the branch checks answer_type first.

    Migrated from stale dispatch-proxy injection to the receipt-based
    harness using a stub LLM whose complete_json() returns crafted JSON.
    """

    def _order_list_scenario(self, order_ids: list[str]) -> dict:  # type: ignore[type-arg]
        """Minimal order_list fixture with given expected order_ids."""
        return {
            "scenario_id": "QRY-GH231-F1-01",
            "category": "query",
            "description": "F-1: order_list fixture for answer_type gate test",
            "events": [
                {
                    "step": 1,
                    "event_type": "clinical_query",
                    "event_data": {
                        "patient_name": "TEST, F1",
                        "age": 45,
                        "sex": "F",
                        "specimen_type": "biopsy",
                        "anatomic_site": "breast",
                        "fixative": "formalin",
                        "fixation_time_hours": 24.0,
                        "ordered_tests": ["Breast IHC Panel"],
                        "priority": "routine",
                        "billing_info_present": True,
                    },
                    "expected_output": {
                        "next_state": "ACCESSIONING",
                        "applied_rules": [],
                        "flags": [],
                        "routing_path": "llm",
                    },
                }
            ],
            "expected_output": {
                "answer_type": "order_list",
                "order_ids": order_ids,
            },
        }

    def test_order_list_fixture_with_wrong_answer_type_fails(self, tmp_path: object) -> None:
        """order_list fixture where model returns order_status → mismatch.

        Before the order_list elif branch had no answer_type check — only
        set-equality on order_ids. A model returning order_status with matching
        order_ids would pass silently. After the branch must check
        parsed_answer_type == "order_list" first.
        """
        from pathlib import Path

        assert isinstance(tmp_path, Path)
        scenario = self._order_list_scenario(order_ids=["ORD-001", "ORD-002"])
        # Stub returns order_status (wrong type) with the EXACT expected order_ids set,
        # so only the answer_type check can produce the mismatch — if that check were
        # removed and the gate fell to set-equality, the matching ids would let it pass.
        report = _run_single_step_query_replay(
            tmp_path,
            scenario,
            _resp_order_status("ORD-001", "ORD-002"),
            filename="qry-gh231-f1-01.json",
        )
        verdict = report.scenario_verdicts[0]
        assert verdict.status == "fail"
        step_status = verdict.step_verdicts[0].status
        assert step_status == "mismatch_query_response", (
            f"order_list fixture with wrong parsed_answer_type should fail; got {step_status!r}"
        )

    def test_order_list_fixture_with_correct_answer_type_passes(self, tmp_path: object) -> None:
        """order_list fixture with correct answer_type and order_ids → pass."""
        from pathlib import Path

        assert isinstance(tmp_path, Path)
        scenario = self._order_list_scenario(order_ids=["ORD-001", "ORD-002"])
        report = _run_single_step_query_replay(
            tmp_path,
            scenario,
            _resp_order_list(["ORD-001", "ORD-002"]),
            filename="qry-gh231-f1-01.json",
        )
        verdict = report.scenario_verdicts[0]
        assert verdict.status == "pass", f"Expected pass, got {verdict.status!r}"


# ---------------------------------------------------------------------------
# no_orders/uncertain gate branch
# ---------------------------------------------------------------------------


class TestNoOrdersUncertainGateGH231F6:
    """no_orders and uncertain fixtures must validate answer_type + empty ids.

    Before these fixture types fell through the gate (no branch covered them)
    so a model returning order_list with non-empty ids would pass silently.

    Migrated from stale dispatch-proxy injection to the receipt-based
    harness using a stub LLM whose complete_json() returns crafted JSON.
    """

    def _no_orders_scenario(self) -> dict:  # type: ignore[type-arg]
        """Minimal no_orders fixture — zero matching orders in the db."""
        return {
            "scenario_id": "QRY-GH231-F6-01",
            "category": "query",
            "description": "F-6: no_orders fixture",
            "events": [
                {
                    "step": 1,
                    "event_type": "clinical_query",
                    "event_data": {
                        "patient_name": "TEST, F6",
                        "age": 40,
                        "sex": "F",
                        "specimen_type": "biopsy",
                        "anatomic_site": "breast",
                        "fixative": "formalin",
                        "fixation_time_hours": 24.0,
                        "ordered_tests": ["Breast IHC Panel"],
                        "priority": "routine",
                        "billing_info_present": True,
                    },
                    "expected_output": {
                        "next_state": "ACCESSIONING",
                        "applied_rules": [],
                        "flags": [],
                        "routing_path": "llm",
                    },
                }
            ],
            "expected_output": {
                "answer_type": "no_orders",
                "order_ids": [],
            },
        }

    def _uncertain_scenario(self) -> dict:  # type: ignore[type-arg]
        """Minimal uncertain fixture — query needs data not in the prompt."""
        return {
            "scenario_id": "QRY-GH231-F6-02",
            "category": "query",
            "description": "F-6: uncertain fixture",
            "events": [
                {
                    "step": 1,
                    "event_type": "clinical_query",
                    "event_data": {
                        "patient_name": "TEST, F6B",
                        "age": 50,
                        "sex": "M",
                        "specimen_type": "biopsy",
                        "anatomic_site": "breast",
                        "fixative": "formalin",
                        "fixation_time_hours": 24.0,
                        "ordered_tests": ["Breast IHC Panel"],
                        "priority": "routine",
                        "billing_info_present": True,
                    },
                    "expected_output": {
                        "next_state": "ACCESSIONING",
                        "applied_rules": [],
                        "flags": [],
                        "routing_path": "llm",
                    },
                }
            ],
            "expected_output": {
                "answer_type": "uncertain",
                "order_ids": [],
            },
        }

    def test_no_orders_fixture_with_order_list_response_fails(self, tmp_path: object) -> None:
        """no_orders fixture where model returns order_list with ids → mismatch.

        Before the gate had no branch for no_orders/uncertain with empty order_ids
        so the empty-set path fell through and passed silently. After, the gate checks
        fixture answer_type BEFORE the empty-ids path, requiring parsed_answer_type match.
        """
        from pathlib import Path

        assert isinstance(tmp_path, Path)
        scenario = self._no_orders_scenario()
        # Stub returns order_list (wrong type) with non-empty order_ids.
        report = _run_single_step_query_replay(
            tmp_path,
            scenario,
            _resp_order_list(["ORD-001", "ORD-002"]),
            filename="qry-gh231-f6-01.json",
        )
        verdict = report.scenario_verdicts[0]
        assert verdict.status == "fail"
        step_status = verdict.step_verdicts[0].status
        assert step_status == "mismatch_query_response", (
            f"no_orders fixture with order_list+non-empty ids should fail; got {step_status!r}"
        )

    def test_no_orders_fixture_with_correct_response_passes(self, tmp_path: object) -> None:
        """no_orders fixture where model returns no_orders with empty ids → pass."""
        from pathlib import Path

        assert isinstance(tmp_path, Path)
        scenario = self._no_orders_scenario()
        report = _run_single_step_query_replay(
            tmp_path,
            scenario,
            _resp_no_orders(),
            filename="qry-gh231-f6-01.json",
        )
        verdict = report.scenario_verdicts[0]
        assert verdict.status == "pass", f"Expected pass, got {verdict.status!r}"

    def test_uncertain_fixture_with_order_list_response_fails(self, tmp_path: object) -> None:
        """Uncertain fixture where model returns order_list → mismatch."""
        from pathlib import Path

        assert isinstance(tmp_path, Path)
        scenario = self._uncertain_scenario()
        report = _run_single_step_query_replay(
            tmp_path,
            scenario,
            _resp_order_list(["ORD-001"]),
            filename="qry-gh231-f6-02.json",
        )
        verdict = report.scenario_verdicts[0]
        assert verdict.status == "fail"
        step_status = verdict.step_verdicts[0].status
        assert step_status == "mismatch_query_response", (
            f"uncertain fixture with wrong answer_type should fail; got {step_status!r}"
        )

    def test_uncertain_fixture_with_correct_response_passes(self, tmp_path: object) -> None:
        """Uncertain fixture where model returns uncertain with empty ids → pass."""
        from pathlib import Path

        assert isinstance(tmp_path, Path)
        scenario = self._uncertain_scenario()
        report = _run_single_step_query_replay(
            tmp_path,
            scenario,
            _resp_uncertain(),
            filename="qry-gh231-f6-02.json",
        )
        verdict = report.scenario_verdicts[0]
        assert verdict.status == "pass", f"Expected pass, got {verdict.status!r}"

    def test_no_orders_fixture_with_correct_type_but_non_empty_ids_fails(
        self, tmp_path: object
    ) -> None:
        """Pins the F-6 gate's *second* elif arm.

        no_orders fixture: model returns `parsed_answer_type=no_orders` (correct)
        but with non-empty `parsed_order_ids` (wrong) → must mismatch. Without
        this test, removing the non-empty-ids check from the gate would not
        red the suite (all other F-6 tests cover the wrong-answer_type arm).

        Produced via a stub that returns no_orders JSON with a non-empty order_ids list.
        The QueryResponseV1 schema allows non-empty order_ids with no_orders answer_type
        (it only enforces the answer_type enum, not semantic cross-field constraints),
        so the stub can produce this combination.
        """
        import json
        from pathlib import Path

        assert isinstance(tmp_path, Path)
        scenario = self._no_orders_scenario()
        # Craft JSON that passes QueryResponseV1 validation but has non-empty order_ids
        # alongside no_orders answer_type — schema-valid but semantically wrong.
        stub_response = json.dumps(
            {
                "answer_type": "no_orders",
                "reasoning": "",
                "order_ids": ["ORD-001"],
                "caveats": "",
            }
        )
        report = _run_single_step_query_replay(
            tmp_path,
            scenario,
            stub_response,
            filename="qry-gh231-f6-03.json",
        )
        verdict = report.scenario_verdicts[0]
        step_status = verdict.step_verdicts[0].status
        assert step_status == "mismatch_query_response", (
            f"no_orders fixture with correct answer_type but non-empty ids should fail; "
            f"got {step_status!r}"
        )
