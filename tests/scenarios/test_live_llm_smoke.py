"""In-process replay() smoke tests for the corpus.

Replaces the subprocess shim tests that shelled out to the
CLI with direct in-process replay() calls against the canonical CLI surface.
The in-process path exercises the same routing/preflight/receipt-emission as
the CLI but avoids the subprocess overhead and makes assertion access easier.

    replay(corpus_dir, include_categories={"query"})  # query smoke
    replay(corpus_dir, include_categories={"llm_review"})  # llm_review smoke

Each test asserts on the returned AccuracyReport. Failure messages include
the AccuracyReport so developers can inspect which fixture failed.

These tests are excluded from the default pytest run by the
``addopts = "-m 'not ... live_llm ...'"`` setting in ``pyproject.toml``.
Run explicitly with:
    uv run pytest -m live_llm
"""

from __future__ import annotations

from pathlib import Path

import pytest

from samantha_server.scenarios.replay import AccuracyReport, replay

_CORPUS_DIR = Path(__file__).parent.parent / "fixtures" / "scenarios"


@pytest.mark.live_llm
def test_query_corpus_via_replay_cli() -> None:
    """Run the query scenario corpus through the in-process replay() surface.

    Calls replay() with include_categories={"query"} and asserts that
    included_accuracy >= 0.995. A failure means at least one query fixture's
    expected order_ids were not in the model's response.

    The engine content gate (#194) produces the non-zero verdict
    on content-mismatch; strict=True is no longer needed and is deprecated.
    """
    report: AccuracyReport = replay(_CORPUS_DIR, include_categories={"query"})

    assert report.included_accuracy >= 0.995, (
        f"Query corpus replay failed: included_accuracy={report.included_accuracy:.4%} "
        f"({report.included_pass}/{report.included_total}). "
        f"Failed scenarios: "
        + ", ".join(
            f"[{v.category}] {v.scenario_id}"
            for v in report.scenario_verdicts
            if v.status == "fail"
        )
    )


@pytest.mark.live_llm
def test_llm_review_corpus_via_replay_cli() -> None:
    """Run the llm_review scenario corpus through the in-process replay() surface.

    Calls replay() with include_categories={"llm_review"} and asserts that
    all scenarios pass. llm_review is out-of-bucket for included_accuracy,
    so we assert directly on scenario_verdicts status.

    The --strict / assertion_failure path is removed. Disposition
    correctness for llm_review is now surfaced via scenario status (the
    engine content gate).
    """
    report: AccuracyReport = replay(_CORPUS_DIR, include_categories={"llm_review"})

    failures = [v for v in report.scenario_verdicts if v.status == "fail"]
    assert not failures, (
        f"LLM review corpus replay failed: {len(failures)} scenario(s) failed. "
        + ", ".join(f"[{v.category}] {v.scenario_id}" for v in failures)
    )
