"""Tests for samantha_server/skills/loader.py — agentskills.io 3-stage lifecycle."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from samantha_server.skills.loader import (
    SkillLoaderError,
    SkillResult,
    SkillSpec,
    discover,
    execute,
    load,
)

# Pinned descriptions per the Step 13 spec.  Used to anchor the LLM
# activation-matching key against accidental edits to any of the seven
# bundled SKILL.md files.
_EXPECTED_DESCRIPTIONS = {
    "accessioning-routing": (
        "Evaluate every accessioning rule against an incoming order; collect all matches "
        "and apply severity hierarchy (REJECT > HOLD > PROCEED > ACCEPT)"
    ),
    "sample-prep-routing": (
        "Route sample-prep events (processing/embedding/sectioning/QC) to the next state "
        "by first-match priority on event.outcome"
    ),
    "he-qc-routing": (
        "Route H&E staining and QC events to PATHOLOGIST_HE_REVIEW or back through "
        "restain/recut/QNS branches"
    ),
    "pathologist-he-review-routing": (
        "Route pathologist H&E diagnosis events (invasive_carcinoma, DCIS, suspicious, "
        "atypical, benign, recut_requested) to IHC, RESULTING, or recut paths"
    ),
    "ihc-routing": (
        "Route IHC staining/QC/scoring/FISH events using applies_at-scoped rules; "
        "handles HER2 fixation rejection and FISH reflex suggestion"
    ),
    "resulting-routing": (
        "Route resulting events through MISSING_INFO_HOLD, signout, report-generation, "
        "and order-complete based on accumulated flags and event outcomes"
    ),
    "query-routing": (
        "Answer free-text clinical queries; return structured JSON; "
        "cite scenario IDs in `reasoning`."
    ),
    # GH-34 Slice 3 / GH-193 Slice 3: updated to JSON-output contract
    "specimen-review": (
        "Pathologist-style disposition playbook for unrecognized specimen types; "
        "returns structured JSON."
    ),
}


# ---------------------------------------------------------------------------
# Slice 1: Discovery — names + pinned descriptions
# ---------------------------------------------------------------------------


def test_discover_contains_all_expected_names() -> None:
    index = discover()
    assert set(index.keys()) == set(_EXPECTED_DESCRIPTIONS.keys())


def test_discover_descriptions_match_pinned_values() -> None:
    index = discover()
    for name, expected in _EXPECTED_DESCRIPTIONS.items():
        assert index[name].description == expected, (
            f"{name}: description drifted from the Step 13 pinned value"
        )


def test_discover_spec_path_is_file() -> None:
    index = discover()
    for spec in index.values():
        assert spec.path.is_file()


# ---------------------------------------------------------------------------
# Slice 2: Activation — body contains known substring (one per skill)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("skill_name", "expected_phrase"),
    [
        ("accessioning-routing", "Accessioning Evaluation Skill"),
        ("sample-prep-routing", "Sample Prep Routing Skill"),
        ("he-qc-routing", "H&E Routing Skill"),
        ("pathologist-he-review-routing", "Pathologist H&E Review Routing Skill"),
        ("ihc-routing", "IHC Routing Skill"),
        ("resulting-routing", "Resulting Routing Skill"),
    ],
)
def test_load_body_contains_known_phrase(skill_name: str, expected_phrase: str) -> None:
    body = load(skill_name)
    assert expected_phrase in body


# ---------------------------------------------------------------------------
# Slice 3: Activation strips frontmatter
# ---------------------------------------------------------------------------


def test_load_body_does_not_start_with_fence() -> None:
    body = load("accessioning-routing")
    assert not body.strip().startswith("---")


def test_load_body_does_not_contain_name_frontmatter_line() -> None:
    body = load("accessioning-routing")
    assert "name: accessioning-routing" not in body


def test_load_body_does_not_contain_description_frontmatter_line() -> None:
    body = load("accessioning-routing")
    # Match the strength of the `name:` companion check — search the whole body,
    # not just the first 11 chars.
    assert "description:" not in body.split("\n")[0]
    assert _EXPECTED_DESCRIPTIONS["accessioning-routing"] not in body


# ---------------------------------------------------------------------------
# Slice 4: Activation on unknown name raises
# ---------------------------------------------------------------------------


def test_load_unknown_name_raises_skill_loader_error() -> None:
    with pytest.raises(SkillLoaderError):
        load("no-such-skill")


# ---------------------------------------------------------------------------
# Slice 5: load() accepts a pre-built index (skips re-discovery)
# ---------------------------------------------------------------------------


def test_load_uses_provided_index_without_rediscovery(tmp_path: Path) -> None:
    """Caller passes a pre-built index; load() reads only from the index path."""
    skill_dir = tmp_path / "custom"
    skill_dir.mkdir()
    custom_md = skill_dir / "SKILL.md"
    custom_md.write_text(
        "---\nname: custom-skill\ndescription: Routes custom events\n---\n\nCustom body content\n"
    )
    custom_index = {
        "custom-skill": SkillSpec(
            name="custom-skill",
            description="Routes custom events",
            path=custom_md,
        )
    }
    body = load("custom-skill", index=custom_index)
    assert "Custom body content" in body


def test_load_with_provided_index_unknown_name_raises(tmp_path: Path) -> None:
    body_path = tmp_path / "x" / "SKILL.md"
    body_path.parent.mkdir()
    body_path.write_text("---\nname: x\ndescription: y\n---\n\nbody\n")
    idx = {"x": SkillSpec(name="x", description="y", path=body_path)}
    with pytest.raises(SkillLoaderError):
        load("missing", index=idx)


# ---------------------------------------------------------------------------
# Slice 6: Body extraction is line-anchored — markdown horizontal rules survive
# ---------------------------------------------------------------------------


def test_load_preserves_markdown_horizontal_rule_in_body(tmp_path: Path) -> None:
    """A markdown horizontal rule (`---` on its own line) inside the body must
    not be mistaken for the closing frontmatter fence."""
    skill_dir = tmp_path / "hr_skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: hr-skill\ndescription: A skill\n---\n\nIntro\n\n---\n\nMore body\n"
    )
    body = load("hr-skill", specs_root=tmp_path)
    assert "Intro" in body
    assert "More body" in body
    # The horizontal rule survives because the closing fence was the first
    # `---\n` line; the body should retain its in-prose `---`.
    assert "---" in body


# ---------------------------------------------------------------------------
# Slice 7: Frontmatter validation — malformed / missing / non-string / non-mapping
# ---------------------------------------------------------------------------


def test_malformed_yaml_raises(tmp_path: Path) -> None:
    skill_dir = tmp_path / "bad_yaml"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: [unclosed bracket\ndescription: x\n---\n\nBody\n"
    )
    with pytest.raises(SkillLoaderError):
        discover(specs_root=tmp_path)


def test_missing_name_field_raises(tmp_path: Path) -> None:
    skill_dir = tmp_path / "no_name"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\ndescription: something\n---\n\nBody\n")
    with pytest.raises(SkillLoaderError):
        discover(specs_root=tmp_path)


def test_missing_description_field_raises(tmp_path: Path) -> None:
    skill_dir = tmp_path / "no_desc"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: some-skill\n---\n\nBody\n")
    with pytest.raises(SkillLoaderError):
        discover(specs_root=tmp_path)


def test_empty_string_name_raises(tmp_path: Path) -> None:
    skill_dir = tmp_path / "empty_name"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text('---\nname: ""\ndescription: x\n---\n\nBody\n')
    with pytest.raises(SkillLoaderError):
        discover(specs_root=tmp_path)


def test_non_string_name_raises(tmp_path: Path) -> None:
    """A YAML list / non-string `name:` value must be rejected, not coerced."""
    skill_dir = tmp_path / "list_name"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname:\n  - a\n  - b\ndescription: x\n---\n\nBody\n")
    with pytest.raises(SkillLoaderError):
        discover(specs_root=tmp_path)


def test_non_mapping_frontmatter_raises(tmp_path: Path) -> None:
    """Frontmatter that parses to a YAML list (not a mapping) must be rejected."""
    skill_dir = tmp_path / "list_fm"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\n- item_a\n- item_b\n---\n\nBody\n")
    with pytest.raises(SkillLoaderError):
        discover(specs_root=tmp_path)


def test_no_frontmatter_raises(tmp_path: Path) -> None:
    skill_dir = tmp_path / "no_fm"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("## Just a body\n\nNo frontmatter here.\n")
    with pytest.raises(SkillLoaderError):
        discover(specs_root=tmp_path)


# ---------------------------------------------------------------------------
# Slice 8: specs_root validation
# ---------------------------------------------------------------------------


def test_missing_specs_root_raises(tmp_path: Path) -> None:
    nonexistent = tmp_path / "does_not_exist"
    with pytest.raises(SkillLoaderError):
        discover(specs_root=nonexistent)


def test_specs_root_is_file_not_dir_raises(tmp_path: Path) -> None:
    f = tmp_path / "not_a_dir"
    f.write_text("hi")
    with pytest.raises(SkillLoaderError):
        discover(specs_root=f)


# ---------------------------------------------------------------------------
# Slice 9: Duplicate name raises during discovery
# ---------------------------------------------------------------------------


def test_duplicate_name_raises(tmp_path: Path) -> None:
    for folder in ("skill_a", "skill_b"):
        d = tmp_path / folder
        d.mkdir()
        (d / "SKILL.md").write_text(
            "---\nname: duplicate-skill\ndescription: A skill\n---\n\nBody\n"
        )
    with pytest.raises(SkillLoaderError):
        discover(specs_root=tmp_path)


# ---------------------------------------------------------------------------
# Slice 10: execute() raises NotImplementedError with milestone marker
# ---------------------------------------------------------------------------


def test_execute_raises_not_implemented_with_phase_marker() -> None:
    with pytest.raises(NotImplementedError, match="W1 Phase 3"):
        execute("accessioning-routing", ctx=object())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Type-shape and immutability tests
# ---------------------------------------------------------------------------


def test_skill_spec_has_required_fields() -> None:
    index = discover()
    spec = index["accessioning-routing"]
    assert isinstance(spec, SkillSpec)
    assert isinstance(spec.name, str)
    assert isinstance(spec.description, str)
    assert isinstance(spec.path, Path)


def test_skill_spec_is_frozen() -> None:
    spec = SkillSpec(
        name="x",
        description="y",
        path=Path("/tmp/z"),  # noqa: S108 (test fixture only)
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.name = "mutated"  # type: ignore[misc]


def test_skill_spec_rejects_empty_name() -> None:
    with pytest.raises(SkillLoaderError):
        SkillSpec(name="", description="y", path=Path("/tmp/z"))  # noqa: S108


def test_skill_spec_rejects_empty_description() -> None:
    with pytest.raises(SkillLoaderError):
        SkillSpec(name="x", description="   ", path=Path("/tmp/z"))  # noqa: S108


def test_skill_result_is_dataclass() -> None:
    result = SkillResult(name="x", decision_text="y")
    assert result.name == "x"
    assert result.decision_text == "y"


# ---------------------------------------------------------------------------
# GH-34 Slice 1: STATES_WITHOUT_SKILL covers new terminal/LLM-routing states
# ---------------------------------------------------------------------------


def test_pending_human_review_in_states_without_skill() -> None:
    """GH-34 Slice 1: PENDING_HUMAN_REVIEW is terminal for v1; no skill."""
    from samantha_server.skills.loader import STATES_WITHOUT_SKILL

    assert "PENDING_HUMAN_REVIEW" in STATES_WITHOUT_SKILL


def test_discover_invariant_passes_after_new_states_added() -> None:
    """GH-34 Slice 1/3: discover() must not raise after PENDING_LLM_REVIEW and
    PENDING_HUMAN_REVIEW are added to VALID_STATES."""
    # Clears the cache so the invariant re-runs with the live VALID_STATES.
    discover.cache_clear()
    # This call raises SkillLoaderError if any VALID_STATES entry is uncovered.
    # PENDING_HUMAN_REVIEW is in STATES_WITHOUT_SKILL.
    # PENDING_LLM_REVIEW is covered by specimen-review skill's applies_to_states.
    index = discover()
    assert index is not None


# ---------------------------------------------------------------------------
# GH-34 Slice 3: specimen-review skill
# ---------------------------------------------------------------------------


def test_specimen_review_skill_loads() -> None:
    """GH-34 Slice 3: specimen-review skill must be discoverable."""
    discover.cache_clear()
    index = discover()
    assert "specimen-review" in index


def test_specimen_review_applies_to_pending_llm_review() -> None:
    """GH-34 Slice 3: specimen-review skill must claim PENDING_LLM_REVIEW state."""
    discover.cache_clear()
    index = discover()
    assert "PENDING_LLM_REVIEW" in index["specimen-review"].applies_to_states


def test_state_to_skill_maps_pending_llm_review_to_specimen_review() -> None:
    """GH-34 Slice 3: STATE_TO_SKILL[PENDING_LLM_REVIEW] == 'specimen-review'."""
    from samantha_server.skills.loader import build_state_to_skill

    discover.cache_clear()
    index = discover()
    state_to_skill = build_state_to_skill(index)
    assert state_to_skill.get("PENDING_LLM_REVIEW") == "specimen-review"


def test_pending_llm_review_not_in_states_without_skill() -> None:
    """GH-34 Slice 3: PENDING_LLM_REVIEW must NOT be in STATES_WITHOUT_SKILL
    once the specimen-review skill covers it."""
    from samantha_server.skills.loader import STATES_WITHOUT_SKILL

    assert "PENDING_LLM_REVIEW" not in STATES_WITHOUT_SKILL


def test_specimen_review_body_contains_disposition_criteria() -> None:
    """GH-34 Slice 3: skill body must contain accept/reject/escalate guidance."""
    discover.cache_clear()
    body = load("specimen-review")
    assert "accept" in body.lower()
    assert "reject" in body.lower()
    assert "escalate" in body.lower()


def test_skill_result_is_frozen() -> None:
    result = SkillResult(name="x", decision_text="y")
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.name = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Slice GH-88 Slice 2: backfilled applies_to_states on the 7 bundled skills
# ---------------------------------------------------------------------------

# Expected applies_to_states per the GH-88 STATE_TO_SKILL mapping spec.
# query-routing intentionally has an empty list (clinical queries don't fire rules).
_EXPECTED_APPLIES_TO_STATES: dict[str, tuple[str, ...]] = {
    "accessioning-routing": (
        "ACCESSIONING",
        "ACCEPTED",
        "MISSING_INFO_HOLD",
        "MISSING_INFO_PROCEED",
        "DO_NOT_PROCESS",
    ),
    "sample-prep-routing": (
        "SAMPLE_PREP_PROCESSING",
        "SAMPLE_PREP_EMBEDDING",
        "SAMPLE_PREP_SECTIONING",
        "SAMPLE_PREP_QC",
    ),
    "he-qc-routing": ("HE_STAINING", "HE_QC"),
    "pathologist-he-review-routing": ("PATHOLOGIST_HE_REVIEW",),
    "ihc-routing": (
        "IHC_STAINING",
        "IHC_QC",
        "IHC_SCORING",
        "SUGGEST_FISH_REFLEX",
        "FISH_SEND_OUT",
    ),
    "resulting-routing": (
        "RESULTING_HOLD",
        "RESULTING",
        "PATHOLOGIST_SIGNOUT",
        "REPORT_GENERATION",
    ),
    "query-routing": (),
}


@pytest.mark.parametrize("skill_name", list(_EXPECTED_APPLIES_TO_STATES.keys()))
def test_bundled_skill_applies_to_states_matches_spec(skill_name: str) -> None:
    """Each bundled skill's applies_to_states must match the GH-88 spec mapping."""
    index = discover()
    spec = index[skill_name]
    expected = _EXPECTED_APPLIES_TO_STATES[skill_name]
    assert set(spec.applies_to_states) == set(expected), (
        f"{skill_name}: applies_to_states {set(spec.applies_to_states)!r} "
        f"does not match expected {set(expected)!r}"
    )


# ---------------------------------------------------------------------------
# Slice GH-88 Slice 1: applies_to_states field on SkillSpec
# ---------------------------------------------------------------------------


def test_skill_spec_has_applies_to_states_field() -> None:
    """SkillSpec must have an applies_to_states: tuple[str, ...] field."""
    spec = SkillSpec(
        name="x",
        description="y",
        path=Path("/tmp/z"),  # noqa: S108
    )
    assert hasattr(spec, "applies_to_states")
    assert isinstance(spec.applies_to_states, tuple)


def test_skill_spec_applies_to_states_default_is_empty() -> None:
    """applies_to_states defaults to () if not supplied."""
    spec = SkillSpec(name="x", description="y", path=Path("/tmp/z"))  # noqa: S108
    assert spec.applies_to_states == ()


def test_skill_spec_applies_to_states_is_tuple_of_str() -> None:
    """applies_to_states must be a tuple of strings."""
    spec = SkillSpec(
        name="x",
        description="y",
        path=Path("/tmp/z"),  # noqa: S108
        applies_to_states=("ACCESSIONING", "ACCEPTED"),
    )
    assert spec.applies_to_states == ("ACCESSIONING", "ACCEPTED")
    for state in spec.applies_to_states:
        assert isinstance(state, str)


def test_skill_spec_with_applies_to_states_is_frozen() -> None:
    """applies_to_states is read-only (frozen dataclass)."""
    spec = SkillSpec(
        name="x",
        description="y",
        path=Path("/tmp/z"),  # noqa: S108
        applies_to_states=("ACCESSIONING",),
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.applies_to_states = ("MUTATED",)  # type: ignore[misc]


def test_parse_skill_md_without_applies_to_states_yields_empty_tuple(tmp_path: Path) -> None:
    """A SKILL.md without applies_to_states yields SkillSpec with applies_to_states=()."""
    skill_dir = tmp_path / "no_states"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: no-states-skill\ndescription: A skill without states\n---\n\nBody\n"
    )
    index = discover(specs_root=tmp_path)
    assert index["no-states-skill"].applies_to_states == ()


def test_parse_skill_md_with_applies_to_states_yields_tuple(tmp_path: Path) -> None:
    """A SKILL.md with applies_to_states: [STATE_A, STATE_B] yields a tuple."""
    skill_dir = tmp_path / "with_states"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: states-skill\ndescription: A skill with states\n"
        "applies_to_states: [ACCESSIONING, ACCEPTED]\n---\n\nBody\n"
    )
    index = discover(specs_root=tmp_path)
    assert index["states-skill"].applies_to_states == ("ACCESSIONING", "ACCEPTED")


# ---------------------------------------------------------------------------
# M-11: _read_text lru_cache — repeated calls don't re-read the file
# ---------------------------------------------------------------------------


def test_read_text_is_cached(tmp_path: Path) -> None:
    """M-11: _read_text returns the same object on repeated calls with the same path.

    The lru_cache means the file is read once per unique path; the in-memory
    str is reused. This verifies the cache is wired (not that filesystem reads
    are suppressed — lru_cache identity equality suffices).
    """
    from samantha_server.skills.loader import _read_text

    # Clear the cache between tests to avoid interference from other test runs
    _read_text.cache_clear()

    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text(
        "---\nname: test-skill\ndescription: A test skill.\n---\nBody text.\n",
        encoding="utf-8",
    )

    result1 = _read_text(skill_file)
    result2 = _read_text(skill_file)

    # Same object (lru_cache returns cached result)
    assert result1 is result2
    # Cache info confirms one miss (first call) and one hit (second call)
    info = _read_text.cache_info()
    assert info.misses == 1
    assert info.hits == 1

    _read_text.cache_clear()  # cleanup


# ---------------------------------------------------------------------------
# H-02: discover() is cached — SKILL.md files read at most once
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# GH-234 S5: specimen-review skill anatomic site disposition section
# ---------------------------------------------------------------------------


def test_specimen_review_skill_contains_anatomic_site_disposition_section() -> None:
    """GH-234 S5: skill body must contain the anatomic site disposition header."""
    discover.cache_clear()
    body = load("specimen-review")
    assert "Anatomic site disposition" in body


def test_specimen_review_skill_anatomic_site_disposition_contains_accepted_guidance() -> None:
    """GH-234 S5: anatomic site disposition section must include accept guidance."""
    discover.cache_clear()
    body = load("specimen-review")
    assert "breast-cancer-relevant tissue" in body


def test_specimen_review_skill_anatomic_site_disposition_contains_rejected_guidance() -> None:
    """GH-234 S5: anatomic site disposition section must include reject guidance."""
    discover.cache_clear()
    body = load("specimen-review")
    assert "clearly outside breast workflow" in body


def test_specimen_review_skill_anatomic_site_disposition_contains_escalated_guidance() -> None:
    """GH-234 S5: anatomic site disposition section must include escalate guidance."""
    discover.cache_clear()
    body = load("specimen-review")
    assert "human pathologist judgment" in body


def test_discover_is_cached_across_calls(tmp_path: Path) -> None:
    """H-02: repeated discover() calls with the same specs_root return the cached dict.

    SKILL.md files must be read at most once across multiple discover() calls.
    This verifies that functools.cache on discover() works correctly.
    """
    from samantha_server.skills.loader import _read_text, discover

    # Build a minimal valid specs tree
    skill_dir = tmp_path / "dummy-skill"
    skill_dir.mkdir()
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        "---\nname: dummy-skill\ndescription: A dummy skill.\n---\nBody.\n",
        encoding="utf-8",
    )

    # Clear caches so we start clean
    discover.cache_clear()
    _read_text.cache_clear()

    result1 = discover(tmp_path)
    result2 = discover(tmp_path)

    # Same object identity (cache hit returns same dict)
    assert result1 is result2, "discover() must return the same object on cache hit"

    # _read_text was called for the SKILL.md file exactly once
    cache_info = _read_text.cache_info()
    assert cache_info.misses == 1, (
        f"Expected exactly 1 _read_text miss (the first discover() call); "
        f"got {cache_info.misses} misses. SKILL.md files must be read at most once."
    )

    discover.cache_clear()
    _read_text.cache_clear()
