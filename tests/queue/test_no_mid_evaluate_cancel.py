"""Architectural test: G3 — no mid-evaluate preemption.

Phase 3 Step 2 plan, Decision G3: STAT goes to the head of the queue;
in-flight evaluations are NOT cancelled. The queue module and the
consumer body must contain no ``task.cancel()`` calls.

 M5: previously this invariant was satisfied only by code
comments and reviewer attention. This AST-walker enforces it
mechanically — adding a ``task.cancel()`` (or any ``.cancel()`` method
call) inside ``samantha_server/queue/priority.py`` or the ``_consume``
function in ``samantha_server/api/app.py`` will fail the test.

Allowed cancel sites (deliberately outside this scope):
- ``AppState.aclose()`` and lifespan ``finally`` cancel the consumer
  task itself at shutdown — that is the explicitly allowed shutdown
  path. They live outside the two files this test scans, so they are
  not flagged.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _find_cancel_calls(tree: ast.AST) -> list[tuple[int, str]]:
    """Return (lineno, source_form) for every Attribute call ending in `.cancel(...)`."""
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "cancel":
            hits.append((node.lineno, ast.unparse(func)))
    return hits


def test_priority_module_has_no_cancel_calls() -> None:
    """samantha_server/queue/priority.py must contain no `.cancel()` call.

    G3: STAT preemption is queue-ordering, not mid-evaluate cancellation.
    The queue's `cancel(event_id)` method is a public API — it is a
    method *definition*, not a method *call*; this test inspects calls
    only.
    """
    src = REPO_ROOT / "samantha_server" / "queue" / "priority.py"
    tree = ast.parse(src.read_text(), filename=str(src))
    hits = _find_cancel_calls(tree)
    assert not hits, (
        f"samantha_server/queue/priority.py contains .cancel() call(s): "
        f"{hits}. G3 forbids mid-evaluate preemption from this module."
    )


def test_consume_function_has_no_cancel_calls() -> None:
    """The `_consume` function in app.py must contain no `.cancel()` call.

    G3: the consumer dequeues and dispatches; it does not cancel
    in-flight work. The lifespan `finally` block (separate from
    `_consume`) is allowed to cancel the consumer task itself at
    shutdown — that's the explicitly permitted lifecycle path.
    """
    src = REPO_ROOT / "samantha_server" / "api" / "app.py"
    tree = ast.parse(src.read_text(), filename=str(src))

    consume_fn: ast.AsyncFunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_consume":
            consume_fn = node
            break

    assert consume_fn is not None, (
        "Could not locate `_consume` in samantha_server/api/app.py — "
        "if the function was renamed, update this test."
    )

    hits = _find_cancel_calls(consume_fn)
    assert not hits, (
        f"_consume contains .cancel() call(s): {hits}. G3 forbids mid-evaluate preemption."
    )
