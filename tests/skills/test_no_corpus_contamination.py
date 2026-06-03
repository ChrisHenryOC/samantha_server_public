"""Skill prompts must not contain live-corpus IDs (GH-190).

Background: a 2026-05-11 live run against `Qwen2.5-Coder-32B-Instruct-MLX-4bit`
produced responses regurgitating the skill prompt's example block byte-for-byte
(see issue body for the transcript). The model used `ORD-101`/`ORD-103` and
cited `QR-001`/`QR-003` instead of producing its own answer because those
identifiers appear in the skill's example response.

This test loads every `scenario_id` and `order_id` from
`tests/fixtures/scenarios/**/*.json` and asserts that no such identifier
appears as a substring of any bundled SKILL.md body. Sentinel IDs
(e.g. `ORD-EXAMPLE-1`, `XX-000`) are by definition absent from the corpus,
so passing this assertion proves the skill prompt only uses sentinels.

Allowlist: a SKILL.md may declare an intentional reference (e.g., a rule
ID that happens to share a prefix with a scenario ID) via an HTML comment:

    <!-- contamination-allow: SP-001 -->

Each allowlisted token is removed from the candidate-ID set for that skill
only. The allowlist exists to handle future `SP-`/`ACC-` prefix collisions
between rule IDs and scenario IDs; today's corpus uses `LR-`/`QR-`/`SC-`
scenario IDs and `ORD-` order IDs, so no allowlist entries are required.

Two distinct HTML-comment markers appear in SKILL.md files:

- ``<!-- contamination-audit: ... -->`` is descriptive only — a human-
  readable audit trail noting that the skill body has been reviewed for
  contamination. This test does NOT parse it.
- ``<!-- contamination-allow: <id> -->`` is machine-parsed by this test
  via ``_ALLOW_RE``; its token suppresses the contamination check for
  the listed identifier in that one skill body.

Do not write `contamination-audit:` expecting the test to honour it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FIXTURES_ROOT = _REPO_ROOT / "tests" / "fixtures" / "scenarios"
_SKILLS_ROOT = _REPO_ROOT / "samantha_server" / "skills" / "specs"

_ALLOW_RE = re.compile(r"<!--\s*contamination-allow:\s*([A-Za-z0-9][A-Za-z0-9_-]*)\s*-->")


def _collect_order_ids(node: Any, out: set[str]) -> None:
    """Recursively gather every ``order_id`` string anywhere in the JSON tree."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "order_id" and isinstance(value, str) and value:
                out.add(value)
            _collect_order_ids(value, out)
    elif isinstance(node, list):
        for item in node:
            _collect_order_ids(item, out)


def _load_fixture_ids() -> frozenset[str]:
    ids: set[str] = set()
    for fixture_path in _FIXTURES_ROOT.rglob("*.json"):
        if fixture_path.name.startswith("."):
            continue
        data = json.loads(fixture_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            scenario_id = data.get("scenario_id")
            if isinstance(scenario_id, str) and scenario_id:
                ids.add(scenario_id)
        _collect_order_ids(data, ids)
    return frozenset(ids)


def _parse_allowlist(body: str) -> frozenset[str]:
    return frozenset(_ALLOW_RE.findall(body))


@pytest.fixture(scope="module")
def fixture_ids() -> frozenset[str]:
    # Preflight the path explicitly: ``Path.rglob`` raises ``OSError`` on a
    # missing root in Python 3.12+, which would surface as a confusing
    # generic OS error instead of telling the developer the fixture root
    # moved. Distinguish the two failure modes so the error points at the
    # right corner of the corpus contract.
    if not _FIXTURES_ROOT.exists():
        pytest.fail(
            f"fixture root not found at {_FIXTURES_ROOT} — has the corpus "
            f"directory moved? Update _FIXTURES_ROOT in this test file."
        )
    ids = _load_fixture_ids()
    assert ids, (
        f"no fixture IDs discovered under {_FIXTURES_ROOT} — the directory "
        f"exists but no scenario_id / order_id fields were found. Schema "
        f"drift? Check that fixture JSON still uses these field names."
    )
    return ids


@pytest.mark.parametrize(
    "skill_md",
    sorted(_SKILLS_ROOT.glob("*/SKILL.md")),
    ids=lambda p: p.parent.name,
)
def test_skill_prompt_has_no_corpus_id_substring(
    skill_md: Path, fixture_ids: frozenset[str]
) -> None:
    body = skill_md.read_text(encoding="utf-8")
    allowlisted = _parse_allowlist(body)
    contamination = sorted(fid for fid in fixture_ids if fid not in allowlisted and fid in body)
    assert not contamination, (
        f"{skill_md.relative_to(_REPO_ROOT)} contains live-corpus identifiers as "
        f"substrings: {contamination}. Rotate to sentinel IDs (e.g. ORD-EXAMPLE-1, "
        f"XX-000) or allowlist intentional references with "
        f"`<!-- contamination-allow: <id> -->`."
    )


def test_parse_allowlist_recognizes_well_formed_comment() -> None:
    """A canonical ``<!-- contamination-allow: SP-001 -->`` parses to {SP-001}."""
    body = "irrelevant prose\n<!-- contamination-allow: SP-001 -->\nmore text\n"
    assert _parse_allowlist(body) == frozenset({"SP-001"})


def test_parse_allowlist_recognizes_multiple_entries() -> None:
    """Multiple allow-comments accumulate; whitespace around the token is tolerated."""
    body = (
        "<!--contamination-allow:ACC-001-->\n"
        "<!--    contamination-allow:    QR-001    -->\n"
        "<!-- contamination-allow: SC-VOCAB01 -->\n"
    )
    assert _parse_allowlist(body) == frozenset({"ACC-001", "QR-001", "SC-VOCAB01"})


def test_parse_allowlist_rejects_malformed_tokens() -> None:
    """Tokens with trailing ``-->`` (missing space) or leading hyphens
    must not parse — those would silently degrade the allowlist to a no-op
    on the intended identifier (the regression behind M1).
    """
    no_space_before_close = "<!-- contamination-allow: SC-001-->"
    leading_hyphen = "<!-- contamination-allow: -SC-001 -->"
    parsed_no_space = _parse_allowlist(no_space_before_close)
    parsed_leading = _parse_allowlist(leading_hyphen)
    assert "SC-001-->" not in parsed_no_space, (
        f"regex captured the closing fence as part of the token: {parsed_no_space}"
    )
    assert "-SC-001" not in parsed_leading, (
        f"regex captured a leading-hyphen typo: {parsed_leading}"
    )


def test_allowlist_suppresses_contamination_for_listed_token(tmp_path: Path) -> None:
    """End-to-end: a SKILL.md body containing a fixture-ID substring passes
    the contamination check iff the same token is allowlisted.

    Runs against synthetic fixtures + a synthetic skill body so the test
    is independent of the real corpus / bundled SKILL.md files.
    """
    skill_body_with_allow = (
        "<!-- contamination-allow: QR-999 -->\n"
        "Example block that legitimately references rule QR-999 in prose.\n"
    )
    skill_body_without_allow = "Example block that legitimately references rule QR-999 in prose.\n"
    fixture_set = frozenset({"QR-999", "ORD-9999"})

    allow = _parse_allowlist(skill_body_with_allow)
    leak_with_allow = [
        fid for fid in fixture_set if fid not in allow and fid in skill_body_with_allow
    ]
    leak_without_allow = [fid for fid in fixture_set if fid in skill_body_without_allow]

    assert leak_with_allow == [], (
        f"allowlist should have suppressed QR-999, got leak={leak_with_allow}"
    )
    assert "QR-999" in leak_without_allow, (
        "without an allow-comment the same body should fail — sanity check"
    )


def test_allowlist_with_absent_token_is_harmless(tmp_path: Path) -> None:
    """Allowlisting a token that does not appear in the body is a no-op
    (stale entries don't crash the test or affect other tokens).
    """
    body = (
        "<!-- contamination-allow: NOT-IN-BODY -->\n"
        "<!-- contamination-allow: QR-999 -->\n"
        "Example referencing QR-999 only.\n"
    )
    fixture_set = frozenset({"QR-999", "NOT-IN-BODY"})
    allow = _parse_allowlist(body)
    assert allow == frozenset({"NOT-IN-BODY", "QR-999"})
    leak = [fid for fid in fixture_set if fid not in allow and fid in body]
    assert leak == [], f"stale allow entry should be harmless, got leak={leak}"
