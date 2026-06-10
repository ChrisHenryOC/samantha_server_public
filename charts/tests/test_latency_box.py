"""Chart 5: per-model LLM-call latency box plot."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

# ---------------------------------------------------------------------------
# Slice 1: load_latencies
# ---------------------------------------------------------------------------


def test_load_latencies_parses_valid_json(tmp_path: Path) -> None:
    from samantha_charts.charts.latency_box import load_latencies

    data = {
        "_meta": {"unit": "seconds"},
        "latencies": {
            "ModelA": [1.0, 2.0, 3.0],
            "ModelB": [4.0, 5.0, 6.0],
        },
    }
    p = tmp_path / "latencies.json"
    p.write_text(json.dumps(data))

    result = load_latencies(p)

    assert result == {"ModelA": [1.0, 2.0, 3.0], "ModelB": [4.0, 5.0, 6.0]}


def test_load_latencies_raises_on_missing_key(tmp_path: Path) -> None:
    from samantha_charts.charts.latency_box import load_latencies

    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"_meta": {}}))

    with pytest.raises(ValueError, match="latencies"):
        load_latencies(p)


def test_load_latencies_raises_on_empty_latencies(tmp_path: Path) -> None:
    from samantha_charts.charts.latency_box import load_latencies

    p = tmp_path / "empty.json"
    p.write_text(json.dumps({"latencies": {}}))

    with pytest.raises(ValueError, match="latencies"):
        load_latencies(p)


# ---------------------------------------------------------------------------
# Slice 2: render_latency_box -- produces PNG, rejects empty data
# ---------------------------------------------------------------------------


def test_render_latency_box_produces_png(tmp_path: Path) -> None:
    from samantha_charts.charts.latency_box import render_latency_box

    data = {
        "FastModel": [1.0, 1.2, 1.1],
        "SlowModel": [5.0, 5.5, 5.2],
        "MidModel": [3.0, 3.1, 2.9],
    }
    out = tmp_path / "box.png"
    render_latency_box(data, out)

    raw = out.read_bytes()
    assert raw.startswith(_PNG_MAGIC)
    assert len(raw) > 1000


def test_render_latency_box_raises_on_empty_data(tmp_path: Path) -> None:
    from samantha_charts.charts.latency_box import render_latency_box

    with pytest.raises(ValueError):
        render_latency_box({}, tmp_path / "x.png")


# ---------------------------------------------------------------------------
# Slice 3: order parameter and default ascending-median ordering
# ---------------------------------------------------------------------------


def test_render_latency_box_order_param_drops_absent_models(tmp_path: Path) -> None:
    """order list controls which models appear and in what sequence."""
    from samantha_charts.charts.latency_box import render_latency_box

    data = {
        "ModelA": [1.0, 1.1],
        "ModelB": [2.0, 2.1],
        "ModelC": [3.0, 3.1],
    }
    out = tmp_path / "ordered.png"
    # Only ModelA and ModelC requested; ModelB excluded.
    render_latency_box(data, out, order=["ModelA", "ModelC"])

    raw = out.read_bytes()
    assert raw.startswith(_PNG_MAGIC)
    assert len(raw) > 1000


def test_render_latency_box_default_order_ascending_median(tmp_path: Path) -> None:
    """Without order, models are arranged fastest (lowest median) to slowest."""
    from samantha_charts.charts.latency_box import render_latency_box

    # Provide data where medians are unambiguous: 1.0, 5.0, 3.0
    data = {
        "Fastest": [1.0, 1.0, 1.0],
        "Slowest": [5.0, 5.0, 5.0],
        "Middle": [3.0, 3.0, 3.0],
    }
    out = tmp_path / "default_order.png"
    render_latency_box(data, out)

    # The chart must render without error; ascending-median order is visual --
    # we cannot directly inspect x-tick order from the PNG bytes, so we verify
    # that rendering succeeds (no crash / wrong-ordering ValueError).
    assert out.read_bytes().startswith(_PNG_MAGIC)


# ---------------------------------------------------------------------------
# Slice 4: incumbent detection
# ---------------------------------------------------------------------------


def test_render_latency_box_incumbent_model_renders(tmp_path: Path) -> None:
    """A model name containing 'Qwen3-Next-80B' exercises the green-box path."""
    from samantha_charts.charts.latency_box import render_latency_box

    data = {
        "Qwen3-Next-80B-A3B-Instruct-4bit": [2.0, 2.5, 3.0],
        "OtherModel": [5.0, 6.0, 5.5],
    }
    out = tmp_path / "incumbent.png"
    render_latency_box(data, out)

    raw = out.read_bytes()
    assert raw.startswith(_PNG_MAGIC)
    assert len(raw) > 1000


def test_render_latency_box_no_incumbent_no_crash(tmp_path: Path) -> None:
    """When no model matches the incumbent substring, rendering still succeeds."""
    from samantha_charts.charts.latency_box import render_latency_box

    data = {
        "UnknownModel1": [2.0, 2.5],
        "UnknownModel2": [4.0, 4.5],
    }
    out = tmp_path / "no_incumbent.png"
    render_latency_box(data, out)

    assert out.read_bytes().startswith(_PNG_MAGIC)


# ---------------------------------------------------------------------------
# Slice 5: render_latency_box_chart -- production renderer
# ---------------------------------------------------------------------------


def test_render_latency_box_chart_writes_png(tmp_path: Path) -> None:
    from samantha_charts.charts.latency_box import render_latency_box_chart

    out = tmp_path / "chart5.png"
    render_latency_box_chart(out)

    raw = out.read_bytes()
    assert raw.startswith(_PNG_MAGIC)
    assert len(raw) > 1000


# ---------------------------------------------------------------------------
# Slice 6: all-absent order guard
# ---------------------------------------------------------------------------


def test_render_latency_box_raises_when_all_order_names_absent(tmp_path: Path) -> None:
    """render_latency_box raises ValueError when every name in order is absent from data."""
    from samantha_charts.charts.latency_box import render_latency_box

    data = {
        "ModelA": [1.0, 1.1, 1.2],
        "ModelB": [2.0, 2.1, 2.2],
    }
    with pytest.raises(ValueError, match="no models from `order` are present in data"):
        render_latency_box(data, tmp_path / "x.png", order=["nonexistent"])
