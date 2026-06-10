"""Fixture-window scanner — pre-flights whether a candidate rule's predicate
would silently match existing fixture scenarios.

Usage:
    python -m samantha_server.tools.fixture_impact <new-rule.yaml> [options]

No LLM imports. Deterministic path only.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from samantha_server.errors import ScenarioCorpusError
from samantha_server.models.context import Event, Order, SpecimenContext
from samantha_server.primitives.trace import PrimitiveTrace
from samantha_server.rules.spec import RuleSpec
from samantha_server.scenarios.loader import Scenario, load_scenarios

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Match:
    """A fixture scenario whose first-event predicate evaluates to True."""

    scenario_id: str
    category: str
    expected_applied_rules: tuple[str, ...]
    true_clauses: tuple[str, ...]


@dataclass(frozen=True)
class ScanResult:
    """Result of scanning a fixtures directory against a candidate rule predicate."""

    matches: tuple[Match, ...]
    skipped: tuple[str, ...]


def _synthesize_order(scenario_id: str, event_data: Mapping[str, Any]) -> Order:
    """Build an Order from the first event's event_data.

    Raises KeyError when required event_data keys are missing, ValidationError
    when an event_data value fails Order's field constraints, and TypeError
    when a field value has an incompatible type.
    """
    ordered_tests_raw = event_data.get("ordered_tests") or []
    return Order(
        order_id=scenario_id,
        patient_name=event_data.get("patient_name"),
        patient_sex=event_data.get("sex"),
        age=event_data.get("age"),
        specimen_type=event_data["specimen_type"],
        anatomic_site=event_data["anatomic_site"],
        fixative=event_data["fixative"],
        fixation_time_hours=event_data.get("fixation_time_hours"),
        ordered_tests=tuple(ordered_tests_raw),
        priority=event_data["priority"],
        billing_info_present=bool(event_data.get("billing_info_present", False)),
    )


def _format_trace_value(expected: Any) -> str:
    """Format a primitive's expected value for display.

    Strings are formatted with double-quotes (e.g. "biopsy").
    Lists/sets/frozensets are shown as sorted lists using the same quoting.
    Numerics and other scalars use repr().
    """
    if isinstance(expected, (list, set, frozenset)):
        items = sorted(str(v) for v in expected)
        formatted_items = ", ".join(f'"{v}"' for v in items)
        return f"[{formatted_items}]"
    if isinstance(expected, str):
        return f'"{expected}"'
    return repr(expected)


def _render_atomic_trace(trace: PrimitiveTrace) -> str:
    """Render a single atomic (leaf) PrimitiveTrace as a human-readable string."""
    name = trace.primitive
    field = trace.field
    expected = trace.expected
    if name == "IsNull":
        if field is None:
            return "IsNull()"
        return f"IsNull({field})"
    if field is None:
        return f"{name}({_format_trace_value(expected)})"
    return f"{name}({field}, {_format_trace_value(expected)})"


def _collect_true_atomic_clauses(trace: PrimitiveTrace) -> list[str]:
    """Walk a PrimitiveTrace tree and collect string representations of
    every true atomic (leaf) node.  Combinator nodes are not included,
    except Not — when Not.result is True, the contributing fact is rendered
    as Not(<child-rendered>) so rule authors see an actionable clause.
    """
    if not trace.children:
        if not trace.result:
            return []
        return [_render_atomic_trace(trace)]

    if trace.primitive == "Not":
        if not trace.result:
            return []
        child = trace.children[0]
        if not child.children:
            return [f"Not({_render_atomic_trace(child)})"]
        return [f"Not({child.primitive}(...))"]

    clauses: list[str] = []
    for child in trace.children:
        clauses.extend(_collect_true_atomic_clauses(child))
    return clauses


def scan_fixtures(rule_spec: RuleSpec, fixtures_dir: Path) -> ScanResult:
    """Evaluate *rule_spec*'s predicate against the first event of every
    fixture scenario in *fixtures_dir*.

    Returns a ScanResult whose matches are sorted by scenario_id.
    Scenarios where Order/Event/SpecimenContext synthesis fails (missing
    required fields, ValidationError from field constraints, or TypeError
    from incompatible field types) are recorded in skipped.
    """
    try:
        scenarios: list[Scenario] = load_scenarios(fixtures_dir)
    except ScenarioCorpusError as exc:
        # Log the underlying cause so a typo'd path
        # or a corrupted corpus is operator-visible rather than silently
        # appearing as "scan ran, no matches." The empty ScanResult is
        # the right return shape for a developer diagnostic, but the
        # cause must surface in logs.
        logger.warning(
            "scan_fixtures: load_scenarios failed at %s; returning empty "
            "ScanResult. malformed_files=%s",
            exc.directory,
            exc.malformed_files or "(none — directory missing or empty)",
        )
        return ScanResult(matches=(), skipped=())
    matches: list[Match] = []
    skipped: list[str] = []

    for scenario in scenarios:
        if not scenario.steps:
            continue

        first_step = scenario.steps[0]
        event_data = first_step.event_data

        try:
            order = _synthesize_order(scenario.scenario_id, event_data)
            event = Event(
                event_type=first_step.event_type,
                event_data=event_data,
                step_index=first_step.step_index,
            )
            ctx = SpecimenContext(
                order=order,
                current_state="ACCESSIONING",
                flags=frozenset(),
                event=event,
            )
        except (KeyError, ValidationError, TypeError):
            skipped.append(scenario.scenario_id)
            continue

        trace = rule_spec.when.trace(ctx)
        if trace.result:
            true_clauses = tuple(_collect_true_atomic_clauses(trace))
            matches.append(
                Match(
                    scenario_id=scenario.scenario_id,
                    category=scenario.category,
                    expected_applied_rules=first_step.expected_applied_rules,
                    true_clauses=true_clauses,
                )
            )

    return ScanResult(
        matches=tuple(sorted(matches, key=lambda m: m.scenario_id)),
        skipped=tuple(skipped),
    )


def format_report(rule_id: str, scan_result: ScanResult) -> str:
    """Format a plain-text report suitable for a PR description.

    When no matches: '<RULE_ID> predicate matches no existing fixtures.'
    When matches:    '<RULE_ID> predicate would newly match:' + one line per match.
    When skipped scenarios exist, appends a summary line.
    """
    lines: list[str]
    if not scan_result.matches:
        lines = [f"{rule_id} predicate matches no existing fixtures."]
    else:
        lines = [f"{rule_id} predicate would newly match:"]
        for m in scan_result.matches:
            applied = ", ".join(m.expected_applied_rules) if m.expected_applied_rules else "(none)"
            clauses = ", ".join(m.true_clauses) if m.true_clauses else "(none)"
            lines.append(
                f"  {m.scenario_id} (scenario expects: {applied}) — true clauses: {clauses}"
            )

    if scan_result.skipped:
        n = len(scan_result.skipped)
        id_str = ", ".join(scan_result.skipped[:5])
        if n > 5:
            id_str += f" ... +{n - 5} more"
        lines.append(
            f"({n} scenario(s) skipped — first event could not be synthesised"
            f" as Order/Event context: {id_str})"
        )

    return "\n".join(lines)


_DEFAULT_SPECS_DIR = Path(__file__).parent.parent / "rules" / "specs"
_DEFAULT_FIXTURES_DIR = Path(__file__).parent.parent.parent / "tests" / "fixtures" / "scenarios"


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: python -m samantha_server.tools.fixture_impact <new-rule.yaml>

    Returns 0 on success, 1 on candidate load error.
    """
    import argparse

    from samantha_server.rules.loader import load_candidate_rule_spec

    parser = argparse.ArgumentParser(
        description=(
            "Pre-flight a candidate rule: check if its predicate already matches"
            " any existing fixture scenario's first event."
        )
    )
    parser.add_argument("candidate", type=Path, help="Path to candidate rule YAML")
    parser.add_argument(
        "--specs-dir",
        type=Path,
        default=_DEFAULT_SPECS_DIR,
        help="Directory of existing rule specs (for rule_ref resolution)",
    )
    parser.add_argument(
        "--fixtures-dir",
        type=Path,
        default=_DEFAULT_FIXTURES_DIR,
        help="Root directory of fixture scenarios",
    )
    args = parser.parse_args(argv)

    try:
        rule_spec = load_candidate_rule_spec(args.candidate, args.specs_dir)
    except ValueError as exc:
        print(f"Error loading candidate rule: {exc}", file=sys.stderr)
        return 1

    if rule_spec.step != "ACCESSIONING":
        print(
            f"Warning: candidate rule step is '{rule_spec.step}'; scanner synthesises"
            " ACCESSIONING context for every fixture, so matches reflect predicate"
            " behaviour, not runtime dispatch.",
            file=sys.stderr,
        )

    scan_result = scan_fixtures(rule_spec, args.fixtures_dir)
    report = format_report(rule_spec.rule_id, scan_result)
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
