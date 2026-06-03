"""Slice 6: render entry point dispatches to chart modules."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest


def test_render_example_exit_zero(tmp_path: Path) -> None:
    from samantha_charts.render import main

    output = tmp_path / "x.png"
    exit_code = main(["--chart", "_example", "--output", str(output)])

    assert exit_code == 0
    assert output.exists()
    assert output.stat().st_size > 0


def test_render_unknown_chart_exits_one(tmp_path: Path) -> None:
    """Unknown chart name → exit code 1 (distinct from render-failure exit 2)."""
    from samantha_charts.render import main

    output = tmp_path / "x.png"
    exit_code = main(["--chart", "does_not_exist", "--output", str(output)])

    assert exit_code == 1


def test_render_failure_exits_two_with_traceback(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chart-module exception → exit code 2 + traceback on stderr.

    Guards against the original `f"Render failed: {exc}"` that dropped the
    stack trace, leaving chart-module bugs effectively undebuggable.
    """
    from samantha_charts import render

    def _boom(_path: Path) -> None:
        raise RuntimeError("simulated chart failure")

    fake_registry: dict[str, Callable[[Path], None]] = {"_boom": _boom}
    monkeypatch.setattr(render, "_CHARTS", fake_registry)
    monkeypatch.setattr(render, "_register", lambda: None)  # registry already populated

    output = tmp_path / "x.png"
    exit_code = render.main(["--chart", "_boom", "--output", str(output)])

    assert exit_code == 2
    captured = capsys.readouterr()
    assert "simulated chart failure" in captured.err
    assert "Traceback" in captured.err  # full stack visible to operator
    assert "RuntimeError" in captured.err


def test_register_is_idempotent() -> None:
    """Calling main() repeatedly does not re-extend the chart registry."""
    from samantha_charts import render

    # Force a fresh registry (in case prior tests populated it).
    render._CHARTS.clear()
    render._register()
    first = dict(render._CHARTS)
    render._register()
    second = dict(render._CHARTS)
    assert first == second
    assert len(first) == 16
    assert "_example" in first
    assert "accuracy_ranking" in first
    assert "benchmark_bars" in first
    assert "category_heatmap" in first
    assert "quantization_card" in first
    assert "scaffolding_progression" in first
    assert "tool_assisted_evolution" in first
    assert "latency_box" in first
    assert "model_radar" in first
    assert "refusal_distribution" in first
    assert "routing_path_daily" in first
    assert "receipt_sample" in first
    assert "receipt_vs_trace" in first
    assert "receipt_coverage" in first
    assert "accuracy_evolution" in first
    assert "swebench_mobile_6x" in first


def test_render_quantization_card_via_cli(tmp_path: Path) -> None:
    """quantization_card chart renders successfully via main() CLI path."""
    from samantha_charts.render import main

    out = tmp_path / "q.png"
    exit_code = main(["--chart", "quantization_card", "--output", str(out)])

    assert exit_code == 0
    assert out.exists()
    assert out.stat().st_size > 0
