"""Chart 8: per-category stable accuracy heatmap (GH-307).

Heatmap with rows = scenario categories, columns = models, cells = stable
accuracy % for that (category, model) pair. Highlights where models differ
(query and llm_review LLM-routed categories) vs. uniformly-100% deterministic
categories.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np

from samantha_charts import style

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CategoryCell:
    """Stable accuracy for one (category, model) pair.

    Parameters
    ----------
    category:
        Scenario category name (e.g. "query", "llm_review").
    model:
        Model display name.
    passed:
        Number of fixtures in this category passing all N sweeps.
        Must be in [0, total].
    total:
        Total fixtures in this category. Must be > 0.
    """

    category: str
    model: str
    passed: int
    total: int

    def __post_init__(self) -> None:
        if self.total <= 0:
            raise ValueError(f"total must be > 0, got {self.total!r}")
        if not (0 <= self.passed <= self.total):
            raise ValueError(f"passed must be in [0, {self.total}], got {self.passed!r}")

    @property
    def accuracy_pct(self) -> float:
        """Stable accuracy as a percentage."""
        return self.passed / self.total * 100


def _contrast_text_color(rgba: tuple[float, float, float, float]) -> str:
    """Pick black or white text for legibility over a cell of color *rgba*.

    Uses Rec. 601 perceptual luminance of the RGB channels (alpha ignored):
    dark cells get white text, light cells get black. This is what makes the
    deep-green 100% cells readable, where a fixed ``pct > 90 -> black`` rule
    put black text on a dark fill.
    """
    r, g, b, _ = rgba  # alpha intentionally ignored
    luminance = 0.299 * r + 0.587 * g + 0.114 * b
    return "white" if luminance < 0.5 else "black"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_category_heatmap(
    path: Path,
) -> tuple[list[CategoryCell], list[str], list[str]]:
    """Read *path* (JSON) and return (cells, models, categories).

    Parameters
    ----------
    path:
        Path to a JSON file with top-level keys:
        ``"category_totals"``: dict mapping category name to total fixture count,
        ``"models"``: ordered list of model display names,
        ``"categories"``: ordered list of category names,
        ``"non_perfect"``: list of objects with ``model``, ``category``, and
        ``passed`` fields for non-100% cells. All other (model, category)
        pairs default to perfect (passed == total).

    Returns
    -------
    tuple[list[CategoryCell], list[str], list[str]]
        A 3-tuple of (cells, models, categories) ready to pass to
        ``render_heatmap``.

    Raises
    ------
    ValueError
        If ``"category_totals"`` or ``"models"`` keys are absent or empty.
    """
    raw: dict[str, Any] = json.loads(path.read_text())
    category_totals: dict[str, int] | None = raw.get("category_totals")
    if not category_totals:
        raise ValueError(
            f"Expected a non-empty 'category_totals' mapping in {path}; got: {category_totals!r}"
        )
    models: list[str] | None = raw.get("models")
    if not models:
        raise ValueError(f"Expected a non-empty 'models' list in {path}; got: {models!r}")
    categories: list[str] = raw.get("categories", list(category_totals.keys()))
    non_perfect: list[dict[str, Any]] = raw.get("non_perfect", [])

    # Build the full cell list: non-perfect first, then fill in all perfect cells.
    cells: list[CategoryCell] = []
    already_present: set[tuple[str, str]] = set()

    for entry in non_perfect:
        model = str(entry["model"])
        cat = str(entry["category"])
        passed = int(entry["passed"])
        if cat not in category_totals:
            raise ValueError(
                f"non_perfect entry references category {cat!r} which is not"
                f" present in category_totals (file: {path})"
            )
        total = category_totals[cat]
        cells.append(CategoryCell(category=cat, model=model, passed=passed, total=total))
        already_present.add((cat, model))

    for model in models:
        for cat in categories:
            if (cat, model) not in already_present:
                total = category_totals[cat]
                cells.append(CategoryCell(category=cat, model=model, passed=total, total=total))

    return cells, models, categories


def render_heatmap(
    cells: list[CategoryCell],
    models: list[str],
    categories: list[str],
    output_path: Path,
) -> None:
    """Render a heatmap of per-category stable accuracy to *output_path* (PNG).

    Parameters
    ----------
    cells:
        List of CategoryCell data. Must be non-empty. Any (category, model)
        pair missing from *cells* defaults to 100.0%.
    models:
        Ordered list of model names (left to right, incumbent first).
    categories:
        Ordered list of category names (top to bottom).
    output_path:
        Destination PNG path. Parent directory is created if needed.

    Raises
    ------
    ValueError
        If *cells* is empty.
    """
    if not cells:
        raise ValueError("cells must be non-empty")

    # Build lookup tables in one pass: (category, model) -> pct and -> (passed, total)
    lookup: dict[tuple[str, str], float] = {}
    cell_totals: dict[tuple[str, str], tuple[int, int]] = {}
    for cell in cells:
        key = (cell.category, cell.model)
        lookup[key] = cell.accuracy_pct
        cell_totals[key] = (cell.passed, cell.total)

    # Build 2D grid: rows=categories, cols=models
    grid = np.full((len(categories), len(models)), 100.0)
    for row_idx, cat in enumerate(categories):
        for col_idx, model in enumerate(models):
            pct = lookup.get((cat, model), 100.0)
            grid[row_idx, col_idx] = pct

    fig_height = max(4.0, len(categories) * 0.9 + 2.5)
    fig_width = max(8.0, len(models) * 2.5 + 2.0)
    fig, ax = plt.subplots(figsize=(fig_width, fig_height))
    # Deliberately NOT style.apply_style here (unlike the line/bar charts):
    # apply_style restyles the spines and turns on axis gridlines, both of
    # which this chart removes on purpose (spines hidden below, separators
    # drawn manually over the imshow). Only the white background is shared.
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    # vmin=80 so the diff between ~83% and 100% is visible
    im = ax.imshow(grid, cmap="RdYlGn", vmin=80.0, vmax=100.0, aspect="auto")

    # Cell separators. Vertical lines (between models) are bold so each model
    # reads as its own column; horizontal lines (between categories) are
    # lighter, delineating rows without competing with the columns. White on
    # the RdYlGn fill gives a crisp tiled-mosaic look. Drawn below the cell
    # annotations (zorder 2 < the text's default 3).
    n_cols, n_rows = len(models), len(categories)
    for x in range(n_cols + 1):
        ax.axvline(x - 0.5, color="white", linewidth=3.0, zorder=2)
    for y in range(n_rows + 1):
        ax.axhline(y - 0.5, color="white", linewidth=1.2, zorder=2)
    # axvline/axhline can nudge the limits; pin them back to the imshow extent.
    ax.set_xlim(-0.5, n_cols - 0.5)
    ax.set_ylim(n_rows - 0.5, -0.5)

    # Colorbar
    cbar = fig.colorbar(im, ax=ax, fraction=0.03, pad=0.02)
    cbar.set_label("stable accuracy %", fontsize=9, color=style.GRAY_SUBTITLE)
    cbar.ax.tick_params(labelsize=8)

    # Axes ticks
    ax.set_xticks(range(len(models)))
    ax.set_yticks(range(len(categories)))

    # Horizontal model labels (they already carry a name/parenthetical line
    # break); category labels with underscores swapped for spaces.
    ax.set_xticklabels(models, fontsize=12, rotation=0, ha="center")
    ax.set_yticklabels([c.replace("_", " ") for c in categories], fontsize=12)
    ax.tick_params(colors=style.GRAY_SUBTITLE, length=0)

    # Hide the axes spines: the default black frame collides with the white
    # cell separators at the grid edge, producing odd corner artifacts.
    for spine in ax.spines.values():
        spine.set_visible(False)

    # Annotate cells
    for row_idx, cat in enumerate(categories):
        for col_idx, model in enumerate(models):
            pct = grid[row_idx, col_idx]
            key = (cat, model)
            if key in cell_totals:
                p, t = cell_totals[key]
                ann = f"{pct:.1f}%\n{p}/{t}"
            else:
                # Perfect — infer total from any cell with this category
                total_for_cat = next(
                    (c.total for c in cells if c.category == cat),
                    None,
                )
                if total_for_cat is not None:
                    ann = f"100.0%\n{total_for_cat}/{total_for_cat}"
                else:
                    ann = "100.0%"

            # Contrast against the actual cell fill (luminance-based), so the
            # dark-green 100% cells get white text and the light mid cells
            # get black text.
            text_color = _contrast_text_color(im.cmap(im.norm(pct)))
            ax.text(
                col_idx,
                row_idx,
                ann,
                ha="center",
                va="center",
                fontsize=12,
                fontweight="bold",
                color=text_color,
            )

    style.set_title(
        ax,
        "Per-category stable accuracy by model",
        subtitle=(
            "Where models differ is exactly the LLM-routed categories"
            " (query, llm_review); deterministic categories are uniformly 100%"
        ),
    )
    style.add_footnote(
        fig,
        "cell = fixtures in that category passing all 5 sweeps;"
        " source: charts/data/chart8-category-heatmap.json;"
        " category totals from tests/fixtures/scenarios/",
    )

    fig.tight_layout(rect=(0, 0.04, 1, 0.88))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=100, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def render_category_heatmap(output_path: Path) -> None:
    """Production renderer: reads the committed data file and renders to *output_path*.

    Data source: ``charts/data/chart8-category-heatmap.json`` (committed,
    following the Charts 1/5/7/N2 precedent of repo-committed data + PNG).
    """
    data_path = Path(__file__).resolve().parents[3] / "data" / "chart8-category-heatmap.json"
    cells, models, categories = load_category_heatmap(data_path)
    render_heatmap(cells, models, categories, output_path)
