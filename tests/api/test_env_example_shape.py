"""Tests that .env.example contains entries for all config.py-consumed vars.

Parses .env.example for variable names (commented or uncommented).
Scans samantha_server/config.py for _required_* / _optional_* call sites.
Asserts every such variable name appears in .env.example.

Forward direction only — stale entries in .env.example are not enforced
(manual cleanup discipline at config-removal time per the plan).
"""

from __future__ import annotations

import re
from pathlib import Path


def _parse_env_example(path: Path) -> set[str]:
    """Return the set of variable names (commented or uncommented) in .env.example."""
    names: set[str] = set()
    for line in path.read_text().splitlines():
        line = line.strip()
        # Strip leading comment character(s) and whitespace
        line = line.lstrip("#").strip()
        # Match VAR_NAME= at the start of the line
        match = re.match(r"^([A-Z][A-Z0-9_]+)=", line)
        if match:
            names.add(match.group(1))
    return names


_CONFIG_INTERNAL_VARS: frozenset[str] = frozenset(
    {
        # Internal pytest env var read by the sentinel guard — not a user-facing config var.
        "PYTEST_CURRENT_TEST",
    }
)


def _find_config_vars(config_path: Path) -> set[str]:
    """Return variable names consumed by _required_* / _optional_* helpers in config.py.

    Scans for call patterns:
      _required_hex("VAR_NAME", ...)
      _optional_hex("VAR_NAME", ...)
      _required_int("VAR_NAME", ...)
      _required_float("VAR_NAME", ...)

    PR #131 M7: this scan is **deliberately limited to helper call sites**
    per the plan's spec. Bare ``os.environ.get(...)`` literals are
    intentionally not included — they're for ad-hoc internal reads
    (PYTEST_CURRENT_TEST sentinel, model-path resolution side paths) and
    are not the user-facing audit boundary. Variables that should be in
    .env.example must be routed through a typed helper.
    """
    text = config_path.read_text()
    names: set[str] = set()

    for match in re.finditer(
        r'_(?:required|optional)_(?:hex|int|float)\(\s*["\']([A-Z][A-Z0-9_]+)["\']',
        text,
    ):
        names.add(match.group(1))

    return names - _CONFIG_INTERNAL_VARS


def test_env_example_contains_all_config_vars() -> None:
    """Every variable consumed by config.py helpers appears in .env.example."""
    repo_root = Path(__file__).resolve().parents[2]
    env_example = repo_root / ".env.example"
    config_py = repo_root / "samantha_server" / "config.py"

    assert env_example.exists(), f".env.example not found at {env_example}"
    assert config_py.exists(), f"config.py not found at {config_py}"

    env_vars = _parse_env_example(env_example)
    config_vars = _find_config_vars(config_py)

    missing = config_vars - env_vars
    assert not missing, (
        f"Variables in config.py but missing from .env.example: {sorted(missing)}\n"
        f"Add commented entries for these variables to .env.example."
    )
