"""Tests for samantha_server.api.preflight — stable re-export shim."""

from __future__ import annotations


def test_api_preflight_exports_preflight_function() -> None:
    """api.preflight.preflight is the same object as llm.preflight.preflight."""
    from samantha_server.api import preflight as api_preflight
    from samantha_server.llm import preflight as llm_preflight

    assert api_preflight.preflight is llm_preflight.preflight


def test_api_preflight_exports_preflight_missing() -> None:
    """api.preflight.PreflightMissing is the same object as llm.preflight.PreflightMissing."""
    from samantha_server.api.preflight import PreflightMissing as api_cls
    from samantha_server.llm.preflight import PreflightMissing as llm_cls

    assert api_cls is llm_cls


def test_api_preflight_returns_same_result_for_fixed_input() -> None:
    """preflight imported from api.preflight returns the same result as from llm.preflight."""
    from samantha_server.api.preflight import PreflightOk, preflight
    from samantha_server.models.context import Event, Order, SpecimenContext

    ctx = SpecimenContext(
        order=Order(
            order_id="TEST-001",
            patient_name=None,
            patient_sex="F",
            age=40,
            specimen_type="biopsy",
            anatomic_site="breast",
            fixative="formalin",
            fixation_time_hours=24.0,
            ordered_tests=("ER",),
            priority="routine",
            billing_info_present=True,
        ),
        current_state="ACCESSIONING",
        flags=frozenset(),
        event=Event(event_type="order_received", event_data={}, step_index=0),
    )
    result = preflight(ctx)
    assert isinstance(result, PreflightOk)
