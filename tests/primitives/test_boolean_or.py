"""Tests for BooleanOr primitive."""

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from samantha_server.models import SpecimenContext
from samantha_server.primitives import BooleanOr, Equals, IsNull, Not
from samantha_server.primitives.trace import PrimitiveTrace

from .conftest import make_context

# ---------------------------------------------------------------------------
# Test double (mirrors test_boolean_and._CountingChild).
# ---------------------------------------------------------------------------

_EVAL_COUNTS: dict[str, int] = {}


class _CountingChild(BaseModel):
    """Minimal primitive-shaped model that counts evaluate() calls."""

    model_config = ConfigDict(frozen=True)

    counter_key: str
    returns: bool

    def evaluate(self, ctx: SpecimenContext) -> bool:
        _EVAL_COUNTS[self.counter_key] = _EVAL_COUNTS.get(self.counter_key, 0) + 1
        return self.returns

    def trace(self, ctx: SpecimenContext) -> PrimitiveTrace:
        return PrimitiveTrace(
            primitive="IsNull",
            field=None,
            expected=None,
            actual=None,
            result=self.evaluate(ctx),
        )


class TestBooleanOrEvaluate:
    def test_returns_true_when_first_child_true(self) -> None:
        ctx = make_context()
        p = BooleanOr(
            children=(
                Equals(field="fixative", value="formalin"),
                Equals(field="priority", value="stat"),  # False but irrelevant
            )
        )
        assert p.evaluate(ctx) is True

    def test_returns_true_when_second_child_true(self) -> None:
        ctx = make_context()
        p = BooleanOr(
            children=(
                Equals(field="fixative", value="ethanol"),  # False
                Equals(field="priority", value="routine"),  # True
            )
        )
        assert p.evaluate(ctx) is True

    def test_returns_false_when_all_children_false(self) -> None:
        ctx = make_context()
        p = BooleanOr(
            children=(
                Equals(field="fixative", value="ethanol"),
                Equals(field="priority", value="stat"),
            )
        )
        assert p.evaluate(ctx) is False

    def test_single_child_true(self) -> None:
        ctx = make_context()
        p = BooleanOr(children=(Equals(field="fixative", value="formalin"),))
        assert p.evaluate(ctx) is True

    def test_single_child_false(self) -> None:
        ctx = make_context()
        p = BooleanOr(children=(Equals(field="fixative", value="ethanol"),))
        assert p.evaluate(ctx) is False

    def test_empty_children_raises(self) -> None:
        with pytest.raises(ValidationError):
            BooleanOr(children=())

    def test_is_frozen(self) -> None:
        p = BooleanOr(children=(Equals(field="fixative", value="formalin"),))
        with pytest.raises(ValidationError):
            p.children = ()  # type: ignore[misc]

    def test_accepts_not_as_child(self) -> None:
        ctx = make_context(patient_name=None)
        # Not(IsNull(fixative)) = Not(false) = true → Or short-circuits here
        p = BooleanOr(
            children=(
                Not(child=IsNull(field="fixative")),
                IsNull(field="patient_name"),
            )
        )
        assert p.evaluate(ctx) is True


class TestBooleanOrShortCircuit:
    def test_evaluate_short_circuits_on_first_true_child(self) -> None:
        """BooleanOr.evaluate() must not call the third child when the second is true.

        model_construct() bypasses Pydantic union validation so _CountingChild
        can be injected without being in the Primitive union.
        """
        _EVAL_COUNTS.clear()
        ctx = make_context()
        p = BooleanOr.model_construct(
            children=(
                _CountingChild(counter_key="c0", returns=False),
                _CountingChild(counter_key="c1", returns=True),
                _CountingChild(counter_key="c2", returns=False),
            )
        )
        result = p.evaluate(ctx)
        assert result is True
        assert _EVAL_COUNTS.get("c0", 0) == 1
        assert _EVAL_COUNTS.get("c1", 0) == 1
        assert _EVAL_COUNTS.get("c2", 0) == 0  # never called — short-circuit worked

    def test_short_circuits_on_first_true_trace_omits_remaining_children(self) -> None:
        """When the first child is true, BooleanOr stops and the trace has only 1 child."""
        ctx = make_context()
        p = BooleanOr(
            children=(
                Equals(field="fixative", value="formalin"),  # True — stops here
                Equals(field="priority", value="stat"),  # False, but never evaluated
                Equals(field="specimen_type", value="NEVER"),  # Never evaluated
            )
        )
        trace = p.trace(ctx)
        assert trace.result is True
        assert len(trace.children) == 1
        assert trace.children[0].result is True

    def test_evaluates_all_when_all_false(self) -> None:
        ctx = make_context()
        p = BooleanOr(
            children=(
                Equals(field="fixative", value="ethanol"),
                Equals(field="priority", value="stat"),
                Equals(field="specimen_type", value="NEVER"),
            )
        )
        trace = p.trace(ctx)
        assert trace.result is False
        assert len(trace.children) == 3


class TestBooleanOrTrace:
    def test_trace_positive(self) -> None:
        ctx = make_context()
        p = BooleanOr(
            children=(
                Equals(field="fixative", value="ethanol"),  # False
                Equals(field="priority", value="routine"),  # True
            )
        )
        trace = p.trace(ctx)
        assert trace.primitive == "BooleanOr"
        assert trace.field is None
        assert trace.expected is None
        assert trace.actual is None
        assert trace.result is True
        assert len(trace.children) == 2  # both evaluated before finding true
        assert trace.children[0].result is False
        assert trace.children[1].result is True

    def test_trace_negative(self) -> None:
        ctx = make_context()
        p = BooleanOr(
            children=(
                Equals(field="fixative", value="ethanol"),
                Equals(field="priority", value="stat"),
            )
        )
        trace = p.trace(ctx)
        assert trace.result is False
        assert len(trace.children) == 2  # all evaluated, all false
