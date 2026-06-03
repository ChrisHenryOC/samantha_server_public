"""Tests for samantha_server.llm.schemas (GH-192 Slice 2).

Tests:
- QueryResponseV1: valid payload parses.
- QueryResponseV1: extra field rejected.
- QueryResponseV1: reasoning over 800 chars rejected (raised from 500 in GH-247);
  caveats over 200 chars rejected.
- QueryResponseV1: answer_type outside literal set rejected.
- QUERY_RESPONSE_V1_JSON_SCHEMA has "additionalProperties": False and "required" at root.
- _make_strict_schema: $defs sub-schema required uses sorted property keys, not hard-coded names.
- _make_strict_schema: inline-nested objects (type=object under properties) left untouched.
- QueryResponseV1: order_ids tuple max 32 items; each item max 64 chars.
"""

from __future__ import annotations

import pytest


def test_query_response_v1_valid_order_list() -> None:
    """Valid order_list payload parses without error."""
    from samantha_server.llm.schemas import QueryResponseV1

    obj = QueryResponseV1(
        answer_type="order_list",
        order_ids=("ORD-001", "ORD-002"),
        reasoning="Matches ACC-001.",
        caveats="Rush priority first.",
    )
    assert obj.answer_type == "order_list"
    assert obj.order_ids == ("ORD-001", "ORD-002")


def test_query_response_v1_valid_no_orders() -> None:
    """Valid no_orders payload parses without error."""
    from samantha_server.llm.schemas import QueryResponseV1

    obj = QueryResponseV1(answer_type="no_orders")
    assert obj.answer_type == "no_orders"
    assert obj.order_ids == ()
    assert obj.reasoning == ""
    assert obj.caveats == ""


def test_query_response_v1_valid_uncertain() -> None:
    """Valid uncertain payload parses without error."""
    from samantha_server.llm.schemas import QueryResponseV1

    obj = QueryResponseV1(answer_type="uncertain", reasoning="Insufficient context.")
    assert obj.answer_type == "uncertain"


def test_query_response_v1_extra_field_rejected() -> None:
    """Extra field causes ValidationError (extra='forbid')."""
    from pydantic import ValidationError

    from samantha_server.llm.schemas import QueryResponseV1

    with pytest.raises(ValidationError):
        QueryResponseV1.model_validate({"answer_type": "no_orders", "unknown_field": "oops"})


def test_query_response_v1_reasoning_800_chars_accepted() -> None:
    """GH-247: reasoning field of exactly 800 chars is valid (QR-020 fix).

    QR-020 (5-item prioritized_list) emitted a 526-char reasoning that
    overflowed the prior 500-char cap. The cap is raised to 800 to
    accommodate per-item ranking justification at the upper end of typical
    prioritized_list answers.
    """
    from samantha_server.llm.schemas import QueryResponseV1

    obj = QueryResponseV1(answer_type="no_orders", reasoning="x" * 800)
    assert len(obj.reasoning) == 800


def test_query_response_v1_reasoning_over_800_chars_rejected() -> None:
    """GH-247: reasoning field over 800 chars raises ValidationError."""
    from pydantic import ValidationError

    from samantha_server.llm.schemas import QueryResponseV1

    with pytest.raises(ValidationError):
        QueryResponseV1(answer_type="no_orders", reasoning="x" * 801)


def test_query_response_v1_caveats_over_200_chars_rejected() -> None:
    """caveats field over 200 chars raises ValidationError."""
    from pydantic import ValidationError

    from samantha_server.llm.schemas import QueryResponseV1

    with pytest.raises(ValidationError):
        QueryResponseV1(answer_type="no_orders", caveats="x" * 201)


def test_query_response_v1_invalid_answer_type_rejected() -> None:
    """answer_type outside Literal set raises ValidationError."""
    from pydantic import ValidationError

    from samantha_server.llm.schemas import QueryResponseV1

    with pytest.raises(ValidationError):
        QueryResponseV1(answer_type="bogus")  # type: ignore[arg-type]


def test_query_response_v1_json_schema_has_additional_properties_false() -> None:
    """QUERY_RESPONSE_V1_JSON_SCHEMA has additionalProperties: False at root."""
    from samantha_server.llm.schemas import QUERY_RESPONSE_V1_JSON_SCHEMA

    assert QUERY_RESPONSE_V1_JSON_SCHEMA.get("additionalProperties") is False


def test_query_response_v1_json_schema_has_required_at_root() -> None:
    """QUERY_RESPONSE_V1_JSON_SCHEMA has required list populated at root."""
    from samantha_server.llm.schemas import QUERY_RESPONSE_V1_JSON_SCHEMA

    required = QUERY_RESPONSE_V1_JSON_SCHEMA.get("required")
    assert isinstance(required, list)
    assert len(required) > 0
    assert "answer_type" in required


def test_query_response_v1_is_frozen() -> None:
    """QueryResponseV1 instances are immutable (frozen=True)."""
    from samantha_server.llm.schemas import QueryResponseV1

    obj = QueryResponseV1(answer_type="no_orders")
    with pytest.raises((AttributeError, TypeError, ValueError)):
        obj.answer_type = "order_list"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Fix #2: _make_strict_schema must not inject alien keys into $defs sub-schemas
# ---------------------------------------------------------------------------


def test_make_strict_schema_defs_required_uses_property_keys_not_hardcoded() -> None:
    """_make_strict_schema adds required=[sorted property keys] to $defs sub-schemas.

    The old elif unconditionally injected "answer_type" into any existing required
    list — including $defs sub-schemas that do not have that field. This test uses
    a synthetic nested schema with a SubObj having properties {x, y} and asserts
    that _make_strict_schema produces required=["x","y"], NOT ["answer_type","x","y"].
    """
    from samantha_server.llm.schemas import _make_strict_schema

    synthetic: dict = {
        "type": "object",
        "properties": {
            "answer_type": {"type": "string"},
        },
        "$defs": {
            "SubObj": {
                "type": "object",
                "properties": {
                    "x": {"type": "integer"},
                    "y": {"type": "string"},
                },
                "required": ["x"],  # pre-existing partial required list
            }
        },
    }
    result = _make_strict_schema(synthetic)

    sub = result["$defs"]["SubObj"]
    # Must be sorted property keys — NOT have "answer_type" injected
    assert sorted(sub["required"]) == ["x", "y"]
    assert "answer_type" not in sub["required"]


# ---------------------------------------------------------------------------
# Fix #15: inline-nested objects left untouched; docstring matches behavior
# ---------------------------------------------------------------------------


def test_make_strict_schema_inline_nested_left_untouched() -> None:
    """_make_strict_schema does not recurse into inline-nested property objects.

    Pydantic v2 sometimes inlines nested objects under properties[field].properties
    rather than promoting them to $defs. The function only recurses into $defs;
    inline-nested objects are left as-is. This test pins that behavior so a
    future accidental broadening of recursion is caught.
    """
    from samantha_server.llm.schemas import _make_strict_schema

    schema_with_inline_nested: dict = {
        "type": "object",
        "properties": {
            "nested": {
                "type": "object",
                "properties": {
                    "a": {"type": "string"},
                    "b": {"type": "integer"},
                },
                # No additionalProperties at inline level — left untouched
            }
        },
    }
    result = _make_strict_schema(schema_with_inline_nested)

    # Root gets additionalProperties: False
    assert result["additionalProperties"] is False
    # Inline-nested does NOT get additionalProperties injected
    nested = result["properties"]["nested"]
    assert "additionalProperties" not in nested


# ---------------------------------------------------------------------------
# Fix #18: order_ids bounds — max 32 items, each max 64 chars
# ---------------------------------------------------------------------------


def test_order_ids_over_32_items_rejected() -> None:
    """order_ids with 33 items raises ValidationError."""
    from pydantic import ValidationError

    from samantha_server.llm.schemas import QueryResponseV1

    with pytest.raises(ValidationError):
        QueryResponseV1(
            answer_type="order_list",
            order_ids=tuple(f"ORD-{i:03d}" for i in range(33)),
        )


def test_order_ids_exactly_32_items_accepted() -> None:
    """order_ids with exactly 32 items is valid."""
    from samantha_server.llm.schemas import QueryResponseV1

    obj = QueryResponseV1(
        answer_type="order_list",
        order_ids=tuple(f"ORD-{i:03d}" for i in range(32)),
    )
    assert len(obj.order_ids) == 32


def test_order_id_over_64_chars_rejected() -> None:
    """An individual order_id string longer than 64 chars raises ValidationError."""
    from pydantic import ValidationError

    from samantha_server.llm.schemas import QueryResponseV1

    with pytest.raises(ValidationError):
        QueryResponseV1(
            answer_type="order_list",
            order_ids=("x" * 65,),
        )


def test_order_id_exactly_64_chars_accepted() -> None:
    """An individual order_id of exactly 64 chars is valid."""
    from samantha_server.llm.schemas import QueryResponseV1

    obj = QueryResponseV1(
        answer_type="order_list",
        order_ids=("x" * 64,),
    )
    assert len(obj.order_ids[0]) == 64


# ---------------------------------------------------------------------------
# GH-220 Slice 1: order_status answer_type
# ---------------------------------------------------------------------------


def test_query_response_v1_accepts_order_status_answer_type() -> None:
    """GH-220 Slice 1: order_status is a valid answer_type.

    order_ids contains the subject order (the one being asked about);
    reasoning carries the substantive answer.
    """
    from samantha_server.llm.schemas import QueryResponseV1

    obj = QueryResponseV1(
        answer_type="order_status",
        order_ids=("ORD-901",),
        reasoning="Order ORD-901 is in ACCEPTED state, ready for sample prep.",
        caveats="",
    )
    assert obj.answer_type == "order_status"
    assert obj.order_ids == ("ORD-901",)


def test_answer_type_literal_synced_with_answer_types_tuple() -> None:
    """Cluster B (#2, #7): ANSWER_TYPES tuple, QueryResponseV1.answer_type Literal, and
    QueryTrace.parsed_answer_type Literal must all contain the same set of values.

    Uses typing.get_args to extract values from both Literal annotations and asserts
    they equal set(ANSWER_TYPES). Guards against silent drift when GH-222 widens the set.
    """
    import typing

    from samantha_server.engine.decision import QueryTrace
    from samantha_server.llm.schemas import ANSWER_TYPES, AnswerType, QueryResponseV1

    # Extract the Literal args from QueryResponseV1.answer_type field annotation.
    schema_annotation = QueryResponseV1.model_fields["answer_type"].annotation
    schema_args = set(typing.get_args(schema_annotation))

    # Extract the Literal args from QueryTrace.parsed_answer_type (Optional[AnswerType]).
    # The annotation is AnswerType | None; get_args yields (AnswerType, None).
    trace_annotation = QueryTrace.model_fields["parsed_answer_type"].annotation
    trace_inner_args = typing.get_args(trace_annotation)
    # AnswerType is the non-None arg; get its Literal values.
    answer_type_args: set[str] = set()
    for arg in trace_inner_args:
        inner = typing.get_args(arg)
        if inner:
            answer_type_args.update(inner)

    tuple_set = set(ANSWER_TYPES)
    answer_type_set = set(typing.get_args(AnswerType))

    assert schema_args == tuple_set, (
        f"QueryResponseV1.answer_type Literal {schema_args!r} "
        f"diverges from ANSWER_TYPES {tuple_set!r}"
    )
    assert answer_type_args == tuple_set, (
        f"QueryTrace.parsed_answer_type Literal {answer_type_args!r} "
        f"diverges from ANSWER_TYPES {tuple_set!r}"
    )
    assert answer_type_set == tuple_set, (
        f"AnswerType TypeAlias Literal {answer_type_set!r} diverges from ANSWER_TYPES {tuple_set!r}"
    )


def test_query_response_v1_order_status_in_json_schema() -> None:
    """GH-220 Slice 1: QUERY_RESPONSE_V1_JSON_SCHEMA reflects the order_status literal."""
    from samantha_server.llm.schemas import QUERY_RESPONSE_V1_JSON_SCHEMA

    # The schema's answer_type property must enumerate order_status.
    answer_type_def = QUERY_RESPONSE_V1_JSON_SCHEMA.get("properties", {}).get("answer_type", {})
    # Pydantic v2 renders Literal as an anyOf of const values.
    any_of = answer_type_def.get("anyOf") or answer_type_def.get("enum") or []
    # Flatten: anyOf may be [{"const": "order_list"}, ...] or a bare enum list.
    if any_of and isinstance(any_of[0], dict):
        literal_values = [item.get("const") for item in any_of]
    else:
        literal_values = list(any_of)
    assert "order_status" in literal_values, (
        f"order_status missing from answer_type JSON schema definition: {answer_type_def!r}"
    )


# ---------------------------------------------------------------------------
# GH-222 Slice 1: prioritized_list answer_type
# ---------------------------------------------------------------------------


def test_query_response_v1_prioritized_list_in_json_schema() -> None:
    """PR224 Cluster C (#4): QUERY_RESPONSE_V1_JSON_SCHEMA reflects the prioritized_list literal.

    Parallels test_query_response_v1_order_status_in_json_schema. Verifies the
    post-processed schema dict — a bug in _make_strict_schema mangling the anyOf
    entries would silently drop prioritized_list from the schema sent to oMLX.
    """
    from samantha_server.llm.schemas import QUERY_RESPONSE_V1_JSON_SCHEMA

    answer_type_def = QUERY_RESPONSE_V1_JSON_SCHEMA.get("properties", {}).get("answer_type", {})
    any_of = answer_type_def.get("anyOf") or answer_type_def.get("enum") or []
    if any_of and isinstance(any_of[0], dict):
        literal_values = [item.get("const") for item in any_of]
    else:
        literal_values = list(any_of)
    assert "prioritized_list" in literal_values, (
        f"prioritized_list missing from answer_type JSON schema definition: {answer_type_def!r}"
    )


def test_query_response_v1_accepts_prioritized_list_answer_type() -> None:
    """GH-222 Slice 1: prioritized_list is a valid answer_type.

    order_ids carries the ranked sequence (position-sensitive); first item is
    highest priority. The schema must accept this value without ValidationError.
    """
    from samantha_server.llm.schemas import QueryResponseV1

    obj = QueryResponseV1(
        answer_type="prioritized_list",
        order_ids=("ORD-X", "ORD-Y", "ORD-Z"),
        reasoning="ranked by priority",
        caveats="",
    )
    assert obj.answer_type == "prioritized_list"
    assert obj.order_ids == ("ORD-X", "ORD-Y", "ORD-Z")


# ---------------------------------------------------------------------------
# GH-254: reasoning declared before order_ids in JSON schema properties
# ---------------------------------------------------------------------------


def test_query_response_v1_properties_order_reasoning_before_order_ids() -> None:
    """GH-254: QUERY_RESPONSE_V1_JSON_SCHEMA properties must list reasoning before order_ids.

    JSON-mode token emission is left-to-right: the model commits to each field
    value as it writes the JSON object. When order_ids appeared before reasoning,
    the model locked in the ranked sequence before it had finished writing its
    rationale — so a mid-prose self-correction in reasoning could never propagate
    back to order_ids. Declaring reasoning first ensures the model completes its
    chain-of-thought before committing the final ranked sequence.
    """
    from samantha_server.llm.schemas import QUERY_RESPONSE_V1_JSON_SCHEMA

    props = list(QUERY_RESPONSE_V1_JSON_SCHEMA["properties"].keys())
    assert props.index("reasoning") < props.index("order_ids")


def test_query_response_v1_required_remains_alphabetical_distinct_from_properties() -> None:
    """GH-254 guardrail: `required` is alphabetical, `properties` is declaration-ordered.

    `_make_strict_schema` sets `required = sorted(properties.keys())`. After the
    GH-254 reorder, `properties` keys follow field declaration order (reasoning
    before order_ids), so `required` and `properties` deliberately disagree.
    If a future change "reconciles" them by making `required` mirror `properties`
    order, oMLX backends that key off `required[]` for emission order could
    silently re-introduce the GH-254 regression. Pin both invariants here.
    """
    from samantha_server.llm.schemas import QUERY_RESPONSE_V1_JSON_SCHEMA

    required = QUERY_RESPONSE_V1_JSON_SCHEMA["required"]
    properties = list(QUERY_RESPONSE_V1_JSON_SCHEMA["properties"].keys())
    assert required == sorted(properties)
    assert required != properties
