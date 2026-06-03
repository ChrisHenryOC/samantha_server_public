"""Scenario index — build and query an id-keyed mapping of Scenario objects.

No LLM imports. No import of load_scenarios — this module is focused on
indexing only.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from samantha_server.errors import ScenarioNotFoundError
from samantha_server.scenarios.loader import Scenario


def build_scenario_index(scenarios: Iterable[Scenario]) -> Mapping[str, Scenario]:
    """Return a mapping scenario_id -> Scenario.

    Returns a `Mapping` to type-discourage callers from mutating the
    returned index — lookup callers rely on the index being stable for
    its lifetime. The runtime object is still a plain `dict`; only the
    static type narrows.

    Raises ValueError if two scenarios share the same scenario_id.
    """
    index: dict[str, Scenario] = {}
    for scenario in scenarios:
        if scenario.scenario_id in index:
            raise ValueError(f"Duplicate scenario_id: {scenario.scenario_id}")
        index[scenario.scenario_id] = scenario
    return index


def lookup_scenario(scenario_id: str, index: Mapping[str, Scenario]) -> Scenario:
    """Return the Scenario for *scenario_id* from *index*.

    Raises ScenarioNotFoundError if the id is not in the index.
    """
    try:
        return index[scenario_id]
    except KeyError:
        raise ScenarioNotFoundError(scenario_id=scenario_id) from None
