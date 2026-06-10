"""Pinned screening-subset scenario allow-list.

The 33-fixture screening subset is the literal union of `DISC_SCENARIOS`
+ `HALL_SCENARIOS` from the upstream POC's `scripts/run_phase1_screen.sh`
(25 discriminating + 8 hallucination). Pinning it in code lets the
parity CLI consume the subset without reaching outside the repo at
runtime; a drift test in `tests/eval/test_screening_subset.py` keeps
this constant in sync with the upstream script when both repos are
checked out.

Hand-edits land here only when the upstream script changes; never as
a side effect of a parity-replay code change.
"""

from __future__ import annotations

from typing import Final

# 25 discriminating scenarios — sourced from samantha's
# `DISC_SCENARIOS=` literal (run_phase1_screen.sh:23).
_DISCRIMINATING: Final[frozenset[str]] = frozenset(
    {
        "SC-003",
        "SC-005",
        "SC-006",
        "SC-009",
        "SC-010",
        "SC-011",
        "SC-012",
        "SC-013",
        "SC-014",
        "SC-016",
        "SC-019",
        "SC-020",
        "SC-024",
        "SC-026",
        "SC-028",
        "SC-038",
        "SC-045",
        "SC-081",
        "SC-082",
        "SC-087",
        "SC-088",
        "SC-100",
        "SC-101",
        "SC-102",
        "SC-103",
    }
)

# 8 hallucination scenarios — sourced from samantha's
# `HALL_SCENARIOS=` literal (run_phase1_screen.sh:25).
_HALLUCINATION: Final[frozenset[str]] = frozenset({f"SC-{n:03d}" for n in range(106, 114)})

SCREENING_SCENARIO_IDS: Final[frozenset[str]] = _DISCRIMINATING | _HALLUCINATION

__all__ = ["SCREENING_SCENARIO_IDS"]
