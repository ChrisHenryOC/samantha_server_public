"""Architectural test: no hardcoded model IDs outside config.

Per CLAUDE.md "Architectural invariants":

> Model-agnostic discipline — no hardcoded model IDs outside
> `config.py` / `.env`.

The test scans every `*.py` file under `samantha_server/`,
`scripts/`, and `tests/` for known provider model-ID patterns. Any
match outside the allowlisted config files (and not suppressed by
``# nosec: model-id``) fails the test.

Allowlisted files:

- ``samantha_server/config.py``
- ``samantha_server/llm/config.py``

Neither file exists in Phase 1. The test is **forward-looking** and
passes vacuously today. It binds the moment Phase 2 introduces a
config module — at that point, the allowlist is the contract that
says "model IDs go HERE and nowhere else."

If a Phase 2 PR needs to mention a model ID outside config (e.g., a
worked example in a docstring or a one-off integration fixture), the
right move is to suppress via an inline ``# nosec: model-id`` comment
on the same line as the match. The comment is reviewer-visible and
narrowly scoped — preferred over expanding the allowlist or adding
the file to ``_ALLOWED_FILES``.

For multi-line content (long docstrings, large fixture blobs), the
match must be on a single line that also carries the ``# nosec``
comment. If a model ID is buried in a multi-line docstring without
a same-line comment, refactor the docstring to put the literal on a
single line, or use a non-literal placeholder.

Pattern coverage chosen for known-broad provider families:

- Anthropic Claude (claude-N-…, claude-opus-N-N, claude-opus-N-N-DDDDDDDD,
  claude-sonnet-N-N, claude-haiku-N-N).
- OpenAI GPT (gpt-N, gpt-N-…, gpt-N.5).
- OpenAI o-series reasoning (bare o1/o3/o4 and dated/suffixed variants).
- MLX community models (mlx-community/…).

If Phase 2's chosen provider isn't in this list, extend ``_PATTERNS``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# Files where model IDs are allowed to appear (config-only convention).
# Paths relative to the repo root.
_ALLOWED_FILES: frozenset[str] = frozenset(
    {
        "samantha_server/config.py",
        "samantha_server/llm/config.py",
        # chat_template_config.py holds model-family substrings used to look
        # up per-model chat-template kwargs (e.g., Qwen3.5/3.6 →
        # `enable_thinking=False`). Bare family stems like "Qwen3.5-27B"
        # don't match the regex patterns above, so the file passes the
        # scan by accident today — but the file's purpose IS per-model
        # config, so making the allowlist entry explicit documents the
        # design intent and protects against future regex tightening.
        # review #2.
        "samantha_server/llm/chat_template_config.py",
    }
)

# Directories scanned for hardcoded model IDs. Tests and scripts are
# in scope so a fixture/script can't sneak in a hardcoded model ID
# without either an allowlist entry or a `# nosec` suppression.
_SCAN_ROOTS: tuple[str, ...] = (
    "samantha_server",
    "scripts",
    "tests",
)

# This test file itself contains model-ID literals as positive-test
# fixtures for the regex patterns. Skipping it here is mechanical:
# the test asserting its own patterns works isn't a violation.
_SELF_PATH = "tests/architectural/test_model_agnostic.py"

# Regex patterns matching model IDs across major provider families.
# Each pattern is matched against full file content (line-by-line, so
# `# nosec: model-id` per-line suppression works). Misses are
# acceptable; false positives are not, so prefer specific anchoring
# over broad alternation.
_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Anthropic family naming with optional date suffix:
    # claude-opus-4, claude-opus-4-5, claude-opus-4-5-20251015,
    # claude-sonnet-4-5, claude-haiku-3-5-20241022, etc.
    re.compile(r"\bclaude-(?:opus|sonnet|haiku)-\d+(?:-\d+)?(?:-\d{8})?\b"),
    # Anthropic legacy / non-family naming: claude-3-opus-20240229,
    # claude-3.5-sonnet, claude-2.1, etc.
    re.compile(r"\bclaude-\d+(?:\.\d+)?(?:-[a-z]+)*(?:-\d{8})?\b"),
    # OpenAI GPT: gpt-4, gpt-4o, gpt-4-turbo, gpt-3.5-turbo,
    # gpt-4o-mini, etc. The optional [a-z]? handles the trailing
    # letter modifier (e.g., the `o` in gpt-4o).
    re.compile(r"\bgpt-\d+(?:\.\d+)?[a-z]?(?:-[a-z0-9]+)*\b"),
    # OpenAI reasoning: bare o1/o3/o4, plus dated/suffixed variants
    # (o3-mini, o4-2025-08-15, etc.).
    re.compile(r"\bo[134](?:-(?:mini|preview|pro|\d{4}-\d{2}-\d{2}|[a-z0-9]+))*\b"),
    # MLX community models on Hugging Face
    re.compile(r"\bmlx-community/[A-Za-z0-9._\-]+\b"),
)

_NOSEC_TOKEN = "# nosec: model-id"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _line_is_suppressed(line: str) -> bool:
    """Return True if *line* carries the inline `# nosec: model-id` token."""
    return _NOSEC_TOKEN in line


def _scan_file(path: Path) -> list[tuple[int, str, str]]:
    """Return list of (line_number, pattern_repr, matched_text) hits.

    Lines containing ``# nosec: model-id`` are skipped — that's the
    documented inline-suppression mechanism.
    """
    hits: list[tuple[int, str, str]] = []
    text = path.read_text()
    for lineno, line in enumerate(text.splitlines(), start=1):
        if _line_is_suppressed(line):
            continue
        for pattern in _PATTERNS:
            for match in pattern.finditer(line):
                hits.append((lineno, pattern.pattern, match.group(0)))
    return hits


def _python_files() -> list[Path]:
    """Yield all .py files under the scan roots, excluding caches and self."""
    repo = _repo_root()
    files: list[Path] = []
    self_path = repo / _SELF_PATH
    for rel in _SCAN_ROOTS:
        root = repo / rel
        if not root.is_dir():
            continue
        for p in root.rglob("*.py"):
            if "__pycache__" in p.parts:
                continue
            if p == self_path:
                continue
            files.append(p)
    return sorted(files)


def test_no_hardcoded_model_ids_outside_config() -> None:
    """Fail if a model-ID pattern matches in any non-allowlisted file."""
    repo = _repo_root()
    violations: list[str] = []
    for py_file in _python_files():
        rel = str(py_file.relative_to(repo))
        if rel in _ALLOWED_FILES:
            continue
        hits = _scan_file(py_file)
        for line_no, pattern, text in hits:
            violations.append(
                f"{rel}:{line_no}: hardcoded model ID `{text}` "
                f"(matched /{pattern}/). "
                f"Move to samantha_server/config.py or "
                f"samantha_server/llm/config.py, or suppress with an "
                f"inline `{_NOSEC_TOKEN}` comment on the offending line "
                f"if the mention is illustrative (e.g., a single-line "
                f"docstring example)."
            )
    if violations:
        pytest.fail("model-agnostic violations found:\n  - " + "\n  - ".join(violations))


# ---------------------------------------------------------------------------
# Tests for the test machinery itself.
# ---------------------------------------------------------------------------


def test_nosec_suppression_is_per_line() -> None:
    """A `# nosec: model-id` comment on the offending line suppresses
    matches; the absence of the comment surfaces them."""
    suppressed = "MODEL = 'claude-opus-4-5'  # nosec: model-id"
    unsuppressed = "MODEL = 'claude-opus-4-5'"
    assert _line_is_suppressed(suppressed) is True
    assert _line_is_suppressed(unsuppressed) is False


@pytest.mark.parametrize(
    "literal",
    [
        "claude-opus-4",
        "claude-opus-4-5",
        "claude-opus-4-5-20251015",
        "claude-sonnet-4-5",
        "claude-haiku-3-5",
        "claude-3-opus-20240229",
        "claude-3.5-sonnet",
        "gpt-4",
        "gpt-4o",
        "gpt-3.5-turbo",
        "o1",
        "o3",
        "o3-mini",
        "o4-mini",
        "mlx-community/Llama-3.2-3B-Instruct-4bit",
    ],
)
def test_known_model_id_literal_matches_a_pattern(literal: str) -> None:
    """The pattern set covers known model-ID literals across providers."""
    matched = any(p.search(literal) for p in _PATTERNS)
    assert matched, f"no pattern matched {literal!r}"


@pytest.mark.parametrize(
    "non_literal",
    [
        # Common false-positive surfaces that prior patterns might over-match.
        "the original ACC-001 spec",  # spec/issue IDs
        "see step-4 for details",  # step references
        "claude something",  # unanchored
        "ourgpt-internal",  # adjacent text
        "pgpt-4",  # not gpt-4 prefix-aligned
    ],
)
def test_non_literal_strings_do_not_match(non_literal: str) -> None:
    """Word boundaries and anchoring keep prose from triggering matches."""
    matched = any(p.search(non_literal) for p in _PATTERNS)
    assert not matched, f"unexpected match in {non_literal!r}"
