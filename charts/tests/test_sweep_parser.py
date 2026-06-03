"""Slice 3: sweep_parser parses N-sweep summary blocks."""

from pathlib import Path

import pandas as pd
import pytest

SYNTHETIC_SWEEP = """\
[Sweep 1/2]

============================================================
Model: test-model-alpha
============================================================
[1/3] FIX-001 pass (0.0s)
[2/3] FIX-002 fail (0.1s)
[3/3] FIX-003 pass (0.0s)

[Sweep 2/2]

============================================================
Model: test-model-alpha
============================================================
[1/3] FIX-001 pass (0.0s)
[2/3] FIX-002 pass (0.1s)
[3/3] FIX-003 fail (0.0s)

============================================================
N-sweep summary for model: test-model-alpha (N=2)
============================================================
Stable accuracy:  1/3 (33.33%)
Raw accuracy:     4/6 (66.67%)

Flaky/failing fixtures:
  FIX-002: 1/2
  FIX-003: 1/2

============================================================
"""


def test_parse_sweep_file_shape(tmp_path: Path) -> None:
    from samantha_charts.data.sweep_parser import parse_sweep_file

    sweep_file = tmp_path / "test.txt"
    sweep_file.write_text(SYNTHETIC_SWEEP)

    df = parse_sweep_file(sweep_file)

    assert isinstance(df, pd.DataFrame)
    assert list(df.columns) == ["model", "fixture", "sweep_n", "passed"]
    # One model, 2 flaky fixtures → 2 rows
    assert len(df) == 2


def test_parse_sweep_file_values(tmp_path: Path) -> None:
    from samantha_charts.data.sweep_parser import parse_sweep_file

    sweep_file = tmp_path / "test.txt"
    sweep_file.write_text(SYNTHETIC_SWEEP)

    df = parse_sweep_file(sweep_file)

    # FIX-002: 1/2 passes
    fix002 = df[df["fixture"] == "FIX-002"].iloc[0]
    assert fix002["model"] == "test-model-alpha"
    assert fix002["sweep_n"] == 2
    assert fix002["passed"] == 1

    # FIX-003: 1/2 passes
    fix003 = df[df["fixture"] == "FIX-003"].iloc[0]
    assert fix003["passed"] == 1
    assert fix003["sweep_n"] == 2


def test_parse_sweep_file_dtypes(tmp_path: Path) -> None:
    from samantha_charts.data.sweep_parser import parse_sweep_file

    sweep_file = tmp_path / "test.txt"
    sweep_file.write_text(SYNTHETIC_SWEEP)

    df = parse_sweep_file(sweep_file)

    assert df["sweep_n"].dtype == int
    assert df["passed"].dtype == int


# Clean-sweep input: model header + summary but NO "Flaky/failing fixtures:" block.
# Real-world output produces this layout when every fixture passes all sweeps (149/149).
_CLEAN_SWEEP = """\
[Sweep 1/5]

============================================================
N-sweep summary for model: perfect-model (N=5)
============================================================
Stable accuracy:  149/149 (100.00%)
Raw accuracy:     745/745 (100.00%)

============================================================
"""


def test_parse_sweep_file_clean_sweep_returns_empty_df(tmp_path: Path) -> None:
    """A model with no failing fixtures yields zero rows + correct columns."""
    from samantha_charts.data.sweep_parser import parse_sweep_file

    sweep_file = tmp_path / "clean.txt"
    sweep_file.write_text(_CLEAN_SWEEP)

    df = parse_sweep_file(sweep_file)

    assert isinstance(df, pd.DataFrame)
    assert list(df.columns) == ["model", "fixture", "sweep_n", "passed"]
    assert len(df) == 0


# Bad input: fixture line with per-fixture denominator that disagrees with header N.
_DRIFTED_SWEEP = """\
============================================================
N-sweep summary for model: drift-model (N=5)
============================================================

Flaky/failing fixtures:
  FIX-001: 4/3
"""


def test_parse_sweep_file_raises_on_denominator_drift(tmp_path: Path) -> None:
    """Fixture denominator mismatch must raise SweepParserError, not silently drop the row."""
    from samantha_charts.data.sweep_parser import SweepParserError, parse_sweep_file

    sweep_file = tmp_path / "drift.txt"
    sweep_file.write_text(_DRIFTED_SWEEP)

    with pytest.raises(SweepParserError, match="disagrees with model header"):
        parse_sweep_file(sweep_file)


_ORPHAN_FIXTURE_SWEEP = """\
Flaky/failing fixtures:
  FIX-001: 1/5
"""


def test_parse_sweep_file_raises_on_orphan_fixture(tmp_path: Path) -> None:
    """A fixture line before any model header must raise (file corruption)."""
    from samantha_charts.data.sweep_parser import SweepParserError, parse_sweep_file

    sweep_file = tmp_path / "orphan.txt"
    sweep_file.write_text(_ORPHAN_FIXTURE_SWEEP)

    with pytest.raises(SweepParserError, match="outside any model header"):
        parse_sweep_file(sweep_file)


# ---------------------------------------------------------------------------
# Slice A: parse_sweep_summaries
# ---------------------------------------------------------------------------

_SUMMARY_COLUMNS = ["model", "sweep_n", "stable_passed", "stable_total", "raw_passed", "raw_total"]

_SINGLE_MODEL_SUMMARY = """\
[Sweep 1/2]

============================================================
N-sweep summary for model: test-model-alpha (N=2)
============================================================
Stable accuracy:  1/3 (33.33%)
Raw accuracy:     4/6 (66.67%)

Flaky/failing fixtures:
  FIX-002: 1/2
  FIX-003: 1/2

============================================================
"""

_TWO_MODEL_SUMMARY = """\
============================================================
N-sweep summary for model: model-one (N=3)
============================================================
Stable accuracy:  2/5 (40.00%)
Raw accuracy:     9/15 (60.00%)

Flaky/failing fixtures:
  FIX-A: 2/3

============================================================

============================================================
N-sweep summary for model: model-two (N=3)
============================================================
Stable accuracy:  5/5 (100.00%)
Raw accuracy:     15/15 (100.00%)

============================================================
"""

_NO_SUMMARY = """\
[Sweep 1/2]

Some other content here.
No N-sweep summary blocks at all.
"""


def test_parse_sweep_summaries_columns(tmp_path: Path) -> None:
    """Returns a DataFrame with the expected columns and int dtypes for count fields."""
    from samantha_charts.data.sweep_parser import parse_sweep_summaries

    f = tmp_path / "single.txt"
    f.write_text(_SINGLE_MODEL_SUMMARY)

    df = parse_sweep_summaries(f)

    assert isinstance(df, pd.DataFrame)
    assert list(df.columns) == _SUMMARY_COLUMNS
    for col in ["sweep_n", "stable_passed", "stable_total", "raw_passed", "raw_total"]:
        assert df[col].dtype == int, f"{col} should be int dtype"


def test_parse_sweep_summaries_single_model(tmp_path: Path) -> None:
    """Single-model summary yields one row with correct values."""
    from samantha_charts.data.sweep_parser import parse_sweep_summaries

    f = tmp_path / "single.txt"
    f.write_text(_SINGLE_MODEL_SUMMARY)

    df = parse_sweep_summaries(f)

    assert len(df) == 1
    row = df.iloc[0]
    assert row["model"] == "test-model-alpha"
    assert row["sweep_n"] == 2
    assert row["stable_passed"] == 1
    assert row["stable_total"] == 3
    assert row["raw_passed"] == 4
    assert row["raw_total"] == 6


def test_parse_sweep_summaries_multi_model(tmp_path: Path) -> None:
    """Two summary blocks yield two rows in document order."""
    from samantha_charts.data.sweep_parser import parse_sweep_summaries

    f = tmp_path / "two.txt"
    f.write_text(_TWO_MODEL_SUMMARY)

    df = parse_sweep_summaries(f)

    assert len(df) == 2
    assert df.iloc[0]["model"] == "model-one"
    assert df.iloc[0]["stable_passed"] == 2
    assert df.iloc[0]["stable_total"] == 5
    assert df.iloc[0]["raw_passed"] == 9
    assert df.iloc[0]["raw_total"] == 15
    assert df.iloc[1]["model"] == "model-two"
    assert df.iloc[1]["stable_passed"] == 5


def test_parse_sweep_summaries_empty_yields_empty_df(tmp_path: Path) -> None:
    """File with no summary blocks yields empty DataFrame with correct columns."""
    from samantha_charts.data.sweep_parser import parse_sweep_summaries

    f = tmp_path / "nosummary.txt"
    f.write_text(_NO_SUMMARY)

    df = parse_sweep_summaries(f)

    assert isinstance(df, pd.DataFrame)
    assert list(df.columns) == _SUMMARY_COLUMNS
    assert len(df) == 0
