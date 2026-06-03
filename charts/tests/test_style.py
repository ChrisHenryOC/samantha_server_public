"""Slice 2: style helpers and example chart smoke test."""

import struct
from pathlib import Path

import matplotlib.pyplot as plt
import pytest


def test_render_example_produces_nonempty_png(tmp_path: Path) -> None:
    from samantha_charts.charts._example import render_example

    output = tmp_path / "example.png"
    render_example(output)

    assert output.exists(), "render_example must create the output file"
    assert output.stat().st_size > 0, "PNG must be non-empty"

    # Confirm it's a valid PNG by checking the 8-byte signature
    with output.open("rb") as f:
        header = f.read(8)
    assert header == b"\x89PNG\r\n\x1a\n", "File must be a valid PNG"


def test_render_example_dimensions(tmp_path: Path) -> None:
    from samantha_charts.charts._example import render_example

    output = tmp_path / "example.png"
    render_example(output)

    # Read PNG IHDR chunk: bytes 16-24 are width (4 bytes) and height (4 bytes)
    with output.open("rb") as f:
        f.seek(16)
        width = struct.unpack(">I", f.read(4))[0]
        height = struct.unpack(">I", f.read(4))[0]

    # Default matplotlib figsize (10, 6) at 100 dpi = 1000x600 pixels.
    # Accept a ±50px tolerance to handle dpi rounding differences.
    assert 950 <= width <= 1050, f"Expected ~1000px width, got {width}"
    assert 550 <= height <= 650, f"Expected ~600px height, got {height}"


def test_set_title_with_no_subtitle_does_not_crash() -> None:
    """Coverage for the subtitle=None branch (only the subtitle-present case
    was covered transitively through render_example)."""
    from samantha_charts.style import set_title

    fig, ax = plt.subplots()
    try:
        set_title(ax, "Title only, no subtitle")
        # No assertion needed — the test asserts the call doesn't raise. The
        # set_title implementation guards `fig is None`, so reaching this
        # line means the figure was attached and text was written.
        assert ax.get_figure() is fig
    finally:
        plt.close(fig)


def test_add_direction_indicator_lower_branch() -> None:
    """Coverage for direction='lower' (only 'higher' covered via render_example)."""
    from samantha_charts.style import add_direction_indicator

    fig, _ax = plt.subplots()
    try:
        add_direction_indicator(fig, "lower")
        # Confirm a text element was added in the top-right region.
        texts = [t for t in fig.texts if "lower is better" in t.get_text()]
        assert len(texts) == 1
    finally:
        plt.close(fig)


def test_add_direction_indicator_raises_on_invalid_direction() -> None:
    """The Literal type-hint protects static callers; runtime catches dynamic ones."""
    from samantha_charts.style import add_direction_indicator

    fig, _ax = plt.subplots()
    try:
        with pytest.raises(ValueError, match="higher.*lower"):
            add_direction_indicator(fig, "sideways")  # type: ignore[arg-type]
    finally:
        plt.close(fig)
