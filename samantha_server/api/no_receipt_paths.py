"""Closed allow-list of documented no-receipt code paths.

Per the Phase 3 receipt contract: every code path that returns an
EngineDecision emits exactly one receipt. The paths enumerated here are
the only documented exceptions — they return no EngineDecision and
therefore emit no receipt.

What the architectural test catches: manifest typos and accidental
shrinks of the closed set (e.g., removing an entry without removing
the corresponding code path). What it does NOT catch: a third
no-receipt code path silently added without registering itself here.
That requires reviewer attention — there is no AST walker today
that maps every "early return without receipt" back to this manifest.

Adding a new no-receipt path requires:
  1. Adding its identifier to ``NoReceiptPath`` and ``NO_RECEIPT_PATHS``
     here, with a comment naming the call site and rationale.
  2. Updating ``tests/architectural/test_no_receipt_allowlist.py`` to
     pin the new closed set. The architectural test asserts equality
     against the literal set, so a PR that adds a third path without
     updating both files fails CI.
"""

from __future__ import annotations

from typing import Literal

# Closed-set type alias. Consumers that reference a no-receipt path by
# string should accept ``NoReceiptPath`` rather than bare ``str`` so a
# typo fails mypy at the call site instead of silently missing the check.
NoReceiptPath = Literal["backpressure_rejection", "pre_dequeue_cancellation"]

NO_RECEIPT_PATHS: frozenset[NoReceiptPath] = frozenset(
    {
        # Call site: enqueue_or_reject (samantha_server.api.backpressure).
        # Rationale: back-pressure rejection returns HTTP 503 before an
        # EngineDecision is produced; no event enters the engine pipeline.
        "backpressure_rejection",
        # Call site: PriorityEventQueue.cancel (samantha_server.queue.priority).
        # Rationale: pre-dequeue cancellation removes the event before any
        # consumer dequeues it; no EngineDecision is produced.
        "pre_dequeue_cancellation",
    }
)

__all__ = ["NO_RECEIPT_PATHS", "NoReceiptPath"]
