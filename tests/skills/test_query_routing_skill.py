"""Slice 1: Tests for the query-routing skill spec."""

from __future__ import annotations

from samantha_server.skills.loader import discover, load

# ---------------------------------------------------------------------------
# Slice 1: query-routing skill spec exists and is discoverable
# ---------------------------------------------------------------------------


def test_discover_includes_query_routing() -> None:
    """query-routing skill is present in the default specs index."""
    index = discover()
    assert "query-routing" in index


def test_query_routing_description_is_present() -> None:
    """query-routing has a non-empty description."""
    index = discover()
    spec = index["query-routing"]
    assert isinstance(spec.description, str)
    assert spec.description.strip()


def test_query_routing_body_contains_cite_scenarios() -> None:
    """Skill body instructs LLM to cite scenarios by ID."""
    body = load("query-routing")
    # Body must describe the cite-by-ID instruction
    assert "scenario" in body.lower()


def test_query_routing_body_does_not_mention_propose_transition() -> None:
    """Skill body must NOT instruct the LLM to propose state transitions."""
    body = load("query-routing")
    assert "propose_transition" not in body
    assert "next_state" not in body


def test_query_routing_body_contains_free_text_response_instruction() -> None:
    """Skill body instructs LLM to return free-text response."""
    body = load("query-routing")
    # The body should reference a free-text response
    lower = body.lower()
    assert "free-text" in lower or "free text" in lower or "response" in lower


def test_query_routing_skill_path_is_file() -> None:
    """Skill spec path resolves to an existing file."""
    index = discover()
    spec = index["query-routing"]
    assert spec.path.is_file()


def test_load_query_routing_strips_frontmatter() -> None:
    """Loaded body does not start with --- frontmatter fence."""
    body = load("query-routing")
    assert not body.strip().startswith("---")
    assert "name: query-routing" not in body


# ---------------------------------------------------------------------------
# JSON-output contract tests
# ---------------------------------------------------------------------------


def test_query_routing_body_instructs_json_only_output() -> None:
    """Skill body instructs LLM to output JSON only (no markdown fences)."""
    body = load("query-routing")
    lower = body.lower()
    # Must reference JSON output — the contract specifies JSON-only output
    assert "json" in lower


def test_query_routing_body_documents_answer_type_field() -> None:
    """Skill body documents the answer_type field."""
    body = load("query-routing")
    assert "answer_type" in body


def test_query_routing_body_has_contamination_audit_marker() -> None:
    """Skill body retains the contamination-audit sentinel comment."""
    from pathlib import Path

    skill_path = (
        Path(__file__).resolve().parents[2]
        / "samantha_server"
        / "skills"
        / "specs"
        / "query_routing"
        / "SKILL.md"
    )
    raw = skill_path.read_text(encoding="utf-8")
    assert "contamination-audit:" in raw


# ---------------------------------------------------------------------------
# Domain-aware prompt structure — state reference, flag reference,
# answer-type guidance, and query idioms.
# ---------------------------------------------------------------------------


def test_query_routing_body_contains_state_reference() -> None:
    """/ PR-219 #11: Skill body contains ALL VALID_STATES.

    Driven from samantha_server.models.context.VALID_STATES so future state additions
    automatically red this test.
    """
    from samantha_server.models.context import VALID_STATES

    body = load("query-routing")
    assert "## State Reference" in body
    for state in VALID_STATES:
        assert state in body, f"State {state!r} missing from skill State Reference"


def test_query_routing_body_contains_flag_reference() -> None:
    """/ PR-219 #12: Skill body contains ALL VALID_FLAGS.

    Driven from samantha_server.models.context.VALID_FLAGS so future flag additions
    automatically red this test.
    """
    from samantha_server.models.context import VALID_FLAGS

    body = load("query-routing")
    assert "## Flag Reference" in body
    for flag in VALID_FLAGS:
        assert flag in body, f"Flag {flag!r} missing from skill Flag Reference"


def test_query_routing_body_contains_answer_type_guidance() -> None:
    """/ PR-219 #16+#20: Skill body has an Answer-type guidance section.

    All five subsection headers must be present:
    ### order_list, ### order_status, ### no_orders, ### uncertain, ### prioritized_list.
    Anchored on section headers rather than sentence wording (PR-219 #20).
    prioritized_list subsection added.
    """
    body = load("query-routing")
    assert "## Answer-type guidance" in body
    # PR-219 #20: anchor on section header, not sentence wording
    assert "### order_list" in body
    # order_status subsection added
    assert "### order_status" in body
    # PR-219 #16: all subsection headers must be present
    assert "### no_orders" in body
    assert "### uncertain" in body
    # prioritized_list subsection added
    assert "### prioritized_list" in body


def test_query_routing_body_contains_order_status_guidance() -> None:
    """Skill body contains ### order_status guidance.

    The section must direct the model to use this answer_type for queries
    about ONE specific order.
    """
    body = load("query-routing")
    assert "### order_status" in body
    # The guidance must contain the key discriminating phrase
    order_status_section = _text_near("### order_status", body, after=600)
    assert "ONE specific order" in order_status_section, (
        "order_status guidance must direct the model to use it for ONE specific order"
    )


def test_query_routing_body_contains_query_idioms() -> None:
    """/ PR-219 #17: Skill body contains all 9 idiom rows in the Query Idioms section."""
    import re

    body = load("query-routing")
    assert "## Query Idioms" in body
    # QR-001: "ready for grossing" must co-occur with ACCEPTED (PR-219 #18)
    assert re.search(r'"ready for grossing".*ACCEPTED', body), (
        '"ready for grossing" and ACCEPTED must co-occur on the same idiom row'
    )
    # PR-219 #21: drop the redundant body.lower() check; keep SAMPLE_PREP
    assert "SAMPLE_PREP" in body
    # PR-219 #17: all 9 natural-language phrases must be present
    for phrase in (
        "ready for grossing",
        "in sample prep",
        "needs H&E QC",
        "waiting for pathologist to review H&E",
        "in IHC",
        "ready for sign-out",
        "on hold",
        "waiting on external results",
        "needs pathologist attention",
    ):
        assert phrase in body, f"Idiom phrase {phrase!r} missing from Query Idioms section"


# ---------------------------------------------------------------------------
# PR-219 review-fix: Cluster A — State / Flag reference content corrections
# ---------------------------------------------------------------------------


def test_query_routing_accepted_description_does_not_mention_grossing() -> None:
    """PR-219 #1: ACCEPTED State Reference row must not describe the state as 'awaiting grossing'.

    'grossing' is not a samantha_server state boundary. The canonical description
    is 'ready to advance to sample prep'. The Query Idioms gloss 'grossing orders' is fine.
    """
    import re

    body = load("query-routing")
    # Isolate the State Reference section (before ## Flag Reference)
    state_ref_section = body.split("## Flag Reference")[0]
    # Find the ACCEPTED bullet line and check it does not carry 'grossing'
    for line in state_ref_section.splitlines():
        if re.search(r"`ACCEPTED`", line):
            assert "grossing" not in line.lower(), (
                f"State Reference ACCEPTED description still carries 'grossing': {line!r}"
            )


def test_query_routing_resulting_hold_describes_ordering_side_info() -> None:
    """PR-219 #2: RESULTING_HOLD row must reference MISSING_INFO_PROCEED (ordering-side info).

    The old description said 'external information' — that is FISH_SEND_OUT semantics.
    """
    body = load("query-routing")
    # The RESULTING_HOLD section must mention MISSING_INFO_PROCEED to anchor the semantics.
    assert "MISSING_INFO_PROCEED" in body.split("## Flag Reference")[0].split("RESULTING_HOLD")[
        1
    ].split("\n")[0] or ("MISSING_INFO_PROCEED" in _text_near("RESULTING_HOLD", body, after=200))


def _text_near(anchor: str, text: str, after: int = 200) -> str:
    """Return up to `after` characters of `text` immediately following `anchor`."""
    idx = text.find(anchor)
    if idx == -1:
        return ""
    return text[idx : idx + len(anchor) + after]


def test_query_routing_fish_suggested_actor_is_system() -> None:
    """PR-219 #3: FISH_SUGGESTED flag row must state the actor is the system (IHC-007).

    The old description said 'pathologist has flagged' — wrong actor.
    """
    body = load("query-routing")
    fish_section = _text_near("FISH_SUGGESTED", body, after=200)
    assert "IHC-007" in fish_section, "IHC-007 rule reference missing from FISH_SUGGESTED row"
    assert "pathologist has flagged" not in fish_section.lower(), (
        "Wrong actor phrase 'pathologist has flagged' still present in FISH_SUGGESTED row"
    )


def test_query_routing_fixation_warning_documents_no_emitter() -> None:
    """PR-219 #6: FIXATION_WARNING row must note that no emitter rule exists yet."""
    body = load("query-routing")
    flag_section = _text_near("FIXATION_WARNING", body, after=300)
    assert "emitter pending" in flag_section.lower() or "no live orders" in flag_section.lower(), (
        "FIXATION_WARNING row does not document that no emitter rule exists"
    )


def test_query_routing_order_terminated_distinguishes_qns_variant() -> None:
    """PR-219 #7: plain ORDER_TERMINATED row must NOT carry 'specimen inadequate' / 'QNS' wording.

    That semantic belongs exclusively to ORDER_TERMINATED_QNS.
    """
    body = load("query-routing")
    # Isolate the plain ORDER_TERMINATED bullet (not the QNS variant)
    # Find the line containing ORDER_TERMINATED that is NOT ORDER_TERMINATED_QNS

    for line in body.splitlines():
        if "ORDER_TERMINATED" in line and "QNS" not in line and line.strip().startswith("-"):
            assert "inadequate" not in line.lower(), (
                f"Plain ORDER_TERMINATED line carries 'inadequate' semantic: {line!r}"
            )
            assert "quantity not sufficient" not in line.lower(), (
                f"Plain ORDER_TERMINATED line carries QNS description: {line!r}"
            )


# ---------------------------------------------------------------------------
# PR-219 review-fix: Cluster B — Query Idioms corrections
# ---------------------------------------------------------------------------


def test_query_routing_on_hold_idiom_excludes_fish_send_out() -> None:
    """PR-219 #4: 'on hold'/'blocked' idiom row must NOT list FISH_SEND_OUT.

    Per QR-006 ground truth, on-hold means MISSING_INFO_HOLD + RESULTING_HOLD only.
    """
    import re

    body = load("query-routing")
    # Find the on-hold table row
    m = re.search(r'"on hold"[^\n]*', body)
    assert m is not None, '"on hold" idiom row not found'
    row = m.group(0)
    assert "FISH_SEND_OUT" not in row, (
        f"FISH_SEND_OUT incorrectly present in 'on hold' idiom row: {row!r}"
    )


def test_query_routing_external_results_idiom_only_fish_send_out() -> None:
    """PR-219 #5: 'waiting on external results' idiom must NOT list RESULTING_HOLD.

    Per QR-027 ground truth, external results means FISH_SEND_OUT only.
    """
    import re

    body = load("query-routing")
    m = re.search(r'"waiting on external results"[^\n]*', body)
    assert m is not None, '"waiting on external results" idiom row not found'
    row = m.group(0)
    assert "RESULTING_HOLD" not in row, (
        f"RESULTING_HOLD incorrectly present in 'waiting on external results' row: {row!r}"
    )


def test_query_routing_idiom_intro_references_state_reference() -> None:
    """PR-219 #10: Query Idioms section must have an intro line referencing the State Reference."""
    body = load("query-routing")
    idioms_section = body.split("## Query Idioms")[1] if "## Query Idioms" in body else ""
    assert "State Reference" in idioms_section, (
        "Query Idioms section lacks intro line referencing the State Reference"
    )


# ---------------------------------------------------------------------------
# PR-219 review-fix: Cluster C — Answer-type guidance
# ---------------------------------------------------------------------------


def test_query_routing_order_list_documents_zero_match_fallback() -> None:
    """PR-219 #9: order_list section must instruct the model to use no_orders when zero match."""
    body = load("query-routing")
    order_list_section = _text_near("### order_list", body, after=600)
    assert "no_orders" in order_list_section, (
        "order_list section does not document the zero-match fallback to no_orders"
    )


def test_query_routing_uncertain_defined_once() -> None:
    """PR-219 #8: 'uncertain' canonical definition appears in exactly one section header.

    The Behavior contract may reference it by name (e.g. answer_type = "uncertain") but
    the ### uncertain subsection header (canonical definition site) must appear exactly once.
    """
    body = load("query-routing")
    count = body.count("### uncertain")
    assert count == 1, f"Expected exactly one '### uncertain' subsection; found {count}"


# ---------------------------------------------------------------------------
# PR-219 review-fix: Cluster E — Bundled test Lows
# ---------------------------------------------------------------------------


def test_query_routing_body_contains_prioritized_list_guidance() -> None:
    """PR224 Cluster C (#6): Skill body has ### prioritized_list guidance with all ranking rules.

    All three ranking sort keys (rush/priority, flags, age) and the worked example
    (B/C/A/D pattern with "Correct ranking") must be present. A future edit dropping
    the flags/age paragraphs or the worked example would not red any test without this.
    """
    body = load("query-routing")
    assert "### prioritized_list" in body
    prioritized_section = _text_near("### prioritized_list", body, after=2000)
    assert "ranked" in prioritized_section.lower() or "top" in prioritized_section.lower(), (
        "prioritized_list section must describe ranking semantics"
    )
    # All three ranking sort keys must be documented
    assert "rush" in prioritized_section.lower(), (
        "prioritized_list section must document rush priority sort key (key 1)"
    )
    assert "flag" in prioritized_section.lower(), (
        "prioritized_list section must document flags sort key (key 2)"
    )
    assert "age" in prioritized_section.lower() or "older" in prioritized_section.lower(), (
        "prioritized_list section must document age sort key (key 3)"
    )
    # Worked example must be present (B/C/A/D ranking with "Correct ranking" marker)
    assert "Correct ranking" in prioritized_section, (
        "prioritized_list section must contain a worked example with 'Correct ranking' marker"
    )


def test_query_routing_prioritized_list_documents_top_n_tier_inclusion() -> None:
    """### prioritized_list section must document the top-N tier-inclusion rule.

    QR-022 regression: 2 of 3 sweeps emit [ORD-2202, ORD-2203, ORD-2201], dropping
    rush ORD-2204 and pulling in routine ORD-2201. Root cause: no explicit rule in
    the skill body stating that ALL rush orders must be included before ANY routine
    orders when building a top-N list.

    Anchored on the rule paragraph name + the two edge-case sub-bullets + the
    Wrong-answer counter-example, so each structural element of the new content
    must survive independently.
    """
    body = load("query-routing")
    # Bumped window from 2000 → 3000 to accommodate the new
    # LIS-pre-sort invariant paragraph added between the section heading
    # and the Top-N tier-inclusion rule. The window must reach the
    # "Wrong answer" counter-example at the end of the section.
    section = _text_near("### prioritized_list", body, after=3000)
    lower = section.lower()

    # Paragraph name (mirrors "Field-not-in-prompt" anchor).
    assert "Top-N tier-inclusion rule" in section, (
        "### prioritized_list section must name the 'Top-N tier-inclusion rule' paragraph"
    )

    # Rule clause — pin "top-N" (skill uses this phrase exclusively) and the "all rush"
    # invariant. Both checks are case-insensitive so a copy-edit lowercasing one doesn't
    # red the test asymmetrically.
    assert "top-n" in lower, (
        "### prioritized_list section must contain the top-N tier-inclusion phrase"
    )
    assert "all rush" in lower, (
        "### prioritized_list section must state that all rush orders are included before "
        "any routine orders in a top-N selection"
    )

    # Two edge-case sub-bullets — one assertion per bullet so dropping either reds.
    # The fewer-rush-than-N branch is the exact QR-022 failure shape.
    assert "more rush" in lower, (
        "### prioritized_list section must document the more-rush-than-N sub-case"
    )
    assert "fewer rush" in lower, (
        "### prioritized_list section must document the fewer-rush-than-N sub-case "
        "(the QR-022 regression shape)"
    )

    # Second worked example must exist beyond the original B/C/A/D one.
    assert section.count("Correct ranking") >= 2, (
        "### prioritized_list section must contain at least two worked examples "
        "('Correct ranking' marker must appear at least twice)"
    )

    # Wrong-answer counter-example must survive — it names the QR-022 failure mode.
    assert "Wrong answer" in section, (
        "### prioritized_list section must include the 'Wrong answer' counter-example "
        "naming the tier-violation failure mode"
    )


def test_query_routing_prioritized_list_documents_pre_sort_lis_invariant() -> None:
    """### prioritized_list section must document that the `<orders>` block
    is pre-sorted in LIS-style priority/age order.

    Background: every non-Gemma candidate in the 2026-05-15 model retest
    failed QR-020 / QR-021 (sequence-ordering queries). Pre-sorting at
    scaffolding time matches real LIS semantics (a `SELECT ... ORDER BY`
    would return ordered rows). The SKILL.md must tell the model the orders
    are already sorted — without this guidance the model defaults to re-
    sorting defensively (QR-021 shape: rush orders swapped despite correct
    pre-sort).

    Anchored on the paragraph name + the load-bearing "do not re-sort"
    clause so a copy-edit dropping the guidance reds the test.
    """
    body = load("query-routing")
    section = _text_near("### prioritized_list", body, after=2500)
    lower = section.lower()

    # Paragraph anchor — name the LIS pre-sort invariant explicitly.
    assert "LIS pre-sort" in section or "LIS-side pre-sort" in section, (
        "### prioritized_list section must name the LIS pre-sort invariant "
        " so a future copy-edit can't silently drop the guidance"
    )

    # Load-bearing clause: orders are already sorted.
    assert "already" in lower and "sorted" in lower, (
        "### prioritized_list section must state that the `<orders>` block "
        "is already in priority/age order"
    )

    # Anti-pattern clause: don't re-sort.
    assert "do not re-sort" in lower or "not re-sort" in lower, (
        "### prioritized_list section must instruct the model NOT to re-sort "
        "the `<orders>` block (this is the QR-021 failure mode where the model "
        "swaps the first two rush orders despite correct pre-sort)"
    )


def test_query_routing_body_documents_order_ids_emptiness_correctly() -> None:
    """Cluster E (#4): SKILL.md must not contain the stale order_ids description.

    The phrase 'empty array when not order_list' is stale — order_status also
    uses a non-empty order_ids array (the subject order_id). The correct wording
    is 'empty when answer_type is "no_orders" or "uncertain"'. Negative test
    modeled on existing negative patterns in this file.
    """
    body = load("query-routing")
    assert "empty array when not order_list" not in body, (
        "SKILL.md still contains stale order_ids description 'empty array when not order_list'; "
        'update to \'empty when answer_type is "no_orders" or "uncertain"\''
    )


# ---------------------------------------------------------------------------
# User role section in skill body
# ---------------------------------------------------------------------------


def test_query_routing_body_contains_user_role_section() -> None:
    """Skill body contains a ## User role section."""
    body = load("query-routing")
    assert "## User role" in body


def test_query_routing_body_user_role_section_mentions_all_four_roles() -> None:
    """User role section documents all four valid role names.

    after=1200 because the PR243 S12 clarification paragraph added ~300 chars
    before the role semantics list, pushing 'pathologist' past the old 800-char window.
    """
    body = load("query-routing")
    assert "## User role" in body
    user_role_section = _text_near("## User role", body, after=1200)
    for role in ("accessioner", "histotech", "pathologist", "lab_manager"):
        assert role in user_role_section, f"Role {role!r} missing from ## User role section"


def test_query_routing_body_user_role_section_documents_absent_block() -> None:
    """User role section states behavior when the block is absent."""
    body = load("query-routing")
    user_role_section = _text_near("## User role", body, after=1200)
    # Must mention what happens when the block is absent
    assert "absent" in user_role_section.lower() or "not present" in user_role_section.lower(), (
        "User role section must document behavior when <user_role> block is absent"
    )


def test_query_routing_uncertain_section_documents_field_not_in_prompt() -> None:
    """### uncertain section must document the field-not-in-prompt pattern.

    QR-029 ("Is billing complete for ORD-291?") mis-routes to order_status because
    "is X complete" pattern-matches as state-of-the-order. The fix is skill-body
    sharpening: ### uncertain must explicitly call out that if the data needed to
    answer the query is not in the <orders> block, the answer is `uncertain` —
    even if phrased as a state-of-order question. Concrete example categories
    (billing/payment, scheduling, external-result, demographics) ground the rule.
    """
    body = load("query-routing")
    # after=2400: the section is ~2300 chars post-PR252 review fixes (rule paragraph +
    # four category bullets — billing carries a RESULTING_HOLD / MISSING_INFO_HOLD
    # carve-out — plus three counter-examples). The window must capture the full
    # section all the way through the counter-examples block; bump if the section
    # grows further. Tracks `awk '/^### uncertain/,/^## Query Idioms/'` length.
    section = _text_near("### uncertain", body, after=2400)

    # Anchor on the rule paragraph itself — deleting the rule while leaving the
    # example bullets intact must red the test.
    assert "Field-not-in-prompt" in section, (
        "### uncertain section must name the 'Field-not-in-prompt rule' paragraph"
    )

    # Four category bullets — one assertion per category so dropping any bullet reds.
    assert "billing" in section.lower(), (
        "### uncertain section must include the billing/payment example (QR-029 pattern)"
    )
    assert "schedul" in section.lower(), "### uncertain section must include the scheduling example"
    assert "external" in section.lower(), (
        "### uncertain section must include the external-result example"
    )
    assert "demograph" in section.lower(), (
        "### uncertain section must include the patient-demographics example"
    )

    # Counter-example block — anchor on a field name that pins the order_status side
    # of the boundary; deletion of the counter-examples block would red this.
    assert "current_state" in section, (
        "### uncertain section must keep counter-examples naming current_state so the "
        "order_status side of the boundary stays explicit"
    )

    # Data-source-boundary phrasing — orders-block reference must remain.
    assert "<orders>" in section or "orders block" in section.lower(), (
        "### uncertain section must name the <orders> block as the data source boundary"
    )


def test_query_routing_skill_contains_no_backtick_fences() -> None:
    """Skill body must not contain markdown code fences.

    The skill says "no markdown fences" but previously showed the example
    wrapped in ```json ... ```, which ICL models follow over the instruction.
    """
    from pathlib import Path

    skill_path = (
        Path(__file__).resolve().parents[2]
        / "samantha_server"
        / "skills"
        / "specs"
        / "query_routing"
        / "SKILL.md"
    )
    raw = skill_path.read_text(encoding="utf-8")
    assert "```" not in raw, (
        "SKILL.md contains backtick fences, which contradict the 'no markdown fences' instruction"
    )


def test_query_routing_prioritized_list_documents_state_filtered_top_n() -> None:
    """### prioritized_list section must include a worked example showing
    state-filtered top-N selection over a mixed-state pre-sorted block.

    Background: (Phase A) landed the pre-sort + "walk top-down"
    guidance. QR-020 + QR-021 flipped 0/5 → 5/5 cleanly. QR-022 newly fails
    0/5: model returns ORD-2205 (Jan 14) instead of ORD-2203 (Jan 12) in
    slot 3 — picks the *newer* of the routine-state-matching orders instead
    of the *older*, despite the existing Top-N tier-inclusion rule saying
    "fill with oldest routines."

    The failure is structurally distinct from QR-020/021. The pre-sort got
    everything in the right order; the state filter eliminated some
    candidates; but when filling the routine tier the model skipped past
    the correct (earlier-positioned, older) routine and grabbed a later
    one. Add a worked example showing this exact shape so the model has a
    structurally-identical pattern to follow.

    Anchored on the example name + key prose so a copy-edit dropping any
    piece reds the test.
    """
    body = load("query-routing")
    # Bumped after=4500 → 5500 to cover the full section
    # with headroom. Section runs ~4730 chars after the example
    # addition; 4500 left the wrong-answer line right at the boundary, so
    # a modest upstream edit could push it past the cut. 5500 gives ~770
    # chars of slack for future additions.
    section = _text_near("### prioritized_list", body, after=5500)

    # Worked example must carry an identifying anchor name.
    assert "State-filtered top-N" in section or "state-filtered top-n" in section.lower(), (
        "### prioritized_list section must contain a 'State-filtered top-N' worked "
        "example — the QR-022 failure shape"
    )

    # Pin the specific "Correct ranking" line of the new
    # example, not just the count. A rename like "Expected ranking" would
    # silently pass the count check via the other two examples (B/C/A/D and
    # E/F/G); the literal-string anchor catches that.
    assert "Correct ranking: K, L, N" in section, (
        "### prioritized_list state-filtered example must show the K/L/N "
        "correct-ranking line; a rename would silently pass the "
        "earlier count-based anchor via the other two examples"
    )

    # The example must show mixed-state orders in the pre-sorted block AND
    # the correct top-3 walk. Anchor on a third worked-example marker
    # (existing two are B/C/A/D and E/F/G). Counts as a structural anchor.
    assert section.count("Correct ranking") >= 3, (
        "### prioritized_list section must contain at least 3 worked examples "
        "with 'Correct ranking' markers; the state-filtered example is the third"
    )

    # The example must include a Wrong-answer counter-example pointing at the
    # QR-022 failure mode (picking a NEWER routine over an OLDER one in the
    # same tier). review #3: tightened from bare "newer" / "older"
    # (could match elsewhere in the window) to the qualified phrases that are
    # unique to the QR-022 failure mode.
    lower = section.lower()
    assert "wrong answer" in lower and "newer routine" in lower and "older routine" in lower, (
        "### prioritized_list state-filtered example must include a Wrong-answer "
        "counter-example naming the 'newer vs older within tier' failure shape (QR-022)"
    )
