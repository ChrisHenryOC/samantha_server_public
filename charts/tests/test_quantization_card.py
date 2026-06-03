"""Slice B: quantization_card renders two side-by-side comparison cards."""

from __future__ import annotations

from pathlib import Path

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

_LEFT = None  # populated per-test via _make_card
_RIGHT = None


def _make_card(
    label: str,
    stable_passed: int,
    stable_total: int,
    latency_p50: str | None = "48.0s",
) -> object:
    from samantha_charts.charts.quantization_card import CardSide

    return CardSide(
        label=label,
        stable_passed=stable_passed,
        stable_total=stable_total,
        raw_passed=stable_passed + 5,
        raw_total=stable_total + 10,
        flake_count=3,
        memory_footprint="20 GB est.",
        latency_p50=latency_p50,
    )


def test_render_cards_produces_nonempty_png(tmp_path: Path) -> None:
    """render_cards writes a non-empty PNG to output_path."""
    from samantha_charts.charts.quantization_card import render_cards

    left = _make_card("Q4 (4-BIT)", stable_passed=137, stable_total=149)
    right = _make_card("Q8 (8-BIT)", stable_passed=114, stable_total=149)
    out = tmp_path / "quant.png"

    render_cards(left, right, out)

    assert out.exists(), "output file was not created"
    assert out.stat().st_size > 0, "output file is empty"
    assert out.read_bytes()[:8] == _PNG_MAGIC, "file does not start with PNG magic bytes"


def test_render_cards_handles_missing_latency(tmp_path: Path) -> None:
    """When both sides have latency_p50=None, render_cards still produces a valid PNG."""
    from samantha_charts.charts.quantization_card import render_cards

    left = _make_card("Q4 (4-BIT)", stable_passed=137, stable_total=149, latency_p50=None)
    right = _make_card("Q8 (8-BIT)", stable_passed=114, stable_total=149, latency_p50=None)
    out = tmp_path / "no_latency.png"

    render_cards(left, right, out)

    assert out.exists()
    assert out.stat().st_size > 0
    assert out.read_bytes()[:8] == _PNG_MAGIC


def test_render_cards_winner_is_higher_stable(tmp_path: Path) -> None:
    """Left card with worse stable accuracy and right with better: no crash, valid PNG."""
    from samantha_charts.charts.quantization_card import render_cards

    # Right side is the winner here (higher stable accuracy)
    left = _make_card("WORSE", stable_passed=50, stable_total=149)
    right = _make_card("BETTER", stable_passed=130, stable_total=149)
    out = tmp_path / "right_wins.png"

    render_cards(left, right, out)

    assert out.exists()
    assert out.stat().st_size > 0
    assert out.read_bytes()[:8] == _PNG_MAGIC


def test_render_quantization_card_smoke(tmp_path: Path) -> None:
    """render_quantization_card produces a non-empty PNG from the real baseline data."""
    from samantha_charts.charts.quantization_card import render_quantization_card

    out = tmp_path / "q.png"
    render_quantization_card(out)

    assert out.exists()
    assert out.stat().st_size > 0
    assert out.read_bytes()[:8] == _PNG_MAGIC
