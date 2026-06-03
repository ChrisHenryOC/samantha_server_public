"""String-field canonicalization for comparison-time normalization.

Top-level module so both ``primitives/`` and ``engine/`` can import without
creating a circular dependency.

Design (G10 — pick-list-only model):

The original GH-35 spec described a synonym-map approach (e.g.,
``lumpectomy with margins → lumpectomy``).  For POC scope that was
intentionally simplified to a pick-list-only check — see the locked
design note at
https://github.com/ChrisHenryOC/samantha_server/issues/35#issuecomment-4407628775.

Pick-list-only — see GH-35 design comment for the simplification rationale.

Policy applied in order:

1. Trim leading/trailing whitespace.
2. Case-fold (Python ``str.casefold`` — handles multi-character fold like ß → ss).
3. Check the casefolded value against the field's pick list from
   ``samantha_server/data/canonical-fields.json``.
4. Unknown values pass through (after trim + casefold) with ``was_unknown=True``.

Data-side only:

The preflight scan walks ``Order`` fields (data side) only, not predicate
literals on the rule side.  Rule-side unknown literals never trigger a
``CanonicalizationTrace``.  This asymmetry is intentional: rule authors write
the canonical form; the engine normalizes whatever the upstream message sends.

No LLM imports. Deterministic path only.
"""

from __future__ import annotations

import importlib.resources
import json
from pathlib import Path
from typing import NamedTuple

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class CanonicalizationResult(NamedTuple):
    """Result of canonicalizing a single string value."""

    canonical: str
    was_unknown: bool  # True iff field is in CANONICAL_FIELDS but value not in its pick list


# ---------------------------------------------------------------------------
# Pick-list data (loaded once at module import from the package data directory)
# ---------------------------------------------------------------------------

# Load via importlib.resources so the file is accessible when the package is
# installed as a wheel (tests/ is not included in the distribution).
_FIXTURE_PATH = Path(
    str(importlib.resources.files("samantha_server.data") / "canonical-fields.json")
)

CANONICAL_FIELDS: frozenset[str]
PICK_LISTS: dict[str, frozenset[str]]


def _load_pick_lists(path: Path) -> tuple[frozenset[str], dict[str, frozenset[str]]]:
    """Load canonical-fields.json and return (field set, per-field frozensets).

    Validation rules:
    - Keys starting with ``"_"`` are metadata and are skipped.
    - Every other key's value must be a ``list`` of ``str``. A non-list value
      or a list containing any non-string element raises ``ValueError`` with
      the offending field name in the message.  Loud failure at import is
      intentional (mirrors ``MisconfiguredEnvironmentError`` pattern) — a
      silently mixed-type pick list would cause every order for that field to
      receive ``was_unknown=True`` and fire spurious ``CanonicalizationTrace``
      records.

    Parameters
    ----------
    path:
        Filesystem path to the JSON file. ``FileNotFoundError`` propagates
        unchanged so callers can distinguish a missing deployment artifact from
        a malformed one.
    """
    with path.open(encoding="utf-8") as fh:
        raw: dict[str, object] = json.load(fh)

    fields: dict[str, frozenset[str]] = {}
    for k, v in raw.items():
        # Skip metadata keys (e.g., "_comment")
        if k.startswith("_"):
            continue
        if not isinstance(v, list):
            raise ValueError(
                f"canonical-fields.json: field {k!r} must be a list of str, "
                f"got {type(v).__name__!r}"
            )
        non_strings = [item for item in v if not isinstance(item, str)]
        if non_strings:
            raise ValueError(
                f"canonical-fields.json: field {k!r} pick list contains non-string "
                f"entries: {non_strings!r}"
            )
        fields[k] = frozenset(v)

    return frozenset(fields.keys()), fields


CANONICAL_FIELDS, PICK_LISTS = _load_pick_lists(_FIXTURE_PATH)

# Fields in CANONICAL_FIELDS whose Order value is a tuple (collection) rather
# than a scalar string.  _collect_canonicalization_traces uses this set to
# decide whether to iterate the value or compare it directly.  Adding a second
# tuple-typed canonical field only requires updating this set — no changes to
# the evaluator branching logic.
_COLLECTION_CANONICAL_FIELDS: frozenset[str] = frozenset({"ordered_tests"})


# ---------------------------------------------------------------------------
# Public helper
# ---------------------------------------------------------------------------


def canonicalize(field: str, value: str) -> CanonicalizationResult:
    """Trim + casefold + pick-list check for a single field value.

    Parameters
    ----------
    field:
        The Order field name (e.g., "specimen_type", "fixative").
    value:
        The raw string value as read from the Order (or rule literal).

    Returns
    -------
    CanonicalizationResult
        ``canonical`` is the trimmed, casefolded string.
        ``was_unknown`` is True iff *field* is in the canonical field set
        but *value* (after trim + casefold) is not in the pick list.
        For fields not in the canonical field set, ``was_unknown`` is always
        False (no pick list exists to be "not in").

        ``was_unknown=True`` signals "this value is not in the pick list, but
        processing continues" — the engine forwards the casefolded value
        downstream rather than rejecting it.  This is a forward-compatibility
        design choice: new specimen types introduced upstream should not crash
        the engine; they surface in the ``CanonicalizationTrace`` on the
        receipt so clinical engineers can see and triage them.
    """
    # G10: trim → casefold → pick-list check (pick-list-only; no synonym map).
    trimmed = value.strip()
    folded = trimmed.casefold()

    if field not in CANONICAL_FIELDS:
        # No pick list for this field; casefold only, never "unknown".
        return CanonicalizationResult(canonical=folded, was_unknown=False)

    pick_list = PICK_LISTS[field]
    if folded in pick_list:
        return CanonicalizationResult(canonical=folded, was_unknown=False)

    # Value not in pick list — forward-compatible pass-through with warning signal.
    return CanonicalizationResult(canonical=folded, was_unknown=True)


__all__ = [
    "CANONICAL_FIELDS",
    "PICK_LISTS",
    "CanonicalizationResult",
    "_COLLECTION_CANONICAL_FIELDS",
    "canonicalize",
]
