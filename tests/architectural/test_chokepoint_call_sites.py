"""Architectural test: exactly one production call site for sign_decision.

Per Phase 3 Step 4 / G18: the orchestrator-side receipt-emission chokepoint
(emit_receipt, in api/receipt_emission.py) is the only place that should call
sign_decision in production code.

Any other production call site fails this test with a message identifying
the new file/line and pointing at the allowed site.

Scope: samantha_server/ (not tests/). CLI scripts under samantha_server/ are
NOT exempt.
"""

from __future__ import annotations

import ast
from pathlib import Path


def _find_sign_decision_calls(root: Path) -> list[tuple[str, int]]:
    """AST-walk all .py files under *root* and return (file_path, lineno) for
    every call expression whose function name is 'sign_decision'."""
    call_sites: list[tuple[str, int]] = []

    for py_file in sorted(root.rglob("*.py")):
        source = py_file.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            # Direct call: sign_decision(...)
            is_name_call = isinstance(func, ast.Name) and func.id == "sign_decision"
            is_attr_call = isinstance(func, ast.Attribute) and func.attr == "sign_decision"
            if is_name_call or is_attr_call:
                call_sites.append((str(py_file), node.lineno))

    return call_sites


def test_sign_decision_has_exactly_one_production_call_site() -> None:
    """Exactly one production file calls sign_decision — emit_receipt.

    G18 (phase-3-implementation.md §4.1): the orchestrator-side chokepoint
    is the only allowed caller.

    Adding another call site breaks the single-chokepoint invariant that makes
    the receipt-emission audit trail tractable.
    """
    import samantha_server

    pkg_root = Path(samantha_server.__file__).parent

    call_sites = _find_sign_decision_calls(pkg_root)

    # Normalize to relative paths for readable assertion messages.
    rel_sites = [(str(Path(f).relative_to(pkg_root.parent)), ln) for f, ln in call_sites]

    allowed_files = {
        "samantha_server/api/receipt_emission.py",
    }

    # Collect violations: files that are not in the allowed set.
    violations = [(f, ln) for f, ln in rel_sites if f not in allowed_files]

    assert not violations, (
        "Found unexpected sign_decision call site(s):\n"
        + "\n".join(f"  {f}:{ln}" for f, ln in violations)
        + "\n\nOnly one production call site is permitted (G18):\n"
        + "\n".join(f"  {f}" for f in sorted(allowed_files))
        + "\n\nTo add another call site, update this test's allowed_files "
        + "and document the rationale."
    )

    # Verify the allowed site is still present (guards against accidental removal).
    present_files = {f for f, _ in rel_sites}
    for expected in allowed_files:
        assert expected in present_files, (
            f"Expected sign_decision call site {expected!r} not found. "
            "Was it removed or renamed? Update this test and document the change."
        )
