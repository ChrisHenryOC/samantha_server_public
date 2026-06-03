"""TraceContext: unified carrier for span-stamping fields.

All three legacy stamping call sites (routing.py, replay.py, conftest.py)
set overlapping but non-identical subsets of span attributes. TraceContext
is a frozen dataclass that holds the union of all fields those sites need.
``stamp_trace_attributes(span, ctx)`` in ``otel.py`` maps it to the full
attribute set defined in the GH-182 union table, replacing the three
drifting call sites with a single, tested code path.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TraceContext:
    """Carrier for all span-stamping fields used by stamp_trace_attributes().

    All fields default to None. Callers populate only the fields they own;
    stamp_trace_attributes() omits attributes whose source field is None.

    Invariant (GH-183 Slice 4): session_id and scenario_id are mutually
    exclusive. No real call site needs both; setting both produces
    inconsistent spans (both fields write to langfuse.trace.name /
    langfuse.trace.metadata.scenario_id with incompatible semantics).
    Raises ValueError if both are set to non-None values.
    """

    session_id: str | None = None
    scenario_id: str | None = None
    scenario_category: str | None = None
    priority: str | None = None
    event_input_hash: str | None = None
    routing_path: str | None = None
    next_state: str | None = None
    outcome: str | None = None
    applied_rule_id: str | None = None
    receipt_id: str | None = None
    environment: str | None = None
    run_id: str | None = None
    sweep_run_id: str | None = None
    latency_us: int | None = None
    # GH-367: synthetic LIS order identifier; independent of the session_id/
    # scenario_id mutual-exclusion invariant.
    order_id: str | None = None

    def __post_init__(self) -> None:
        if self.session_id is not None and self.scenario_id is not None:
            raise ValueError(
                "TraceContext.session_id and TraceContext.scenario_id are mutually exclusive: "
                "both are non-None. Set one or the other, not both. "
                "They both write to langfuse.trace.name / langfuse.trace.metadata.scenario_id "
                "and setting both produces inconsistent spans."
            )
