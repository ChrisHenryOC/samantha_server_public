"""Slice 1: verify the samantha_charts package is importable + exports __version__."""


def test_samantha_charts_importable_and_versioned() -> None:
    import samantha_charts

    # Verify the package exports a __version__ (not just that import succeeds).
    # An empty __init__.py would pass `import samantha_charts` alone.
    assert hasattr(samantha_charts, "__version__")
    assert isinstance(samantha_charts.__version__, str)
    assert samantha_charts.__version__  # non-empty


def test_model_display_names_importable_from_models_module() -> None:
    """MODEL_DISPLAY_NAMES is exported from samantha_charts.models."""
    from samantha_charts.models import MODEL_DISPLAY_NAMES

    assert isinstance(MODEL_DISPLAY_NAMES, dict)
    # Four production model names must be present
    assert "Qwen3-Next-80B-A3B-Instruct-4bit" in MODEL_DISPLAY_NAMES
    assert "Qwen3.5-35B-A3B-8bit" in MODEL_DISPLAY_NAMES
    assert "gemma-4-26B-A4B-it-MLX-4bit" in MODEL_DISPLAY_NAMES
    assert "Qwen2.5-Coder-32B-Instruct-MLX-4bit" in MODEL_DISPLAY_NAMES


def test_chart_modules_use_shared_display_names() -> None:
    """latency_box and benchmark_bars use the shared MODEL_DISPLAY_NAMES directly;
    model_radar augments it (incumbent suffix) but keeps all base keys (no drift)."""
    from samantha_charts.charts import benchmark_bars, latency_box, model_radar
    from samantha_charts.models import MODEL_DISPLAY_NAMES

    # benchmark_bars and latency_box reference the shared map object (no local copy).
    assert benchmark_bars.MODEL_DISPLAY_NAMES is MODEL_DISPLAY_NAMES
    assert latency_box.MODEL_DISPLAY_NAMES is MODEL_DISPLAY_NAMES
    # model_radar keeps a local augmented dict but must cover every base key.
    for key in MODEL_DISPLAY_NAMES:
        assert key in model_radar._DISPLAY_NAMES, f"{key} missing from model_radar._DISPLAY_NAMES"


def test_samantha_charts_sets_matplotlib_backend_to_agg() -> None:
    """Side effect on package import: matplotlib backend → Agg.

    Documented in __init__.py; verified here to prevent regression.
    """
    import matplotlib
    import samantha_charts  # noqa: F401  side effect we're asserting

    assert matplotlib.get_backend().lower() == "agg"
