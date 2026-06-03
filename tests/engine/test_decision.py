"""Tests for EngineDecision schema and compute_event_input_hash."""

import pytest
from pydantic import ValidationError

from samantha_server.engine.decision import (
    EngineDecision,
    _make_serialisable,
    compute_event_input_hash,
)
from samantha_server.models.context import Event, Order, SpecimenContext
from samantha_server.primitives.trace import PrimitiveTrace


def _make_order(**overrides: object) -> Order:
    defaults = dict(
        order_id="O-001",
        patient_name="Jane Doe",
        patient_sex="F",
        age=45,
        specimen_type="biopsy",
        anatomic_site="breast",
        fixative="formalin",
        fixation_time_hours=8.0,
        ordered_tests=("HER2",),
        priority="ROUTINE",
        billing_info_present=True,
    )
    defaults.update(overrides)
    return Order(**defaults)  # type: ignore[arg-type]


def _make_ctx(
    state: str = "ACCESSIONING",
    event_type: str = "order_received",
    event_data: dict | None = None,
    flags: frozenset[str] | None = None,
    **order_overrides: object,
) -> SpecimenContext:
    return SpecimenContext(
        order=_make_order(**order_overrides),
        current_state=state,
        flags=flags or frozenset(),
        event=Event(
            event_type=event_type,
            event_data=event_data or {},
            step_index=0,
        ),
    )


class TestEngineDecision:
    def test_engine_decision_is_frozen(self) -> None:
        """EngineDecision must be immutable (frozen Pydantic model)."""
        decision = EngineDecision(
            applied_rule_id="ACC-001",
            next_state="MISSING_INFO_HOLD",
            flags_added=(),
            flags_cleared=(),
            outcome="held_missing_patient_name",
            also_matched=(),
            dispatched_rule_ids=("ACC-001",),
            event_input_hash="abc123",
            primitive_traces={},
            latency_us=100,
        )
        with pytest.raises(ValidationError):
            decision.applied_rule_id = "ACC-002"  # type: ignore[misc]

    def test_engine_decision_applied_rule_id_can_be_none(self) -> None:
        decision = EngineDecision(
            applied_rule_id=None,
            next_state="ACCESSIONING",
            flags_added=(),
            flags_cleared=(),
            outcome="dispatch_empty",
            also_matched=(),
            dispatched_rule_ids=(),
            event_input_hash="abc123",
            primitive_traces={},
            latency_us=0,
        )
        assert decision.applied_rule_id is None

    def test_engine_decision_has_all_spec_fields(self) -> None:
        """Confirm the schema matches the spec exactly."""
        decision = EngineDecision(
            applied_rule_id="ACC-008",
            next_state="ACCEPTED",
            flags_added=(),
            flags_cleared=(),
            outcome="accessioning_validations_passed",
            also_matched=("ACC-001",),
            dispatched_rule_ids=("ACC-001", "ACC-008"),
            event_input_hash="deadbeef",
            primitive_traces={},
            latency_us=42,
        )
        assert decision.next_state == "ACCEPTED"
        assert decision.flags_added == ()
        assert decision.flags_cleared == ()
        assert decision.also_matched == ("ACC-001",)
        assert decision.dispatched_rule_ids == ("ACC-001", "ACC-008")
        assert decision.latency_us == 42

    def test_engine_decision_primitive_traces_keyed_by_rule_id(self) -> None:
        trace = PrimitiveTrace(
            primitive="Equals",
            field="patient_name",
            expected=None,
            actual=None,
            result=True,
        )
        decision = EngineDecision(
            applied_rule_id="ACC-001",
            next_state="MISSING_INFO_HOLD",
            flags_added=(),
            flags_cleared=(),
            outcome="held_missing_patient_name",
            also_matched=(),
            dispatched_rule_ids=("ACC-001",),
            event_input_hash="abc",
            primitive_traces={"ACC-001": trace},
            latency_us=10,
        )
        assert "ACC-001" in decision.primitive_traces
        assert decision.primitive_traces["ACC-001"] == trace


class TestComputeEventInputHash:
    def test_same_ctx_same_hash(self) -> None:
        ctx = _make_ctx()
        h1 = compute_event_input_hash(ctx)
        h2 = compute_event_input_hash(ctx)
        assert h1 == h2

    def test_different_ctx_different_hash(self) -> None:
        ctx1 = _make_ctx(patient_name="Jane Doe")
        ctx2 = _make_ctx(patient_name=None)
        assert compute_event_input_hash(ctx1) != compute_event_input_hash(ctx2)

    def test_hash_is_hex_sha256(self) -> None:
        ctx = _make_ctx()
        h = compute_event_input_hash(ctx)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_different_event_type_different_hash(self) -> None:
        ctx1 = _make_ctx(event_type="order_received")
        ctx2 = _make_ctx(event_type="qc_complete")
        assert compute_event_input_hash(ctx1) != compute_event_input_hash(ctx2)

    def test_different_state_different_hash(self) -> None:
        ctx1 = _make_ctx(state="ACCESSIONING", event_type="order_received")
        ctx2 = _make_ctx(state="SAMPLE_PREP_PROCESSING", event_type="order_received")
        assert compute_event_input_hash(ctx1) != compute_event_input_hash(ctx2)

    def test_nonempty_frozenset_flags_hash_determinism(self) -> None:
        """T3: Two SpecimenContexts with equal flags frozensets constructed in different
        insertion orders produce the same hash — pins the sorted-list normalisation in
        _make_serialisable."""
        flags_a = frozenset({"MISSING_INFO_PROCEED", "RECUT_REQUESTED"})
        flags_b = frozenset({"RECUT_REQUESTED", "MISSING_INFO_PROCEED"})
        ctx1 = _make_ctx(flags=flags_a)
        ctx2 = _make_ctx(flags=flags_b)
        # Python frozensets with the same elements are equal regardless of insertion order,
        # but the hash function serialises via sorted list — this test pins that.
        assert compute_event_input_hash(ctx1) == compute_event_input_hash(ctx2)

    def test_hash_stable_across_pythonhashseed(self) -> None:
        """The hash must be byte-identical across processes with different PYTHONHASHSEED
        values — Python randomises string hashes per process, which used to make
        ``frozenset[str]`` iteration order non-deterministic across processes. The
        in-process companion test above cannot reproduce that condition (PYTHONHASHSEED
        is fixed for the interpreter's lifetime); this subprocess-based test does.
        """
        import os
        import subprocess
        import sys
        import textwrap

        script = textwrap.dedent(
            """
            from samantha_server.engine.decision import compute_event_input_hash
            from samantha_server.models.context import Event, Order, SpecimenContext

            order = Order(
                order_id="O-CROSSSEED",
                patient_name="Jane Doe",
                patient_sex="F",
                age=45,
                specimen_type="biopsy",
                anatomic_site="breast",
                fixative="formalin",
                fixation_time_hours=8.0,
                ordered_tests=("HER2",),
                priority="ROUTINE",
                billing_info_present=True,
            )
            ctx = SpecimenContext(
                order=order,
                current_state="ACCESSIONING",
                flags=frozenset(
                    {"HER2_FIXATION_REJECT", "RECUT_REQUESTED", "MISSING_INFO_PROCEED"}
                ),
                event=Event(event_type="order_received", event_data={}, step_index=0),
            )
            print(compute_event_input_hash(ctx))
            """
        )

        hashes: list[str] = []
        for seed in ("0", "1", "42", "9999"):
            env = {**os.environ, "PYTHONHASHSEED": seed}
            out = subprocess.check_output([sys.executable, "-c", script], env=env, text=True)
            hashes.append(out.strip())

        assert all(h == hashes[0] for h in hashes), (
            f"Hash diverged across PYTHONHASHSEED values: {hashes}"
        )

    def test_different_event_data_different_hash(self) -> None:
        """event_data contents are included in the hash."""
        ctx1 = _make_ctx(event_data={"outcome": "success"})
        ctx2 = _make_ctx(event_data={"outcome": "failure"})
        assert compute_event_input_hash(ctx1) != compute_event_input_hash(ctx2)


class TestEngineDecisionOrderId:
    def test_order_id_defaults_to_none(self) -> None:
        """order_id has an additive optional default of None."""
        decision = EngineDecision(
            applied_rule_id="ACC-001",
            next_state="HOLD",
            flags_added=(),
            flags_cleared=(),
            outcome="held_missing_name",
            also_matched=(),
            dispatched_rule_ids=("ACC-001",),
            event_input_hash="abc123",
            primitive_traces={},
            latency_us=10,
        )
        assert decision.order_id is None

    def test_order_id_round_trips_when_set(self) -> None:
        """order_id set at construction survives round-trip."""
        decision = EngineDecision(
            applied_rule_id="ACC-001",
            next_state="HOLD",
            flags_added=(),
            flags_cleared=(),
            outcome="held_missing_name",
            also_matched=(),
            dispatched_rule_ids=("ACC-001",),
            event_input_hash="abc123",
            primitive_traces={},
            latency_us=10,
            order_id="O-TEST-001",
        )
        assert decision.order_id == "O-TEST-001"


class TestMakeSerializable:
    def test_unsupported_type_raises_type_error(self) -> None:
        """S1: _make_serialisable raises TypeError on unsupported types (datetime, custom)."""
        import datetime

        with pytest.raises(TypeError, match="unsupported type"):
            _make_serialisable(datetime.datetime(2024, 1, 1))

        class _Custom:
            pass

        with pytest.raises(TypeError, match="unsupported type"):
            _make_serialisable(_Custom())

    def test_set_is_serialisable_as_sorted_list(self) -> None:
        """Bare set normalises to a sorted list — same path as frozenset."""
        result = _make_serialisable({3, 1, 2})
        assert result == [1, 2, 3]
