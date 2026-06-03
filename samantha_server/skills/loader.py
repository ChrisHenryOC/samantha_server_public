"""agentskills.io 3-stage skill lifecycle: discover, load, execute."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache, lru_cache
from pathlib import Path
from typing import Any, Protocol

import yaml

from samantha_server.errors import SamanthaError

# Default specs root: the `specs/` directory bundled next to this module.
_DEFAULT_SPECS_ROOT = Path(__file__).resolve().parent / "specs"

# ---------------------------------------------------------------------------
# STATE_TO_SKILL infrastructure
# ---------------------------------------------------------------------------

# Workflow states that intentionally have no associated skill — terminal/admin
# states where the LLM cannot propose transitions. Every VALID_STATES entry
# must either appear in some skill's applies_to_states OR in this set; discover()
# enforces this invariant at startup.
STATES_WITHOUT_SKILL: frozenset[str] = frozenset(
    {
        "ORDER_COMPLETE",
        "ORDER_TERMINATED",
        "ORDER_TERMINATED_QNS",
        # PENDING_HUMAN_REVIEW is terminal for v1 (no worker yet; Phase 4 will
        # promote it to a queue-backed state). No skill claimed for it here.
        "PENDING_HUMAN_REVIEW",
        # PENDING_LLM_REVIEW is covered by the specimen-review skill's
        # applies_to_states (specimen_review/SKILL.md). It is NOT listed here.
    }
)


# Module-level reference to VALID_STATES used for the discover-time invariant.
# Exposed as a separate name so tests can monkeypatch it without importing
# samantha_server.models.context directly into the loader.
def _load_valid_states() -> frozenset[str]:
    from samantha_server.models.context import VALID_STATES

    return VALID_STATES


# The actual frozenset is imported lazily at discover() time to avoid a
# circular-import at module load. _VALID_STATES_FOR_INVARIANT is the
# patchable shim used by tests.
_VALID_STATES_FOR_INVARIANT: frozenset[str] | None = None

# Line-anchored frontmatter fence: matches a SKILL.md whose first line is `---`,
# captures the YAML between two fence lines, and the body that follows.  The
# fences must occupy whole lines, so a markdown horizontal rule (`---`) inside
# the body never collides with the closing fence.
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(?P<yaml>.*?)\n---\s*(?:\n|\Z)(?P<body>.*)", re.DOTALL)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class SkillLoaderError(SamanthaError):
    """Raised for any skill loading failure (parse error, missing field, etc.)."""


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SkillSpec:
    name: str
    description: str
    path: Path
    applies_to_states: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise SkillLoaderError(f"SkillSpec.name must be a non-empty string, got {self.name!r}")
        if not isinstance(self.description, str) or not self.description.strip():
            raise SkillLoaderError(
                f"SkillSpec.description must be a non-empty string, got {self.description!r}"
            )


@dataclass(frozen=True)
class SkillResult:
    # TODO(W1 Phase 3): widen to carry applied_rules, next_state, flags,
    # model_id, skill_doc_hash, and receipt-linkage fields. The current
    # `decision_text` placeholder is the raw LLM completion only.
    name: str
    decision_text: str


class SkillContext(Protocol):
    """Marker protocol for the per-decision context passed to ``execute()``.

    Intentionally empty until W1 Phase 3 lands the LLM path; the protocol
    exists so callers and the future implementation share a named seam
    rather than passing ``object``.
    """


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


@lru_cache(maxsize=32)
def _read_text(path: Path) -> str:
    """Read *path* as UTF-8, wrapping OSError as SkillLoaderError.

    M-11: cached so repeated load() calls for the same skill path do not
    re-read the file. maxsize=32 covers the expected number of skills (O(10))
    with headroom; LRU eviction protects against unbounded growth.
    """
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SkillLoaderError(f"{path}: failed to read SKILL.md — {exc}") from exc


def _parse_skill_md(text: str, path: Path) -> tuple[dict[str, Any], str]:
    """Split *text* into (frontmatter mapping, body).

    Raises SkillLoaderError on missing fence, malformed YAML, non-mapping
    frontmatter, or empty/non-string ``name``/``description``.
    """
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        raise SkillLoaderError(
            f"{path}: missing or malformed frontmatter — expected '---' fence on line 1 "
            "and a closing '---' on its own line"
        )

    try:
        meta = yaml.safe_load(match.group("yaml"))
    except yaml.YAMLError as exc:
        raise SkillLoaderError(f"{path}: invalid YAML in frontmatter — {exc}") from exc

    if not isinstance(meta, dict):
        raise SkillLoaderError(
            f"{path}: frontmatter must be a YAML mapping, got {type(meta).__name__}: {meta!r}"
        )

    for field in ("name", "description"):
        value = meta.get(field)
        if not isinstance(value, str) or not value.strip():
            raise SkillLoaderError(
                f"{path}: frontmatter field '{field}' must be a non-empty string, got {value!r}"
            )

    return meta, match.group("body").lstrip("\n")


# ---------------------------------------------------------------------------
# STATE_TO_SKILL builder
# ---------------------------------------------------------------------------


def build_state_to_skill(specs: Mapping[str, SkillSpec]) -> dict[str, str]:
    """Invert the per-skill applies_to_states lists into a state → skill mapping.

    Skills with an empty applies_to_states (e.g., ``query-routing``) are
    silently skipped — they don't participate in rule-firing.

    Parameters
    ----------
    specs:
        A mapping of skill_name → SkillSpec, as returned by ``discover()``.

    Returns
    -------
    dict[str, str]
        Mapping of state_name → skill_name. Each state appears at most once;
        duplicate state claims across skills will raise ``SkillLoaderError``.
    """
    state_to_skill: dict[str, str] = {}
    for skill_name, spec in specs.items():
        for state in spec.applies_to_states:
            if state in state_to_skill:
                raise SkillLoaderError(
                    f"State {state!r} is claimed by both {state_to_skill[state]!r} "
                    f"and {skill_name!r}. Each state may be claimed by at most one skill."
                )
            state_to_skill[state] = skill_name
    return state_to_skill


# ---------------------------------------------------------------------------
# Stage 1: Discovery
# ---------------------------------------------------------------------------


@cache
def discover(specs_root: Path = _DEFAULT_SPECS_ROOT) -> dict[str, SkillSpec]:
    """Scan *specs_root*/*/SKILL.md files and return an index keyed by skill name.

    Cached with functools.cache so repeated calls with the same specs_root return
    the same dict[str, SkillSpec] result without re-globbing the filesystem.
    Subsequent calls are O(1).

    Cache invalidation: call discover.cache_clear() when the skill files on disk
    change (e.g., in tests that write temporary SKILL.md files). The
    receipts_test_isolation conftest fixture calls discover.cache_clear() at
    teardown to ensure per-test isolation.

    Designed to be called **once at engine init**; pass the returned index
    to ``load()`` to avoid re-globbing the filesystem on every activation.

    Raises SkillLoaderError if:
    - *specs_root* does not exist or is not a directory.
    - Any SKILL.md cannot be read (OSError).
    - A SKILL.md has missing or malformed frontmatter.
    - The YAML between the fences is malformed or not a mapping.
    - A required field (``name`` or ``description``) is absent or non-string.
    - Two SKILL.md files declare the same ``name``.
    - A VALID_STATES entry is neither covered by a skill's applies_to_states
      nor listed in STATES_WITHOUT_SKILL (discover-time invariant).
    """
    if not specs_root.is_dir():
        raise SkillLoaderError(f"specs_root does not exist or is not a directory: {specs_root}")

    index: dict[str, SkillSpec] = {}

    for skill_md in sorted(specs_root.glob("*/SKILL.md")):
        text = _read_text(skill_md)
        meta, _body = _parse_skill_md(text, skill_md)

        skill_name: str = meta["name"]
        if skill_name in index:
            raise SkillLoaderError(
                f"Duplicate skill name '{skill_name}': {index[skill_name].path} and {skill_md}"
            )
        # applies_to_states is optional; coerce list → tuple (empty when absent).
        raw_states = meta.get("applies_to_states") or []
        applies_to_states: tuple[str, ...] = tuple(str(s) for s in raw_states)

        index[skill_name] = SkillSpec(
            name=skill_name,
            description=meta["description"],
            path=skill_md,
            applies_to_states=applies_to_states,
        )

    # Discover-time invariant: every VALID_STATES entry must be covered.
    # Only enforce when loading from the default (bundled) specs root so that
    # tmp_path-based tests with partial skill sets can opt in by patching
    # _VALID_STATES_FOR_INVARIANT.  The invariant fires when
    # _VALID_STATES_FOR_INVARIANT is set (non-None) OR when using the
    # default specs root.
    # M-09: removed unnecessary global statement — function reads
    # _VALID_STATES_FOR_INVARIANT but never assigns to it at module scope.
    valid_states_to_check: frozenset[str] | None
    if _VALID_STATES_FOR_INVARIANT is not None:
        valid_states_to_check = _VALID_STATES_FOR_INVARIANT
    elif specs_root == _DEFAULT_SPECS_ROOT:
        valid_states_to_check = _load_valid_states()
    else:
        valid_states_to_check = None

    if valid_states_to_check is not None:
        state_to_skill = build_state_to_skill(index)
        missing = sorted(
            state
            for state in valid_states_to_check
            if state not in state_to_skill and state not in STATES_WITHOUT_SKILL
        )
        if missing:
            raise SkillLoaderError(
                f"discover() invariant violated: the following VALID_STATES entries are not "
                f"covered by any skill's applies_to_states or STATES_WITHOUT_SKILL: "
                f"{missing}. Either add applies_to_states to the relevant skill's SKILL.md "
                f"or add the state to STATES_WITHOUT_SKILL."
            )

    return index


# ---------------------------------------------------------------------------
# Stage 2: Activation
# ---------------------------------------------------------------------------


def load(
    name: str,
    specs_root: Path = _DEFAULT_SPECS_ROOT,
    index: Mapping[str, SkillSpec] | None = None,
) -> str:
    """Return the playbook body for *name*, with frontmatter stripped.

    For hot-path callers, pass a pre-built *index* from ``discover()`` to
    avoid re-globbing on every call. When *index* is ``None``, ``discover()``
    is called once for convenience.

    Raises SkillLoaderError if *name* is not in the index.
    """
    if index is None:
        index = discover(specs_root=specs_root)
    if name not in index:
        raise SkillLoaderError(f"Unknown skill '{name}'. Available: {sorted(index.keys())}")

    text = _read_text(index[name].path)
    _meta, body = _parse_skill_md(text, index[name].path)
    return body


# ---------------------------------------------------------------------------
# Stage 3: Execution stub
# ---------------------------------------------------------------------------


def execute(
    name: str,
    ctx: SkillContext,
    specs_root: Path = _DEFAULT_SPECS_ROOT,
) -> SkillResult:
    """Execute the named skill against *ctx*.

    Not implemented; will be delivered in W1 Phase 3. The ``specs_root``
    parameter mirrors ``discover``/``load`` so callers can wire it through;
    it will be forwarded to the discovery step when the implementation lands.
    """
    raise NotImplementedError(
        f"execute() for skill '{name}' is not yet implemented; "
        "execution support lands in W1 Phase 3."
    )
