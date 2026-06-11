"""SCREENING_SCENARIO_IDS pin + drift guard.

The 33-fixture screening allow-list is the literal union of `DISC_SCENARIOS`
+ `HALL_SCENARIOS` in the upstream POC's `scripts/run_phase1_screen.sh`. The
parity CLI consumes `SCREENING_SCENARIO_IDS` at runtime; this test
diffs the pinned set against the upstream script when present, so a
drift in the upstream literal surfaces immediately rather than silently
producing a wrong-shape parity report.

Skip semantics: when the upstream POC's `scripts/run_phase1_screen.sh`
isn't available (CI, fresh checkout, contributor without the private
repo), the test skips cleanly. The pinned constant remains the
authoritative source for the parity CLI; the drift test is a developer
convenience that fires only on hosts that have both repos.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

# `SAMANTHA_REPO_PATH` overrides the search path so contributors with the
# upstream repo checked out elsewhere still get the drift guard. The
# committed default is the original developer's path; everyone else falls
# back to the env-var lookup or the test cleanly skips.
_DEFAULT_SAMANTHA_REPO = Path("/nonexistent/samantha-poc")
_SAMANTHA_REPO = Path(os.environ.get("SAMANTHA_REPO_PATH") or _DEFAULT_SAMANTHA_REPO)
_UPSTREAM_SCRIPT = _SAMANTHA_REPO / "scripts" / "run_phase1_screen.sh"


def test_screening_scenario_ids_pinned_count_is_33() -> None:
    """The pinned screening set has exactly 33 entries (25 discriminating + 8 hall)."""
    from samantha_server.eval.screening import SCREENING_SCENARIO_IDS

    assert len(SCREENING_SCENARIO_IDS) == 33, (
        f"Pinned screening set must be 33 fixtures (25 discriminating "
        f"+ 8 hallucination); got {len(SCREENING_SCENARIO_IDS)}"
    )


def test_screening_scenario_ids_match_upstream_script() -> None:
    """Drift guard against the upstream POC's `scripts/run_phase1_screen.sh`.

    Parses `DISC_SCENARIOS=...` and `HALL_SCENARIOS=...` literals from
    the script; the union must equal the pinned constant. Skipped when
    the upstream script isn't present.
    """
    if not _UPSTREAM_SCRIPT.exists():
        pytest.skip(
            f"Upstream script {_UPSTREAM_SCRIPT} not found — drift check "
            f"runs only on hosts with the samantha private repo checked out"
        )

    body = _UPSTREAM_SCRIPT.read_text()
    disc = _extract(body, "DISC_SCENARIOS")
    hall = _extract(body, "HALL_SCENARIOS")
    upstream = disc | hall

    from samantha_server.eval.screening import SCREENING_SCENARIO_IDS

    assert upstream == SCREENING_SCENARIO_IDS, (
        f"Pinned screening set drifted from upstream "
        f"{_UPSTREAM_SCRIPT}.\n"
        f"  pinned − upstream = {sorted(SCREENING_SCENARIO_IDS - upstream)}\n"
        f"  upstream − pinned = {sorted(upstream - SCREENING_SCENARIO_IDS)}"
    )


def _extract(body: str, var: str) -> set[str]:
    """Pull the comma-separated SC-NNN list from a `VAR="..."` bash literal."""
    match = re.search(rf'^{var}="([^"]*)"$', body, re.MULTILINE)
    assert match, f"Couldn't find {var}= in upstream script"
    return {token.strip() for token in match.group(1).split(",") if token.strip()}
