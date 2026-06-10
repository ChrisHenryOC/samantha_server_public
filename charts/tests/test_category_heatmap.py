"""Tests for Chart 8: per-category accuracy heatmap."""

from __future__ import annotations

from pathlib import Path

import pytest

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


# ---------------------------------------------------------------------------
# Dataclass property tests
# ---------------------------------------------------------------------------


def test_category_cell_stores_values() -> None:
    """CategoryCell stores all fields correctly."""
    from samantha_charts.charts.category_heatmap import CategoryCell

    cell = CategoryCell(category="query", model="ModelA", passed=29, total=29)
    assert cell.category == "query"
    assert cell.model == "ModelA"
    assert cell.passed == 29
    assert cell.total == 29


def test_category_cell_accuracy_pct() -> None:
    """CategoryCell.accuracy_pct computes correctly."""
    from samantha_charts.charts.category_heatmap import CategoryCell

    cell = CategoryCell(category="llm_review", model="M", passed=5, total=6)
    assert abs(cell.accuracy_pct - 83.333_333) < 0.001


def test_category_cell_perfect_accuracy() -> None:
    """passed == total gives 100.0 pct."""
    from samantha_charts.charts.category_heatmap import CategoryCell

    cell = CategoryCell(category="rule_coverage", model="M", passed=81, total=81)
    assert cell.accuracy_pct == 100.0


def test_category_cell_is_frozen() -> None:
    """CategoryCell is immutable."""
    from samantha_charts.charts.category_heatmap import CategoryCell

    cell = CategoryCell(category="query", model="M", passed=10, total=10)
    with pytest.raises((AttributeError, TypeError)):
        cell.passed = 9  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------


def test_category_cell_rejects_passed_above_total() -> None:
    """passed > total raises ValueError."""
    from samantha_charts.charts.category_heatmap import CategoryCell

    with pytest.raises(ValueError, match="passed"):
        CategoryCell(category="query", model="M", passed=30, total=29)


def test_category_cell_rejects_zero_total() -> None:
    """total <= 0 raises ValueError."""
    from samantha_charts.charts.category_heatmap import CategoryCell

    with pytest.raises(ValueError, match="total"):
        CategoryCell(category="query", model="M", passed=0, total=0)


def test_category_cell_rejects_negative_total() -> None:
    """total < 0 trips the same `total <= 0` guard."""
    from samantha_charts.charts.category_heatmap import CategoryCell

    with pytest.raises(ValueError, match="total"):
        CategoryCell(category="query", model="M", passed=0, total=-5)


def test_category_cell_rejects_negative_passed() -> None:
    """passed < 0 raises ValueError."""
    from samantha_charts.charts.category_heatmap import CategoryCell

    with pytest.raises(ValueError, match="passed"):
        CategoryCell(category="query", model="M", passed=-1, total=29)


# ---------------------------------------------------------------------------
# Cell text contrast
# ---------------------------------------------------------------------------


def test_contrast_text_color_white_on_dark() -> None:
    """A dark fill (e.g. deep green 100% cell) gets white text."""
    from samantha_charts.charts.category_heatmap import _contrast_text_color

    assert _contrast_text_color((0.0, 0.27, 0.11, 1.0)) == "white"
    assert _contrast_text_color((0.0, 0.0, 0.0, 1.0)) == "white"


def test_contrast_text_color_black_on_light() -> None:
    """A light fill (e.g. pale yellow-green mid cell) gets black text."""
    from samantha_charts.charts.category_heatmap import _contrast_text_color

    assert _contrast_text_color((1.0, 1.0, 1.0, 1.0)) == "black"
    assert _contrast_text_color((0.85, 0.93, 0.55, 1.0)) == "black"


def test_contrast_text_color_threshold_is_pinned_near_half() -> None:
    """Boundary: grey (v, v, v) has luminance ~v (Rec.601 weights sum to ~1).

    Brackets the 0.5 cutoff tightly (0.48 -> white, 0.52 -> black) without
    sitting on the float boundary, so a threshold typo in either direction
    (e.g. 0.4 or 0.6) fails rather than slipping through.
    """
    from samantha_charts.charts.category_heatmap import _contrast_text_color

    assert _contrast_text_color((0.48, 0.48, 0.48, 1.0)) == "white"
    assert _contrast_text_color((0.52, 0.52, 0.52, 1.0)) == "black"


# ---------------------------------------------------------------------------
# render_heatmap: produces a non-empty PNG
# ---------------------------------------------------------------------------


def test_render_heatmap_produces_nonempty_png(tmp_path: Path) -> None:
    """render_heatmap writes a valid PNG for a small synthetic input."""
    from samantha_charts.charts.category_heatmap import CategoryCell, render_heatmap

    cells = [
        CategoryCell(category="query", model="ModelA", passed=29, total=29),
        CategoryCell(category="query", model="ModelB", passed=28, total=29),
        CategoryCell(category="llm_review", model="ModelA", passed=6, total=6),
        CategoryCell(category="llm_review", model="ModelB", passed=5, total=6),
    ]
    out = tmp_path / "heatmap.png"
    render_heatmap(cells, ["ModelA", "ModelB"], ["query", "llm_review"], out)

    assert out.exists(), "output file was not created"
    data = out.read_bytes()
    assert data[:8] == _PNG_MAGIC, "file does not start with PNG magic"
    assert len(data) > 1000, "PNG suspiciously small"


def test_render_heatmap_rejects_empty_cells(tmp_path: Path) -> None:
    """render_heatmap raises ValueError when given empty cells."""
    from samantha_charts.charts.category_heatmap import render_heatmap

    out = tmp_path / "empty.png"
    with pytest.raises(ValueError, match="cells"):
        render_heatmap([], ["ModelA"], ["query"], out)


def test_render_heatmap_single_cell(tmp_path: Path) -> None:
    """render_heatmap works with a single (category, model) cell."""
    from samantha_charts.charts.category_heatmap import CategoryCell, render_heatmap

    cells = [CategoryCell(category="query", model="M", passed=10, total=29)]
    out = tmp_path / "single.png"
    render_heatmap(cells, ["M"], ["query"], out)

    assert out.exists()
    assert out.read_bytes()[:8] == _PNG_MAGIC


def test_render_heatmap_category_with_no_cells_renders(tmp_path: Path) -> None:
    """A category absent from every cell hits the bare-100% annotation branch.

    ``categories`` lists "orphan", but no CategoryCell has that category, so
    the total-inference ``next(...)`` returns None and the annotation degrades
    to a plain "100.0%". The render must still succeed.
    """
    from samantha_charts.charts.category_heatmap import CategoryCell, render_heatmap

    cells = [CategoryCell(category="query", model="M", passed=29, total=29)]
    out = tmp_path / "orphan.png"
    render_heatmap(cells, ["M"], ["query", "orphan"], out)

    assert out.exists()
    assert out.read_bytes()[:8] == _PNG_MAGIC


# ---------------------------------------------------------------------------
# Production renderer
# ---------------------------------------------------------------------------


def test_render_category_heatmap_writes_png(tmp_path: Path) -> None:
    """render_category_heatmap produces a non-empty PNG from locked values."""
    from samantha_charts.charts.category_heatmap import render_category_heatmap

    out = tmp_path / "chart8.png"
    render_category_heatmap(out)

    assert out.exists(), "output file was not created"
    data = out.read_bytes()
    assert data[:8] == _PNG_MAGIC
    assert len(data) > 1000


# ---------------------------------------------------------------------------
# Slice 2: load_category_heatmap -- reads from committed JSON
# ---------------------------------------------------------------------------


def test_load_category_heatmap_parses_valid_json(tmp_path: Path) -> None:
    """load_category_heatmap parses a chart8 JSON into cells, models, categories."""
    import json

    from samantha_charts.charts.category_heatmap import CategoryCell, load_category_heatmap

    data = {
        "category_totals": {"query": 29, "llm_review": 6},
        "models": ["ModelA", "ModelB"],
        "categories": ["query", "llm_review"],
        "non_perfect": [{"model": "ModelB", "category": "query", "passed": 28}],
    }
    p = tmp_path / "chart8.json"
    p.write_text(json.dumps(data))

    cells, models, categories = load_category_heatmap(p)

    assert models == ["ModelA", "ModelB"]
    assert categories == ["query", "llm_review"]
    assert isinstance(cells[0], CategoryCell)
    # The non-perfect cell
    non_perfect = [c for c in cells if c.model == "ModelB" and c.category == "query"]
    assert len(non_perfect) == 1
    assert non_perfect[0].passed == 28
    assert non_perfect[0].total == 29


def test_load_category_heatmap_raises_on_missing_category_totals(tmp_path: Path) -> None:
    """load_category_heatmap raises ValueError when 'category_totals' is absent."""
    import json

    from samantha_charts.charts.category_heatmap import load_category_heatmap

    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"models": ["M"], "categories": ["q"], "non_perfect": []}))

    with pytest.raises(ValueError, match="category_totals"):
        load_category_heatmap(p)


def test_load_category_heatmap_raises_on_missing_models(tmp_path: Path) -> None:
    """load_category_heatmap raises ValueError when 'models' is absent."""
    import json

    from samantha_charts.charts.category_heatmap import load_category_heatmap

    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"category_totals": {"q": 1}, "categories": ["q"], "non_perfect": []}))

    with pytest.raises(ValueError, match="models"):
        load_category_heatmap(p)


def test_load_category_heatmap_raises_valueerror_on_missing_category_in_totals(
    tmp_path: Path,
) -> None:
    """load_category_heatmap raises ValueError when non_perfect references a category
    not present in category_totals, with a message naming the category."""
    import json

    from samantha_charts.charts.category_heatmap import load_category_heatmap

    data = {
        "category_totals": {"query": 29},
        "models": ["ModelA"],
        "categories": ["query"],
        "non_perfect": [{"model": "ModelA", "category": "ghost_category", "passed": 5}],
    }
    p = tmp_path / "bad_cat.json"
    p.write_text(json.dumps(data))

    with pytest.raises(ValueError, match="ghost_category"):
        load_category_heatmap(p)


def test_load_category_heatmap_all_perfect_cells_included(tmp_path: Path) -> None:
    """load_category_heatmap includes perfect cells for every (category, model) pair."""
    import json

    from samantha_charts.charts.category_heatmap import load_category_heatmap

    data = {
        "category_totals": {"query": 29},
        "models": ["ModelA", "ModelB"],
        "categories": ["query"],
        "non_perfect": [],
    }
    p = tmp_path / "all_perfect.json"
    p.write_text(json.dumps(data))

    cells, models, categories = load_category_heatmap(p)

    # Both models x 1 category = 2 cells, all perfect
    assert len(cells) == 2
    assert all(c.passed == c.total for c in cells)


# ---------------------------------------------------------------------------
# Slice: load_category_heatmap fallback branches (#6)
# ---------------------------------------------------------------------------


def test_load_category_heatmap_categories_fallback_to_totals_keys(tmp_path: Path) -> None:
    """When 'categories' key is omitted, loader falls back to category_totals.keys()."""
    import json

    from samantha_charts.charts.category_heatmap import load_category_heatmap

    # No 'categories' key in JSON
    data = {
        "category_totals": {"query": 29, "llm_review": 6},
        "models": ["ModelA"],
        # 'categories' deliberately absent
        "non_perfect": [],
    }
    p = tmp_path / "no_categories_key.json"
    p.write_text(json.dumps(data))

    cells, models, categories = load_category_heatmap(p)

    # Falls back to keys from category_totals
    assert set(categories) == {"query", "llm_review"}
    # All cells are perfect (non_perfect is empty)
    assert all(c.passed == c.total for c in cells)


def test_load_category_heatmap_non_perfect_omitted_defaults_to_all_perfect(
    tmp_path: Path,
) -> None:
    """When 'non_perfect' key is omitted, loader defaults to empty -> all cells perfect."""
    import json

    from samantha_charts.charts.category_heatmap import load_category_heatmap

    # No 'non_perfect' key in JSON
    data = {
        "category_totals": {"query": 29},
        "models": ["ModelA", "ModelB"],
        "categories": ["query"],
        # 'non_perfect' deliberately absent
    }
    p = tmp_path / "no_non_perfect_key.json"
    p.write_text(json.dumps(data))

    cells, models, categories = load_category_heatmap(p)

    # 2 models x 1 category = 2 cells, all perfect
    assert len(cells) == 2
    assert all(c.passed == c.total for c in cells)
