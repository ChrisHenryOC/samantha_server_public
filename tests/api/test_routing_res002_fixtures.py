"""Revalidate the four RES-002 billing fixtures through production dispatch_event.

Closes the parent criterion #2. The four fixtures below assert, at their step-14
``missing_info_received`` event with ``info_type: "billing"``, that the order
resolves to ``next_state: RESULTING`` with ``flags: []`` (MISSING_INFO_PROCEED
cleared by the action handler). Historically that clearing lived in a replay-only
stub (``scenarios/action_handlers.py``); moved it into the production engine
(``engine/action_handlers.py``) and wired it into ``dispatch_event``.

These tests prove the four named fixtures resolve correctly end-to-end via the
production ``dispatch_event`` path — not just through the replay harness. Each test
reconstructs the step-14 input directly from the fixture (the order from step 0, the
entry ``current_state``/``flags`` from the immediately-preceding event's
``expected_output``, and the event from step 14) and asserts the fixture's own
step-14 ``expected_output``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from samantha_server.models.context import Event, Order, SpecimenContext
from samantha_server.queue.priority import EventPriority
from tests.api.test_routing_res002 import _make_deps, _make_rule_index

_FIXTURES_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "scenarios"

# The four RES-002 billing fixtures from the audit (2026-05-13 audit, F-15).
_RES002_BILLING_FIXTURES = [
    _FIXTURES_ROOT / "rule_coverage" / "sc-072.json",
    _FIXTURES_ROOT / "rule_coverage" / "sc-079.json",
    _FIXTURES_ROOT / "accumulated_state" / "sc-091.json",
    _FIXTURES_ROOT / "accumulated_state" / "sc-098.json",
]


def _build_order(order_data: dict[str, Any], order_id: str) -> Order:
    """Build an Order from a fixture's step-0 order_received event_data."""
    return Order(
        order_id=order_id,
        patient_name=order_data["patient_name"],
        patient_sex=order_data["sex"],
        age=order_data["age"],
        specimen_type=order_data["specimen_type"],
        anatomic_site=order_data["anatomic_site"],
        fixative=order_data["fixative"],
        fixation_time_hours=order_data["fixation_time_hours"],
        ordered_tests=tuple(order_data["ordered_tests"]),
        priority=order_data["priority"],
        billing_info_present=order_data["billing_info_present"],
    )


def _find_billing_step_index(events: list[dict[str, Any]]) -> int:
    """Return the index of the missing_info_received billing step."""
    for i, ev in enumerate(events):
        if ev["event_type"] == "missing_info_received" and (
            ev["event_data"].get("info_type") == "billing"
        ):
            return i
    raise AssertionError("fixture has no missing_info_received/billing step")


@pytest.mark.parametrize("fixture_path", _RES002_BILLING_FIXTURES, ids=lambda p: p.stem)
def test_res002_billing_fixture_resolves_through_dispatch_event(fixture_path: Path) -> None:
    """Each RES-002 billing fixture's step-14 resolves to RESULTING via dispatch_event."""
    raw = json.loads(fixture_path.read_text())
    events = raw["events"]

    billing_idx = _find_billing_step_index(events)
    assert billing_idx > 0, "billing step cannot be the first event"

    billing_step = events[billing_idx]
    entry = events[billing_idx - 1]["expected_output"]
    expected = billing_step["expected_output"]

    # Sanity: the fixture itself documents the billing-clearing outcome we revalidate.
    assert entry["next_state"] == "RESULTING_HOLD"
    assert entry["flags"] == ["MISSING_INFO_PROCEED"]
    assert expected["next_state"] == "RESULTING"
    assert expected["applied_rules"] == ["RES-002"]
    assert expected["flags"] == []

    ctx = SpecimenContext(
        order=_build_order(events[0]["event_data"], order_id=raw["scenario_id"]),
        current_state=entry["next_state"],
        flags=frozenset(entry["flags"]),
        event=Event(
            event_type=billing_step["event_type"],
            event_data=billing_step["event_data"],
            step_index=billing_idx,
        ),
    )

    from samantha_server.api.routing import dispatch_event

    deps = _make_deps(rule_index=_make_rule_index())
    written = deps.pop("_written")

    async def run() -> tuple[Any, Any, Any]:
        return await dispatch_event(
            ctx,
            session_id=f"gh235-{raw['scenario_id']}",
            priority=EventPriority.ROUTINE,
            queue_wait_us=0,
            **deps,
        )

    decision, dispatch_ctx, _receipt = asyncio.run(run())

    assert dispatch_ctx.routing_path == "deterministic"
    assert decision.applied_rule_id == "RES-002"
    assert decision.next_state == "RESULTING", (
        f"{fixture_path.stem}: expected RESULTING (MISSING_INFO_PROCEED cleared), "
        f"got {decision.next_state!r}"
    )
    # The signed receipt carries the same resolved state.
    assert len(written) == 1
    assert written[0].decision.next_state == "RESULTING"
