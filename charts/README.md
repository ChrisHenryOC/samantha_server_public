# samantha-charts

Python charting pipeline for `samantha_server` metrics. Produces publication-quality
PNGs using the gtan-samantha aesthetic (bold titles, direction indicators, blue/green/red
palette, methodology footnotes).

Parent issue: [GH-303](https://github.com/ChrisHenryOC/samantha_server/issues/303).

## Setup

```sh
cd charts
uv sync
```

This creates `.venv/` inside `charts/` — a separate environment from `samantha_server`.

## Fetch data

### Sweep baselines

Sweep files already live at `../results/baselines/*.txt`. No fetch step required.

### Langfuse traces

Set environment variables first:

```sh
export LANGFUSE_HOST=https://your-langfuse-instance
export LANGFUSE_PUBLIC_KEY=pk-...
export LANGFUSE_SECRET_KEY=sk-...
```

Then call the client from Python:

```python
from samantha_charts.data.langfuse_client import LangfuseClient

client = LangfuseClient()
traces = client.fetch_traces(limit=500)
```

A standalone `uv run python -m samantha_charts.fetch` entry point is planned
(see GH-303 for scope).

### Receipts

```python
from samantha_charts.data.receipts import query_receipts
from pathlib import Path

df = query_receipts(Path("../data/receipts.db"))
```

## Render a chart

```sh
uv run python -m samantha_charts.render --chart NAME --output PATH
```

Example:

```sh
uv run python -m samantha_charts.render --chart _example --output /tmp/example.png
```

To list all registered charts, omit `--chart` and read the help:

```sh
uv run python -m samantha_charts.render --help
```

## Add a new chart

1. **Create** `src/samantha_charts/charts/<my_chart>.py`.

   The file must export a single function:

   ```python
   from pathlib import Path

   def render_<my_chart>(output_path: Path) -> None:
       ...
   ```

   Use the helpers from `samantha_charts.style` to apply the shared aesthetic:

   ```python
   from samantha_charts.style import apply_style, set_title, add_direction_indicator, add_footnote
   ```

2. **Register** the chart in `src/samantha_charts/render.py` inside `_register()`:

   ```python
   from samantha_charts.charts.<my_chart> import render_<my_chart>
   _CHARTS["<my_chart>"] = render_<my_chart>
   ```

3. **Add a test** in `tests/test_<my_chart>.py` that calls the render function
   with a `tmp_path` fixture and asserts the PNG is non-empty.

4. Run `uv run pytest` from inside `charts/` to confirm everything passes.

## Known cosmetic issues

- **Triangle-glyph font warning.** The `add_direction_indicator()` helper uses
  `▶` / `▼` Unicode glyphs (U+25B6 / U+25BC). On macOS, Helvetica Neue doesn't
  include these geometric shapes, so matplotlib emits
  `UserWarning: Glyph 9654 ... missing from font(s) Helvetica Neue` and falls back
  to DejaVu Sans, which has them. The output PNG renders correctly; the warning
  is harmless. If you want to silence it, replace the glyph in `style.py` with
  a plain ASCII marker (`>`, `<`) or set the fontconfig to a font with the
  geometric shape range (e.g., Noto Sans Symbols 2).
