"""Architectural test: routing_path is orchestrator-only (not on EngineDecision).

Per Phase 3 Step 4 design (phase-3-implementation.md §4.1):
- EngineDecision must NOT have a routing_path field.
- EventDispatchContext is the ONLY class that defines routing_path.
- No production code reads routing_path off an EngineDecision-like object.

This test guards against a future accidental breach of the deterministic-
path-purity invariant where someone adds routing_path to EngineDecision
(which would couple the engine to the orchestrator layer).
"""

from __future__ import annotations

import ast
from pathlib import Path


def test_engine_decision_has_no_routing_path_field() -> None:
    """EngineDecision does not have a routing_path field.

    Engine-purity invariant: routing classification is orchestrator-only.
    The EngineDecision is the engine's output; it must not carry
    orchestrator-side metadata.
    """
    from samantha_server.engine.decision import EngineDecision

    field_names = set(EngineDecision.model_fields.keys())
    assert "routing_path" not in field_names, (
        "EngineDecision has a 'routing_path' field — this violates the "
        "deterministic-path-purity invariant. routing_path belongs on "
        "EventDispatchContext (samantha_server/api/event_context.py), not "
        "on the engine's output model."
    )


def test_event_dispatch_context_defines_routing_path() -> None:
    """EventDispatchContext is the only class that defines routing_path."""
    import dataclasses

    from samantha_server.api.event_context import EventDispatchContext

    field_names = {f.name for f in dataclasses.fields(EventDispatchContext)}
    assert "routing_path" in field_names, (
        "EventDispatchContext does not define 'routing_path'. "
        "This field belongs here per the orchestrator design."
    )


def test_only_event_dispatch_context_defines_routing_path_field() -> None:
    """Class-definition AST sweep: only approved classes define routing_path.

    Stronger than the runtime-only check above: walks every .py file in the
    production package for class definitions that contain a routing_path
    assignment or annotation in their body. The set of such classes must
    equal exactly the approved set.

    Approved classes:
      - EventDispatchContext: orchestrator-side dispatch metadata.
      - StepVerdict: replay harness output (scenarios/replay.py). Records the
        routing path *actually taken* per step so the latency bucket is keyed
        on actual path, not scenario category. StepVerdict
        is a replay-only output model — not an engine output model — so this
        does not breach the engine-purity invariant.

    This catches a future "add routing_path to EngineDecision" regression
    that runtime isinstance checks might miss.
    """
    import ast
    from pathlib import Path

    import samantha_server

    pkg_root = Path(samantha_server.__file__).parent

    # Approved classes that may define routing_path.
    # Add here only with reviewer sign-off and a justification comment.
    _APPROVED_CLASSES_WITH_ROUTING_PATH: frozenset[str] = frozenset(
        {
            # Orchestrator-side dispatch metadata (Phase 3 Step 4).
            "EventDispatchContext",
            # Replay harness step output. Records the routing path per step
            # for routing_path-keyed latency bucketing.
            # Not an engine output; not a purity violation.
            "StepVerdict",
            # Phase 3 Step 10 trace serializer output. The TypedDict
            # mirrors the documented Langfuse trace shape, which carries
            # routing_path as a top-level dashboard filter. Not an engine
            # output — produced by the observability layer.
            "TraceDict",
            # Unified span-stamping carrier. Holds the union of fields
            # needed by stamp_trace_attributes() to replace three drifting call
            # sites. Observability-layer dataclass — not an engine output.
            "TraceContext",
        }
    )

    classes_with_routing_path: set[str] = set()

    for py_file in sorted(pkg_root.rglob("*.py")):
        source = py_file.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, (ast.ClassDef,)):
                continue
            class_name = node.name
            for body_item in node.body:
                # Check for annotated assignment: routing_path: ...
                if isinstance(body_item, ast.AnnAssign):
                    target = body_item.target
                    if isinstance(target, ast.Name) and target.id == "routing_path":
                        classes_with_routing_path.add(class_name)
                # Check for plain assignment: routing_path = ...
                elif isinstance(body_item, ast.Assign):
                    for t in body_item.targets:
                        if isinstance(t, ast.Name) and t.id == "routing_path":
                            classes_with_routing_path.add(class_name)

    unapproved = classes_with_routing_path - _APPROVED_CLASSES_WITH_ROUTING_PATH
    assert not unapproved, (
        f"Unapproved classes define routing_path: {unapproved!r}\n"
        "routing_path belongs on EventDispatchContext (api/event_context.py). "
        "If another class legitimately needs it, add it to "
        "_APPROVED_CLASSES_WITH_ROUTING_PATH with a justification comment."
    )


def test_no_production_attribute_access_routing_path_on_decision() -> None:
    """No production code reads .routing_path off a variable named like a decision.

    AST-walk samantha_server/ for attribute accesses of the form
    <name>.routing_path where <name> suggests an EngineDecision
    (decision, engine_decision, result).

    This is a best-effort heuristic, not exhaustive — it catches the most
    likely accidental patterns while avoiding false positives from
    legitimate EventDispatchContext access.
    """
    import samantha_server

    pkg_root = Path(samantha_server.__file__).parent

    # Variable name prefixes that suggest an EngineDecision binding.
    decision_like_names = frozenset({"decision", "engine_decision"})

    violations: list[tuple[str, int]] = []

    for py_file in sorted(pkg_root.rglob("*.py")):
        source = py_file.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            if node.attr != "routing_path":
                continue
            # Check if the receiver is a Name node with a decision-like name.
            if isinstance(node.value, ast.Name) and node.value.id in decision_like_names:
                rel = str(py_file.relative_to(pkg_root.parent))
                violations.append((rel, node.lineno))

    assert not violations, (
        "Found suspicious .routing_path access on decision-like variables:\n"
        + "\n".join(f"  {f}:{ln}" for f, ln in violations)
        + "\n\nrouting_path belongs on EventDispatchContext, not EngineDecision. "
        "If this is legitimate, rename the variable to avoid the decision-like name."
    )
