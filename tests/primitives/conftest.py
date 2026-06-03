"""Shared fixtures for primitives tests."""

from typing import Any

import pytest

from samantha_server.models import Event, Order, SpecimenContext


def _make_order(**kwargs: Any) -> Order:
    defaults: dict[str, Any] = {
        "order_id": "ORD-001",
        "patient_name": "Jane Doe",
        "patient_sex": "F",
        "age": 45,
        "specimen_type": "core_needle_biopsy",
        "anatomic_site": "breast",
        "fixative": "formalin",
        "fixation_time_hours": 24.0,
        "ordered_tests": ("er", "pr", "her2"),
        "priority": "routine",
        "billing_info_present": True,
    }
    defaults.update(kwargs)
    return Order(**defaults)


def _make_event(**kwargs: Any) -> Event:
    defaults: dict[str, Any] = {
        "event_type": "order_received",
        "event_data": {},
        "step_index": 0,
    }
    defaults.update(kwargs)
    return Event(**defaults)


def make_context(**kwargs: Any) -> SpecimenContext:
    """Build a SpecimenContext with sensible defaults; override via kwargs."""
    order_kwargs = {
        k: v for k, v in kwargs.items() if k not in ("current_state", "flags", "event", "order")
    }
    if order_kwargs and "order" not in kwargs:
        order = _make_order(**order_kwargs)
    else:
        order = kwargs.get("order", _make_order())

    event = kwargs.get("event", _make_event())
    defaults: dict[str, Any] = {
        "order": order,
        "current_state": "ACCESSIONING",
        "flags": frozenset(),
        "event": event,
    }
    for k in ("current_state", "flags", "event"):
        if k in kwargs:
            defaults[k] = kwargs[k]
    return SpecimenContext(**defaults)


@pytest.fixture
def ctx() -> SpecimenContext:
    """Default context: all order fields populated, empty flags, ACCESSIONING state."""
    return make_context()


@pytest.fixture
def ctx_null_patient() -> SpecimenContext:
    """Context where patient_name is None."""
    return make_context(patient_name=None)
