"""Chart 14 (GH-312): accuracy evolution across milestones."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

# ---------------------------------------------------------------------------
# Slice 11: EvolutionPoint dataclass + load_accuracy_evolution
# ---------------------------------------------------------------------------


def _make_valid_json(points: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if points is None:
        points = [
            {
                "date": "2026-01-01",
                "label": "Baseline",
                "accuracy_pct": 95.0,
                "passed": 142,
                "total": 149,
                "sweep_file": "baseline.txt",
            },
            {
                "date": "2026-02-01",
                "label": "Improvement",
                "accuracy_pct": 100.0,
                "passed": 149,
                "total": 149,
                "sweep_file": "improved.txt",
            },
        ]
    return {
        "_meta": {
            "metric": "stable accuracy (N=5)",
            "source": "results/baselines/*.txt",
            "note": "test data",
        },
        "points": points,
    }


def test_load_accuracy_evolution_roundtrip(tmp_path: Path) -> None:
    from samantha_charts.charts.accuracy_evolution import EvolutionPoint, load_accuracy_evolution

    data = _make_valid_json()
    p = tmp_path / "chart14.json"
    p.write_text(json.dumps(data))

    result = load_accuracy_evolution(p)

    assert len(result) == 2
    assert all(isinstance(pt, EvolutionPoint) for pt in result)
    assert result[0].date == "2026-01-01"
    assert result[0].label == "Baseline"
    assert result[0].accuracy_pct == pytest.approx(95.0)
    assert result[0].passed == 142
    assert result[0].total == 149
    assert result[0].sweep_file == "baseline.txt"
    assert result[1].date == "2026-02-01"
    assert result[1].accuracy_pct == pytest.approx(100.0)


def test_load_accuracy_evolution_preserves_file_order(tmp_path: Path) -> None:
    from samantha_charts.charts.accuracy_evolution import load_accuracy_evolution

    data = _make_valid_json(
        points=[
            {
                "date": "2026-03-01",
                "label": "C",
                "accuracy_pct": 99.0,
                "passed": 148,
                "total": 149,
                "sweep_file": "c.txt",
            },
            {
                "date": "2026-01-01",
                "label": "A",
                "accuracy_pct": 95.0,
                "passed": 142,
                "total": 149,
                "sweep_file": "a.txt",
            },
            {
                "date": "2026-02-01",
                "label": "B",
                "accuracy_pct": 97.0,
                "passed": 145,
                "total": 149,
                "sweep_file": "b.txt",
            },
        ]
    )
    p = tmp_path / "chart14_order.json"
    p.write_text(json.dumps(data))

    result = load_accuracy_evolution(p)

    # File order preserved: C, A, B
    assert [pt.label for pt in result] == ["C", "A", "B"]


def test_load_accuracy_evolution_raises_on_missing_points(tmp_path: Path) -> None:
    from samantha_charts.charts.accuracy_evolution import load_accuracy_evolution

    data: dict[str, Any] = {"_meta": {"metric": "test"}}
    p = tmp_path / "missing.json"
    p.write_text(json.dumps(data))

    with pytest.raises(ValueError, match="points"):
        load_accuracy_evolution(p)


def test_load_accuracy_evolution_raises_on_empty_points(tmp_path: Path) -> None:
    from samantha_charts.charts.accuracy_evolution import load_accuracy_evolution

    data = _make_valid_json(points=[])
    p = tmp_path / "empty.json"
    p.write_text(json.dumps(data))

    with pytest.raises(ValueError, match="points"):
        load_accuracy_evolution(p)


# ---------------------------------------------------------------------------
# Slice 12: render_accuracy_evolution -- produces PNG, rejects empty points
# ---------------------------------------------------------------------------


def _make_points(count: int = 2) -> list[Any]:
    from samantha_charts.charts.accuracy_evolution import EvolutionPoint

    pts = [
        EvolutionPoint(
            date="2026-01-01",
            label="Baseline",
            accuracy_pct=95.0,
            passed=142,
            total=149,
            sweep_file="baseline.txt",
        ),
        EvolutionPoint(
            date="2026-02-01",
            label="Improvement",
            accuracy_pct=100.0,
            passed=149,
            total=149,
            sweep_file="improved.txt",
        ),
    ]
    return pts[:count]


def test_render_accuracy_evolution_produces_png(tmp_path: Path) -> None:
    from samantha_charts.charts.accuracy_evolution import render_accuracy_evolution

    points = _make_points(2)
    out = tmp_path / "chart14.png"
    render_accuracy_evolution(points, out)

    raw = out.read_bytes()
    assert raw.startswith(_PNG_MAGIC)
    assert len(raw) > 1000


def test_render_accuracy_evolution_raises_on_empty_points(tmp_path: Path) -> None:
    from samantha_charts.charts.accuracy_evolution import render_accuracy_evolution

    with pytest.raises(ValueError):
        render_accuracy_evolution([], tmp_path / "x.png")


def test_render_accuracy_evolution_single_point(tmp_path: Path) -> None:
    from samantha_charts.charts.accuracy_evolution import render_accuracy_evolution

    points = _make_points(1)
    out = tmp_path / "single.png"
    render_accuracy_evolution(points, out)

    raw = out.read_bytes()
    assert raw.startswith(_PNG_MAGIC)
    assert len(raw) > 1000


# ---------------------------------------------------------------------------
# Slice 13: render_accuracy_evolution_chart -- production renderer
# ---------------------------------------------------------------------------


def test_render_accuracy_evolution_chart_writes_png(tmp_path: Path) -> None:
    from samantha_charts.charts.accuracy_evolution import render_accuracy_evolution_chart

    out = tmp_path / "chart14.png"
    render_accuracy_evolution_chart(out)

    raw = out.read_bytes()
    assert raw.startswith(_PNG_MAGIC)
    assert len(raw) > 1000
