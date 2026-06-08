"""Vendor scenarios from samantha-public into tests/fixtures/scenarios/.

Usage:
    python -m scripts.vendor_scenarios [--source <path>] [--dest <path>]

For each JSON scenario file under the source root:
  - Copies it to tests/fixtures/scenarios/<category>/<scenario_id_lower>.json
  - Appends "routing_path": "deterministic" to every expected_output block
  - Skips writing if the destination file already has identical content (idempotent)

Writes a .vendor.json manifest to the dest root with provenance info.

No LLM imports. No samantha_server imports. Standalone script.
"""

from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

OVERRIDES_FILENAME = ".vendor-overrides.json"


@dataclass(frozen=True)
class VendorResult:
    """Summary of a vendor() run."""

    written: int
    skipped_identical: int
    overrides_respected: int
    unannounced_divergences: tuple[str, ...]  # scenario_ids
    stale_overrides: tuple[str, ...]  # override keys not found in source


def _load_overrides(dest_root: Path) -> dict[str, str]:
    """Load the per-file override list from *dest_root*/.vendor-overrides.json.

    Returns:
        dict mapping scenario_id -> citation URL.
        Empty dict when the file is absent or when overrides key is empty.

    Raises:
        ValueError: when the file exists but contains malformed JSON.
    """
    overrides_path = dest_root / OVERRIDES_FILENAME
    if not overrides_path.exists():
        return {}
    try:
        data = json.loads(overrides_path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed JSON in {overrides_path}: {exc}") from exc
    return dict(data.get("overrides", {}))


def _get_git_sha(repo_path: Path) -> str:
    """Return the HEAD SHA of the git repo at *repo_path*, or 'unknown'."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        pass
    return "unknown"


def _annotate_routing_path(data: dict) -> dict:  # type: ignore[type-arg]
    """Return a copy of *data* with routing_path added to every expected_output block."""
    data = copy.deepcopy(data)
    for event in data.get("events", []):
        expected = event.get("expected_output")
        if isinstance(expected, dict) and "routing_path" not in expected:
            expected["routing_path"] = "deterministic"
    return data


def vendor(source_root: Path, dest_root: Path, dry_run: bool = False) -> VendorResult:
    """Vendor all JSON scenarios from *source_root* into *dest_root*.

    Three states per source file:
    1. Identical — destination exists with same content => skip silently.
    2. Divergent + override entry => skip with "respected override" log.
    3. Divergent + no override => refuse to overwrite; record as unannounced divergence.

    When *dry_run* is True, no files are written (including the manifest).

    Returns:
        VendorResult summarising what happened.
    """
    if not dry_run:
        dest_root.mkdir(parents=True, exist_ok=True)

    overrides = _load_overrides(dest_root)

    written = 0
    skipped_identical = 0
    overrides_respected = 0
    unannounced: list[str] = []
    encountered_ids: set[str] = set()

    for source_path in sorted(source_root.rglob("*.json")):
        if source_path.name.startswith("."):
            continue

        category = source_path.parent.name
        if category == source_root.name:
            # File sits directly in the source root with no category subdir — skip
            continue

        try:
            raw = json.loads(source_path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            print(f"WARNING: skipping {source_path}: {exc}", file=sys.stderr)
            continue

        annotated = _annotate_routing_path(raw)
        scenario_id: str = raw.get("scenario_id", source_path.stem)
        encountered_ids.add(scenario_id)
        dest_filename = scenario_id.lower().replace("_", "-") + ".json"
        dest_dir = dest_root / category
        if not dry_run:
            dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / dest_filename

        new_content = json.dumps(annotated, indent=2) + "\n"

        if dest_path.exists():
            existing = dest_path.read_text()
            if existing == new_content:
                # State 1: identical — skip silently
                skipped_identical += 1
                continue
            # Content diverges
            if scenario_id in overrides:
                # State 2: override registered — respect it
                citation = overrides[scenario_id]
                print(
                    f"Respected override for {scenario_id} (see {citation});"
                    " keeping local version.",
                    file=sys.stderr,
                )
                overrides_respected += 1
                continue
            # State 3: unannounced divergence — refuse to overwrite
            print(
                f"WARNING: {scenario_id} diverges from upstream but has no override entry. "
                f"Add it to {dest_root / OVERRIDES_FILENAME} to suppress this warning.",
                file=sys.stderr,
            )
            unannounced.append(scenario_id)
            continue

        # Destination does not exist — write (or dry-run log)
        if dry_run:
            print(f"dry-run: would write {dest_path}", file=sys.stderr)
        else:
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_path.write_text(new_content)
        written += 1

    # Detect stale override entries (keys not encountered in source)
    stale: list[str] = sorted(set(overrides) - encountered_ids)
    if stale:
        print(
            f"WARNING: stale override entries (not found in source): {', '.join(stale)}. "
            "Remove or update them in .vendor-overrides.json.",
            file=sys.stderr,
        )

    # Write provenance manifest (skip in dry-run)
    if not dry_run:
        manifest = {
            "source_path": str(source_root.resolve()),
            "vendor_timestamp": datetime.now(UTC).isoformat(),
            "source_repo_git_sha": _get_git_sha(source_root),
        }
        manifest_path = dest_root / ".vendor.json"
        manifest_content = json.dumps(manifest, indent=2) + "\n"
        if not (manifest_path.exists() and manifest_path.read_text() == manifest_content):
            manifest_path.write_text(manifest_content)

    result = VendorResult(
        written=written,
        skipped_identical=skipped_identical,
        overrides_respected=overrides_respected,
        unannounced_divergences=tuple(unannounced),
        stale_overrides=tuple(stale),
    )

    mode_label = "dry-run" if dry_run else "Vendor"
    print(
        f"{mode_label} complete: {written} written, {skipped_identical} skipped (identical), "
        f"{overrides_respected} overrides respected, "
        f"{len(unannounced)} unannounced divergences."
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Vendor scenarios from samantha-public.")
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("samantha-public") / "scenarios",
        help="Source scenarios root directory",
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=Path(__file__).parent.parent / "tests" / "fixtures" / "scenarios",
        help="Destination fixtures directory",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help=(
            "Report what would happen without writing any files. "
            "Exits non-zero if unannounced divergences are found."
        ),
    )
    args = parser.parse_args(argv)

    if not args.source.exists():
        print(f"ERROR: source directory does not exist: {args.source}", file=sys.stderr)
        return 1

    try:
        result = vendor(args.source, args.dest, dry_run=args.dry_run)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if result.unannounced_divergences:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
