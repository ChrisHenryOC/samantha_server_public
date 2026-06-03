"""Architectural test: closed allow-list for no-receipt code paths.

Per Phase 3 Step 3 plan: every code path that returns an EngineDecision
emits exactly one receipt. The two enumerated exceptions return no
EngineDecision: back-pressure rejection (Step 3) and pre-dequeue
cancellation (Step 2). Adding a third no-receipt path requires PR review
that explicitly extends both NO_RECEIPT_PATHS and this assertion.

Limitation: this test enforces the *manifest* — it catches typos and
accidental shrinks of the closed set. It does NOT catch a new
no-receipt code path that fails to register itself in the manifest.
That gap must be closed by reviewer attention on every PR that adds
a new early-return into the request-handling pipeline.
"""

from __future__ import annotations


def test_no_receipt_paths_allowlist_is_closed() -> None:
    """Pinned set: any change to NO_RECEIPT_PATHS must update this test."""
    from samantha_server.api.no_receipt_paths import NO_RECEIPT_PATHS

    assert (
        frozenset(
            {
                "backpressure_rejection",
                "pre_dequeue_cancellation",
            }
        )
        == NO_RECEIPT_PATHS
    )
