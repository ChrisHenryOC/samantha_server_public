"""Tests for samantha_server.engine.action_handlers.

apply_runtime_flag_clearing promoted from scenarios/ to engine/.
"""

from __future__ import annotations

from samantha_server.engine.decision import EngineDecision


def _make_decision(next_state: str) -> EngineDecision:
    return EngineDecision(
        applied_rule_id=None,
        also_matched=(),
        next_state=next_state,
        flags_added=(),
        flags_cleared=(),
        outcome="",
        dispatched_rule_ids=(),
        event_input_hash="",
        primitive_traces={},
        latency_us=0,
    )


class TestApplyRuntimeFlagClearing:
    """Tests for apply_runtime_flag_clearing in its engine-layer home."""

    def test_billing_info_clears_missing_info_proceed(self) -> None:
        """RES-002: when next_state is RESOLVE_MISSING_INFO and info_type is 'billing',
        MISSING_INFO_PROCEED is cleared from flags."""
        from samantha_server.engine.action_handlers import apply_runtime_flag_clearing

        decision = _make_decision("RESOLVE_MISSING_INFO")
        event = {"info_type": "billing"}
        flags = frozenset({"MISSING_INFO_PROCEED", "SOME_OTHER_FLAG"})

        result = apply_runtime_flag_clearing(decision, event, flags)

        assert "MISSING_INFO_PROCEED" not in result
        assert "SOME_OTHER_FLAG" in result

    def test_non_billing_info_type_does_not_clear_flag(self) -> None:
        """RES-002: when info_type is not 'billing', MISSING_INFO_PROCEED is preserved."""
        from samantha_server.engine.action_handlers import apply_runtime_flag_clearing

        decision = _make_decision("RESOLVE_MISSING_INFO")
        event = {"info_type": "clinical_notes"}
        flags = frozenset({"MISSING_INFO_PROCEED"})

        result = apply_runtime_flag_clearing(decision, event, flags)

        assert "MISSING_INFO_PROCEED" in result

    def test_non_resolve_missing_info_state_does_not_alter_flags(self) -> None:
        """For non-RESOLVE_MISSING_INFO transitions, flags pass through unchanged."""
        from samantha_server.engine.action_handlers import apply_runtime_flag_clearing

        decision = _make_decision("ACCEPTED")
        event = {"info_type": "billing"}
        flags = frozenset({"MISSING_INFO_PROCEED", "RECUT_REQUESTED"})

        result = apply_runtime_flag_clearing(decision, event, flags)

        assert result == flags

    def test_returns_frozenset(self) -> None:
        """apply_runtime_flag_clearing always returns frozenset."""
        from samantha_server.engine.action_handlers import apply_runtime_flag_clearing

        decision = _make_decision("RESOLVE_MISSING_INFO")
        event = {"info_type": "billing"}
        flags = frozenset({"MISSING_INFO_PROCEED"})

        result = apply_runtime_flag_clearing(decision, event, flags)

        assert isinstance(result, frozenset)

    def test_missing_info_type_preserves_missing_info_proceed(self) -> None:
        """RES-002: absent info_type key does NOT clear MISSING_INFO_PROCEED.

        Conservative fallback: without info_type the action handler cannot confirm
        a billing resolution, so the flag is preserved → RESULTING_HOLD.
        """
        from samantha_server.engine.action_handlers import apply_runtime_flag_clearing

        decision = _make_decision("RESOLVE_MISSING_INFO")
        event: dict[str, object] = {}
        flags = frozenset({"MISSING_INFO_PROCEED"})

        result = apply_runtime_flag_clearing(decision, event, flags)

        assert "MISSING_INFO_PROCEED" in result

    def test_none_info_type_preserves_missing_info_proceed(self) -> None:
        """RES-002: info_type=None does NOT clear MISSING_INFO_PROCEED (conservative fallback).

        A None info_type is not the 'billing' sentinel, so the flag is preserved →
        resolve_transition returns RESULTING_HOLD.
        """
        from samantha_server.engine.action_handlers import apply_runtime_flag_clearing

        decision = _make_decision("RESOLVE_MISSING_INFO")
        event = {"info_type": None}
        flags = frozenset({"MISSING_INFO_PROCEED"})

        result = apply_runtime_flag_clearing(decision, event, flags)

        assert "MISSING_INFO_PROCEED" in result
