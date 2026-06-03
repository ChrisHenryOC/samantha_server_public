"""GH-156: replay() additive kwargs for parity-replay support.

Three additive kwargs:
- `receipts_db_path: Path | None` — when set, replay receipts go to that
  SQLite file rather than the default ":memory:" store. The parity CLI
  passes a per-run temp file; production receipts.db is never touched.
- `include_scenario_ids: set[str] | None` — when set, only matching
  scenarios are included. Filter applies *after* `load_scenarios()` and
  *before* fan-out so `overall_total` reflects the filtered population.
- `skiplist_path: Path | None` — when set, that path replaces the
  default `<directory>/.skiplist.json` lookup. Pointing at a
  nonexistent path effectively disables the skiplist for parity runs
  that need to score against the as-published corpus.

All defaults are `None` so existing call sites are unaffected.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


def _all_pass_scenario(scenario_id: str = "SC-PARITY01") -> dict:  # type: ignore[type-arg]
    """Minimal scenario that passes through the deterministic engine."""
    return {
        "scenario_id": scenario_id,
        "category": "rule_coverage",
        "description": f"Parity-kwarg test fixture {scenario_id}",
        "events": [
            {
                "step": 1,
                "event_type": "order_received",
                "event_data": {
                    "patient_name": "TEST, Parity",
                    "age": 50,
                    "sex": "F",
                    "specimen_type": "biopsy",
                    "anatomic_site": "breast",
                    "fixative": "formalin",
                    "fixation_time_hours": 24.0,
                    "ordered_tests": ["Breast IHC Panel"],
                    "priority": "routine",
                    "billing_info_present": True,
                },
                "expected_output": {
                    "next_state": "ACCEPTED",
                    "applied_rules": ["ACC-008"],
                    "flags": [],
                    "routing_path": "deterministic",
                },
            }
        ],
    }


def _write(tmp_path: Path, scenario: dict, category: str = "rule_coverage") -> Path:  # type: ignore[type-arg]
    sub = tmp_path / category
    sub.mkdir(parents=True, exist_ok=True)
    path = sub / f"{scenario['scenario_id'].lower()}.json"
    path.write_text(json.dumps(scenario))
    return path


# ---------------------------------------------------------------------------
# Slice 2 — receipts_db_path
# ---------------------------------------------------------------------------


def test_replay_writes_receipts_to_explicit_db_path(tmp_path: Path) -> None:
    """When `receipts_db_path` is set, replay() persists receipts there."""
    from samantha_server.scenarios.replay import replay

    _write(tmp_path, _all_pass_scenario("SC-RDB01"))
    db_path = tmp_path / "parity-receipts.db"

    replay(tmp_path, receipts_db_path=db_path)

    assert db_path.exists(), (
        f"GH-156: receipts_db_path={db_path} must exist after replay() — "
        f"the parity CLI relies on this for receipt-store isolation."
    )
    # The DB has the receipts schema; the deterministic-only scenario above
    # doesn't go through dispatch_event (which writes receipts), so the
    # exact row count depends on whether _build_replay_deps fired. The
    # contract is "deps are constructed against this path" — verify the
    # SQLite connection at least opens cleanly.
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
    finally:
        conn.close()
    # Schema must include the receipts table; even a zero-row scenario
    # leaves the table behind.
    assert tables, (
        f"GH-156: receipts DB at {db_path} has no tables — schema not "
        f"initialised. Got tables={tables!r}"
    )


def test_replay_default_keeps_receipts_in_memory(tmp_path: Path) -> None:
    """Default `receipts_db_path=None` preserves the in-memory receipts
    store — production receipts.db is never touched."""
    from samantha_server.scenarios.replay import replay

    _write(tmp_path, _all_pass_scenario("SC-RDB02"))
    home_db = tmp_path / "would-not-exist.db"

    # Run with no path — the replay must NOT create a file at home_db
    # or anywhere we can detect. The strongest assertion is "no new files
    # appeared in tmp_path beyond the fixture we wrote".
    before = {p.name for p in tmp_path.rglob("*") if p.is_file()}
    replay(tmp_path)
    after = {p.name for p in tmp_path.rglob("*") if p.is_file()}
    assert after == before, (
        "GH-156: default receipts_db_path=None must not create any DB "
        f"file. New files appeared: {after - before}"
    )
    assert not home_db.exists()


# ---------------------------------------------------------------------------
# Slice 3 — include_scenario_ids
# ---------------------------------------------------------------------------


def test_replay_include_scenario_ids_filters_population(tmp_path: Path) -> None:
    """When `include_scenario_ids` is set, only matching scenarios run and
    `overall_total` reflects the filtered population."""
    from samantha_server.scenarios.replay import replay

    _write(tmp_path, _all_pass_scenario("SC-INC01"))
    _write(tmp_path, _all_pass_scenario("SC-INC02"))
    _write(tmp_path, _all_pass_scenario("SC-INC03"))

    report = replay(tmp_path, include_scenario_ids={"SC-INC01", "SC-INC03"})

    assert report.overall_total == 2, (
        f"GH-156: filtered population must be 2 (SC-INC01 + SC-INC03); "
        f"got overall_total={report.overall_total}. Filter must apply "
        f"before fan-out, not after."
    )
    seen_ids = {v.scenario_id for v in report.scenario_verdicts}
    assert seen_ids == {"SC-INC01", "SC-INC03"}, (
        f"GH-156: scenario_verdicts must contain only the filtered ids; got {sorted(seen_ids)}"
    )


def test_replay_include_scenario_ids_none_is_no_filter(tmp_path: Path) -> None:
    """`include_scenario_ids=None` (default) preserves current behaviour:
    every scenario in the directory runs."""
    from samantha_server.scenarios.replay import replay

    _write(tmp_path, _all_pass_scenario("SC-INC04"))
    _write(tmp_path, _all_pass_scenario("SC-INC05"))

    report = replay(tmp_path)
    assert report.overall_total == 2


# ---------------------------------------------------------------------------
# Slice 4 — skiplist_path
# ---------------------------------------------------------------------------


def test_replay_skiplist_path_overrides_default_lookup(tmp_path: Path) -> None:
    """`skiplist_path` replaces the default `<directory>/.skiplist.json`.

    Pointing at a path with an empty/missing skiplist effectively disables
    the skiplist for the run — needed for the parity CLI which scores
    against the as-published corpus, not samantha_server's curated subset.
    """
    from samantha_server.scenarios.replay import replay

    corpus_dir = tmp_path / "corpus"
    overrides_dir = tmp_path / "overrides"
    overrides_dir.mkdir()
    _write(corpus_dir, _all_pass_scenario("SC-SKIP01"))

    # Plant a default skiplist that excludes SC-SKIP01 — without override,
    # this would put it in the skipped bucket.
    default_skip = corpus_dir / ".skiplist.json"
    default_skip.write_text(json.dumps({"skipped": {"SC-SKIP01": "https://example/issues/1"}}))

    # Override with a separate file (outside the corpus) that has no entries.
    parity_skip = overrides_dir / "parity-skiplist.json"
    parity_skip.write_text(json.dumps({"skipped": {}}))

    report = replay(corpus_dir, skiplist_path=parity_skip)

    # SC-SKIP01 must be in overall_total (not skipped via parity skiplist).
    assert report.overall_total == 1, (
        f"GH-156: skiplist_path override must replace default lookup; "
        f"got overall_total={report.overall_total} (default would have "
        f"excluded SC-SKIP01)"
    )


def test_replay_skiplist_path_nonexistent_disables_skiplist(tmp_path: Path) -> None:
    """A path that doesn't exist evaluates to "no skiplist" (matching the
    pre-existing `_load_skiplist` semantics for missing files)."""
    from samantha_server.scenarios.replay import replay

    _write(tmp_path, _all_pass_scenario("SC-SKIP02"))

    report = replay(tmp_path, skiplist_path=tmp_path / "does-not-exist.json")
    assert report.overall_total == 1


def test_replay_skiplist_path_override_with_invalid_url_raises(tmp_path: Path) -> None:
    """Override path validation must mirror the default-path validation:
    an entry with an empty URL raises ValueError before any scenarios run."""
    import pytest

    from samantha_server.scenarios.replay import replay

    corpus_dir = tmp_path / "corpus"
    overrides_dir = tmp_path / "overrides"
    overrides_dir.mkdir()
    _write(corpus_dir, _all_pass_scenario("SC-SKIP03"))

    bad_skip = overrides_dir / "parity-skiplist.json"
    bad_skip.write_text(json.dumps({"skipped": {"SC-SKIP03": ""}}))

    with pytest.raises(ValueError, match="empty or null URL"):
        replay(corpus_dir, skiplist_path=bad_skip)
