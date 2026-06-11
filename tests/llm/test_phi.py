"""Tests for samantha_server.llm.phi — PHI strip/hash pipeline."""

from __future__ import annotations

import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from samantha_server.models.context import VALID_STATES

# ---------------------------------------------------------------------------
# Slice 2 — SafeOrder / SafeContext shape tests
# ---------------------------------------------------------------------------


def test_order_pass_through_is_frozenset() -> None:
    from samantha_server.llm.phi import _ORDER_PASS_THROUGH

    assert isinstance(_ORDER_PASS_THROUGH, frozenset)


def test_order_pass_through_covers_exactly_non_phi_fields() -> None:
    """_ORDER_PASS_THROUGH must match the documented allowlist exactly.

    order_id is now a pass-through (synthetic LIS id, not Safe Harbor,
    local oMLX inside trust boundary).

    If a future Order field is added without updating this set, this test
    catches the discrepancy.
    """
    from samantha_server.llm.phi import _ORDER_PASS_THROUGH

    expected = frozenset(
        {
            "order_id",
            "specimen_type",
            "anatomic_site",
            "fixative",
            "fixation_time_hours",
            "ordered_tests",
            "priority",
            "billing_info_present",
            "age",
        }
    )
    assert expected == _ORDER_PASS_THROUGH


def test_order_pass_through_subset_of_safe_order_fields() -> None:
    """Every pass-through Order field name must appear verbatim in SafeOrder.

    Catches drift between the allowlist and the SafeOrder constructor block:
    if a name is added to _ORDER_PASS_THROUGH but the corresponding field
    is not declared on SafeOrder, the field is silently dropped at runtime.
    """
    from samantha_server.llm.phi import _ORDER_PASS_THROUGH, SafeOrder

    safe_order_fields = set(SafeOrder.model_fields)
    assert safe_order_fields >= _ORDER_PASS_THROUGH, (
        f"_ORDER_PASS_THROUGH names not present on SafeOrder: "
        f"{_ORDER_PASS_THROUGH - safe_order_fields}"
    )


def test_every_order_field_is_classified() -> None:
    """Structural completeness: every Order field is pass-through, hashed, or stripped.

    Adding a new field to Order without updating _ORDER_PASS_THROUGH,
    _HASHED_PHI_FIELDS, or _STRIPPED_PHI_FIELDS would silently drop it
    from SafeOrder — no exception, no log. This test forces the maintainer
    to make the classification explicit.

    A "hashed" field <name> must appear on SafeOrder as <name>_hash.
    A "stripped" field has no representation on SafeOrder.
    """
    from samantha_server.llm.phi import (
        _HASHED_PHI_FIELDS,
        _ORDER_PASS_THROUGH,
        _STRIPPED_PHI_FIELDS,
        SafeOrder,
    )
    from samantha_server.models.context import Order

    safe_order_fields = set(SafeOrder.model_fields)
    hashed_targets = {f"{name}_hash" for name in _HASHED_PHI_FIELDS}
    assert hashed_targets <= safe_order_fields, (
        f"_HASHED_PHI_FIELDS expects SafeOrder fields {hashed_targets - safe_order_fields} "
        f"that are not declared."
    )

    classified = _ORDER_PASS_THROUGH | _HASHED_PHI_FIELDS | _STRIPPED_PHI_FIELDS
    unclassified = set(Order.model_fields) - classified
    assert not unclassified, (
        f"Order fields {unclassified} are not classified as pass-through, hashed, "
        f"or stripped. Update _ORDER_PASS_THROUGH, _HASHED_PHI_FIELDS, or "
        f"_STRIPPED_PHI_FIELDS in samantha_server/llm/phi.py and adjust "
        f"phi_safe() / SafeOrder accordingly."
    )

    # And the inverse: every classification entry must name a real Order field.
    stale = classified - set(Order.model_fields)
    assert not stale, (
        f"Classification names {stale} do not correspond to any Order field. "
        f"Remove them or rename them to match Order.model_fields."
    )


def test_safe_order_is_frozen_pydantic() -> None:
    from samantha_server.llm.phi import SafeOrder

    s = SafeOrder(
        order_id="ORD-FROZEN-001",
        specimen_type="Biopsy",
        anatomic_site="Breast",
        fixative="Formalin",
        fixation_time_hours=24.0,
        ordered_tests=("HER2",),
        priority="ROUTINE",
        billing_info_present=True,
        age=45,
    )
    with pytest.raises((TypeError, AttributeError, ValidationError)):
        s.order_id = "other"  # type: ignore[misc]


def test_safe_context_is_frozen_pydantic() -> None:
    from samantha_server.llm.phi import SafeContext, SafeOrder

    safe_order = SafeOrder(
        order_id="ORD-CTX-FROZEN-001",
        specimen_type="Biopsy",
        anatomic_site="Breast",
        fixative="Formalin",
        fixation_time_hours=24.0,
        ordered_tests=("HER2",),
        priority="ROUTINE",
        billing_info_present=True,
        age=45,
    )
    sc = SafeContext(
        order=safe_order,
        current_state="ACCESSIONING",
        flags=("RECUT_REQUESTED",),
        event_type="ORDER_RECEIVED",
        event_data_hash="a" * 64,
    )
    with pytest.raises((TypeError, AttributeError, ValidationError)):
        sc.current_state = "ACCEPTED"  # type: ignore[misc]


def test_safe_context_flags_is_tuple_of_strings() -> None:
    from samantha_server.llm.phi import SafeContext, SafeOrder

    safe_order = SafeOrder(
        order_id="ORD-FLAGS-001",
        specimen_type="Biopsy",
        anatomic_site="Breast",
        fixative="Formalin",
        fixation_time_hours=None,
        ordered_tests=(),
        priority="STAT",
        billing_info_present=False,
        age=None,
    )
    sc = SafeContext(
        order=safe_order,
        current_state="ACCEPTED",
        flags=("MISSING_INFO_PROCEED", "RECUT_REQUESTED"),
        event_type="ORDER_RECEIVED",
        event_data_hash="b" * 64,
    )
    assert isinstance(sc.flags, tuple)
    assert all(isinstance(f, str) for f in sc.flags)


def test_safe_context_flags_round_trips_through_json() -> None:
    from samantha_server.llm.phi import SafeContext, SafeOrder

    safe_order = SafeOrder(
        order_id="ORD-FLAGS-JSON-001",
        specimen_type="Biopsy",
        anatomic_site="Breast",
        fixative="Formalin",
        fixation_time_hours=None,
        ordered_tests=(),
        priority="STAT",
        billing_info_present=False,
        age=None,
    )
    flags_input: tuple[str, ...] = ("FLAG_A", "FLAG_B")
    sc = SafeContext(
        order=safe_order,
        current_state="ACCEPTED",
        flags=flags_input,
        event_type="ORDER_RECEIVED",
        event_data_hash="c" * 64,
    )
    dumped = sc.model_dump_json()
    parsed = json.loads(dumped)
    assert parsed["flags"] == list(flags_input)


# ---------------------------------------------------------------------------
# Helpers shared by Slices 3–6
# ---------------------------------------------------------------------------


def _make_ctx(
    *,
    order_id: str = "ORD-001",
    patient_name: str | None = "Jane Doe",
    patient_sex: str | None = "F",
    age: int | None = 45,
    specimen_type: str | None = "Biopsy",
    anatomic_site: str | None = "Breast",
    fixative: str | None = "Formalin",
    fixation_time_hours: float | None = 24.0,
    ordered_tests: tuple[str, ...] = ("HER2",),
    priority: str | None = "ROUTINE",
    billing_info_present: bool = True,
    current_state: str = "ACCESSIONING",
    flags: frozenset[str] = frozenset(),
    event_type: str = "order_received",
    event_data: dict[str, object] | None = None,
    step_index: int = 0,
) -> object:
    """Build a SpecimenContext for tests — defaults are benign (no PHI boundary)."""
    from samantha_server.models.context import Event, Order, SpecimenContext

    order = Order(
        order_id=order_id,
        patient_name=patient_name,
        patient_sex=patient_sex,
        age=age,
        specimen_type=specimen_type,
        anatomic_site=anatomic_site,
        fixative=fixative,
        fixation_time_hours=fixation_time_hours,
        ordered_tests=ordered_tests,
        priority=priority,
        billing_info_present=billing_info_present,
    )
    event = Event(
        event_type=event_type,
        event_data=event_data or {},
        step_index=step_index,
    )
    return SpecimenContext(
        order=order,
        current_state=current_state,
        flags=flags,
        event=event,
    )


# ---------------------------------------------------------------------------
# Slice 3 — phi_safe happy path
# ---------------------------------------------------------------------------


def test_phi_safe_returns_safe_context_for_benign_ctx() -> None:
    from samantha_server.llm.phi import SafeContext, phi_safe
    from samantha_server.models.context import SpecimenContext

    ctx = _make_ctx()
    assert isinstance(ctx, SpecimenContext)
    result = phi_safe(ctx)  # type: ignore[arg-type]
    assert isinstance(result, SafeContext)


def test_phi_safe_propagates_none_for_relaxed_str_fields() -> None:
    """SC-104 shape: all four newly-nullable Order str fields are None;
    phi_safe must carry them through to SafeOrder as None rather than
    coercing to "" or dropping them.
    """
    from samantha_server.llm.phi import phi_safe

    ctx = _make_ctx(
        specimen_type=None,
        anatomic_site=None,
        fixative=None,
        priority=None,
    )
    result = phi_safe(ctx)  # type: ignore[arg-type]
    assert result.order.specimen_type is None
    assert result.order.anatomic_site is None
    assert result.order.fixative is None
    assert result.order.priority is None


def test_phi_safe_order_id_passes_through_verbatim() -> None:
    """order_id is now a pass-through (synthetic LIS id, not Safe Harbor).

    phi_safe(ctx).order.order_id must equal ctx.order.order_id verbatim.
    SafeOrder no longer has order_id_hash.
    """
    from samantha_server.llm.phi import phi_safe

    ctx = _make_ctx(order_id="ORD-UNIQUE-42")
    result = phi_safe(ctx)  # type: ignore[arg-type]
    assert result.order.order_id == "ORD-UNIQUE-42"
    assert not hasattr(result.order, "order_id_hash")


def test_phi_safe_pass_through_fields_survive_verbatim() -> None:
    from samantha_server.llm.phi import phi_safe

    ctx = _make_ctx(
        specimen_type="Surgical Excision",
        anatomic_site="Prostate",
        fixative="NBF",
        fixation_time_hours=12.5,
        ordered_tests=("PD-L1", "HER2"),
        priority="STAT",
        billing_info_present=False,
        age=72,
    )
    result = phi_safe(ctx)  # type: ignore[arg-type]
    assert result.order.specimen_type == "Surgical Excision"
    assert result.order.anatomic_site == "Prostate"
    assert result.order.fixative == "NBF"
    assert result.order.fixation_time_hours == 12.5
    assert result.order.ordered_tests == ("PD-L1", "HER2")
    assert result.order.priority == "STAT"
    assert result.order.billing_info_present is False
    assert result.order.age == 72


def test_phi_safe_patient_name_not_in_json() -> None:
    from samantha_server.llm.phi import phi_safe

    ctx = _make_ctx(patient_name="Alice Wonderland")
    result = phi_safe(ctx)  # type: ignore[arg-type]
    safe_str = result.model_dump_json()
    assert "Alice Wonderland" not in safe_str


def test_phi_safe_patient_sex_not_in_json() -> None:
    from samantha_server.llm.phi import phi_safe

    ctx = _make_ctx(patient_sex="intersex")
    result = phi_safe(ctx)  # type: ignore[arg-type]
    safe_str = result.model_dump_json()
    assert "intersex" not in safe_str


def test_phi_safe_order_id_is_in_json() -> None:
    """order_id is a pass-through; it must appear verbatim in SafeContext JSON."""
    from samantha_server.llm.phi import phi_safe

    ctx = _make_ctx(order_id="UNIQUE-ORDER-XYZ-789")
    result = phi_safe(ctx)  # type: ignore[arg-type]
    safe_str = result.model_dump_json()
    assert "UNIQUE-ORDER-XYZ-789" in safe_str


def test_phi_safe_event_data_hash_is_64_char_hex() -> None:
    from samantha_server.llm.phi import phi_safe

    ctx = _make_ctx(event_data={"key": "value", "num": 42})
    result = phi_safe(ctx)  # type: ignore[arg-type]
    h = result.event_data_hash
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


# ---------------------------------------------------------------------------
# Slice 4 — age > 89 guard (G18)
# ---------------------------------------------------------------------------


def test_phi_safe_age_90_raises_phi_boundary_error() -> None:
    from samantha_server.errors import PHIBoundaryError
    from samantha_server.llm.phi import phi_safe

    ctx = _make_ctx(age=90)
    with pytest.raises(PHIBoundaryError) as exc_info:
        phi_safe(ctx)  # type: ignore[arg-type]
    assert exc_info.value.age == 90


def test_phi_safe_age_91_raises_phi_boundary_error_with_age_91() -> None:
    from samantha_server.errors import PHIBoundaryError
    from samantha_server.llm.phi import phi_safe

    ctx = _make_ctx(age=91)
    with pytest.raises(PHIBoundaryError) as exc_info:
        phi_safe(ctx)  # type: ignore[arg-type]
    assert exc_info.value.age == 91


def test_phi_safe_age_89_does_not_raise() -> None:
    from samantha_server.llm.phi import SafeContext, phi_safe

    ctx = _make_ctx(age=89)
    result = phi_safe(ctx)  # type: ignore[arg-type]
    assert isinstance(result, SafeContext)
    assert result.order.age == 89


def test_phi_safe_age_none_does_not_raise() -> None:
    from samantha_server.llm.phi import SafeContext, phi_safe

    ctx = _make_ctx(age=None)
    result = phi_safe(ctx)  # type: ignore[arg-type]
    assert isinstance(result, SafeContext)
    assert result.order.age is None


# ---------------------------------------------------------------------------
# Slice 5 — Hypothesis property test: no raw PHI value leaks (G14)
# ---------------------------------------------------------------------------


@given(
    # PHI fields use st.uuids() (rendered as str) to guarantee the raw value
    # is a 36-char UUID-format string that is almost certainly unique within
    # the generated JSON.  Short or repeated strings like '00000000' collide
    # with pass-through field content and produce false positives in the
    # substring-absence check.
    #
    # patient_sex must fit FIELD_MAX_LENGTHS["patient_sex"] = 10 chars.
    # u.hex[:10] gives 10 hex chars — preserves UUID-level uniqueness
    # within the 10-char budget without a low-entropy "Sex-" prefix that
    # could collide as a substring with pass-through field content.
    order_id=st.uuids().map(str),
    patient_name=st.one_of(st.none(), st.uuids().map(lambda u: f"Name-{u}")),
    patient_sex=st.one_of(st.none(), st.uuids().map(lambda u: u.hex[:10])),
    age=st.one_of(st.none(), st.integers(min_value=0, max_value=89)),
    specimen_type=st.text(min_size=1, max_size=99),
    anatomic_site=st.text(min_size=1, max_size=99),
    fixative=st.text(min_size=1, max_size=49),
    fixation_time_hours=st.one_of(
        st.none(),
        st.floats(
            min_value=0.0,
            max_value=10000.0,
            allow_nan=False,
            allow_infinity=False,
        ),
    ),
    priority=st.text(min_size=1, max_size=19),
    billing_info_present=st.booleans(),
    current_state=st.sampled_from(sorted(VALID_STATES)),
    event_type=st.text(min_size=1, max_size=49),
)
@settings(max_examples=1000)
def test_phi_safe_field_introspection_no_leak(
    order_id: str,
    patient_name: str | None,
    patient_sex: str | None,
    age: int | None,
    specimen_type: str,
    anatomic_site: str,
    fixative: str,
    fixation_time_hours: float | None,
    priority: str,
    billing_info_present: bool,
    current_state: str,
    event_type: str,
) -> None:
    """Field-introspection PHI-absence: no raw PHI value appears in
    SafeContext JSON unless its field is in _ORDER_PASS_THROUGH.

    PHI string fields use st.uuids() (36-char) to ensure generated values
    are unique within the JSON output.  Short or repeated strings (e.g.
    '00000000') can collide as substrings with pass-through field content
    and produce false positives in the substring-absence check.
    """
    from samantha_server.llm.phi import _ORDER_PASS_THROUGH, phi_safe
    from samantha_server.models.context import Event, Order, SpecimenContext

    try:
        order = Order(
            order_id=order_id,
            patient_name=patient_name,
            patient_sex=patient_sex,
            age=age,
            specimen_type=specimen_type,
            anatomic_site=anatomic_site,
            fixative=fixative,
            fixation_time_hours=fixation_time_hours,
            ordered_tests=(),
            priority=priority,
            billing_info_present=billing_info_present,
        )
    except (ValidationError, ValueError):
        # Hypothesis may generate strings that violate FIELD_MAX_LENGTHS
        # despite max_size caps (e.g. multibyte chars exceed byte limits).
        # Skip those inputs — the validator invariant is tested elsewhere.
        return

    try:
        event = Event(
            event_type=event_type,
            event_data={},
            step_index=0,
        )
    except (ValidationError, ValueError):
        return

    try:
        ctx = SpecimenContext(
            order=order,
            current_state=current_state,
            flags=frozenset(),
            event=event,
        )
    except (ValidationError, ValueError):
        return

    safe = phi_safe(ctx)
    safe_str = safe.model_dump_json()

    for fld_name in Order.model_fields:
        raw = getattr(ctx.order, fld_name, None)
        if raw is None or fld_name in _ORDER_PASS_THROUGH:
            continue
        assert str(raw) not in safe_str, (
            f"PHI-bearing field {fld_name!r} (value {raw!r}) leaked into "
            f"SafeContext serialization. Either allowlist it in "
            f"_ORDER_PASS_THROUGH (with a documented HIPAA assessment) "
            f"or update phi_safe to hash/strip it."
        )


# ---------------------------------------------------------------------------
# Slice 6 — Hash determinism + salt entropy smoke tests
# ---------------------------------------------------------------------------


def test_phi_safe_is_deterministic() -> None:
    """phi_safe(ctx) produces identical output on two calls with identical input."""
    from samantha_server.llm.phi import phi_safe

    ctx = _make_ctx(order_id="ORD-DET-001", event_data={"step": 1})
    a = phi_safe(ctx)  # type: ignore[arg-type]
    b = phi_safe(ctx)  # type: ignore[arg-type]
    assert a == b


def test_phi_hash_salt_passes_entropy_floor() -> None:
    """G18 sanity: PHI_HASH_SALT has non-trivial byte diversity.

    Reject all-zero, low-entropy, and wrong-length salts. The >= 16
    distinct-bytes floor is high enough that os.urandom(32) passes
    overwhelmingly (probability of < 16 distinct bytes is negligible)
    while flagging hand-typed salts that repeat a small alphabet.
    The conftest's test sentinel (BADC0FFEE-prefixed, padded with
    readable hex words) carries 27 distinct bytes by construction.
    """
    from samantha_server.config import PHI_HASH_SALT

    assert PHI_HASH_SALT != b"\x00" * 32, "salt is all zeros"
    assert len(set(PHI_HASH_SALT)) >= 16, (
        f"salt has only {len(set(PHI_HASH_SALT))} distinct bytes "
        f"(minimum: 16). Regenerate with os.urandom(32)."
    )
    assert len(PHI_HASH_SALT) == 32, f"salt length: {len(PHI_HASH_SALT)} bytes"


# ---------------------------------------------------------------------------
# Slice 7 — _ORDER_PASS_THROUGH driven copy pin
# ---------------------------------------------------------------------------


def test_phi_safe_pass_through_copy_driven_by_constant() -> None:
    """Every field in _ORDER_PASS_THROUGH lands on safe_order with the source value.

    Iterates the constant directly so a future field addition to
    _ORDER_PASS_THROUGH is automatically covered without a test update.

    Division of labor: this test is deliberately
    self-referential on the constant — it pins the COPY, not the membership.
    A PHI field wrongly added to _ORDER_PASS_THROUGH is caught by
    test_order_pass_through_covers_exactly_non_phi_fields, not here.
    """
    from samantha_server.llm.phi import _ORDER_PASS_THROUGH, phi_safe

    ctx = _make_ctx(
        order_id="ORD-CONST-001",
        age=45,
        specimen_type="biopsy",
        anatomic_site="breast",
        fixative="formalin",
        fixation_time_hours=24.0,
        ordered_tests=("HER2",),
        priority="routine",
        billing_info_present=True,
    )
    result = phi_safe(ctx)  # type: ignore[arg-type]

    for field in _ORDER_PASS_THROUGH:
        source_val = getattr(ctx.order, field)  # type: ignore[union-attr]
        safe_val = getattr(result.order, field)
        assert safe_val == source_val, (
            f"_ORDER_PASS_THROUGH field {field!r}: "
            f"expected {source_val!r} on safe_order, got {safe_val!r}"
        )


def test_phi_safe_pass_through_copies_none_values_verbatim() -> None:
    """Optional pass-through fields propagate None unchanged.

    The constant-driven sibling uses all-non-None values; this variant pins
    the None path for the Optional fields so a future Optional addition to
    _ORDER_PASS_THROUGH is exercised on both paths.
    """
    from samantha_server.llm.phi import _ORDER_PASS_THROUGH, phi_safe

    ctx = _make_ctx(
        order_id="ORD-NONE-001",
        age=None,
        specimen_type=None,
        anatomic_site=None,
        fixative=None,
        fixation_time_hours=None,
        ordered_tests=(),
        priority=None,
        billing_info_present=True,
    )
    result = phi_safe(ctx)  # type: ignore[arg-type]
    for field in _ORDER_PASS_THROUGH:
        assert getattr(result.order, field) == getattr(ctx.order, field), (
            f"pass-through field {field!r} must copy verbatim (incl. None)"
        )
