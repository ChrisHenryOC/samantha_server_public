"""Tests for STATES_WITHOUT_SKILL + STATE_TO_SKILL discover-time invariant."""

from __future__ import annotations

from pathlib import Path

import pytest

from samantha_server.models.context import VALID_STATES
from samantha_server.skills.loader import (
    STATES_WITHOUT_SKILL,
    SkillLoaderError,
    build_state_to_skill,
    discover,
)

# ---------------------------------------------------------------------------
# STATES_WITHOUT_SKILL constant
# ---------------------------------------------------------------------------


def test_states_without_skill_is_frozenset() -> None:
    assert isinstance(STATES_WITHOUT_SKILL, frozenset)


def test_states_without_skill_contains_terminal_states() -> None:
    """Canonical terminal/admin states that have no skill."""
    assert "ORDER_COMPLETE" in STATES_WITHOUT_SKILL
    assert "ORDER_TERMINATED" in STATES_WITHOUT_SKILL
    assert "ORDER_TERMINATED_QNS" in STATES_WITHOUT_SKILL


def test_states_without_skill_are_valid_states() -> None:
    """Every state in STATES_WITHOUT_SKILL must be a real VALID_STATES entry."""
    for state in STATES_WITHOUT_SKILL:
        assert state in VALID_STATES, (
            f"STATES_WITHOUT_SKILL contains {state!r} which is not in VALID_STATES"
        )


# ---------------------------------------------------------------------------
# build_state_to_skill helper
# ---------------------------------------------------------------------------


def test_build_state_to_skill_inverts_applies_to_states(tmp_path: Path) -> None:
    """build_state_to_skill inverts the per-skill applies_to_states lists."""
    from samantha_server.skills.loader import SkillSpec

    specs = {
        "skill-a": SkillSpec(
            name="skill-a",
            description="Skill A",
            path=tmp_path / "a" / "SKILL.md",
            applies_to_states=("STATE_X", "STATE_Y"),
        ),
        "skill-b": SkillSpec(
            name="skill-b",
            description="Skill B",
            path=tmp_path / "b" / "SKILL.md",
            applies_to_states=("STATE_Z",),
        ),
    }
    mapping = build_state_to_skill(specs)
    assert mapping["STATE_X"] == "skill-a"
    assert mapping["STATE_Y"] == "skill-a"
    assert mapping["STATE_Z"] == "skill-b"


def test_build_state_to_skill_skips_empty_applies_to_states(tmp_path: Path) -> None:
    """Skills with empty applies_to_states (like query-routing) are silently skipped."""
    from samantha_server.skills.loader import SkillSpec

    specs = {
        "query-routing": SkillSpec(
            name="query-routing",
            description="Query routing",
            path=tmp_path / "q" / "SKILL.md",
            applies_to_states=(),
        ),
    }
    mapping = build_state_to_skill(specs)
    assert "query-routing" not in mapping.values()
    assert len(mapping) == 0


# ---------------------------------------------------------------------------
# discover() includes STATE_TO_SKILL in its return value / validates invariant
# ---------------------------------------------------------------------------


def test_discover_state_to_skill_accessioning_maps_to_accessioning_routing() -> None:
    """STATE_TO_SKILL['ACCESSIONING'] == 'accessioning-routing'."""
    index = discover()
    from samantha_server.skills.loader import build_state_to_skill

    state_to_skill = build_state_to_skill(index)
    assert state_to_skill["ACCESSIONING"] == "accessioning-routing"


def test_discover_every_valid_state_covered(tmp_path: Path) -> None:
    """Every VALID_STATES entry is covered by a skill or STATES_WITHOUT_SKILL.

    This is the discover-time invariant: a missing mapping raises SkillLoaderError.
    Test passes a real discover() — the bundled skills + STATES_WITHOUT_SKILL must
    together cover all 24 VALID_STATES entries.
    """
    index = discover()
    from samantha_server.skills.loader import build_state_to_skill

    state_to_skill = build_state_to_skill(index)
    for state in VALID_STATES:
        assert state in state_to_skill or state in STATES_WITHOUT_SKILL, (
            f"{state!r} is in VALID_STATES but not covered by any skill's "
            "applies_to_states or STATES_WITHOUT_SKILL"
        )


def test_discover_invariant_raises_on_uncovered_state(tmp_path: Path) -> None:
    """discover() raises SkillLoaderError when a VALID_STATES entry is uncovered.

    Simulates adding a new state to VALID_STATES without backfilling the
    corresponding skill frontmatter. Uses a patched VALID_STATES for isolation.
    """
    # Create a minimal skill that covers no states
    skill_dir = tmp_path / "no_coverage"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: no-coverage-skill\ndescription: Covers nothing\n---\n\nBody\n"
    )
    # Monkeypatch VALID_STATES to add a new state that isn't covered
    from unittest.mock import patch

    import samantha_server.skills.loader as loader_mod

    extended_valid_states = frozenset(VALID_STATES) | {"UNCOVERED_TEST_STATE"}
    with (
        patch.object(loader_mod, "_VALID_STATES_FOR_INVARIANT", extended_valid_states),
        pytest.raises(SkillLoaderError, match="UNCOVERED_TEST_STATE"),
    ):
        discover(specs_root=tmp_path)


# ---------------------------------------------------------------------------
# M-04: build_state_to_skill raises SkillLoaderError on duplicate state claims
# ---------------------------------------------------------------------------


def test_build_state_to_skill_raises_on_duplicate_state_claims() -> None:
    """M-04: build_state_to_skill raises SkillLoaderError when two skills
    both claim the same state in their applies_to_states frontmatter.

    This exercises the error branch at loader.py:436-441 which was previously
    untested.
    """
    from pathlib import Path

    from samantha_server.skills.loader import SkillLoaderError, SkillSpec, build_state_to_skill

    # Create two SkillSpecs that both claim ACCESSIONING
    skill_a = SkillSpec(
        name="skill-a",
        description="First skill",
        path=Path("/fake/skill-a/SKILL.md"),
        applies_to_states=("ACCESSIONING",),
    )
    skill_b = SkillSpec(
        name="skill-b",
        description="Second skill",
        path=Path("/fake/skill-b/SKILL.md"),
        applies_to_states=("ACCESSIONING",),
    )

    with pytest.raises(SkillLoaderError, match="ACCESSIONING"):
        build_state_to_skill({"skill-a": skill_a, "skill-b": skill_b})
