"""Tests for build_predicate — the recursive YAML-node → Primitive converter."""

from __future__ import annotations

from typing import Any

import pytest

from samantha_server.primitives import (
    BooleanAnd,
    BooleanOr,
    Contains,
    Equals,
    InEnum,
    IsNull,
    Not,
    ThresholdGTE,
    ThresholdLTE,
)
from samantha_server.rules.loader import build_predicate

# ---------------------------------------------------------------------------
# Slice 1 — atomic predicate builder
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "node, expected_type, expected_attrs",
    [
        (
            {"is_null": {"field": "patient_name"}},
            IsNull,
            {"field": "patient_name"},
        ),
        (
            {"equals": {"field": "billing_info_present", "value": False}},
            Equals,
            {"field": "billing_info_present", "value": False},
        ),
        (
            {"threshold_gte": {"field": "fixation_time_hours", "value": 6.0}},
            ThresholdGTE,
            {"field": "fixation_time_hours", "value": 6.0},
        ),
        (
            {"threshold_lte": {"field": "fixation_time_hours", "value": 72.0}},
            ThresholdLTE,
            {"field": "fixation_time_hours", "value": 72.0},
        ),
        (
            {"in_enum": {"field": "anatomic_site", "values": ["breast", "left breast"]}},
            InEnum,
            {"field": "anatomic_site"},
        ),
        (
            {"contains": {"field": "ordered_tests", "value": "HER2"}},
            Contains,
            {"field": "ordered_tests", "value": "HER2"},
        ),
    ],
)
def test_atomic_build_predicate(
    node: dict[str, Any],
    expected_type: type,
    expected_attrs: dict[str, Any],
) -> None:
    result = build_predicate(node)
    assert isinstance(result, expected_type)
    for attr, val in expected_attrs.items():
        assert getattr(result, attr) == val


def test_in_enum_values_stored_as_frozenset() -> None:
    result = build_predicate(
        {"in_enum": {"field": "anatomic_site", "values": ["breast", "left breast"]}}
    )
    assert isinstance(result, InEnum)
    assert result.values == frozenset({"breast", "left breast"})


# ---------------------------------------------------------------------------
# Slice 2 — combinator builder (recursive)
# ---------------------------------------------------------------------------


def test_not_wraps_atomic() -> None:
    result = build_predicate({"not": {"is_null": {"field": "patient_name"}}})
    assert isinstance(result, Not)
    assert isinstance(result.child, IsNull)
    assert result.child.field == "patient_name"


def test_boolean_and_two_children() -> None:
    result = build_predicate(
        {
            "boolean_and": [
                {"is_null": {"field": "patient_name"}},
                {"equals": {"field": "billing_info_present", "value": False}},
            ]
        }
    )
    assert isinstance(result, BooleanAnd)
    assert len(result.children) == 2
    assert isinstance(result.children[0], IsNull)
    assert isinstance(result.children[1], Equals)


def test_boolean_or_two_children() -> None:
    result = build_predicate(
        {
            "boolean_or": [
                {"threshold_gte": {"field": "fixation_time_hours", "value": 6.0}},
                {"threshold_lte": {"field": "fixation_time_hours", "value": 72.0}},
            ]
        }
    )
    assert isinstance(result, BooleanOr)
    assert len(result.children) == 2
    assert isinstance(result.children[0], ThresholdGTE)
    assert isinstance(result.children[1], ThresholdLTE)


def test_acc006_three_level_nested_tree() -> None:
    """The ACC-006 example predicate tree from the spec, verifying 3-level nesting."""
    node = {
        "boolean_and": [
            {"contains": {"field": "ordered_tests", "value": "HER2"}},
            {
                "boolean_or": [
                    {"not": {"threshold_gte": {"field": "fixation_time_hours", "value": 6.0}}},
                    {"not": {"threshold_lte": {"field": "fixation_time_hours", "value": 72.0}}},
                ]
            },
        ]
    }
    result = build_predicate(node)

    assert isinstance(result, BooleanAnd)
    assert len(result.children) == 2

    contains_node = result.children[0]
    assert isinstance(contains_node, Contains)
    assert contains_node.field == "ordered_tests"
    assert contains_node.value == "HER2"

    or_node = result.children[1]
    assert isinstance(or_node, BooleanOr)
    assert len(or_node.children) == 2

    not_gte = or_node.children[0]
    assert isinstance(not_gte, Not)
    assert isinstance(not_gte.child, ThresholdGTE)
    assert not_gte.child.value == 6.0

    not_lte = or_node.children[1]
    assert isinstance(not_lte, Not)
    assert isinstance(not_lte.child, ThresholdLTE)
    assert not_lte.child.value == 72.0


# ---------------------------------------------------------------------------
# Slice 3 — reject invalid primitive name
# ---------------------------------------------------------------------------


def test_invalid_primitive_name_raises_value_error() -> None:
    with pytest.raises(ValueError, match="foobar"):
        build_predicate({"foobar": {"field": "some_field"}})


def test_rule_ref_raises_descriptive_error() -> None:
    with pytest.raises(ValueError, match="rule_ref must be resolved by load_rule_specs"):
        build_predicate({"rule_ref": "ACC-001"})


# ---------------------------------------------------------------------------
# Slice — empty and multi-key node rejection (#8)
# ---------------------------------------------------------------------------


def test_empty_node_raises_value_error() -> None:
    with pytest.raises(ValueError, match="exactly one key"):
        build_predicate({})


def test_multi_key_node_raises_value_error() -> None:
    with pytest.raises(ValueError, match="exactly one key"):
        build_predicate(
            {
                "is_null": {"field": "patient_name"},
                "equals": {"field": "billing_info_present", "value": False},
            }
        )


# ---------------------------------------------------------------------------
# Slice — combinator payload shape validation (#1)
# ---------------------------------------------------------------------------


def test_not_with_list_payload_raises_value_error() -> None:
    with pytest.raises(ValueError, match="not"):
        build_predicate({"not": [{"is_null": {"field": "patient_name"}}]})


def test_not_with_null_payload_raises_value_error() -> None:
    with pytest.raises(ValueError, match="not"):
        build_predicate({"not": None})


def test_boolean_and_with_string_payload_raises_value_error() -> None:
    with pytest.raises(ValueError, match="boolean_and"):
        build_predicate({"boolean_and": "is_null"})


def test_boolean_and_with_null_payload_raises_value_error() -> None:
    with pytest.raises(ValueError, match="boolean_and"):
        build_predicate({"boolean_and": None})


def test_boolean_and_with_single_mapping_payload_raises_value_error() -> None:
    with pytest.raises(ValueError, match="boolean_and"):
        build_predicate({"boolean_and": {"is_null": {"field": "patient_name"}}})


def test_boolean_or_with_string_payload_raises_value_error() -> None:
    with pytest.raises(ValueError, match="boolean_or"):
        build_predicate({"boolean_or": "is_null"})


def test_boolean_or_with_null_payload_raises_value_error() -> None:
    with pytest.raises(ValueError, match="boolean_or"):
        build_predicate({"boolean_or": None})


# ---------------------------------------------------------------------------
# Slice — boolean_and / boolean_or with 0 or 1 children (#20)
# ---------------------------------------------------------------------------


def test_boolean_and_zero_children_raises_value_error() -> None:
    with pytest.raises(ValueError, match="boolean_and"):
        build_predicate({"boolean_and": []})


def test_boolean_or_zero_children_raises_value_error() -> None:
    with pytest.raises(ValueError, match="boolean_or"):
        build_predicate({"boolean_or": []})


def test_boolean_and_one_child_raises_value_error() -> None:
    with pytest.raises(ValueError, match="boolean_and"):
        build_predicate({"boolean_and": [{"is_null": {"field": "patient_name"}}]})


def test_boolean_or_one_child_raises_value_error() -> None:
    with pytest.raises(ValueError, match="boolean_or"):
        build_predicate({"boolean_or": [{"is_null": {"field": "patient_name"}}]})


# ---------------------------------------------------------------------------
# Slice — 4-level nested predicate tree (#34)
# ---------------------------------------------------------------------------


def test_four_level_nested_tree() -> None:
    """Verify build_predicate correctly handles >=4 levels of nesting."""
    node = {
        "boolean_and": [
            {
                "boolean_or": [
                    {
                        "not": {
                            "boolean_and": [
                                {"is_null": {"field": "patient_name"}},
                                {"equals": {"field": "billing_info_present", "value": False}},
                            ]
                        }
                    },
                    {"is_null": {"field": "event_type"}},
                ]
            },
            {"equals": {"field": "step", "value": "ACCESSIONING"}},
        ]
    }
    result = build_predicate(node)

    assert isinstance(result, BooleanAnd)
    or_node = result.children[0]
    assert isinstance(or_node, BooleanOr)
    not_node = or_node.children[0]
    assert isinstance(not_node, Not)
    inner_and = not_node.child
    assert isinstance(inner_and, BooleanAnd)
    assert isinstance(inner_and.children[0], IsNull)
    assert isinstance(inner_and.children[1], Equals)
