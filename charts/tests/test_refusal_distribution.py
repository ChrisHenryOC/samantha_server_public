"""Chart 9: refusal-type distribution horizontal bar chart."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

# ---------------------------------------------------------------------------
# Slice 1: aggregate_refusal_types
# ---------------------------------------------------------------------------


def test_aggregate_counts_known_refusal_outcomes() -> None:
    from samantha_charts.charts.refusal_distribution import aggregate_refusal_types

    traces: list[dict[str, Any]] = [
        {"timestamp": "2026-05-20T10:00:00Z", "metadata": {"outcome": "refused_llm_unavailable"}},
        {"timestamp": "2026-05-20T10:01:00Z", "metadata": {"outcome": "refused_llm_unavailable"}},
        {"timestamp": "2026-05-20T10:02:00Z", "metadata": {"outcome": "refused_phi_boundary"}},
        {"timestamp": "2026-05-20T10:03:00Z", "metadata": {"outcome": "deterministic_pass"}},
    ]

    result = aggregate_refusal_types(traces)

    assert result["refused_llm_unavailable"] == 2
    assert result["refused_phi_boundary"] == 1
    assert result["refused_no_skill_for_state"] == 0


def test_aggregate_ignores_non_refused_outcomes() -> None:
    from samantha_charts.charts.refusal_distribution import aggregate_refusal_types

    traces: list[dict[str, Any]] = [
        {"timestamp": "2026-05-20T10:00:00Z", "metadata": {"outcome": "deterministic_pass"}},
        {"timestamp": "2026-05-20T10:01:00Z", "metadata": {"outcome": "llm_review_pass"}},
        {"timestamp": "2026-05-20T10:02:00Z", "metadata": {"outcome": "query"}},
    ]

    result = aggregate_refusal_types(traces)

    assert sum(result.values()) == 0


def test_aggregate_routes_unknown_refused_to_other() -> None:
    from samantha_charts.charts.refusal_distribution import aggregate_refusal_types

    traces: list[dict[str, Any]] = [
        {"timestamp": "2026-05-20T10:00:00Z", "metadata": {"outcome": "refused_mystery"}},
        {"timestamp": "2026-05-20T10:01:00Z", "metadata": {"outcome": "refused_mystery"}},
    ]

    result = aggregate_refusal_types(traces)

    assert result["other"] == 2
    assert result["refused_llm_unavailable"] == 0


def test_aggregate_returns_all_known_keys_plus_other() -> None:
    from samantha_charts.charts.refusal_distribution import (
        KNOWN_REFUSAL_TYPES,
        aggregate_refusal_types,
    )

    traces: list[dict[str, Any]] = []

    result = aggregate_refusal_types(traces)

    for key in KNOWN_REFUSAL_TYPES:
        assert key in result, f"missing key: {key}"
    assert "other" in result


def test_aggregate_counts_new_refusal_types_not_collapsed_to_other() -> None:
    """The 4 new known types are bucketed directly, not into 'other'."""
    from samantha_charts.charts.refusal_distribution import aggregate_refusal_types

    new_types = [
        "refused_ungrounded_stage_b",
        "refused_uncertain",
        "refused_unparseable",
        "refused_unparseable_response",
    ]
    traces: list[dict[str, Any]] = [{"metadata": {"outcome": t}} for t in new_types]

    result = aggregate_refusal_types(traces)

    for t in new_types:
        assert result[t] == 1, f"{t} should be 1, got {result.get(t)}"
    assert result["other"] == 0


def test_aggregate_ignores_non_string_outcome() -> None:
    """A non-string outcome value is silently ignored."""
    from samantha_charts.charts.refusal_distribution import aggregate_refusal_types

    traces: list[dict[str, Any]] = [
        {"metadata": {"outcome": 99}},
    ]

    result = aggregate_refusal_types(traces)

    assert result["other"] == 0
    assert sum(result.values()) == 0


def test_aggregate_handles_missing_metadata_gracefully() -> None:
    """Traces with no metadata or no outcome key are treated as non-refused (ignored)."""
    from samantha_charts.charts.refusal_distribution import aggregate_refusal_types

    traces: list[dict[str, Any]] = [
        {"timestamp": "2026-05-20T10:00:00Z"},  # no metadata key at all
        {"timestamp": "2026-05-20T10:01:00Z", "metadata": {}},  # metadata but no outcome
        {"timestamp": "2026-05-20T10:02:00Z", "metadata": {"outcome": None}},  # None outcome
    ]

    result = aggregate_refusal_types(traces)

    assert sum(result.values()) == 0


def test_aggregate_mixed_traces() -> None:
    """Mixed: known refusals, unknown refused_, non-refused, missing metadata."""
    from samantha_charts.charts.refusal_distribution import aggregate_refusal_types

    traces: list[dict[str, Any]] = [
        {"timestamp": "2026-05-20T10:00:00Z", "metadata": {"outcome": "refused_llm_unavailable"}},
        {"timestamp": "2026-05-20T10:01:00Z", "metadata": {"outcome": "refused_phi_boundary"}},
        {"timestamp": "2026-05-20T10:02:00Z", "metadata": {"outcome": "refused_mystery"}},
        {"timestamp": "2026-05-20T10:03:00Z", "metadata": {"outcome": "deterministic_pass"}},
        {"timestamp": "2026-05-20T10:04:00Z"},
    ]

    result = aggregate_refusal_types(traces)

    assert result["refused_llm_unavailable"] == 1
    assert result["refused_phi_boundary"] == 1
    assert result["other"] == 1
    assert result["refused_no_skill_for_state"] == 0


# ---------------------------------------------------------------------------
# Slice 2: RefusalDistribution dataclass + load_refusal_distribution
# ---------------------------------------------------------------------------


def test_refusal_distribution_raises_on_incomplete_counts() -> None:
    """Constructing RefusalDistribution with an incomplete counts dict raises ValueError."""
    from samantha_charts.charts.refusal_distribution import RefusalDistribution

    with pytest.raises(ValueError, match="counts"):
        RefusalDistribution(
            counts={"refused_llm_unavailable": 1},
            sample_size=1,
            time_range="x",
        )


def test_refusal_distribution_accepts_complete_counts() -> None:
    """Constructing RefusalDistribution with all KNOWN_REFUSAL_TYPES + other succeeds."""
    from samantha_charts.charts.refusal_distribution import (
        KNOWN_REFUSAL_TYPES,
        RefusalDistribution,
    )

    counts = {k: 0 for k in KNOWN_REFUSAL_TYPES}
    counts["other"] = 0
    dist = RefusalDistribution(counts=counts, sample_size=0, time_range="x")
    assert dist.sample_size == 0


def test_load_refusal_distribution_parses_valid_json(tmp_path: Path) -> None:
    from samantha_charts.charts.refusal_distribution import load_refusal_distribution

    data = {
        "_meta": {
            "sample_size": 66,
            "time_range": "2026-05-20 to 2026-05-27",
            "note": "data generated during testing",
        },
        "refusal_counts": {
            "refused_llm_unavailable": 14,
            "refused_phi_boundary": 3,
            "refused_no_skill_for_state": 9,
            "refused_skill_unavailable": 5,
            "refused_ungrounded_stage_a": 7,
            "refused_ungrounded_stage_b": 6,
            "refused_uncertain": 8,
            "refused_unparseable": 3,
            "refused_unparseable_response": 2,
            "refused_rule_not_in_dispatch": 2,
            "refused_undispatched_rule": 1,
            "refused_internal_error": 4,
            "other": 2,
        },
    }
    p = tmp_path / "chart9.json"
    p.write_text(json.dumps(data))

    result = load_refusal_distribution(p)

    assert result.sample_size == 66
    assert result.time_range == "2026-05-20 to 2026-05-27"
    assert result.counts["refused_llm_unavailable"] == 14
    assert result.counts["other"] == 2


def test_load_refusal_distribution_raises_on_list_refusal_counts(tmp_path: Path) -> None:
    """A JSON with refusal_counts as a list raises ValueError at the loader."""
    from samantha_charts.charts.refusal_distribution import load_refusal_distribution

    data = {
        "_meta": {"sample_size": 10, "time_range": "2026-05-20 to 2026-05-27"},
        "refusal_counts": ["refused_llm_unavailable", "other"],
    }
    p = tmp_path / "bad_list.json"
    p.write_text(json.dumps(data))

    with pytest.raises(ValueError, match="refusal_counts"):
        load_refusal_distribution(p)


def test_load_refusal_distribution_raises_on_missing_refusal_counts(tmp_path: Path) -> None:
    from samantha_charts.charts.refusal_distribution import load_refusal_distribution

    data = {
        "_meta": {"sample_size": 10, "time_range": "2026-05-20 to 2026-05-27"},
    }
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(data))

    with pytest.raises(ValueError, match="refusal_counts"):
        load_refusal_distribution(p)


def test_load_refusal_distribution_raises_on_missing_sample_size(tmp_path: Path) -> None:
    from samantha_charts.charts.refusal_distribution import load_refusal_distribution

    data = {
        "_meta": {"time_range": "2026-05-20 to 2026-05-27"},
        "refusal_counts": {"refused_llm_unavailable": 1, "other": 0},
    }
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(data))

    with pytest.raises(ValueError, match="sample_size"):
        load_refusal_distribution(p)


def test_load_refusal_distribution_raises_on_missing_time_range(tmp_path: Path) -> None:
    from samantha_charts.charts.refusal_distribution import load_refusal_distribution

    data = {
        "_meta": {"sample_size": 10},
        "refusal_counts": {"refused_llm_unavailable": 1, "other": 0},
    }
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(data))

    with pytest.raises(ValueError, match="time_range"):
        load_refusal_distribution(p)


# ---------------------------------------------------------------------------
# Slice 3: render_refusal_distribution
# ---------------------------------------------------------------------------


def test_render_refusal_distribution_produces_png(tmp_path: Path) -> None:
    from samantha_charts.charts.refusal_distribution import (
        RefusalDistribution,
        render_refusal_distribution,
    )

    counts = {
        "refused_llm_unavailable": 14,
        "refused_phi_boundary": 3,
        "refused_no_skill_for_state": 9,
        "refused_skill_unavailable": 5,
        "refused_ungrounded_stage_a": 7,
        "refused_ungrounded_stage_b": 6,
        "refused_uncertain": 8,
        "refused_unparseable": 3,
        "refused_unparseable_response": 2,
        "refused_rule_not_in_dispatch": 2,
        "refused_undispatched_rule": 1,
        "refused_internal_error": 4,
        "other": 2,
    }
    dist = RefusalDistribution(
        counts=counts,
        sample_size=66,
        time_range="2026-05-20 to 2026-05-27",
    )
    out = tmp_path / "chart9.png"
    render_refusal_distribution(dist, out)

    raw = out.read_bytes()
    assert raw.startswith(_PNG_MAGIC)
    assert len(raw) > 1000


def test_render_refusal_distribution_raises_on_all_zero(tmp_path: Path) -> None:
    from samantha_charts.charts.refusal_distribution import (
        RefusalDistribution,
        render_refusal_distribution,
    )

    counts = {
        "refused_llm_unavailable": 0,
        "refused_phi_boundary": 0,
        "refused_no_skill_for_state": 0,
        "refused_skill_unavailable": 0,
        "refused_ungrounded_stage_a": 0,
        "refused_ungrounded_stage_b": 0,
        "refused_uncertain": 0,
        "refused_unparseable": 0,
        "refused_unparseable_response": 0,
        "refused_rule_not_in_dispatch": 0,
        "refused_undispatched_rule": 0,
        "refused_internal_error": 0,
        "other": 0,
    }
    dist = RefusalDistribution(counts=counts, sample_size=0, time_range="n/a")
    with pytest.raises(ValueError, match="all.*zero|empty|no data"):
        render_refusal_distribution(dist, tmp_path / "x.png")


# ---------------------------------------------------------------------------
# Slice 4: render_refusal_distribution_chart — production renderer
# ---------------------------------------------------------------------------


def test_render_refusal_distribution_chart_writes_png(tmp_path: Path) -> None:
    from samantha_charts.charts.refusal_distribution import render_refusal_distribution_chart

    out = tmp_path / "chart9.png"
    render_refusal_distribution_chart(out)

    raw = out.read_bytes()
    assert raw.startswith(_PNG_MAGIC)
    assert len(raw) > 1000
