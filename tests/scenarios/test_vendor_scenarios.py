"""Tests for scripts/vendor_scenarios.py."""

import dataclasses
import json
import sys
from pathlib import Path

import pytest

# Allow importing from scripts/ without installing
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from scripts.vendor_scenarios import VendorResult, _load_overrides, vendor  # noqa: E402

_SAMANTHA_PUBLIC_SCENARIOS = Path.home() / "source" / "samantha-public" / "scenarios"
_REPO_FIXTURE_SCENARIOS = Path(__file__).parent.parent / "fixtures" / "scenarios"


def _make_source_scenario(tmp_path: Path, category: str, scenario_id: str) -> Path:
    """Write a minimal source scenario JSON into a category subdir."""
    subdir = tmp_path / category
    subdir.mkdir(parents=True, exist_ok=True)
    path = subdir / f"{scenario_id.lower()}.json"
    payload = {
        "scenario_id": scenario_id,
        "category": category,
        "description": "Test scenario",
        "events": [
            {
                "step": 1,
                "event_type": "order_received",
                "event_data": {"specimen_type": "biopsy"},
                "expected_output": {
                    "next_state": "ACCEPTED",
                    "applied_rules": ["ACC-008"],
                    "flags": [],
                },
            }
        ],
    }
    path.write_text(json.dumps(payload, indent=2))
    return path


def _run_vendor(source: Path, dest: Path) -> None:
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.vendor_scenarios",
            "--source",
            str(source),
            "--dest",
            str(dest),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"vendor_scenarios failed:\n{result.stdout}\n{result.stderr}")


def test_vendor_appends_routing_path(tmp_path: Path) -> None:
    source = tmp_path / "source"
    dest = tmp_path / "dest"
    _make_source_scenario(source, "rule_coverage", "SC-V01")

    _run_vendor(source, dest)

    vendored = dest / "rule_coverage" / "sc-v01.json"
    assert vendored.exists()
    data = json.loads(vendored.read_text())
    for event in data["events"]:
        assert event["expected_output"].get("routing_path") == "deterministic"


def test_vendor_writes_vendor_manifest(tmp_path: Path) -> None:
    source = tmp_path / "source"
    dest = tmp_path / "dest"
    _make_source_scenario(source, "rule_coverage", "SC-V02")

    _run_vendor(source, dest)

    manifest_path = dest / ".vendor.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text())
    assert "source_path" in manifest
    assert "vendor_timestamp" in manifest
    assert "source_repo_git_sha" in manifest


def test_vendor_is_idempotent(tmp_path: Path) -> None:
    source = tmp_path / "source"
    dest = tmp_path / "dest"
    _make_source_scenario(source, "rule_coverage", "SC-V03")

    _run_vendor(source, dest)

    vendored = dest / "rule_coverage" / "sc-v03.json"
    content_before = vendored.read_bytes()
    mtime_before = vendored.stat().st_mtime

    # Second run should not change the file
    _run_vendor(source, dest)

    content_after = vendored.read_bytes()
    mtime_after = vendored.stat().st_mtime

    assert content_before == content_after
    # mtime should not change because the file was not rewritten
    assert mtime_before == mtime_after


def test_vendor_lowercase_scenario_id_filename(tmp_path: Path) -> None:
    source = tmp_path / "source"
    dest = tmp_path / "dest"
    _make_source_scenario(source, "multi_rule", "SC-V04")

    _run_vendor(source, dest)

    # Filename should be lowercase scenario_id
    vendored = dest / "multi_rule" / "sc-v04.json"
    assert vendored.exists()


def test_vendor_multiple_categories(tmp_path: Path) -> None:
    source = tmp_path / "source"
    dest = tmp_path / "dest"
    _make_source_scenario(source, "rule_coverage", "SC-V05")
    _make_source_scenario(source, "multi_rule", "SC-V06")
    _make_source_scenario(source, "accumulated_state", "SC-V07")

    _run_vendor(source, dest)

    assert (dest / "rule_coverage" / "sc-v05.json").exists()
    assert (dest / "multi_rule" / "sc-v06.json").exists()
    assert (dest / "accumulated_state" / "sc-v07.json").exists()


# ---------------------------------------------------------------------------
# Slice 1: _load_overrides
# ---------------------------------------------------------------------------


def test_load_overrides_returns_empty_when_file_missing(tmp_path: Path) -> None:
    """Missing .vendor-overrides.json => empty dict."""
    result = _load_overrides(tmp_path)
    assert result == {}


def test_load_overrides_returns_overrides_dict_when_present(tmp_path: Path) -> None:
    """Present .vendor-overrides.json with valid overrides => returns the overrides dict."""
    overrides_data = {
        "_comment": "test",
        "overrides": {
            "SC-021": "https://github.com/ChrisHenryOC/samantha_server/pull/68",
            "SC-022": "https://github.com/ChrisHenryOC/samantha_server/pull/68",
        },
    }
    (tmp_path / ".vendor-overrides.json").write_text(json.dumps(overrides_data))

    result = _load_overrides(tmp_path)

    assert result == {
        "SC-021": "https://github.com/ChrisHenryOC/samantha_server/pull/68",
        "SC-022": "https://github.com/ChrisHenryOC/samantha_server/pull/68",
    }


def test_load_overrides_empty_overrides_key_is_valid(tmp_path: Path) -> None:
    """overrides: {} is valid and returns empty dict."""
    overrides_data = {"_comment": "no overrides yet", "overrides": {}}
    (tmp_path / ".vendor-overrides.json").write_text(json.dumps(overrides_data))

    result = _load_overrides(tmp_path)

    assert result == {}


def test_load_overrides_missing_overrides_key_returns_empty_dict(tmp_path: Path) -> None:
    """No 'overrides' key in JSON file => empty dict (both bare {} and other-keys forms)."""
    # bare empty object — no overrides key at all
    (tmp_path / ".vendor-overrides.json").write_text(json.dumps({}))
    assert _load_overrides(tmp_path) == {}

    # object with other keys but no overrides key
    (tmp_path / ".vendor-overrides.json").write_text(
        json.dumps({"_comment": "placeholder — no overrides yet"})
    )
    assert _load_overrides(tmp_path) == {}


def test_load_overrides_raises_on_malformed_json(tmp_path: Path) -> None:
    """Malformed JSON raises ValueError."""
    (tmp_path / ".vendor-overrides.json").write_text("not { valid json")

    import pytest

    with pytest.raises(ValueError, match="malformed"):
        _load_overrides(tmp_path)


# ---------------------------------------------------------------------------
# Slice 2: VendorResult dataclass + divergence handling in vendor()
# ---------------------------------------------------------------------------


def _write_override_file(dest_root: Path, overrides: dict[str, str]) -> None:
    data = {"_comment": "test overrides", "overrides": overrides}
    (dest_root / ".vendor-overrides.json").write_text(json.dumps(data))


def test_vendor_returns_vendor_result(tmp_path: Path) -> None:
    """vendor() must return a VendorResult dataclass, not None."""
    source = tmp_path / "source"
    dest = tmp_path / "dest"
    dest.mkdir(parents=True)
    _make_source_scenario(source, "rule_coverage", "SC-V10")

    result = vendor(source, dest)

    assert isinstance(result, VendorResult)


def test_vendor_result_is_frozen_dataclass_with_tuple_fields(tmp_path: Path) -> None:
    """VendorResult must be a frozen dataclass; unannounced_divergences must be a tuple."""
    source = tmp_path / "source"
    dest = tmp_path / "dest"
    dest.mkdir(parents=True)
    _make_source_scenario(source, "rule_coverage", "SC-V10B")

    result = vendor(source, dest)

    assert dataclasses.is_dataclass(result), "VendorResult must be a dataclass"
    assert isinstance(result.unannounced_divergences, tuple), (
        "unannounced_divergences must be a tuple, not a list"
    )
    assert isinstance(result.stale_overrides, tuple), "stale_overrides must be a tuple, not a list"
    # Frozen: mutation must raise
    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
        result.written = 999  # type: ignore[misc]


def test_vendor_skips_identical_content_silently(tmp_path: Path) -> None:
    """Identical content between source and dest: skipped_identical increments, no overwrite."""
    source = tmp_path / "source"
    dest = tmp_path / "dest"
    dest.mkdir(parents=True)
    _make_source_scenario(source, "rule_coverage", "SC-V11")

    # First run writes
    result1 = vendor(source, dest)
    assert result1.written == 1
    assert result1.skipped_identical == 0

    # Second run skips
    result2 = vendor(source, dest)
    assert result2.written == 0
    assert result2.skipped_identical == 1
    assert result2.overrides_respected == 0
    assert result2.unannounced_divergences == ()


def test_vendor_respects_override_for_diverged_fixture(tmp_path: Path) -> None:
    """Dest diverges AND scenario_id is in overrides => skip, count overrides_respected."""
    source = tmp_path / "source"
    dest = tmp_path / "dest"
    dest.mkdir(parents=True)
    _make_source_scenario(source, "rule_coverage", "SC-V12")

    # First vendor run writes the file
    vendor(source, dest)

    # Corrupt the destination (simulate local correction)
    dest_file = dest / "rule_coverage" / "sc-v12.json"
    original_content = dest_file.read_text()
    corrupted = original_content.replace("deterministic", "LOCAL_OVERRIDE")
    dest_file.write_text(corrupted)

    # Register it as an override
    _write_override_file(dest, {"SC-V12": "https://github.com/example/pull/1"})

    result = vendor(source, dest)

    # Dest should still have the locally-corrected content
    assert dest_file.read_text() == corrupted
    assert result.overrides_respected == 1
    assert result.written == 0
    assert result.unannounced_divergences == ()


def test_vendor_refuses_to_overwrite_unannounced_divergence(tmp_path: Path) -> None:
    """Dest diverges and NO override => unannounced_divergences non-empty, dest unchanged."""
    source = tmp_path / "source"
    dest = tmp_path / "dest"
    dest.mkdir(parents=True)
    _make_source_scenario(source, "rule_coverage", "SC-V13")

    # First vendor run writes the file
    vendor(source, dest)

    # Corrupt the destination (simulate local correction)
    dest_file = dest / "rule_coverage" / "sc-v13.json"
    corrupted = dest_file.read_text().replace("deterministic", "LOCAL_OVERRIDE")
    dest_file.write_text(corrupted)

    # No override file added — divergence is unannounced
    result = vendor(source, dest)

    # Dest must still have the locally-corrected content (not overwritten)
    assert dest_file.read_text() == corrupted
    assert "SC-V13" in result.unannounced_divergences
    assert result.written == 0
    assert result.overrides_respected == 0


# ---------------------------------------------------------------------------
# Slice 3: --dry-run flag
# ---------------------------------------------------------------------------


def test_dry_run_does_not_write_new_files(tmp_path: Path) -> None:
    """In dry-run mode, new source files are NOT written to dest."""
    source = tmp_path / "source"
    dest = tmp_path / "dest"
    dest.mkdir(parents=True)
    _make_source_scenario(source, "rule_coverage", "SC-V20")

    result = vendor(source, dest, dry_run=True)

    dest_file = dest / "rule_coverage" / "sc-v20.json"
    assert not dest_file.exists()
    assert result.written == 1  # counted as "would write"


def test_dry_run_does_not_overwrite_identical_dest(tmp_path: Path) -> None:
    """In dry-run mode, identical-content destinations are not touched."""
    source = tmp_path / "source"
    dest = tmp_path / "dest"
    dest.mkdir(parents=True)
    _make_source_scenario(source, "rule_coverage", "SC-V21")

    # Normal run first to create dest file
    vendor(source, dest)
    dest_file = dest / "rule_coverage" / "sc-v21.json"
    mtime_before = dest_file.stat().st_mtime

    result = vendor(source, dest, dry_run=True)

    # File must not be touched
    assert dest_file.stat().st_mtime == mtime_before
    assert result.skipped_identical == 1


def test_dry_run_does_not_write_manifest(tmp_path: Path) -> None:
    """In dry-run mode, the .vendor.json manifest is NOT written."""
    source = tmp_path / "source"
    dest = tmp_path / "dest"
    dest.mkdir(parents=True)
    _make_source_scenario(source, "rule_coverage", "SC-V22")

    vendor(source, dest, dry_run=True)

    manifest_path = dest / ".vendor.json"
    assert not manifest_path.exists()


def test_dry_run_returns_nonzero_for_unannounced_divergence(tmp_path: Path) -> None:
    """In dry-run mode, unannounced divergences cause unannounced_divergences to be non-empty."""
    import subprocess as sp

    source = tmp_path / "source"
    dest = tmp_path / "dest"
    dest.mkdir(parents=True)
    _make_source_scenario(source, "rule_coverage", "SC-V23")

    # First vendor run (normal) writes the file
    vendor(source, dest)

    # Corrupt dest (simulate local correction without an override entry)
    dest_file = dest / "rule_coverage" / "sc-v23.json"
    dest_file.write_text(dest_file.read_text().replace("deterministic", "LOCAL_OVERRIDE"))

    result = vendor(source, dest, dry_run=True)

    assert "SC-V23" in result.unannounced_divergences

    # Confirm main() --dry-run flag is accepted and exits non-zero (not argparse error)
    proc = sp.run(
        [
            sys.executable,
            "-m",
            "scripts.vendor_scenarios",
            "--source",
            str(source),
            "--dest",
            str(dest),
            "--dry-run",
        ],
        capture_output=True,
        text=True,
    )
    assert "unrecognized arguments" not in proc.stderr, (
        "--dry-run flag must be accepted by argparse"
    )
    assert proc.returncode != 0


# ---------------------------------------------------------------------------
# Slice 5: CI guard test — no unannounced divergences against real upstream
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Slice 4: data-only (overrides file populated; no test)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Slice 5: CI guard test — fixture-controlled (always runs) + real upstream
# ---------------------------------------------------------------------------


def test_vendor_dry_run_against_tmp_source_with_planted_divergence(
    tmp_path: Path,
) -> None:
    """Fixture-controlled CI guard: planted divergence + override + identical + new.

    Constructs a small source corpus with exactly 4 scenarios:
    - SC-PLANTED-NEW: exists in source only (no dest) — should be counted as written=1
    - SC-PLANTED-IDENTICAL: identical content in source and dest — skipped_identical=1
    - SC-PLANTED-OVERRIDE: divergent in dest, but override registered — overrides_respected=1
    - SC-PLANTED-UNANNOUNCED: divergent in dest, no override — unannounced_divergences=1

    Asserts the result tallies exactly and that NO files were written to dest.
    This test runs unconditionally in CI.
    """
    source = tmp_path / "source"
    dest = tmp_path / "dest"

    # Build source scenarios
    _make_source_scenario(source, "rule_coverage", "SC-PLANTED-NEW")
    _make_source_scenario(source, "rule_coverage", "SC-PLANTED-IDENTICAL")
    _make_source_scenario(source, "rule_coverage", "SC-PLANTED-OVERRIDE")
    _make_source_scenario(source, "rule_coverage", "SC-PLANTED-UNANNOUNCED")

    # Build dest — pre-populate all except the "new" one
    dest.mkdir(parents=True)
    dest_cat = dest / "rule_coverage"
    dest_cat.mkdir(parents=True)

    # Produce the annotated content for these scenarios (mirrors vendor() logic)

    def _annotated_content(scenario_id: str) -> str:
        payload = {
            "scenario_id": scenario_id,
            "category": "rule_coverage",
            "description": "Test scenario",
            "events": [
                {
                    "step": 1,
                    "event_type": "order_received",
                    "event_data": {"specimen_type": "biopsy"},
                    "expected_output": {
                        "next_state": "ACCEPTED",
                        "applied_rules": ["ACC-008"],
                        "flags": [],
                        "routing_path": "deterministic",  # already annotated
                    },
                }
            ],
        }
        return json.dumps(payload, indent=2) + "\n"

    # SC-PLANTED-IDENTICAL: dest has exact same content as vendor() would produce
    (dest_cat / "sc-planted-identical.json").write_text(_annotated_content("SC-PLANTED-IDENTICAL"))

    # SC-PLANTED-OVERRIDE: dest has divergent content (local correction)
    (dest_cat / "sc-planted-override.json").write_text(
        _annotated_content("SC-PLANTED-OVERRIDE").replace("deterministic", "LOCAL_OVERRIDE")
    )

    # SC-PLANTED-UNANNOUNCED: dest has divergent content (no override registered)
    (dest_cat / "sc-planted-unannounced.json").write_text(
        _annotated_content("SC-PLANTED-UNANNOUNCED").replace("deterministic", "LOCAL_UNANNOUNCED")
    )

    # Write override file registering only SC-PLANTED-OVERRIDE
    _write_override_file(dest, {"SC-PLANTED-OVERRIDE": "https://github.com/example/pull/1"})

    # Capture dest tree state before call
    dest_files_before = {p: p.read_bytes() for p in dest.rglob("*") if p.is_file()}

    result = vendor(source, dest, dry_run=True)

    # Tally assertions
    assert result.written == 1, f"expected written=1, got {result.written}"
    assert result.skipped_identical == 1, (
        f"expected skipped_identical=1, got {result.skipped_identical}"
    )
    assert result.overrides_respected == 1, (
        f"expected overrides_respected=1, got {result.overrides_respected}"
    )
    assert result.unannounced_divergences == ("SC-PLANTED-UNANNOUNCED",), (
        f"expected unannounced_divergences==('SC-PLANTED-UNANNOUNCED',), "
        f"got {result.unannounced_divergences}"
    )

    # No files written: dest tree must be byte-for-byte identical after the call
    dest_files_after = {p: p.read_bytes() for p in dest.rglob("*") if p.is_file()}
    new_files = set(dest_files_after) - set(dest_files_before)
    assert not new_files, f"dry_run wrote new files: {new_files}"
    for path, content in dest_files_before.items():
        assert dest_files_after.get(path) == content, f"dry_run modified {path}"


# ---------------------------------------------------------------------------
# Slice 5a: main() catches ValueError from malformed overrides file
# ---------------------------------------------------------------------------


def test_main_prints_clean_error_on_malformed_overrides_file(tmp_path: Path) -> None:
    """Malformed .vendor-overrides.json must exit 1 with a clean ERROR: line, not a traceback."""
    import subprocess as sp

    source = tmp_path / "source"
    dest = tmp_path / "dest"
    dest.mkdir(parents=True)
    _make_source_scenario(source, "rule_coverage", "SC-V40")

    # Write a malformed overrides file
    (dest / ".vendor-overrides.json").write_text("not { valid json")

    proc = sp.run(
        [
            sys.executable,
            "-m",
            "scripts.vendor_scenarios",
            "--source",
            str(source),
            "--dest",
            str(dest),
        ],
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 1, f"expected exit code 1, got {proc.returncode}"
    combined_output = proc.stdout + proc.stderr
    assert "ERROR:" in combined_output, (
        f"expected a clean ERROR: line in output, got:\n{combined_output}"
    )
    assert "Traceback" not in combined_output, (
        f"expected no Traceback in output, got:\n{combined_output}"
    )


# ---------------------------------------------------------------------------
# Slice 5b: dry-run does not create dest directory
# ---------------------------------------------------------------------------


def test_dry_run_does_not_create_dest_directory(tmp_path: Path) -> None:
    """When dry_run=True and dest does not exist, vendor() must not create it."""
    source = tmp_path / "source"
    dest = tmp_path / "nonexistent-dest"
    _make_source_scenario(source, "rule_coverage", "SC-V30")

    assert not dest.exists(), "pre-condition: dest must not exist before call"
    vendor(source, dest, dry_run=True)
    assert not dest.exists(), "dry_run=True must not create the dest directory"


# ---------------------------------------------------------------------------
# Slice 6: stale override entries
# ---------------------------------------------------------------------------


def test_vendor_reports_stale_override_entries_in_result(tmp_path: Path) -> None:
    """Override file lists SC-NONEXISTENT; source has only SC-REAL.

    Expects result.stale_overrides == ("SC-NONEXISTENT",).
    """
    source = tmp_path / "source"
    dest = tmp_path / "dest"
    dest.mkdir(parents=True)
    _make_source_scenario(source, "rule_coverage", "SC-REAL")

    # Override file references a scenario that does NOT exist in source
    _write_override_file(dest, {"SC-NONEXISTENT": "https://github.com/example/pr/99"})

    result = vendor(source, dest)

    assert result.stale_overrides == ("SC-NONEXISTENT",), (
        f"expected stale_overrides==('SC-NONEXISTENT',), got {result.stale_overrides}"
    )


# ---------------------------------------------------------------------------
# Slice 6b: mixed-state tally test
# ---------------------------------------------------------------------------


def test_vendor_tallies_mixed_states_correctly(tmp_path: Path) -> None:
    """All four counters are non-zero in a single vendor() call.

    Source has 5 scenarios:
    - SC-MIX-NEW: no dest file (new)
    - SC-MIX-ID1, SC-MIX-ID2: identical content in dest (2 skipped_identical)
    - SC-MIX-PROT: divergent + override registered (overrides_respected=1)
    - SC-MIX-UNANNO: divergent + no override (unannounced_divergences=1)
    """
    source = tmp_path / "source"
    dest = tmp_path / "dest"

    # Build source scenarios
    _make_source_scenario(source, "rule_coverage", "SC-MIX-NEW")
    _make_source_scenario(source, "rule_coverage", "SC-MIX-ID1")
    _make_source_scenario(source, "rule_coverage", "SC-MIX-ID2")
    _make_source_scenario(source, "rule_coverage", "SC-MIX-PROT")
    _make_source_scenario(source, "rule_coverage", "SC-MIX-UNANNO")

    # Pre-populate dest by running vendor once (gets correct annotated content)
    result0 = vendor(source, dest)
    assert result0.written == 5  # sanity check: all written on first run

    # Now corrupt SC-MIX-PROT and SC-MIX-UNANNO to create divergences
    dest_cat = dest / "rule_coverage"
    prot_file = dest_cat / "sc-mix-prot.json"
    unanno_file = dest_cat / "sc-mix-unanno.json"
    prot_file.write_text(prot_file.read_text().replace("deterministic", "LOCAL_PROT"))
    unanno_file.write_text(unanno_file.read_text().replace("deterministic", "LOCAL_UNANNO"))

    # Register the protected one as an override
    _write_override_file(dest, {"SC-MIX-PROT": "https://github.com/example/pr/2"})

    # Remove SC-MIX-NEW from dest to make it a new file again
    (dest_cat / "sc-mix-new.json").unlink()

    result = vendor(source, dest)

    assert result.written == 1, f"expected written=1, got {result.written}"
    assert result.skipped_identical == 2, (
        f"expected skipped_identical=2, got {result.skipped_identical}"
    )
    assert result.overrides_respected == 1, (
        f"expected overrides_respected=1, got {result.overrides_respected}"
    )
    assert result.unannounced_divergences == ("SC-MIX-UNANNO",), (
        f"expected unannounced_divergences==('SC-MIX-UNANNO',), "
        f"got {result.unannounced_divergences}"
    )

    # Protected file must not be overwritten
    assert "LOCAL_PROT" in prot_file.read_text(), "override-protected file was overwritten"
    # Unannounced file must not be overwritten
    assert "LOCAL_UNANNO" in unanno_file.read_text(), "unannounced-divergence file was overwritten"


# ---------------------------------------------------------------------------
# Slice 6c: dry_run=True + overrides_respected combination
# ---------------------------------------------------------------------------


def test_dry_run_with_overrides_respected_does_not_modify_files(tmp_path: Path) -> None:
    """dry_run=True with a divergent-but-overridden scenario.

    Asserts: overrides_respected=1, file content unchanged, manifest not modified.
    """
    source = tmp_path / "source"
    dest = tmp_path / "dest"
    dest.mkdir(parents=True)
    _make_source_scenario(source, "rule_coverage", "SC-DR-OVR")

    # First normal run writes the file
    vendor(source, dest)
    dest_file = dest / "rule_coverage" / "sc-dr-ovr.json"
    original_content = dest_file.read_text()

    # Corrupt dest (simulate local correction)
    dest_file.write_text(original_content.replace("deterministic", "LOCAL_DR"))

    # Register as an override
    _write_override_file(dest, {"SC-DR-OVR": "https://github.com/example/pr/3"})

    corrupted_content = dest_file.read_text()
    manifest_path = dest / ".vendor.json"
    manifest_mtime_before = manifest_path.stat().st_mtime if manifest_path.exists() else None

    result = vendor(source, dest, dry_run=True)

    assert result.overrides_respected == 1, (
        f"expected overrides_respected=1, got {result.overrides_respected}"
    )
    # File content must be unchanged
    assert dest_file.read_text() == corrupted_content, "dry_run modified file content"
    # Manifest must not be created or modified by dry-run
    if manifest_mtime_before is not None:
        assert manifest_path.stat().st_mtime == manifest_mtime_before, "dry_run modified manifest"
    else:
        assert not manifest_path.exists(), "dry_run created a manifest"


# ---------------------------------------------------------------------------
# Slice 7: CI guard test (real upstream, developer-only)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _SAMANTHA_PUBLIC_SCENARIOS.exists(),
    reason="samantha-public source not available locally",
)
def test_vendor_no_unannounced_divergences_against_upstream() -> None:
    """Dry-run vendor against the real upstream must report zero unannounced divergences.

    This test is the CI guard: if a locally-corrected fixture diverges from
    upstream without an entry in .vendor-overrides.json, this test will fail,
    making the regression visible before any silent overwrite can occur.

    Skipped on machines where ~/source/samantha-public/scenarios does not exist.
    """
    result = vendor(_SAMANTHA_PUBLIC_SCENARIOS, _REPO_FIXTURE_SCENARIOS, dry_run=True)

    assert result.unannounced_divergences == (), (
        f"Unannounced divergences found: {result.unannounced_divergences}. "
        f"Add each scenario ID to tests/fixtures/scenarios/.vendor-overrides.json "
        f"with a citation URL before merging."
    )
