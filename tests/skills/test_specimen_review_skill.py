"""Tests for the specimen-review skill spec."""

from __future__ import annotations

from samantha_server.skills.loader import discover, load


def _section_between(start_anchor: str, body: str, end_marker: str = "\n## ") -> str:
    """Return body slice from `start_anchor` up to the next `end_marker`.

    Section-bounded slicing prevents a misplaced edit in a neighbouring
    section from silently satisfying anatomic-site anchor assertions
    (PR #267 review finding #3).
    """
    start_idx = body.find(start_anchor)
    if start_idx == -1:
        return ""
    end_idx = body.find(end_marker, start_idx + len(start_anchor))
    return body[start_idx:end_idx] if end_idx != -1 else body[start_idx:]


def test_discover_includes_specimen_review() -> None:
    """specimen-review skill is present in the default specs index."""
    index = discover()
    assert "specimen-review" in index


def test_specimen_review_anatomic_site_documents_internal_code_worked_example() -> None:
    """GH-265: anatomic-site escalate section must include an opaque-internal-code worked example.

    LR-006 regression: model accepts the novel anatomic_site `IL-3-bx` instead of
    escalating (0/5 stable failure on gemma-4-26B-A4B-it-MLX-4bit, full-corpus
    N=5 baseline 2026-05-15). The existing escalate criterion already names
    "internal codes" but lacks a worked example; the model isn't honoring the
    criterion in practice.

    Anchored on the paragraph name + the worked example structure (input site,
    escalated disposition, rationale citing the opaque-code criterion). Each
    structural element gets its own assertion so a copy-edit dropping one
    surfaces an isolated failure.
    """
    body = load("specimen-review")
    section = _section_between("## Anatomic site disposition", body)
    assert section, "## Anatomic site disposition heading not found in skill body"
    lower = section.lower()

    # Anchor: the escalate sub-section must name internal codes as a trigger.
    assert "internal code" in lower, (
        "## Anatomic site disposition section must name 'internal code' as an escalate trigger"
    )

    # The worked example must demonstrate the letters-digit-suffix shape of an
    # opaque internal lab code. Don't reuse the corpus token `IL-3-bx` — the
    # contamination-hygiene point of the sentinel is defeated if a future edit
    # copies the corpus token into the skill body.
    assert "IL-3-bx" not in section, (
        "anatomic-site worked example must not reuse the LR-006 corpus token 'IL-3-bx' "
        "(use a structurally-equivalent sentinel instead)"
    )

    # The worked example must visibly carry a letters-digits-suffix opaque
    # token so the pattern is teachable. Pin the structural anchors so each
    # piece reds in isolation if dropped.
    assert "Worked example" in section or "worked example" in section, (
        "## Anatomic site disposition section must contain a worked example block"
    )
    assert '"escalated"' in section, (
        "anatomic-site worked example must show disposition='escalated' as the JSON output"
    )
    assert "opaque" in lower, (
        "anatomic-site worked example rationale must call the code 'opaque' to mirror "
        "the SOP escalate criterion"
    )
    # Pin the Wrong-answer counter-example (mirrors the QR-022 precedent).
    assert "Wrong answer" in section, (
        "anatomic-site worked example must include a 'Wrong answer' counter-example "
        "naming the SOP human-review skip"
    )


def test_specimen_review_anatomic_site_worked_example_has_no_backtick_fences() -> None:
    """PR #267 review #1: anatomic-site worked example must not use markdown fences.

    The `### Behavior contract` instructs the LLM to emit "no markdown fences";
    ICL models follow in-context examples over instructions, so fenced examples
    in the skill body teach the wrong output shape. Query-routing resolved this
    in `test_query_routing_skill_contains_no_backtick_fences` after the QR-022
    fix. Scoped to the anatomic-site section so this PR does not regress on the
    pre-existing fenced `### Examples` block at the file bottom (out of scope
    for GH-265; deferred to a follow-up).
    """
    body = load("specimen-review")
    # Bound tightly at `### Behavior contract` (the next heading after the
    # worked example) so the pre-existing fenced `### Examples` block at file
    # bottom stays out of scope — its fence-strip is a separate follow-up.
    section = _section_between(
        "## Anatomic site disposition", body, end_marker="\n### Behavior contract"
    )
    assert section, "## Anatomic site disposition heading not found"
    assert "```" not in section, (
        "anatomic-site worked example must not contain backtick fences — they "
        "contradict the '### Behavior contract' instruction and teach the wrong "
        "output shape via in-context-learning mimicry"
    )


def test_specimen_review_skill_path_is_file() -> None:
    """Skill spec path resolves to an existing file."""
    index = discover()
    spec = index["specimen-review"]
    assert spec.path.is_file()
