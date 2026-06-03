"""Chart N2 (GH-309): benchmarks clustered bar chart (CONVEX Slide 24b)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

# ---------------------------------------------------------------------------
# Slice 1: load_benchmarks
# ---------------------------------------------------------------------------


def test_load_benchmarks_parses_valid_json(tmp_path: Path) -> None:
    from samantha_charts.charts.benchmark_bars import load_benchmarks

    data = {
        "_meta": {"incumbent": "ModelA"},
        "models": {
            "ModelA": {
                "local": {"mmlu": 84.0, "composite": 83.7},
                "published": {"mmlu_pro": 80.6},
                "samantha_stable_pct": 100.0,
            },
            "ModelB": {
                "local": {"mmlu": 86.7, "composite": 84.3},
                "published": {"mmlu_pro": 85.3},
                "samantha_stable_pct": 98.66,
            },
        },
    }
    p = tmp_path / "benchmarks.json"
    p.write_text(json.dumps(data))

    result = load_benchmarks(p)

    assert set(result.keys()) == {"ModelA", "ModelB"}
    assert result["ModelA"]["samantha_stable_pct"] == 100.0
    assert result["ModelB"]["local"]["composite"] == 84.3


def test_load_benchmarks_raises_on_missing_models_key(tmp_path: Path) -> None:
    from samantha_charts.charts.benchmark_bars import load_benchmarks

    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"_meta": {}}))

    with pytest.raises(ValueError, match="models"):
        load_benchmarks(p)


def test_load_benchmarks_raises_on_empty_models(tmp_path: Path) -> None:
    from samantha_charts.charts.benchmark_bars import load_benchmarks

    p = tmp_path / "empty.json"
    p.write_text(json.dumps({"models": {}}))

    with pytest.raises(ValueError, match="models"):
        load_benchmarks(p)


# ---------------------------------------------------------------------------
# Slice 2: render_bars -- produces PNG, rejects empty models
# ---------------------------------------------------------------------------


def _minimal_models() -> dict[str, dict]:  # type: ignore[type-arg]
    """Two-model synthetic data for render_bars tests."""
    return {
        "ModelA": {
            "local": {"composite": 85.0},
            "published": {"mmlu_pro": 80.0},
            "samantha_stable_pct": 100.0,
        },
        "ModelB": {
            "local": {"composite": 78.0},
            "published": {"mmlu_pro": 75.0},
            "samantha_stable_pct": 95.0,
        },
    }


def test_render_bars_produces_png(tmp_path: Path) -> None:
    from samantha_charts.charts.benchmark_bars import render_bars

    out = tmp_path / "bars.png"
    render_bars(_minimal_models(), out)

    raw = out.read_bytes()
    assert raw.startswith(_PNG_MAGIC)
    assert len(raw) > 1000


def test_render_bars_raises_on_empty_models(tmp_path: Path) -> None:
    from samantha_charts.charts.benchmark_bars import render_bars

    with pytest.raises(ValueError):
        render_bars({}, tmp_path / "x.png")


def test_render_bars_three_model_data(tmp_path: Path) -> None:
    """Three-model synthetic data also renders without error."""
    from samantha_charts.charts.benchmark_bars import render_bars

    models = {
        "ModelA": {
            "local": {"composite": 85.0},
            "published": {"mmlu_pro": 82.0},
            "samantha_stable_pct": 100.0,
        },
        "ModelB": {
            "local": {"composite": 80.0},
            "published": {"mmlu_pro": 79.0},
            "samantha_stable_pct": 97.0,
        },
        "ModelC": {
            "local": {"composite": 75.0},
            "published": {"mmlu_pro": 70.0},
            "samantha_stable_pct": 94.0,
        },
    }
    out = tmp_path / "bars3.png"
    render_bars(models, out)

    raw = out.read_bytes()
    assert raw.startswith(_PNG_MAGIC)
    assert len(raw) > 1000


# ---------------------------------------------------------------------------
# Slice 3: incumbent highlight path
# ---------------------------------------------------------------------------


def test_render_bars_incumbent_renders(tmp_path: Path) -> None:
    """Passing incumbent= a present model name exercises the green-bar path."""
    from samantha_charts.charts.benchmark_bars import render_bars

    models = _minimal_models()
    out = tmp_path / "incumbent.png"
    render_bars(models, out, incumbent="ModelA")

    raw = out.read_bytes()
    assert raw.startswith(_PNG_MAGIC)
    assert len(raw) > 1000


def test_render_bars_absent_incumbent_no_crash(tmp_path: Path) -> None:
    """When incumbent= does not match any model, rendering still succeeds."""
    from samantha_charts.charts.benchmark_bars import render_bars

    out = tmp_path / "no_incumbent.png"
    render_bars(_minimal_models(), out, incumbent="NonExistentModel")

    assert out.read_bytes().startswith(_PNG_MAGIC)


# ---------------------------------------------------------------------------
# Slice 4: render_benchmark_bars -- production renderer from committed JSON
# ---------------------------------------------------------------------------


def test_render_benchmark_bars_writes_png(tmp_path: Path) -> None:
    from samantha_charts.charts.benchmark_bars import render_benchmark_bars

    out = tmp_path / "chart-n2.png"
    render_benchmark_bars(out)

    raw = out.read_bytes()
    assert raw.startswith(_PNG_MAGIC)
    assert len(raw) > 1000


# ---------------------------------------------------------------------------
# Slice 5: grouped-by-benchmark layout -- 3 x-groups, one bar per model per group
# ---------------------------------------------------------------------------


def _four_model_data() -> dict[str, dict]:  # type: ignore[type-arg]
    """Four-model synthetic data matching the production N2 shape."""
    return {
        "Qwen3-Next-80B-A3B-Instruct-4bit": {
            "local": {"composite": 83.7},
            "published": {"mmlu_pro": 80.6},
            "samantha_stable_pct": 100.0,
        },
        "Qwen3.5-35B-A3B-8bit": {
            "local": {"composite": 84.3},
            "published": {"mmlu_pro": 85.3},
            "samantha_stable_pct": 98.66,
        },
        "gemma-4-26B-A4B-it-MLX-4bit": {
            "local": {"composite": 85.1},
            "published": {"mmlu_pro": 82.6},
            "samantha_stable_pct": 98.66,
        },
        "Qwen2.5-Coder-32B-Instruct-MLX-4bit": {
            "local": {"composite": 76.8},
            "published": {"mmlu_pro": 50.0},
            "samantha_stable_pct": 96.64,
        },
    }


def test_render_bars_uses_three_benchmark_x_groups(tmp_path: Path) -> None:
    """render_bars groups by benchmark on x-axis: exactly 3 x-ticks.

    The approved grouped-by-benchmark layout puts benchmark categories
    (Published/Local/samantha) on the x-axis, with 4 per-model bars in each
    group. The old by-model layout put 4 model names on x. This test pins the
    new layout: 3 x-tick positions.
    """
    import matplotlib
    import matplotlib.pyplot as plt

    matplotlib.use("Agg")

    from samantha_charts.charts.benchmark_bars import render_bars

    captured: list[list[str]] = []

    original_savefig = plt.Figure.savefig

    def _spy_savefig(self: plt.Figure, *args: object, **kwargs: object) -> None:  # type: ignore[override]
        ax = self.axes[0]
        captured.append([t.get_text() for t in ax.get_xticklabels()])
        original_savefig(self, *args, **kwargs)

    plt.Figure.savefig = _spy_savefig  # type: ignore[method-assign]
    try:
        out = tmp_path / "grouped.png"
        render_bars(_four_model_data(), out)
    finally:
        plt.Figure.savefig = original_savefig  # type: ignore[method-assign]

    assert captured, "savefig was not called"
    x_labels = captured[0]
    assert len(x_labels) == 3, (
        f"Expected 3 x-tick groups (benchmark categories), got {len(x_labels)}: {x_labels}"
    )
    assert x_labels == [
        "Published\n(MMLU-Pro)",
        "Local\n(composite)",
        "samantha\n(N=5 stable)",
    ], f"Unexpected x-group label strings: {x_labels}"


# ---------------------------------------------------------------------------
# Slice 6: render_bars -- validation of nested keys (#3)
# ---------------------------------------------------------------------------


def test_render_bars_raises_valueerror_on_missing_mmlu_pro(tmp_path: Path) -> None:
    """render_bars raises ValueError naming the model when published.mmlu_pro absent."""
    from samantha_charts.charts.benchmark_bars import render_bars

    models = {
        "ModelA": {
            "local": {"composite": 83.7},
            "published": {},  # mmlu_pro missing
            "samantha_stable_pct": 100.0,
        },
    }
    with pytest.raises(ValueError, match="ModelA"):
        render_bars(models, tmp_path / "bad.png")


def test_render_bars_raises_valueerror_on_missing_local_composite(tmp_path: Path) -> None:
    """render_bars raises ValueError naming the model when local.composite absent."""
    from samantha_charts.charts.benchmark_bars import render_bars

    models = {
        "ModelB": {
            "local": {},  # composite missing
            "published": {"mmlu_pro": 80.0},
            "samantha_stable_pct": 98.0,
        },
    }
    with pytest.raises(ValueError, match="ModelB"):
        render_bars(models, tmp_path / "bad.png")


def test_render_bars_ylim_is_124_and_yticks_to_100(tmp_path: Path) -> None:
    """render_bars sets ylim top=124 and yticks at [0,20,40,60,80,100]."""
    import matplotlib
    import matplotlib.pyplot as plt

    matplotlib.use("Agg")

    from samantha_charts.charts.benchmark_bars import render_bars

    captured_ylim: list[tuple[float, float]] = []
    captured_yticks: list[list[float]] = []

    original_savefig = plt.Figure.savefig

    def _spy_savefig(self: plt.Figure, *args: object, **kwargs: object) -> None:  # type: ignore[override]
        ax = self.axes[0]
        captured_ylim.append(ax.get_ylim())
        captured_yticks.append(list(ax.get_yticks()))
        original_savefig(self, *args, **kwargs)

    plt.Figure.savefig = _spy_savefig  # type: ignore[method-assign]
    try:
        out = tmp_path / "ylim.png"
        render_bars(_four_model_data(), out)
    finally:
        plt.Figure.savefig = original_savefig  # type: ignore[method-assign]

    assert captured_ylim, "savefig was not called"
    _, top = captured_ylim[0]
    assert top == pytest.approx(124.0), f"Expected ylim top=124, got {top}"
    assert captured_yticks[0] == pytest.approx([0, 20, 40, 60, 80, 100])
