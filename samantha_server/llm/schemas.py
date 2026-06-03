"""LLM output schema definitions for structured JSON output.

QueryResponseV1: Pydantic v2 model for the query-routing JSON response.
QUERY_RESPONSE_V1_JSON_SCHEMA: dict suitable for passing as the
    response_format.json_schema.schema value to /v1/chat/completions.
    Post-processed to add additionalProperties=False and required at
    root and $defs levels — required for strict-mode enforcement.

PHI boundary: the `reasoning` and `caveats` fields are LLM-generated
(not PHI). Length caps enforce the schema contract: reasoning <= 800
chars (GH-247; raised from 500 set by GH-225), caveats <= 200 chars.
"""

from __future__ import annotations

from typing import Annotated, Any, Final, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field

# Single source of truth for the answer_type enumeration (GH-220 review Cluster B).
# ANSWER_TYPES is the runtime tuple; AnswerType is the TypeAlias for annotations.
# Both must stay in sync — the test_answer_type_literal_synced_with_answer_types_tuple
# parity test enforces this. To add a new answer_type: append to both ANSWER_TYPES
# and AnswerType in the same edit; the parity test catches drift on each side.
ANSWER_TYPES: Final = ("order_list", "order_status", "no_orders", "uncertain", "prioritized_list")
AnswerType: TypeAlias = Literal[  # noqa: UP040
    "order_list", "order_status", "no_orders", "uncertain", "prioritized_list"
]


def _coerce_order_ids(v: object) -> object:
    """Pass-through; validation of individual items handled by Annotated type."""
    return v


class QueryResponseV1(BaseModel, frozen=True):
    """Structured JSON response for the clinical query routing path.

    Field declaration order is load-bearing: `reasoning` is declared before
    `order_ids` so JSON-mode left-to-right emission writes the rationale before
    committing the ranked sequence (GH-254). See
    `test_query_response_v1_properties_order_reasoning_before_order_ids` for
    the pinned invariant.

    answer_type:
        "order_list"       — question asks which orders; list specific IDs.
        "order_status"     — query asks about ONE specific order (named by order_id);
                             order_ids contains that subject; the answer lives in reasoning.
        "no_orders"        — answerable but no orders satisfy the query.
        "uncertain"        — context insufficient; put reason in reasoning.
        "prioritized_list" — query asks for ranked/sorted orders (e.g., "top N", "in
                             priority order"); order_ids is the ranked sequence (position
                             matters); first item is highest priority.
    reasoning:
        LLM-generated; <= 800 chars (GH-247; raised from 500 to accommodate
        5-item prioritized_list rankings — observed high-water mark 526 chars
        on QR-020 against gemma-4-26B); cite scenario IDs here (e.g. ACC-001).
    order_ids:
        Tuple of order ID strings. Empty when answer_type == "no_orders" or "uncertain".
        For order_list: orders matching the filter.
        For order_status: a single-element list containing the subject order_id.
        For prioritized_list: ranked sequence (position-sensitive); index 0 is highest
            priority.
        Bounded: at most 32 items, each item at most 64 characters.
    caveats:
        LLM-generated; <= 200 chars; may be empty.
    """

    model_config = ConfigDict(extra="forbid")

    answer_type: AnswerType
    reasoning: str = Field(default="", max_length=800)
    order_ids: Annotated[
        tuple[Annotated[str, Field(max_length=64)], ...], Field(max_length=32)
    ] = ()
    caveats: str = Field(default="", max_length=200)


def _make_strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Post-process a Pydantic JSON schema to add strict-mode fields.

    Pydantic v2 does not always emit additionalProperties=False or a
    complete required list at every nesting level. oMLX's strict=True
    enforcement requires both to be present at the root and at each
    $defs level.

    Scope: applies to the root object and to every $defs sub-schema.
    Inline-nested objects (type=object directly under properties[field])
    are NOT recursed into — only the $defs path is processed. The
    required list at each processed level is set to sorted(properties.keys()),
    which is correct for any extra="forbid" model regardless of field name.
    """
    result = dict(schema)

    # Ensure additionalProperties: False at this level.
    result["additionalProperties"] = False

    # Set required to all property keys (sorted for determinism).
    # Correct for any extra="forbid" Pydantic model; avoids hard-coding field names.
    if "properties" in result:
        result["required"] = sorted(result["properties"].keys())

    # Recursively fix $defs sub-schemas.
    if "$defs" in result:
        result["$defs"] = {k: _make_strict_schema(v) for k, v in result["$defs"].items()}

    return result


QUERY_RESPONSE_V1_JSON_SCHEMA: dict[str, Any] = _make_strict_schema(
    QueryResponseV1.model_json_schema()
)


class SpecimenReviewResponseV1(BaseModel, frozen=True):
    """Structured JSON response for the specimen-review path.

    disposition:
        "accepted"  — specimen is clinically processable; proceed to ACCEPTED.
        "rejected"  — specimen is outside the histology workflow; route to DO_NOT_PROCESS.
        "escalated" — requires human pathologist judgment; route to PENDING_HUMAN_REVIEW.
    reasoning:
        LLM-generated; <= 200 chars; brief clinical rationale.
    """

    model_config = ConfigDict(extra="forbid")

    disposition: Literal["accepted", "rejected", "escalated"]
    # PR204 review #4: no default — let Pydantic enforce 'required' so a backend
    # not strictly enforcing the JSON-schema required[] gets caught as a
    # schema_violation rather than silently defaulting to "".
    reasoning: str = Field(max_length=200)


SPECIMEN_REVIEW_RESPONSE_V1_JSON_SCHEMA: dict[str, Any] = _make_strict_schema(
    SpecimenReviewResponseV1.model_json_schema()
)

__all__ = [
    "ANSWER_TYPES",
    "AnswerType",
    "QUERY_RESPONSE_V1_JSON_SCHEMA",
    "QueryResponseV1",
    "SPECIMEN_REVIEW_RESPONSE_V1_JSON_SCHEMA",
    "SpecimenReviewResponseV1",
    "_make_strict_schema",
]
