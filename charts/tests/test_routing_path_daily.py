"""Chart 10: daily routing-path 100% stacked bar chart."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

# ---------------------------------------------------------------------------
# Slice 5: aggregate_routing_path_daily
# ---------------------------------------------------------------------------


def test_aggregate_groups_by_day() -> None:
    from samantha_charts.charts.routing_path_daily import aggregate_routing_path_daily

    traces: list[dict[str, Any]] = [
        {
            "timestamp": "2026-05-20T10:00:00Z",
            "metadata": {"routing_path": "deterministic"},
        },
        {
            "timestamp": "2026-05-20T11:00:00Z",
            "metadata": {"routing_path": "llm"},
        },
        {
            "timestamp": "2026-05-21T09:00:00Z",
            "metadata": {"routing_path": "deterministic"},
        },
    ]

    result = aggregate_routing_path_daily(traces)

    assert result["2026-05-20"]["deterministic"] == 1
    assert result["2026-05-20"]["llm"] == 1
    assert result["2026-05-21"]["deterministic"] == 1
    assert result["2026-05-21"]["llm"] == 0


def test_aggregate_raises_on_unknown_routing_path() -> None:
    from samantha_charts.charts.routing_path_daily import aggregate_routing_path_daily

    traces: list[dict[str, Any]] = [
        {
            "timestamp": "2026-05-20T10:00:00Z",
            "metadata": {"routing_path": "unknown_path"},
        },
    ]

    with pytest.raises(ValueError, match="routing_path"):
        aggregate_routing_path_daily(traces)


def test_aggregate_day_with_only_deterministic_has_llm_zero() -> None:
    """A day with only deterministic traces still has llm=0 in the result."""
    from samantha_charts.charts.routing_path_daily import aggregate_routing_path_daily

    traces: list[dict[str, Any]] = [
        {
            "timestamp": "2026-05-20T10:00:00Z",
            "metadata": {"routing_path": "deterministic"},
        },
        {
            "timestamp": "2026-05-20T11:00:00Z",
            "metadata": {"routing_path": "deterministic"},
        },
    ]

    result = aggregate_routing_path_daily(traces)

    assert result["2026-05-20"]["deterministic"] == 2
    assert result["2026-05-20"]["llm"] == 0


def test_aggregate_day_with_only_llm_has_deterministic_zero() -> None:
    from samantha_charts.charts.routing_path_daily import aggregate_routing_path_daily

    traces: list[dict[str, Any]] = [
        {
            "timestamp": "2026-05-20T10:00:00Z",
            "metadata": {"routing_path": "llm"},
        },
    ]

    result = aggregate_routing_path_daily(traces)

    assert result["2026-05-20"]["llm"] == 1
    assert result["2026-05-20"]["deterministic"] == 0


def test_aggregate_raises_with_message_on_missing_timestamp() -> None:
    """A trace with a valid routing_path but no 'timestamp' key raises a named ValueError."""
    from samantha_charts.charts.routing_path_daily import aggregate_routing_path_daily

    traces: list[dict[str, Any]] = [
        {
            "metadata": {"routing_path": "deterministic"},
            # no 'timestamp' key
        },
    ]

    with pytest.raises(ValueError, match="timestamp"):
        aggregate_routing_path_daily(traces)


def test_aggregate_skips_trace_with_empty_metadata() -> None:
    """A trace with metadata={} (no routing_path key) is silently skipped."""
    from samantha_charts.charts.routing_path_daily import aggregate_routing_path_daily

    traces: list[dict[str, Any]] = [
        {
            "timestamp": "2026-05-20T10:00:00Z",
            "metadata": {},
        },
    ]

    result = aggregate_routing_path_daily(traces)

    assert result == {}


def test_aggregate_skips_trace_with_no_metadata_key() -> None:
    """A trace with no 'metadata' key at all is silently skipped."""
    from samantha_charts.charts.routing_path_daily import aggregate_routing_path_daily

    traces: list[dict[str, Any]] = [
        {
            "timestamp": "2026-05-20T10:00:00Z",
        },
    ]

    result = aggregate_routing_path_daily(traces)

    assert result == {}


def test_aggregate_still_raises_on_non_none_unknown_routing_path() -> None:
    """A trace with a non-None routing_path that isn't valid still raises ValueError."""
    from samantha_charts.charts.routing_path_daily import aggregate_routing_path_daily

    traces: list[dict[str, Any]] = [
        {
            "timestamp": "2026-05-20T10:00:00Z",
            "metadata": {"routing_path": "unknown_path"},
        },
    ]

    with pytest.raises(ValueError, match="routing_path"):
        aggregate_routing_path_daily(traces)


def test_aggregate_collapsed_days_no_gaps() -> None:
    """Days with no traces are not included in the result (collapsed)."""
    from samantha_charts.charts.routing_path_daily import aggregate_routing_path_daily

    traces: list[dict[str, Any]] = [
        {
            "timestamp": "2026-05-20T10:00:00Z",
            "metadata": {"routing_path": "deterministic"},
        },
        # 2026-05-21 deliberately absent
        {
            "timestamp": "2026-05-22T10:00:00Z",
            "metadata": {"routing_path": "llm"},
        },
    ]

    result = aggregate_routing_path_daily(traces)

    assert "2026-05-20" in result
    assert "2026-05-21" not in result
    assert "2026-05-22" in result


# ---------------------------------------------------------------------------
# Slice 6: RoutingPathDaily dataclass + load_routing_path_daily
# ---------------------------------------------------------------------------


def test_load_routing_path_daily_parses_valid_json(tmp_path: Path) -> None:
    from samantha_charts.charts.routing_path_daily import load_routing_path_daily

    data = {
        "_meta": {
            "sample_size": 2516,
            "time_range": "2026-05-20 to 2026-05-27 (missing days collapsed)",
            "note": "data generated during testing; missing days collapsed",
        },
        "days": [
            {"date": "2026-05-20", "deterministic": 380, "llm": 20},
            {"date": "2026-05-21", "deterministic": 412, "llm": 18},
        ],
    }
    p = tmp_path / "chart10.json"
    p.write_text(json.dumps(data))

    result = load_routing_path_daily(p)

    assert result.sample_size == 2516
    assert result.time_range == "2026-05-20 to 2026-05-27 (missing days collapsed)"
    assert len(result.days) == 2
    assert result.days[0].date == "2026-05-20"
    assert result.days[0].deterministic == 380
    assert result.days[0].llm == 20


def test_load_routing_path_daily_raises_on_missing_days(tmp_path: Path) -> None:
    from samantha_charts.charts.routing_path_daily import load_routing_path_daily

    data = {
        "_meta": {"sample_size": 100, "time_range": "2026-05-20 to 2026-05-27"},
    }
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(data))

    with pytest.raises(ValueError, match="days"):
        load_routing_path_daily(p)


def test_load_routing_path_daily_raises_on_empty_days(tmp_path: Path) -> None:
    from samantha_charts.charts.routing_path_daily import load_routing_path_daily

    data = {
        "_meta": {"sample_size": 0, "time_range": "n/a"},
        "days": [],
    }
    p = tmp_path / "empty.json"
    p.write_text(json.dumps(data))

    with pytest.raises(ValueError, match="days"):
        load_routing_path_daily(p)


def test_load_routing_path_daily_raises_on_missing_sample_size(tmp_path: Path) -> None:
    from samantha_charts.charts.routing_path_daily import load_routing_path_daily

    data = {
        "_meta": {"time_range": "2026-05-20 to 2026-05-27"},
        "days": [{"date": "2026-05-20", "deterministic": 380, "llm": 20}],
    }
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(data))

    with pytest.raises(ValueError, match="sample_size"):
        load_routing_path_daily(p)


def test_load_routing_path_daily_raises_on_missing_time_range(tmp_path: Path) -> None:
    from samantha_charts.charts.routing_path_daily import load_routing_path_daily

    data = {
        "_meta": {"sample_size": 100},
        "days": [{"date": "2026-05-20", "deterministic": 380, "llm": 20}],
    }
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(data))

    with pytest.raises(ValueError, match="time_range"):
        load_routing_path_daily(p)


# ---------------------------------------------------------------------------
# Slice 7: _normalize helper + render_routing_path_daily
# ---------------------------------------------------------------------------


def test_normalize_both_zero_returns_zero_pair() -> None:
    from samantha_charts.charts.routing_path_daily import _normalize

    assert _normalize(0, 0) == (0.0, 0.0)


def test_normalize_fractions_sum_to_one() -> None:
    from samantha_charts.charts.routing_path_daily import _normalize

    det_frac, llm_frac = _normalize(380, 20)

    assert det_frac + llm_frac == pytest.approx(1.0)
    assert det_frac == pytest.approx(380 / 400)
    assert llm_frac == pytest.approx(20 / 400)


def test_normalize_all_deterministic() -> None:
    from samantha_charts.charts.routing_path_daily import _normalize

    det_frac, llm_frac = _normalize(100, 0)

    assert det_frac == pytest.approx(1.0)
    assert llm_frac == pytest.approx(0.0)


def test_normalize_all_llm() -> None:
    from samantha_charts.charts.routing_path_daily import _normalize

    det_frac, llm_frac = _normalize(0, 50)

    assert det_frac == pytest.approx(0.0)
    assert llm_frac == pytest.approx(1.0)


def test_render_routing_path_daily_produces_png(tmp_path: Path) -> None:
    from samantha_charts.charts.routing_path_daily import (
        DayMix,
        RoutingPathDaily,
        render_routing_path_daily,
    )

    days = [
        DayMix(date="2026-05-20", deterministic=380, llm=20),
        DayMix(date="2026-05-21", deterministic=412, llm=18),
        DayMix(date="2026-05-22", deterministic=395, llm=25),
        DayMix(date="2026-05-25", deterministic=401, llm=16),
        DayMix(date="2026-05-26", deterministic=388, llm=22),
        DayMix(date="2026-05-27", deterministic=420, llm=19),
    ]
    data = RoutingPathDaily(
        days=days,
        sample_size=2516,
        time_range="2026-05-20 to 2026-05-27 (missing days collapsed)",
    )
    out = tmp_path / "chart10.png"
    render_routing_path_daily(data, out)

    raw = out.read_bytes()
    assert raw.startswith(_PNG_MAGIC)
    assert len(raw) > 1000


def test_render_routing_path_daily_raises_on_empty_days(tmp_path: Path) -> None:
    from samantha_charts.charts.routing_path_daily import (
        RoutingPathDaily,
        render_routing_path_daily,
    )

    data = RoutingPathDaily(days=[], sample_size=0, time_range="n/a")
    with pytest.raises(ValueError, match="empty"):
        render_routing_path_daily(data, tmp_path / "x.png")


# ---------------------------------------------------------------------------
# Slice 8: render_routing_path_daily_chart — production renderer
# ---------------------------------------------------------------------------


def test_render_routing_path_daily_chart_writes_png(tmp_path: Path) -> None:
    from samantha_charts.charts.routing_path_daily import render_routing_path_daily_chart

    out = tmp_path / "chart10.png"
    render_routing_path_daily_chart(out)

    raw = out.read_bytes()
    assert raw.startswith(_PNG_MAGIC)
    assert len(raw) > 1000
