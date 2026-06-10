"""Tests for Chart N3: accuracy-evolution line chart."""

from __future__ import annotations

from pathlib import Path

import pytest

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


# ---------------------------------------------------------------------------
# Dataclass property / validation tests
# ---------------------------------------------------------------------------


def test_evolution_point_stores_values() -> None:
    """EvolutionPoint stores its fields correctly."""
    from samantha_charts.charts.scaffolding_progression import EvolutionPoint

    p = EvolutionPoint("Prompt\ntuning", 86.0, "Llama 70B\n+ few-shot")
    assert p.stage == "Prompt\ntuning"
    assert p.pct == 86.0
    assert p.model_label == "Llama 70B\n+ few-shot"
    assert p.is_endpoint is False


def test_evolution_point_endpoint_flag() -> None:
    """is_endpoint is stored when set."""
    from samantha_charts.charts.scaffolding_progression import EvolutionPoint

    p = EvolutionPoint("end", 100.0, "model", is_endpoint=True)
    assert p.is_endpoint is True


def test_evolution_point_boundary_pcts_valid() -> None:
    """pct=0 and pct=100 are valid boundaries."""
    from samantha_charts.charts.scaffolding_progression import EvolutionPoint

    assert EvolutionPoint("a", 0.0, "m").pct == 0.0
    assert EvolutionPoint("b", 100.0, "m").pct == 100.0


def test_evolution_point_rejects_negative_pct() -> None:
    """pct < 0 raises ValueError."""
    from samantha_charts.charts.scaffolding_progression import EvolutionPoint

    with pytest.raises(ValueError, match="pct"):
        EvolutionPoint("bad", -1.0, "m")


def test_evolution_point_rejects_pct_over_100() -> None:
    """pct > 100 raises ValueError."""
    from samantha_charts.charts.scaffolding_progression import EvolutionPoint

    with pytest.raises(ValueError, match="pct"):
        EvolutionPoint("bad", 100.1, "m")


def test_evolution_point_is_frozen() -> None:
    """EvolutionPoint is immutable (frozen dataclass)."""
    from samantha_charts.charts.scaffolding_progression import EvolutionPoint

    p = EvolutionPoint("x", 50.0, "m")
    with pytest.raises((AttributeError, TypeError)):
        p.pct = 99.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# render_evolution: produces a non-empty PNG
# ---------------------------------------------------------------------------


def test_render_evolution_produces_nonempty_png(tmp_path: Path) -> None:
    """render_evolution writes a valid PNG for a small input list."""
    from samantha_charts.charts.scaffolding_progression import (
        EvolutionPoint,
        render_evolution,
    )

    points = [
        EvolutionPoint("A", 62.0, "m1"),
        EvolutionPoint("B", 99.0, "m2"),
        EvolutionPoint("C", 100.0, "m3", is_endpoint=True),
    ]
    out = tmp_path / "evolution.png"
    render_evolution(points, out)

    assert out.exists(), "output file was not created"
    data = out.read_bytes()
    assert data[:8] == _PNG_MAGIC, "file does not start with PNG magic"
    assert len(data) > 1000, "PNG suspiciously small"


def test_render_evolution_without_cloud_baseline(tmp_path: Path) -> None:
    """cloud_baseline=None omits the reference line and still renders."""
    from samantha_charts.charts.scaffolding_progression import (
        EvolutionPoint,
        render_evolution,
    )

    out = tmp_path / "no_cloud.png"
    render_evolution([EvolutionPoint("A", 80.0, "m")], out, cloud_baseline=None)
    assert out.read_bytes()[:8] == _PNG_MAGIC


def test_render_evolution_rejects_empty_list(tmp_path: Path) -> None:
    """render_evolution raises ValueError when given an empty list."""
    from samantha_charts.charts.scaffolding_progression import render_evolution

    out = tmp_path / "empty.png"
    with pytest.raises(ValueError, match="points"):
        render_evolution([], out)


# ---------------------------------------------------------------------------
# Production renderer
# ---------------------------------------------------------------------------


def test_render_scaffolding_progression_writes_png(tmp_path: Path) -> None:
    """render_scaffolding_progression produces a non-empty PNG."""
    from samantha_charts.charts.scaffolding_progression import (
        render_scaffolding_progression,
    )

    out = tmp_path / "chart-n3.png"
    render_scaffolding_progression(out)

    assert out.exists(), "output file was not created"
    data = out.read_bytes()
    assert data[:8] == _PNG_MAGIC
    assert len(data) > 1000


def test_production_arc_ends_at_hundred_and_is_endpoint(tmp_path: Path) -> None:
    """The locked arc ends at 100.0% flagged as the endpoint."""
    import samantha_charts.charts.scaffolding_progression as mod

    captured: list[mod.EvolutionPoint] = []

    def _capture(points: list[mod.EvolutionPoint], output_path: Path, **_: object) -> None:
        captured.extend(points)

    original = mod.render_evolution
    mod.render_evolution = _capture  # type: ignore[assignment]
    try:
        mod.render_scaffolding_progression(tmp_path / "unused.png")
    finally:
        mod.render_evolution = original  # type: ignore[assignment]

    assert captured[0].pct == 62.0
    assert captured[-1].pct == 100.0
    assert captured[-1].is_endpoint is True
