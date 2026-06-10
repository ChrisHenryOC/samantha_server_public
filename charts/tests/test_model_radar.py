"""Chart 7: model-selection radar (Option B: 3 axes)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

# ---------------------------------------------------------------------------
# Slice 1: load_radar_data
# ---------------------------------------------------------------------------


def test_load_radar_data_parses_valid_json(tmp_path: Path) -> None:
    from samantha_charts.charts.model_radar import load_radar_data

    data = {
        "_meta": {"axes": ["accuracy", "robustness", "speed"], "incumbent": "ModelA"},
        "models": {
            "ModelA": {
                "accuracy": 100.0,
                "robustness": 95.0,
                "worst_category": "none",
                "latency_p50_s": 2.5,
            },
            "ModelB": {
                "accuracy": 98.0,
                "robustness": 85.0,
                "worst_category": "query",
                "latency_p50_s": 8.0,
            },
        },
    }
    p = tmp_path / "radar.json"
    p.write_text(json.dumps(data))

    result = load_radar_data(p)

    assert set(result.keys()) == {"ModelA", "ModelB"}
    assert result["ModelA"]["accuracy"] == 100.0
    assert result["ModelA"]["robustness"] == 95.0
    assert result["ModelA"]["latency_p50_s"] == 2.5
    assert result["ModelB"]["latency_p50_s"] == 8.0


def test_load_radar_data_raises_on_missing_models_key(tmp_path: Path) -> None:
    from samantha_charts.charts.model_radar import load_radar_data

    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"_meta": {}}))

    with pytest.raises(ValueError, match="models"):
        load_radar_data(p)


def test_load_radar_data_raises_on_empty_models(tmp_path: Path) -> None:
    from samantha_charts.charts.model_radar import load_radar_data

    p = tmp_path / "empty.json"
    p.write_text(json.dumps({"models": {}}))

    with pytest.raises(ValueError, match="models"):
        load_radar_data(p)


# ---------------------------------------------------------------------------
# Slice 2: _normalize_axes
# ---------------------------------------------------------------------------


def test_normalize_axes_higher_better_axes(tmp_path: Path) -> None:
    """accuracy and robustness: higher raw value -> higher normalized value.

    Floor mapping: worst model -> 0.1, best model -> 1.0.
    """
    from samantha_charts.charts.model_radar import _normalize_axes

    models = {
        "A": {"accuracy": 100.0, "robustness": 90.0, "latency_p50_s": 5.0},
        "B": {"accuracy": 80.0, "robustness": 70.0, "latency_p50_s": 5.0},
    }

    result = _normalize_axes(models)

    # accuracy: A=100 (best) -> 1.0, B=80 (worst) -> 0.1
    assert result["A"]["accuracy"] == pytest.approx(1.0)
    assert result["B"]["accuracy"] == pytest.approx(0.1)
    # robustness: A=90 (best) -> 1.0, B=70 (worst) -> 0.1
    assert result["A"]["robustness"] == pytest.approx(1.0)
    assert result["B"]["robustness"] == pytest.approx(0.1)


def test_normalize_axes_speed_inversion(tmp_path: Path) -> None:
    """speed (latency): lowest latency -> 1.0 (fastest = farther from center).

    Floor mapping: slowest model -> 0.1, fastest model -> 1.0.
    """
    from samantha_charts.charts.model_radar import _normalize_axes

    models = {
        "Fast": {"accuracy": 100.0, "robustness": 100.0, "latency_p50_s": 2.0},
        "Slow": {"accuracy": 100.0, "robustness": 100.0, "latency_p50_s": 10.0},
    }

    result = _normalize_axes(models)

    # speed: Fast latency=2 (best) -> 1.0
    assert result["Fast"]["speed"] == pytest.approx(1.0)
    # speed: Slow latency=10 (worst) -> 0.1
    assert result["Slow"]["speed"] == pytest.approx(0.1)


def test_normalize_axes_three_model_midpoint(tmp_path: Path) -> None:
    """Middle model gets 0.55 normalized on a 3-model range (floor mapping midpoint)."""
    from samantha_charts.charts.model_radar import _normalize_axes

    models = {
        "A": {"accuracy": 100.0, "robustness": 100.0, "latency_p50_s": 2.0},
        "B": {"accuracy": 90.0, "robustness": 90.0, "latency_p50_s": 6.0},
        "C": {"accuracy": 80.0, "robustness": 80.0, "latency_p50_s": 10.0},
    }

    result = _normalize_axes(models)

    # B is the midpoint: 0.1 + 0.9 * 0.5 = 0.55
    assert result["B"]["accuracy"] == pytest.approx(0.55)
    assert result["B"]["robustness"] == pytest.approx(0.55)
    # speed: B latency=6, raw midpoint = (10-6)/(10-2) = 0.5 -> 0.55
    assert result["B"]["speed"] == pytest.approx(0.55)


def test_normalize_axes_degenerate_axis_returns_half(tmp_path: Path) -> None:
    """When all models share the same value (max==min), return 0.5 for all."""
    from samantha_charts.charts.model_radar import _normalize_axes

    models = {
        "A": {"accuracy": 100.0, "robustness": 100.0, "latency_p50_s": 5.0},
        "B": {"accuracy": 100.0, "robustness": 100.0, "latency_p50_s": 5.0},
    }

    result = _normalize_axes(models)

    # accuracy: all same -> 0.5
    assert result["A"]["accuracy"] == pytest.approx(0.5)
    assert result["B"]["accuracy"] == pytest.approx(0.5)
    # robustness: all same -> 0.5
    assert result["A"]["robustness"] == pytest.approx(0.5)
    assert result["B"]["robustness"] == pytest.approx(0.5)
    # speed: all same latency -> 0.5
    assert result["A"]["speed"] == pytest.approx(0.5)
    assert result["B"]["speed"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Slice 3: render_radar -- produces PNG, rejects empty
# ---------------------------------------------------------------------------


def test_render_radar_produces_png(tmp_path: Path) -> None:
    from samantha_charts.charts.model_radar import render_radar

    models = {
        "ModelA": {"accuracy": 100.0, "robustness": 95.0, "latency_p50_s": 2.5},
        "ModelB": {"accuracy": 98.0, "robustness": 85.0, "latency_p50_s": 8.0},
        "ModelC": {"accuracy": 96.0, "robustness": 80.0, "latency_p50_s": 6.0},
    }
    out = tmp_path / "radar.png"
    render_radar(models, out)

    raw = out.read_bytes()
    assert raw.startswith(_PNG_MAGIC)
    assert len(raw) > 1000


def test_render_radar_raises_on_empty_models(tmp_path: Path) -> None:
    from samantha_charts.charts.model_radar import render_radar

    with pytest.raises(ValueError):
        render_radar({}, tmp_path / "x.png")


# ---------------------------------------------------------------------------
# Slice 4: incumbent highlighting path
# ---------------------------------------------------------------------------


def test_render_radar_incumbent_renders(tmp_path: Path) -> None:
    """Passing incumbent= a present model name exercises the green polygon path."""
    from samantha_charts.charts.model_radar import render_radar

    models = {
        "Qwen3-Next-80B-A3B-Instruct-4bit": {
            "accuracy": 100.0,
            "robustness": 100.0,
            "latency_p50_s": 2.9,
        },
        "OtherModel": {"accuracy": 96.0, "robustness": 83.0, "latency_p50_s": 6.4},
    }
    out = tmp_path / "incumbent.png"
    render_radar(models, out, incumbent="Qwen3-Next-80B-A3B-Instruct-4bit")

    raw = out.read_bytes()
    assert raw.startswith(_PNG_MAGIC)
    assert len(raw) > 1000


def test_render_radar_absent_incumbent_no_crash(tmp_path: Path) -> None:
    """When incumbent= does not match any model, rendering still succeeds."""
    from samantha_charts.charts.model_radar import render_radar

    models = {
        "ModelA": {"accuracy": 100.0, "robustness": 95.0, "latency_p50_s": 2.5},
        "ModelB": {"accuracy": 98.0, "robustness": 85.0, "latency_p50_s": 8.0},
    }
    out = tmp_path / "no_incumbent.png"
    render_radar(models, out, incumbent="NonExistentModel")

    assert out.read_bytes().startswith(_PNG_MAGIC)


# ---------------------------------------------------------------------------
# Slice 5: render_model_radar -- production renderer
# ---------------------------------------------------------------------------


def test_render_model_radar_writes_png(tmp_path: Path) -> None:
    from samantha_charts.charts.model_radar import render_model_radar

    out = tmp_path / "chart7.png"
    render_model_radar(out)

    raw = out.read_bytes()
    assert raw.startswith(_PNG_MAGIC)
    assert len(raw) > 1000


# ---------------------------------------------------------------------------
# Slice 6: color distinctness -- no two non-incumbent models share a color
# ---------------------------------------------------------------------------


def test_non_incumbent_colors_are_all_distinct() -> None:
    """The three non-incumbent colors must all be distinct hues."""
    from samantha_charts import style
    from samantha_charts.charts import model_radar

    colors = model_radar._NON_INCUMBENT_COLORS
    assert len(colors) == 3, "Expected exactly 3 non-incumbent colors"
    assert len(set(colors)) == 3, "Non-incumbent colors must all be distinct"
    # BLUE_MEDIUM must not appear (it is too close to BLUE_PRIMARY)
    assert style.BLUE_MEDIUM not in colors, "BLUE_MEDIUM is visually too close to BLUE_PRIMARY"


def test_non_incumbent_colors_include_required_hues() -> None:
    """The palette must include BLUE_PRIMARY, ORANGE_ACCENT, and RED_ACCENT."""
    from samantha_charts import style
    from samantha_charts.charts import model_radar

    colors = set(model_radar._NON_INCUMBENT_COLORS)
    assert style.BLUE_PRIMARY in colors
    assert style.ORANGE_ACCENT in colors
    assert style.RED_ACCENT in colors


# ---------------------------------------------------------------------------
# Slice 7: normalization floor -- worst model never collapses to center
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Slice 8: _normalize_axes -- validation of required keys (#3)
# ---------------------------------------------------------------------------


def test_normalize_axes_raises_valueerror_on_missing_accuracy() -> None:
    """_normalize_axes raises ValueError naming the model when accuracy absent."""
    from samantha_charts.charts.model_radar import _normalize_axes

    models = {
        "ModelA": {"robustness": 90.0, "latency_p50_s": 2.5},  # accuracy missing
    }
    with pytest.raises(ValueError, match="ModelA"):
        _normalize_axes(models)


def test_normalize_axes_raises_valueerror_on_missing_robustness() -> None:
    """_normalize_axes raises ValueError naming the model when robustness absent."""
    from samantha_charts.charts.model_radar import _normalize_axes

    models = {
        "ModelB": {"accuracy": 98.0, "latency_p50_s": 5.0},  # robustness missing
    }
    with pytest.raises(ValueError, match="ModelB"):
        _normalize_axes(models)


def test_normalize_axes_raises_valueerror_on_missing_latency() -> None:
    """_normalize_axes raises ValueError naming the model when latency_p50_s absent."""
    from samantha_charts.charts.model_radar import _normalize_axes

    models = {
        "ModelC": {"accuracy": 98.0, "robustness": 90.0},  # latency_p50_s missing
    }
    with pytest.raises(ValueError, match="ModelC"):
        _normalize_axes(models)


def test_normalize_axes_worst_model_has_floor() -> None:
    """Worst model on any axis must be >= 0.1 (floor), not 0.0."""
    from samantha_charts.charts.model_radar import _normalize_axes

    models = {
        "Best": {"accuracy": 100.0, "robustness": 100.0, "latency_p50_s": 1.0},
        "Worst": {"accuracy": 50.0, "robustness": 40.0, "latency_p50_s": 20.0},
    }

    result = _normalize_axes(models)

    assert result["Worst"]["accuracy"] >= 0.1
    assert result["Worst"]["robustness"] >= 0.1
    assert result["Worst"]["speed"] >= 0.1
    # Best model should still reach 1.0
    assert result["Best"]["accuracy"] == pytest.approx(1.0)
    assert result["Best"]["robustness"] == pytest.approx(1.0)
    assert result["Best"]["speed"] == pytest.approx(1.0)
