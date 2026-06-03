"""Parse N-sweep summary blocks from results/baselines/*.txt files.

The summary section has this structure:

    N-sweep summary for model: <model_name> (N=<n>)
    ====...
    Stable accuracy:  <x>/<total> (<pct>%)
    Raw accuracy:     <x>/<total> (<pct>%)

    Flaky/failing fixtures:
      <FIXTURE_ID>: <passed>/<sweep_n>
      ...

    ====...

Returns a DataFrame with columns: [model, fixture, sweep_n, passed].
Each row is one fixture from the "Flaky/failing fixtures:" block.

``sweep_n`` semantics
---------------------
``sweep_n`` is the **header** N (from the ``(N=<n>)`` group of the model
header), i.e., the total number of sweeps the corpus was run through for
that model. It is NOT the per-fixture denominator from the fixture line
(though the two are expected to be equal). The parser validates the
two match and raises :class:`SweepParserError` on mismatch — this would
indicate either a corrupt summary block or a regex/header-format drift.

A model with **no failing fixtures** (perfect run) has no
"Flaky/failing fixtures:" block. Such models contribute zero rows to
the resulting DataFrame; this is the empty-DataFrame path documented
in :func:`parse_sweep_file`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd


class SweepParserError(ValueError):
    """Raised when a sweep .txt file's structure violates the parser's expectations."""


_HEADER_RE = re.compile(r"^N-sweep summary for model:\s+(.+?)\s+\(N=(\d+)\)")
_FIXTURE_RE = re.compile(r"^\s{2}(\S+):\s+(\d+)/(\d+)")
_STABLE_RE = re.compile(r"^Stable accuracy:\s+(\d+)/(\d+)")
_RAW_RE = re.compile(r"^Raw accuracy:\s+(\d+)/(\d+)")
_COLUMNS = ["model", "fixture", "sweep_n", "passed"]
_SUMMARY_COLUMNS = ["model", "sweep_n", "stable_passed", "stable_total", "raw_passed", "raw_total"]


def parse_sweep_file(path: Path) -> pd.DataFrame:
    """Parse *path* and return one row per flaky/failing fixture per model.

    Parameters
    ----------
    path:
        Path to a sweep results .txt file.

    Returns
    -------
    pd.DataFrame
        Columns: model (str), fixture (str), sweep_n (int), passed (int).
        Only fixtures that appear in the "Flaky/failing fixtures:" block are
        included (fixtures with perfect scores are not listed there). A
        model with no failing fixtures contributes zero rows; if no model
        has any failing fixtures, the returned DataFrame is empty.

    Raises
    ------
    SweepParserError
        If a fixture line appears outside any model header (file
        corruption), or if a fixture line's per-fixture denominator
        disagrees with the model header's N (regex / format drift).
    """
    rows: list[dict[str, object]] = []
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    current_model: str | None = None
    current_n: int | None = None
    in_flaky_section = False

    for line_no, line in enumerate(lines, start=1):
        header_match = _HEADER_RE.match(line)
        if header_match:
            current_model = header_match.group(1)
            current_n = int(header_match.group(2))
            in_flaky_section = False
            continue

        if line.strip() == "Flaky/failing fixtures:":
            in_flaky_section = True
            continue

        if not in_flaky_section:
            continue

        fixture_match = _FIXTURE_RE.match(line)
        if fixture_match:
            if current_model is None or current_n is None:
                raise SweepParserError(
                    f"{path}:{line_no}: fixture line outside any model header "
                    f"({line.strip()!r}). File structure violated."
                )
            per_fixture_n = int(fixture_match.group(3))
            if per_fixture_n != current_n:
                raise SweepParserError(
                    f"{path}:{line_no}: per-fixture denominator {per_fixture_n} "
                    f"disagrees with model header N={current_n} "
                    f"(model={current_model!r}). Format drift or corruption."
                )
            rows.append(
                {
                    "model": current_model,
                    "fixture": fixture_match.group(1),
                    "sweep_n": current_n,
                    "passed": int(fixture_match.group(2)),
                }
            )
        elif line.strip().startswith("="):
            # Separator ends the flaky section. Blank lines inside the
            # block are tolerated (some emitters insert them); they do
            # NOT end the section by themselves.
            in_flaky_section = False

    return pd.DataFrame(rows, columns=_COLUMNS)


def parse_sweep_summaries(path: Path) -> pd.DataFrame:
    """Parse the "N-sweep summary for model: ... (N=n)" blocks.

    Returns one row per model with columns:
        model (str), sweep_n (int),
        stable_passed (int), stable_total (int),
        raw_passed (int), raw_total (int)
    """
    rows: list[dict[str, object]] = []
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    current_model: str | None = None
    current_n: int | None = None
    stable: tuple[int, int] | None = None
    raw: tuple[int, int] | None = None

    for line in lines:
        header_match = _HEADER_RE.match(line)
        if header_match:
            current_model = header_match.group(1)
            current_n = int(header_match.group(2))
            stable = None
            raw = None
            continue

        if current_model is None:
            continue

        stable_match = _STABLE_RE.match(line)
        if stable_match:
            stable = (int(stable_match.group(1)), int(stable_match.group(2)))
            continue

        raw_match = _RAW_RE.match(line)
        if raw_match:
            raw = (int(raw_match.group(1)), int(raw_match.group(2)))

        if stable is not None and raw is not None:
            rows.append(
                {
                    "model": current_model,
                    "sweep_n": current_n,
                    "stable_passed": stable[0],
                    "stable_total": stable[1],
                    "raw_passed": raw[0],
                    "raw_total": raw[1],
                }
            )
            current_model = None
            current_n = None
            stable = None
            raw = None

    return pd.DataFrame(rows, columns=_SUMMARY_COLUMNS)
