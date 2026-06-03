"""Runtime action-handler flag clearing for the deterministic dispatch path.

Applies symbolic-transition-aware flag mutations that are determined at
runtime by event data — behaviors that the rule spec encodes as
action.branch rather than clear_flags/set_flags.

No LLM imports. Imports only from samantha_server.engine and
samantha_server.models. Deterministic path only.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from samantha_server.engine.decision import EngineDecision


def apply_runtime_flag_clearing(
    decision: EngineDecision,
    event: Mapping[str, Any],
    flags: frozenset[str],
) -> frozenset[str]:
    """Apply symbolic-transition-aware flag clearing based on decision and event.

    For RESOLVE_MISSING_INFO (RES-002): the action handler clears
    MISSING_INFO_PROCEED when info_type == "billing" at runtime.
    The rule spec has clear_flags: [] (action handler, not spec-encoded),
    so we apply the clearing here.

    For all other transitions, flags are returned unchanged.

    Args:
        decision: The EngineDecision returned by evaluate().
        event: The event_data dict for the current step.
        flags: The accumulated flags *after* spec-level clearing/setting.

    Returns:
        A frozenset of flags after applying any action-handler clearing.
    """
    if decision.next_state == "RESOLVE_MISSING_INFO":
        info_type = event.get("info_type")
        if info_type == "billing":
            return flags - {"MISSING_INFO_PROCEED"}
    return flags
