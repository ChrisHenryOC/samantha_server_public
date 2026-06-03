"""Render entry point.

Usage:
    uv run python -m samantha_charts.render --chart NAME --output PATH
    uv run python -m samantha_charts.render --chart _example --output /tmp/x.png

Exit codes
----------
0   Success.
1   Unknown chart name (--chart NAME not in the registry).
2   Render exception (chart module raised; full traceback to stderr).

Trust model
-----------
This is a developer-facing CLI tool. ``--output`` is accepted verbatim and
``output.parent.mkdir(parents=True)`` creates arbitrary directory trees.
Acceptable here — the operator runs this against local paths they control.
If this surface is ever wrapped behind a web endpoint or accepts untrusted
input, add path-traversal sanitization before exposing.
"""

from __future__ import annotations

import argparse
import sys
import traceback
from collections.abc import Callable
from pathlib import Path

# Matplotlib backend (``Agg`` headless) is set in ``samantha_charts/__init__.py``
# at package-import time — any import of the package triggers it.

# Registry: chart name → callable(output_path: Path) -> None
_CHARTS: dict[str, Callable[[Path], None]] = {}


def _register() -> None:
    """Populate _CHARTS lazily to avoid import-time matplotlib overhead.

    Idempotent: subsequent calls are no-ops once the registry is populated.
    Without this guard, repeated ``main()`` invocations in the same process
    (e.g., tests, notebook usage) would re-import chart modules — currently
    harmless but a correctness hazard once chart bundles grow.
    """
    if _CHARTS:
        return
    from samantha_charts.charts._example import render_example
    from samantha_charts.charts.accuracy_evolution import render_accuracy_evolution_chart
    from samantha_charts.charts.accuracy_ranking import render_accuracy_ranking
    from samantha_charts.charts.benchmark_bars import render_benchmark_bars
    from samantha_charts.charts.category_heatmap import render_category_heatmap
    from samantha_charts.charts.latency_box import render_latency_box_chart
    from samantha_charts.charts.model_radar import render_model_radar
    from samantha_charts.charts.quantization_card import render_quantization_card
    from samantha_charts.charts.receipt_coverage import render_receipt_coverage_chart
    from samantha_charts.charts.receipt_sample import render_receipt_sample_chart
    from samantha_charts.charts.receipt_vs_trace import render_receipt_vs_trace_chart
    from samantha_charts.charts.refusal_distribution import render_refusal_distribution_chart
    from samantha_charts.charts.routing_path_daily import render_routing_path_daily_chart
    from samantha_charts.charts.scaffolding_progression import (
        render_scaffolding_progression,
    )
    from samantha_charts.charts.swebench_mobile_6x import render_swebench_mobile_6x

    _CHARTS["_example"] = render_example
    _CHARTS["accuracy_ranking"] = render_accuracy_ranking
    _CHARTS["benchmark_bars"] = render_benchmark_bars
    _CHARTS["category_heatmap"] = render_category_heatmap
    _CHARTS["latency_box"] = render_latency_box_chart
    _CHARTS["model_radar"] = render_model_radar
    _CHARTS["quantization_card"] = render_quantization_card
    _CHARTS["refusal_distribution"] = render_refusal_distribution_chart
    _CHARTS["routing_path_daily"] = render_routing_path_daily_chart
    _CHARTS["scaffolding_progression"] = render_scaffolding_progression
    _CHARTS["receipt_sample"] = render_receipt_sample_chart
    _CHARTS["receipt_vs_trace"] = render_receipt_vs_trace_chart
    _CHARTS["receipt_coverage"] = render_receipt_coverage_chart
    _CHARTS["accuracy_evolution"] = render_accuracy_evolution_chart
    _CHARTS["swebench_mobile_6x"] = render_swebench_mobile_6x


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and render the requested chart.

    Parameters
    ----------
    argv:
        Argument list (defaults to sys.argv[1:]).

    Returns
    -------
    int
        Exit code per module docstring.
    """
    _register()

    parser = argparse.ArgumentParser(
        prog="samantha-charts-render",
        description="Render a samantha-charts chart to PNG.",
    )
    parser.add_argument(
        "--chart",
        required=True,
        metavar="NAME",
        help=f"Chart name. Known charts: {', '.join(sorted(_CHARTS))}",
    )
    parser.add_argument(
        "--output",
        required=True,
        metavar="PATH",
        help="Output PNG file path.",
    )

    args = parser.parse_args(argv)

    if args.chart not in _CHARTS:
        known = ", ".join(sorted(_CHARTS)) or "(none registered)"
        print(
            f"Unknown chart '{args.chart}'. Known charts: {known}",
            file=sys.stderr,
        )
        return 1

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    try:
        _CHARTS[args.chart](output)
    except Exception as exc:  # noqa: BLE001
        # Surface the full traceback to stderr so chart-module bugs are
        # debuggable without re-running under -X dev. The terse `{exc}`
        # message that was here lost the stack trace.
        print(f"Render failed for chart {args.chart!r}: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
