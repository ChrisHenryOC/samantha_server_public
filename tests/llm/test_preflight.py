"""Tests for samantha_server.llm.preflight — PreflightOk/PreflightMissing and preflight()."""

from __future__ import annotations

import pytest

from samantha_server.models.context import Event, Order, SpecimenContext

# ---------------------------------------------------------------------------
# Slice 1: PreflightOk / PreflightMissing — discriminated union shape
# ---------------------------------------------------------------------------


def test_preflight_ok_kind_is_ok() -> None:
    """PreflightOk has kind='ok'."""
    from samantha_server.llm.preflight import PreflightOk

    assert PreflightOk().kind == "ok"


def test_preflight_ok_is_frozen() -> None:
    """PreflightOk is immutable."""
    from samantha_server.llm.preflight import PreflightOk

    result = PreflightOk()
    with pytest.raises((TypeError, AttributeError, ValueError)):
        result.kind = "missing"  # type: ignore[misc]


def test_preflight_missing_kind_is_missing() -> None:
    """PreflightMissing has kind='missing'."""
    from samantha_server.llm.preflight import PreflightMissing

    result = PreflightMissing(missing_fields=("foo",))
    assert result.kind == "missing"


def test_preflight_missing_with_empty_inputs_raises() -> None:
    """PreflightMissing rejects construction with both tuples empty."""
    from samantha_server.llm.preflight import PreflightMissing

    with pytest.raises(ValueError):
        PreflightMissing()


def test_preflight_missing_with_missing_fields_only() -> None:
    """PreflightMissing accepts only missing_fields."""
    from samantha_server.llm.preflight import PreflightMissing

    result = PreflightMissing(missing_fields=("age",))
    assert result.missing_fields == ("age",)
    assert result.unknown_canonical_fields == ()


def test_preflight_missing_with_unknown_canonical_only() -> None:
    """PreflightMissing accepts only unknown_canonical_fields."""
    from samantha_server.llm.preflight import PreflightMissing

    result = PreflightMissing(unknown_canonical_fields=("fixative",))
    assert result.missing_fields == ()
    assert result.unknown_canonical_fields == ("fixative",)


def test_preflight_missing_is_frozen() -> None:
    """PreflightMissing is immutable."""
    from samantha_server.llm.preflight import PreflightMissing

    result = PreflightMissing(missing_fields=("age",))
    with pytest.raises((TypeError, AttributeError, ValueError)):
        result.missing_fields = ("other",)  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Slice 2: preflight() returns PreflightOk on valid context
# ---------------------------------------------------------------------------


def _make_valid_ctx() -> SpecimenContext:
    """Build a SpecimenContext with all known canonical values."""
    return SpecimenContext(
        order=Order(
            order_id="PREFLIGHT-001",
            patient_name="Jane Doe",
            patient_sex="F",
            age=45,
            specimen_type="biopsy",
            anatomic_site="breast",
            fixative="formalin",
            fixation_time_hours=24.0,
            ordered_tests=("ER", "PR"),
            priority="routine",
            billing_info_present=True,
        ),
        current_state="ACCESSIONING",
        flags=frozenset(),
        event=Event(event_type="order_received", event_data={}, step_index=0),
    )


def test_preflight_returns_ok_for_valid_context() -> None:
    """preflight() returns PreflightOk when all canonical fields are known."""
    from samantha_server.llm.preflight import PreflightOk, preflight

    result = preflight(_make_valid_ctx())
    assert isinstance(result, PreflightOk)


# ---------------------------------------------------------------------------
# Slice 3: preflight() flags unknown canonical values
# specimen_type is intentionally excluded from CANONICALIZED_ORDER_FIELDS (ACC-010
# owns unknown specimen routing); these tests use anatomic_site / fixative / priority.
# ---------------------------------------------------------------------------


def _make_ctx_with(
    *,
    anatomic_site: str = "breast",
    fixative: str = "formalin",
    priority: str = "routine",
) -> SpecimenContext:
    """Build a SpecimenContext with overrides for canonicalized scalar fields."""
    return SpecimenContext(
        order=Order(
            order_id="PREFLIGHT-002",
            patient_name=None,
            patient_sex="F",
            age=40,
            specimen_type="biopsy",
            anatomic_site=anatomic_site,
            fixative=fixative,
            fixation_time_hours=24.0,
            ordered_tests=("ER",),
            priority=priority,
            billing_info_present=True,
        ),
        current_state="ACCESSIONING",
        flags=frozenset(),
        event=Event(event_type="order_received", event_data={}, step_index=0),
    )


def test_preflight_flags_unknown_fixative() -> None:
    """preflight() flags unknown fixative in unknown_canonical_fields."""
    from samantha_server.llm.preflight import PreflightMissing, preflight

    result = preflight(_make_ctx_with(fixative="ethanol"))
    assert isinstance(result, PreflightMissing)
    assert result.unknown_canonical_fields == ("fixative",)
    assert result.missing_fields == ()


def test_preflight_flags_unknown_priority() -> None:
    """preflight() flags unknown priority value."""
    from samantha_server.llm.preflight import PreflightMissing, preflight

    result = preflight(_make_ctx_with(priority="critical"))
    assert isinstance(result, PreflightMissing)
    assert result.unknown_canonical_fields == ("priority",)


def test_preflight_does_not_flag_unknown_specimen_type() -> None:
    """specimen_type is excluded from preflight scope (ACC-010 owns this routing)."""
    from samantha_server.llm.preflight import PreflightOk, preflight

    ctx = SpecimenContext(
        order=Order(
            order_id="PREFLIGHT-EXCL",
            patient_name="J",
            patient_sex="F",
            age=40,
            specimen_type="LCMI",  # not in pick list, but preflight ignores specimen_type
            anatomic_site="breast",
            fixative="formalin",
            fixation_time_hours=24.0,
            ordered_tests=(),
            priority="routine",
            billing_info_present=True,
        ),
        current_state="ACCESSIONING",
        flags=frozenset(),
        event=Event(event_type="order_received", event_data={}, step_index=0),
    )
    result = preflight(ctx)
    assert isinstance(result, PreflightOk)


def test_preflight_does_not_flag_off_vocab_anatomic_site() -> None:
    """anatomic_site is excluded from preflight scope (ACC-011 owns this routing).

    Parallel to specimen_type/ACC-010: an off-vocab anatomic_site must reach the
    deterministic rules so ACC-011 can route it to PENDING_LLM_REVIEW, rather than
    being short-circuited into needs_clarification by preflight. Regression guard
    for (SC-115 / LR-006).
    """
    from samantha_server.llm.preflight import PreflightOk, preflight

    result = preflight(_make_ctx_with(anatomic_site="tibia"))
    assert isinstance(result, PreflightOk)


def test_preflight_flags_multiple_unknown_fields() -> None:
    """preflight() reports every unknown canonical field, sorted."""
    from samantha_server.llm.preflight import PreflightMissing, preflight

    result = preflight(_make_ctx_with(fixative="ethanol", priority="critical"))
    assert isinstance(result, PreflightMissing)
    # Sorted iteration order is part of the contract (receipt determinism).
    assert result.unknown_canonical_fields == ("fixative", "priority")


# ---------------------------------------------------------------------------
# Slice 4: REQUIRED_ORDER_FIELDS — exercise `is None` semantics directly.
# REQUIRED_ORDER_FIELDS is empty in production; monkeypatch fills it so the
# falsy-but-valid guarantee is verified at the logic level (M-06).
# ---------------------------------------------------------------------------


def _ctx_with_optional_overrides(
    *,
    fixation_time_hours: float | None = 24.0,
    ordered_tests: tuple[str, ...] = ("ER",),
    billing_info_present: bool = True,
    age: int | None = 45,
) -> SpecimenContext:
    return SpecimenContext(
        order=Order(
            order_id="PREFLIGHT-REQ",
            patient_name="J",
            patient_sex="F",
            age=age,
            specimen_type="biopsy",
            anatomic_site="breast",
            fixative="formalin",
            fixation_time_hours=fixation_time_hours,
            ordered_tests=ordered_tests,
            priority="routine",
            billing_info_present=billing_info_present,
        ),
        current_state="ACCESSIONING",
        flags=frozenset(),
        event=Event(event_type="order_received", event_data={}, step_index=0),
    )


def test_preflight_treats_zero_fixation_time_as_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """fixation_time_hours=0.0 must NOT be flagged as missing — `is None`, not falsy."""
    from samantha_server.llm import preflight as preflight_mod
    from samantha_server.llm.preflight import PreflightOk, preflight

    monkeypatch.setattr(preflight_mod, "REQUIRED_ORDER_FIELDS", frozenset({"fixation_time_hours"}))
    result = preflight(_ctx_with_optional_overrides(fixation_time_hours=0.0))
    assert isinstance(result, PreflightOk)


def test_preflight_treats_null_fixation_time_as_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """fixation_time_hours=None IS flagged when listed in REQUIRED_ORDER_FIELDS."""
    from samantha_server.llm import preflight as preflight_mod
    from samantha_server.llm.preflight import PreflightMissing, preflight

    monkeypatch.setattr(preflight_mod, "REQUIRED_ORDER_FIELDS", frozenset({"fixation_time_hours"}))
    result = preflight(_ctx_with_optional_overrides(fixation_time_hours=None))
    assert isinstance(result, PreflightMissing)
    assert result.missing_fields == ("fixation_time_hours",)


def test_preflight_does_not_flag_empty_ordered_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """ordered_tests=() must NOT be flagged as missing (empty tuple is falsy but valid)."""
    from samantha_server.llm import preflight as preflight_mod
    from samantha_server.llm.preflight import PreflightOk, preflight

    monkeypatch.setattr(preflight_mod, "REQUIRED_ORDER_FIELDS", frozenset({"ordered_tests"}))
    result = preflight(_ctx_with_optional_overrides(ordered_tests=()))
    assert isinstance(result, PreflightOk)


def test_preflight_does_not_flag_false_billing(monkeypatch: pytest.MonkeyPatch) -> None:
    """billing_info_present=False must NOT be flagged (False is falsy but valid)."""
    from samantha_server.llm import preflight as preflight_mod
    from samantha_server.llm.preflight import PreflightOk, preflight

    monkeypatch.setattr(preflight_mod, "REQUIRED_ORDER_FIELDS", frozenset({"billing_info_present"}))
    result = preflight(_ctx_with_optional_overrides(billing_info_present=False))
    assert isinstance(result, PreflightOk)


def test_preflight_missing_fields_sorted(monkeypatch: pytest.MonkeyPatch) -> None:
    """preflight()'s missing_fields tuple is sorted (receipt determinism)."""
    from samantha_server.llm import preflight as preflight_mod
    from samantha_server.llm.preflight import PreflightMissing, preflight

    monkeypatch.setattr(
        preflight_mod, "REQUIRED_ORDER_FIELDS", frozenset({"age", "fixation_time_hours"})
    )
    result = preflight(_ctx_with_optional_overrides(age=None, fixation_time_hours=None))
    assert isinstance(result, PreflightMissing)
    assert result.missing_fields == ("age", "fixation_time_hours")
