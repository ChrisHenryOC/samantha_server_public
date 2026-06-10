""" + query/ fixtures admitted as LLM-path scenarios.

 wired the original 8 ``answer_type == "order_list"`` fixtures.
 extended the wireup to the remaining 19 fixtures (covering
``order_status``, ``prioritized_list``, and the 5 remaining
``order_list`` fixtures). The full set of 27 carries an ``events`` key
so the corpus loader returns them as full Scenario objects.

 acceptance set (8 fixtures): the original ``answer_type ==
"order_list"`` subset used by the per-id assertion path on the
live_llm sweep — QR-001/002/003/005/006/007/008/023.

 extension set (11 fixtures): the remaining wired fixtures —
QR-004 was already in the override list pre-PR; the actual 11 newly
admitted are QR-009/010/011/013/014/015/018/020/022/026/027.

Test gate semantics:
- ``_LOCKED_QUERY_PICKS`` — the 8 -locked picks. The
  ``database_state.orders`` ↔ ``events[0].event_data.orders`` invariant
  test applies to this subset only because the prompt-injection
  path was designed around them.
- ``_FULL_QUERY_PICKS`` — the union of 29 admitted picks (QR-001..QR-029;
  QR-028 + QR-029 added by for the no_orders/uncertain gate
  branches). Used by the presence test that guards against accidental
  removal of any wired fixture from the loader's output.
"""

from __future__ import annotations

from pathlib import Path

_LOCKED_QUERY_PICKS: frozenset[str] = frozenset(
    {"QR-001", "QR-002", "QR-003", "QR-005", "QR-006", "QR-007", "QR-008", "QR-023"}
)

# Full set of 29 query fixtures wired into the replay harness. QR-001..QR-027
# came in; QR-028 + QR-029 were added by / to cover
# the no_orders / uncertain answer_type gate branches. All carry an ``events``
# key. Adding a new query fixture without an ``events`` key (or removing one's
# events block) will fail ``test_full_query_picks_load_with_events_key`` below.
_FULL_QUERY_PICKS: frozenset[str] = frozenset(f"QR-{n:03d}" for n in range(1, 30))


# Each picked fixture's events[0].expected_output.next_state is
# "ACCESSIONING". This is correct but coupled to two implementation details:
#   1. ``handle_clinical_query`` returns ``next_state=ctx.current_state``
#      (queries don't transition). See ``samantha_server/llm/handlers.py``.
#   2. The replay harness seeds ``current_state="ACCESSIONING"`` for every
#      scenario at step 0 (``samantha_server/scenarios/replay.py:506``).
# Together those produce ``predicted_next_state == "ACCESSIONING"``, matching
# the fixture's expected_output. If either invariant changes, every query
# fixture's expected_output.next_state must be revisited.
_QUERY_EXPECTED_NEXT_STATE: str = "ACCESSIONING"


def test_locked_query_picks_load_with_events_key() -> None:
    """All 8 picked query fixtures must round-trip through the scenario loader.

    Previously the loader silently skipped event-less query fixtures
    (loader.py: ``_is_recognized_non_workflow``). This test fails until
    each picked fixture grows a top-level ``events`` key.
    """
    from samantha_server.scenarios.loader import load_scenarios

    fixture_dir = Path(__file__).parent.parent / "fixtures" / "scenarios" / "query"
    scenarios = load_scenarios(fixture_dir)
    loaded_ids = {s.scenario_id for s in scenarios}

    missing = _LOCKED_QUERY_PICKS - loaded_ids
    assert not missing, (
        f"Query fixtures {sorted(missing)} have no `events` key; "
        f"loader silently skips them and they don't contribute to the "
        f"LLM-path latency bucket."
    )


def test_full_query_picks_load_with_events_key() -> None:
    """all 27 query fixtures (the set plus the 19
    additions) must round-trip through the scenario loader.

    A regression that drops an ``events`` key from any fixture (e.g. an
    accidental re-sync of upstream samantha-public over the wired files)
    would silently lose that fixture from the replay corpus. This test
    catches the drop before the replay run silently underreports.
    """
    from samantha_server.scenarios.loader import load_scenarios

    fixture_dir = Path(__file__).parent.parent / "fixtures" / "scenarios" / "query"
    scenarios = load_scenarios(fixture_dir)
    loaded_ids = {s.scenario_id for s in scenarios}

    missing = _FULL_QUERY_PICKS - loaded_ids
    assert not missing, (
        f"query fixtures {sorted(missing)} have no `events` key; "
        f"loader silently skips them and they drop out of the corpus."
    )


def test_locked_query_picks_have_clinical_query_event_type() -> None:
    """Each admitted fixture's events key drives a clinical_query through dispatch."""
    from samantha_server.scenarios.loader import load_scenarios

    fixture_dir = Path(__file__).parent.parent / "fixtures" / "scenarios" / "query"
    scenarios = {s.scenario_id: s for s in load_scenarios(fixture_dir)}

    for picked in sorted(_LOCKED_QUERY_PICKS):
        scenario = scenarios.get(picked)
        # Assert presence rather than silent-skip — keeps
        # this test independently correct under reordered/isolated execution.
        assert scenario is not None, f"{picked}: scenario not loaded"
        assert scenario.steps, f"{picked}: must have at least one step"
        first = scenario.steps[0]
        assert first.event_type == "clinical_query", (
            f"{picked}: step.event_type must be 'clinical_query' so the "
            f"replay harness routes through dispatch_event; "
            f"got {first.event_type!r}"
        )


def test_locked_query_picks_event_data_carries_query_and_orders() -> None:
    """Each step's event_data must carry the query string + orders list so
    the prompt-injection path produces a meaningful LLM round-trip."""
    from samantha_server.scenarios.loader import load_scenarios

    fixture_dir = Path(__file__).parent.parent / "fixtures" / "scenarios" / "query"
    scenarios = {s.scenario_id: s for s in load_scenarios(fixture_dir)}

    for picked in sorted(_LOCKED_QUERY_PICKS):
        scenario = scenarios.get(picked)
        # Assert presence rather than silent-skip.
        assert scenario is not None, f"{picked}: scenario not loaded"
        first = scenario.steps[0]
        assert "query" in first.event_data, (
            f"{picked}: event_data must include 'query' so handle_clinical_query "
            f"hashes the right text"
        )
        assert "orders" in first.event_data, (
            f"{picked}: event_data must include 'orders' so the "
            f"prompt builder injects the database state"
        )
        orders = first.event_data["orders"]
        assert isinstance(orders, list) and len(orders) > 0, (
            f"{picked}: event_data['orders'] must be a non-empty list"
        )


# ---------------------------------------------------------------------------
# replay() over the picked fixtures populates the
# LLM-path latency bucket so ``p99_latency_us_llm`` is no longer None.
# ---------------------------------------------------------------------------


def test_replay_query_picks_populate_llm_latency_bucket(tmp_path: Path) -> None:
    """Replaying the 8 picked query fixtures with a stub-dispatch produces a
    non-None ``p99_latency_us_llm`` and ``llm_latency_step_count >= 8``.

    A real LLM call is mocked via ``_dispatch_event_for_replay`` patching —
    the gate flip itself is the lab-host nightly job's responsibility per
    the issue's "informational records produced; gate flip is the nightly
    job's responsibility" line. This test pins the structural property:
    once the loader admits these fixtures, replay() emits LLM-path latency
    samples for them.
    """
    import json
    import shutil
    from unittest.mock import patch

    from samantha_server.scenarios.replay import replay

    src = Path(__file__).parent.parent / "fixtures" / "scenarios" / "query"
    target = tmp_path / "query"
    target.mkdir(parents=True)
    for stem in ("qr-001", "qr-002", "qr-003", "qr-005", "qr-006", "qr-007", "qr-008", "qr-023"):
        shutil.copy(src / f"{stem}.json", target / f"{stem}.json")

    # Sanity: each fixture in the temp corpus carries an `events` key.
    for path in target.glob("qr-*.json"):
        raw = json.loads(path.read_text())
        assert "events" in raw, f"{path.name}: missing events key"

    # Mock dispatch_event: return a fake decision marked routing_path="llm" so
    # _collect_latencies categorises the step into the LLM bucket. The mock LLM
    # client is required to build deps but never actually called.
    #
    # Capture the SpecimenContext per call so we can
    # assert each dispatched step actually carried event_data.orders. A
    # regression where the loader fails to plumb orders through would
    # otherwise pass this test (the mock would still record the bucket count).
    from tests.scenarios.test_replay import _fake_dispatch_with_receipt, _make_mock_llm_client

    captured_ctxs: list[object] = []

    async def _fake_dispatch(ctx: object, *, session_id: str, **kwargs: object) -> object:
        captured_ctxs.append(ctx)
        return await _fake_dispatch_with_receipt(session_id, latency_us=12_345_000, **kwargs)

    # Endpoint path calls routing.dispatch_event from _consume;
    # patch there so the fake intercepts all 8 clinical_query steps.
    with patch("samantha_server.api.routing.dispatch_event", side_effect=_fake_dispatch):
        report = replay(tmp_path, _llm_client_override=_make_mock_llm_client())

    # Tightened to ``== 8`` for the known 8-fixture corpus.
    assert report.llm_latency_step_count == 8, (
        f"Expected exactly 8 LLM-path latency samples for the 8-fixture "
        f"temp corpus; got {report.llm_latency_step_count}"
    )
    assert report.p99_latency_us_llm is not None, (
        "p99_latency_us_llm must be non-None once the LLM-path "
        "bucket has at least one sample"
    )
    assert len(captured_ctxs) == 8, (
        f" M-3: dispatch_event must be called once per fixture; "
        f"got {len(captured_ctxs)} calls"
    )
    for ctx in captured_ctxs:
        # ctx is a SpecimenContext; event_data carries the orders payload.
        ed = ctx.event.event_data  # type: ignore[attr-defined]
        ed_orders = ed.get("orders") if hasattr(ed, "get") else None
        assert ed_orders, (
            " M-3: dispatched ctx.event.event_data must carry a "
            "non-empty 'orders' list — a regression that drops the "
            "plumb-through would otherwise pass undetected here"
        )


# ---------------------------------------------------------------------------
# Stronger assertions per consolidated review.
# ---------------------------------------------------------------------------


def test_query_corpus_meets_sample_floor() -> None:
    """Assert the *real* query/ + unknown_input/ + hallucination/ directories
    expose ≥ ``_LLM_ANCHOR_MIN_STEP_COUNT`` LLM-path step samples — the
    threshold pinned in ``samantha_server/scenarios/replay.py`` that flips
    the p99 latency anchor from informational to enforcing.

    The temp-corpus test only covers the 8 picked fixtures in isolation; this
    test guards the real-fixture-directory contract against accidental
    deletions or future fixture-cleanup PRs.
    """
    import json

    from samantha_server.scenarios.replay import _LLM_ANCHOR_MIN_STEP_COUNT

    # Count steps across every category that contributes to the LLM-path
    # bucket: query (this PR's contribution), unknown_input, hallucination.
    fixtures_root = Path(__file__).parent.parent / "fixtures" / "scenarios"
    total_steps = 0
    for category in ("query", "unknown_input", "hallucination"):
        for path in (fixtures_root / category).glob("*.json"):
            if path.name.startswith("."):
                continue
            raw = json.loads(path.read_text())
            events = raw.get("events", [])
            total_steps += len(events)

    assert total_steps >= _LLM_ANCHOR_MIN_STEP_COUNT, (
        f"LLM-path step count is {total_steps}, below "
        f"_LLM_ANCHOR_MIN_STEP_COUNT={_LLM_ANCHOR_MIN_STEP_COUNT}. "
        f"The latency anchor will stay informational rather than enforcing on "
        f"the nightly job. A fixture deletion or a forgotten `events` key is "
        f"the most likely cause."
    )


def test_event_data_orders_matches_database_state_orders() -> None:
    """Drift guard for the duplicated orders array.

    The ``events[0].event_data.orders`` payload is the live path the
    prompt builder reads; the legacy ``database_state.orders`` is the
    human-readable copy retained for fixture authoring. They are byte-equal
    at merge time. This test asserts that invariant per fixture so a future
    edit that updates one and forgets the other surfaces immediately.
    """
    import json

    fixture_dir = Path(__file__).parent.parent / "fixtures" / "scenarios" / "query"
    for picked in sorted(_LOCKED_QUERY_PICKS):
        path = fixture_dir / f"{picked.lower()}.json"
        raw = json.loads(path.read_text())
        db_orders = raw.get("database_state", {}).get("orders", [])
        events = raw.get("events", [])
        assert events, f"{picked}: missing events key"
        ev_orders = events[0].get("event_data", {}).get("orders", [])
        assert db_orders == ev_orders, (
            f"{picked}: database_state.orders and events[0].event_data.orders "
            f"have drifted. The two MUST be byte-equal — the LLM prompt path "
            f"reads event_data.orders; a divergence silently injects a stale "
            f"database view."
        )


def test_vendor_overrides_contains_all_locked_picks() -> None:
    """The `.vendor-overrides.json` file must list every
    fixture this PR diverged from upstream samantha-public. A missing entry
    would let `vendor_scenarios.py` silently re-overwrite the fixture and
    strip its `events` key."""
    import json

    overrides_path = (
        Path(__file__).parent.parent / "fixtures" / "scenarios" / ".vendor-overrides.json"
    )
    overrides = json.loads(overrides_path.read_text())["overrides"]
    missing = _LOCKED_QUERY_PICKS - set(overrides.keys())
    assert not missing, (
        f"Locked query picks {sorted(missing)} must appear in "
        f".vendor-overrides.json with PR provenance"
    )


def test_every_query_fixture_step_has_llm_routing_path() -> None:
    """Every query-fixture step's `expected_routing_path`
    must be `"llm"`.

    / annotated all 29 fixtures' step-1 `expected_output` with
    `routing_path: "llm"` to close the verdict-comparator silent-gate window
    (`replay.py` skips routing comparison when `expected_routing_path is None`,
    so an un-annotated step lets a router regression mis-routing a clinical
    query to the deterministic path pass silently). The existing parity
    guard at `test_replay.py` is category-level — it passes as long as ANY
    fixture is annotated. This fixture-level guard catches the case where a
    new query fixture is added without the annotation.
    """
    from samantha_server.scenarios.loader import load_scenarios

    fixture_dir = Path(__file__).parent.parent / "fixtures" / "scenarios" / "query"
    scenarios = load_scenarios(fixture_dir)

    missing: list[str] = []
    for scenario in scenarios:
        for step in scenario.steps:
            if step.expected_routing_path != "llm":
                missing.append(
                    f"{scenario.scenario_id} step {step.step_index} "
                    f"(expected_routing_path={step.expected_routing_path!r})"
                )

    assert not missing, (
        " / invariant: every query fixture step must annotate "
        '`routing_path: "llm"` so the replay verdict comparator can catch '
        f"router regressions. Steps missing the annotation: {missing}"
    )


# ---------------------------------------------------------------------------
# Fixture completeness — all 29 query fixtures carry user_role
# ---------------------------------------------------------------------------


def test_all_query_fixtures_carry_valid_user_role() -> None:
    """Every query fixture has a valid user_role field.

    All 29 query fixtures must carry a top-level user_role string that is
    one of the four valid roles. This ensures the LLM sees role context on
    every replay.
    """
    from samantha_server.models.roles import VALID_USER_ROLES
    from samantha_server.scenarios.loader import load_scenarios

    fixture_dir = Path(__file__).parent.parent / "fixtures" / "scenarios" / "query"
    scenarios = load_scenarios(fixture_dir)

    # L10 PR243 review: pin fixture count to catch silent fixture-set shrinkage.
    assert len(scenarios) == 29, (
        f"Expected exactly 29 query fixtures; got {len(scenarios)}. "
        "A fixture was added or removed without updating this count."
    )

    missing_role: list[str] = []
    for scenario in sorted(scenarios, key=lambda s: s.scenario_id):
        if scenario.user_role is None:
            missing_role.append(scenario.scenario_id)
        elif scenario.user_role not in VALID_USER_ROLES:
            missing_role.append(f"{scenario.scenario_id} (invalid: {scenario.user_role!r})")

    assert not missing_role, (
        f"Query fixtures missing a valid user_role: {missing_role}. "
        f"Add top-level user_role to each fixture JSON."
    )


def test_qr028_qr029_pin_top_level_expected_answer_type() -> None:
    """Pin QR-028's and QR-029's top-level
    expected_output.answer_type.

    These two fixtures are the unit-suite anchors for the `no_orders` and
    `uncertain` answer_type gate branches. The replay harness only verifies
    the answer_type against a live oMLX response, so a fixture edit that
    silently flipped either back to `order_status` (or any other type)
    would not red any existing test — only the next live sweep would
    catch it. This static pin closes that gap by asserting the fixture
    intent directly at the JSON layer.
    """
    import json

    fixture_dir = Path(__file__).parent.parent / "fixtures" / "scenarios" / "query"
    expected = {"QR-028": "no_orders", "QR-029": "uncertain"}
    for scenario_id, expected_answer_type in expected.items():
        path = fixture_dir / f"{scenario_id.lower()}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        actual = data["expected_output"]["answer_type"]
        assert actual == expected_answer_type, (
            f"{scenario_id} fixture expected_output.answer_type drifted: "
            f"expected {expected_answer_type!r}, found {actual!r}. "
            f"Flipping this value silently disables the unit-level guard for "
            f"the {expected_answer_type} answer_type gate branch."
        )
