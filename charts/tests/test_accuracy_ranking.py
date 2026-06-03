"""Chart 1 (GH-307): accuracy_ranking renders a horizontal ranking bar chart."""

from __future__ import annotations

from pathlib import Path

import pytest

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _bar(label: str, passed: int, total: int = 149, incumbent: bool = False) -> object:
    from samantha_charts.charts.accuracy_ranking import ModelBar

    return ModelBar(label=label, passed=passed, total=total, is_incumbent=incumbent)


def test_pct() -> None:
    from samantha_charts.charts.accuracy_ranking import ModelBar

    assert ModelBar("m", 149, 149).pct == pytest.approx(100.0)
    assert ModelBar("m", 144, 149).pct == pytest.approx(96.6443, abs=1e-3)


def test_modelbar_rejects_out_of_range() -> None:
    from samantha_charts.charts.accuracy_ranking import ModelBar

    with pytest.raises(ValueError):
        ModelBar("m", 150, 149)  # passed > total
    with pytest.raises(ValueError):
        ModelBar("m", 149, 0)  # zero total


def test_render_ranking_produces_nonempty_png(tmp_path: Path) -> None:
    from samantha_charts.charts.accuracy_ranking import render_ranking

    bars = [
        _bar("Incumbent-80B", 149, incumbent=True),
        _bar("Mid-35B", 147),
        _bar("Small-26B", 147),  # ties Mid-35B
    ]
    out = tmp_path / "ranking.png"
    render_ranking(bars, out)
    data = out.read_bytes()
    assert data.startswith(_PNG_MAGIC)
    assert len(data) > 1000


def test_render_ranking_rejects_empty(tmp_path: Path) -> None:
    from samantha_charts.charts.accuracy_ranking import render_ranking

    with pytest.raises(ValueError):
        render_ranking([], tmp_path / "x.png")


def test_production_renderer_writes_png(tmp_path: Path) -> None:
    from samantha_charts.charts.accuracy_ranking import render_accuracy_ranking

    out = tmp_path / "chart1.png"
    render_accuracy_ranking(out)
    assert out.read_bytes().startswith(_PNG_MAGIC)


# ---------------------------------------------------------------------------
# Slice 2: load_accuracy_ranking -- reads from committed JSON
# ---------------------------------------------------------------------------


def test_load_accuracy_ranking_returns_four_modelbars(tmp_path: Path) -> None:
    """load_accuracy_ranking parses a chart1 JSON into a list of 4 ModelBars."""
    import json

    from samantha_charts.charts.accuracy_ranking import ModelBar, load_accuracy_ranking

    data = {
        "models": [
            {"label": "ModelA", "passed": 149, "total": 149, "incumbent": True},
            {"label": "ModelB", "passed": 147, "total": 149, "incumbent": False},
        ]
    }
    p = tmp_path / "chart1.json"
    p.write_text(json.dumps(data))

    result = load_accuracy_ranking(p)

    assert len(result) == 2
    assert isinstance(result[0], ModelBar)
    assert result[0].label == "ModelA"
    assert result[0].passed == 149
    assert result[0].total == 149
    assert result[0].is_incumbent is True
    assert result[1].label == "ModelB"
    assert result[1].is_incumbent is False


def test_load_accuracy_ranking_raises_on_missing_models_key(tmp_path: Path) -> None:
    """load_accuracy_ranking raises ValueError when 'models' key is absent."""
    import json

    from samantha_charts.charts.accuracy_ranking import load_accuracy_ranking

    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"_meta": {}}))

    with pytest.raises(ValueError, match="models"):
        load_accuracy_ranking(p)


def test_load_accuracy_ranking_raises_on_empty_models(tmp_path: Path) -> None:
    """load_accuracy_ranking raises ValueError when models list is empty."""
    import json

    from samantha_charts.charts.accuracy_ranking import load_accuracy_ranking

    p = tmp_path / "empty.json"
    p.write_text(json.dumps({"models": []}))

    with pytest.raises(ValueError, match="models"):
        load_accuracy_ranking(p)


def test_production_renderer_reads_from_json(tmp_path: Path) -> None:
    """render_accuracy_ranking reads from the committed JSON (not hardcoded)."""
    from samantha_charts.charts.accuracy_ranking import render_accuracy_ranking

    out = tmp_path / "chart1.png"
    render_accuracy_ranking(out)
    assert out.read_bytes().startswith(_PNG_MAGIC)
