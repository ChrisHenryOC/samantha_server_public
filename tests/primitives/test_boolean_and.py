"""Tests for BooleanAnd primitive."""

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from samantha_server.models import SpecimenContext
from samantha_server.primitives import BooleanAnd, Equals, IsNull, Not
from samantha_server.primitives.trace import PrimitiveTrace

from .conftest import make_context

# ---------------------------------------------------------------------------
# Test double: counting primitive that records how many times evaluate() fires.
# model_construct() bypasses Pydantic's union validation so we can pass
# _CountingChild (a real BaseModel) into BooleanAnd.children without it needing
# to be in the Primitive union.
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


class TestBooleanAndEvaluate:
    def test_returns_true_when_all_children_true(self) -> None:
        ctx = make_context()
        p = BooleanAnd(
            children=(
                Equals(field="fixative", value="formalin"),
                Equals(field="priority", value="routine"),
            )
        )
        assert p.evaluate(ctx) is True

    def test_returns_false_when_one_child_false(self) -> None:
        ctx = make_context()
        p = BooleanAnd(
            children=(
                Equals(field="fixative", value="formalin"),
                Equals(field="priority", value="stat"),  # False
            )
        )
        assert p.evaluate(ctx) is False

    def test_returns_false_when_first_child_false(self) -> None:
        ctx = make_context()
        p = BooleanAnd(
            children=(
                Equals(field="fixative", value="ethanol"),  # False
                Equals(field="priority", value="routine"),
            )
        )
        assert p.evaluate(ctx) is False

    def test_single_child_true(self) -> None:
        ctx = make_context()
        p = BooleanAnd(children=(Equals(field="fixative", value="formalin"),))
        assert p.evaluate(ctx) is True

    def test_single_child_false(self) -> None:
        ctx = make_context()
        p = BooleanAnd(children=(Equals(field="fixative", value="ethanol"),))
        assert p.evaluate(ctx) is False

    def test_empty_children_raises(self) -> None:
        with pytest.raises(ValidationError):
            BooleanAnd(children=())

    def test_is_frozen(self) -> None:
        p = BooleanAnd(children=(Equals(field="fixative", value="formalin"),))
        with pytest.raises(ValidationError):
            p.children = ()  # type: ignore[misc]

    def test_accepts_not_as_child(self) -> None:
        ctx = make_context(patient_name=None)
        p = BooleanAnd(
            children=(
                IsNull(field="patient_name"),
                Not(child=IsNull(field="fixative")),
            )
        )
        assert p.evaluate(ctx) is True


class TestBooleanAndShortCircuit:
    def test_evaluate_short_circuits_on_first_false_child(self) -> None:
        """BooleanAnd.evaluate() must not call the third child when the second is false.

        model_construct() is used to bypass Pydantic union validation so a
        plain _CountingChild (not in the Primitive union) can be injected.
        """
        _EVAL_COUNTS.clear()
        ctx = make_context()
        p = BooleanAnd.model_construct(
            children=(
                _CountingChild(counter_key="c0", returns=True),
                _CountingChild(counter_key="c1", returns=False),
                _CountingChild(counter_key="c2", returns=True),
            )
        )
        result = p.evaluate(ctx)
        assert result is False
        assert _EVAL_COUNTS.get("c0", 0) == 1
        assert _EVAL_COUNTS.get("c1", 0) == 1
        assert _EVAL_COUNTS.get("c2", 0) == 0  # never called — short-circuit worked

    def test_short_circuits_on_first_false_trace_omits_remaining_children(self) -> None:
        """When the first child is false, BooleanAnd stops and the trace has only 1 child."""
        ctx = make_context()
        p = BooleanAnd(
            children=(
                Equals(field="fixative", value="ethanol"),  # False — should stop here
                Equals(field="priority", value="routine"),  # Would be True but never evaluated
                Equals(field="specimen_type", value="core_needle_biopsy"),  # Never evaluated
            )
        )
        trace = p.trace(ctx)
        assert trace.result is False
        assert len(trace.children) == 1
        assert trace.children[0].result is False

    def test_evaluates_all_when_all_true(self) -> None:
        ctx = make_context()
        p = BooleanAnd(
            children=(
                Equals(field="fixative", value="formalin"),
                Equals(field="priority", value="routine"),
                Equals(field="specimen_type", value="core_needle_biopsy"),
            )
        )
        trace = p.trace(ctx)
        assert trace.result is True
        assert len(trace.children) == 3


class TestBooleanAndTrace:
    def test_trace_positive(self) -> None:
        ctx = make_context()
        p = BooleanAnd(
            children=(
                Equals(field="fixative", value="formalin"),
                Equals(field="priority", value="routine"),
            )
        )
        trace = p.trace(ctx)
        assert trace.primitive == "BooleanAnd"
        assert trace.field is None
        assert trace.expected is None
        assert trace.actual is None
        assert trace.result is True
        assert len(trace.children) == 2

    def test_trace_negative(self) -> None:
        ctx = make_context()
        p = BooleanAnd(
            children=(
                Equals(field="fixative", value="formalin"),
                Equals(field="priority", value="stat"),  # False
            )
        )
        trace = p.trace(ctx)
        assert trace.result is False
        assert len(trace.children) == 2
        assert trace.children[0].result is True
        assert trace.children[1].result is False
