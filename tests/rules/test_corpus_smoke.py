"""Final corpus-level smoke gate (Step 10).

Asserts that the entire 40-rule corpus loads correctly and that every
``rule_id`` in ``docs/rule-breakdown/inventory.csv`` has a matching YAML
spec under ``samantha_server/rules/specs/`` (and vice versa).  This is
the gate that unblocks Step 11 (engine kernel) — Steps 6–9 each ship a
per-step count assertion (9, 6, 9, 11) and Step 10 closes the loop.

A drift between the inventory CSV and the specs directory is the most
likely failure mode (someone adds a row to inventory but forgets to
write the spec, or vice versa).  The set comparison below catches both
directions.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from samantha_server.rules.loader import load_rule_specs

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SPECS_DIR = _REPO_ROOT / "samantha_server" / "rules" / "specs"
_INVENTORY_CSV = _REPO_ROOT / "docs" / "rule-breakdown" / "inventory.csv"

_EXPECTED_CORPUS_SIZE = 44 # +2 for ACC-011, ACC-012


def _expected_rule_ids() -> set[str]:
    """Read the rule_id column from inventory.csv.

    Raises ValueError loudly if the `rule_id` column is missing (header
    rename, BOM, etc.) or if any data row carries a blank rule_id, so the
    corpus smoke gate can't silently produce an empty set on schema drift.
    """
    with _INVENTORY_CSV.open(newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None or "rule_id" not in reader.fieldnames:
            raise ValueError(
                f"inventory.csv missing 'rule_id' column; got fieldnames={reader.fieldnames!r}"
            )
        ids: set[str] = set()
        for line_no, row in enumerate(reader, start=2):
            value = (row.get("rule_id") or "").strip()
            if not value:
                raise ValueError(f"inventory.csv line {line_no}: blank 'rule_id' cell")
            ids.add(value)
    return ids


@pytest.fixture(scope="module")
def loaded_rule_ids() -> set[str]:
    return {spec.rule_id for spec in load_rule_specs(_SPECS_DIR)}


@pytest.fixture(scope="module")
def expected_rule_ids() -> set[str]:
    return _expected_rule_ids()


def test_corpus_has_expected_rule_count(loaded_rule_ids: set[str]) -> None:
    assert len(loaded_rule_ids) == _EXPECTED_CORPUS_SIZE


def test_inventory_has_expected_rule_count(expected_rule_ids: set[str]) -> None:
    assert len(expected_rule_ids) == _EXPECTED_CORPUS_SIZE, (
        f"inventory.csv has {len(expected_rule_ids)} rule_ids; "
        f"expected {_EXPECTED_CORPUS_SIZE} — fix the CSV"
    )


def test_specs_cover_every_inventory_row(
    loaded_rule_ids: set[str], expected_rule_ids: set[str]
) -> None:
    missing_specs = expected_rule_ids - loaded_rule_ids
    assert not missing_specs, (
        f"inventory.csv lists rule_ids without YAML specs: {sorted(missing_specs)}"
    )


def test_inventory_covers_every_loaded_spec(
    loaded_rule_ids: set[str], expected_rule_ids: set[str]
) -> None:
    extra_specs = loaded_rule_ids - expected_rule_ids
    assert not extra_specs, f"YAML specs without inventory.csv rows: {sorted(extra_specs)}"
