"""Scenario loader — reads JSON scenario files into typed dataclasses.

No LLM imports. Deterministic path only.

Schema discrimination
---------------------
The loader distinguishes three categories of JSON file:

1. **Workflow scenario** — has an ``events`` key.  The loader parses it into
   a :class:`Scenario`; any structural error is accumulated as a malformed-
   file path.

2. **Recognizable non-workflow file** — lacks ``events`` but carries at least
   one recognized alternative key (currently ``"query"`` for query-scenario
   fixtures).  These are silently skipped with a ``DEBUG``-level log.  They
   are *never* counted as malformed; changing this is a scope increase.

3. **Unrecognized file** — lacks ``events`` and carries none of the
   alternative recognized keys.  Treated as malformed by default.

After the full directory walk, if any malformed files were collected:
  - **Default (production / CI):** raise
    :class:`~samantha_server.errors.ScenarioCorpusError` with
    ``malformed_files`` populated.
  - **Debug-tolerance mode:** set the environment variable
    ``SCENARIO_LOADER_TOLERATE_MALFORMED=true``.  Malformed files are
    logged at ``WARNING`` level and skipped; good scenarios are returned.

Environment variables
---------------------
``SCENARIO_LOADER_TOLERATE_MALFORMED``
    When ``"true"`` (case-insensitive), downgrade malformed-file errors to
    warnings and skip those files.  Default: ``"false"``.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from samantha_server.errors import ScenarioCorpusError
from samantha_server.llm.schemas import ANSWER_TYPES, AnswerType
from samantha_server.models.roles import VALID_USER_ROLES, UserRole

logger = logging.getLogger(__name__)

# Alternative top-level keys that identify a recognized non-workflow file
# shape.  Files with any of these keys (and no ``events`` key) are silently
# skipped with a DEBUG log rather than treated as malformed.
#
# Currently the only safe matcher is ``query`` — every file under
# ``tests/fixtures/scenarios/query/`` carries it. ``database_state`` was
# previously included but removed (see PR #96 review M-02): it is plausible
# metadata for a workflow-adjacent file, so an accidentally-corrupted
# workflow scenario that lost ``events`` but retained ``database_state``
# would be silently skipped instead of surfacing as malformed. If a future
# corpus introduces a new non-workflow shape, add its discriminator here
# *only after* confirming it's not also a plausible workflow-file key.
_NON_WORKFLOW_KEYS: frozenset[str] = frozenset({"query"})


@dataclass(frozen=True)
class ScenarioStep:
    step_index: int
    event_type: str
    event_data: Mapping[str, Any]
    expected_next_state: str
    expected_applied_rules: tuple[str, ...]
    expected_flags: tuple[str, ...]
    # GH-171 / PR #179 review M2: corpus annotation of which engine path the
    # step is expected to flow through (`"deterministic"` or `"llm"`). Loaded
    # only when present in the JSON; treated as advisory by `_verdict_for_step`
    # — None means "don't compare" so older fixtures without the field
    # remain valid input.
    expected_routing_path: str | None = None
    # GH-184: LLM disposition expected by this step (only for llm_review
    # fixtures where the step carries ``expected_output.llm_disposition``).
    # None for all other step types; consumers treat None as "no assertion".
    llm_disposition: str | None = None


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    category: str  # rule_coverage / multi_rule / accumulated_state / etc.
    description: str
    steps: tuple[ScenarioStep, ...]
    # GH-184: top-level expected_output block from query fixtures
    # (``answer_type``, ``order_ids``, etc.). Not present in workflow
    # scenarios that lack a top-level expected_output key. Consumers
    # treat None as "no top-level assertion required".
    raw_expected_output: dict[str, Any] | None = None
    # GH-233: "now" anchor for temporal-reasoning queries. When present in
    # the fixture, loaded verbatim. When absent, computed as
    # max(events[0].event_data.orders[*].created_at) + 24h (ISO-8601 with Z).
    # None when no orders with created_at are present and no explicit value
    # was set. Prompt builders emit <prompt_timestamp> only when non-None.
    prompt_timestamp: str | None = None
    # GH-227: role of the user making the query. When present in the fixture,
    # loaded verbatim (validated against VALID_USER_ROLES). When absent, None.
    user_role: UserRole | None = None

    @property
    def expected_query_content(self) -> frozenset[str] | None:
        """GH-194 Slice 2: typed accessor for the top-level expected order_ids.

        Returns ``frozenset[str]`` when ``raw_expected_output`` is present and
        ``order_ids`` is a list of strings. Returns ``None`` when
        ``raw_expected_output`` is absent or ``order_ids`` is missing/malformed
        (with a WARNING log on malformed payloads).

        **Dual semantic** (GH-220): for ``answer_type=="order_list"`` fixtures
        the frozenset is a *filter result* (orders matching the query). For
        ``answer_type=="order_status"`` fixtures the frozenset is the *subject
        of the question* (the single order_id being asked about). Callers
        must read ``expected_answer_type`` to distinguish; the content gate
        and assertion path both do.
        """
        if self.raw_expected_output is None:
            return None
        order_ids = self.raw_expected_output.get("order_ids")
        if order_ids is None:
            return None
        if not isinstance(order_ids, list) or not all(isinstance(v, str) for v in order_ids):
            logger.warning(
                "Scenario %r: raw_expected_output.order_ids is malformed "
                "(expected list[str], got %r) — skipping content assertion",
                self.scenario_id,
                type(order_ids).__name__,
            )
            return None
        return frozenset(order_ids)

    @property
    def expected_query_sequence(self) -> tuple[str, ...] | None:
        """GH-222: typed accessor for the top-level expected order_ids as a ranked sequence.

        Returns ``tuple[str, ...]`` when ``raw_expected_output`` is present and
        ``order_ids`` is a list of strings. Returns ``None`` when
        ``raw_expected_output`` is absent or ``order_ids`` is missing/malformed
        (with a WARNING log on malformed payloads).

        Semantics: unlike ``expected_query_content`` (which returns a frozenset for
        set-equality), this accessor preserves insertion order for position-sensitive
        answer_types such as ``"prioritized_list"``. Callers should only invoke this
        accessor when ``expected_answer_type == "prioritized_list"``.
        """
        if self.raw_expected_output is None:
            return None
        order_ids = self.raw_expected_output.get("order_ids")
        if order_ids is None:
            return None
        if not isinstance(order_ids, list) or not all(isinstance(v, str) for v in order_ids):
            logger.warning(
                "Scenario %r: raw_expected_output.order_ids is malformed "
                "(expected list[str], got %r) — skipping sequence assertion",
                self.scenario_id,
                type(order_ids).__name__,
            )
            return None
        return tuple(order_ids)

    @property
    def expected_answer_type(self) -> AnswerType | None:
        """Cluster D (#6): typed accessor for the top-level expected answer_type.

        Returns an ``AnswerType`` string when ``raw_expected_output`` is present
        and ``answer_type`` is a recognized value (one of ``ANSWER_TYPES``).
        Returns ``None`` when ``raw_expected_output`` is absent, ``answer_type``
        is missing, or the value is unrecognized (with a WARNING log on malformed
        payloads). Mirrors the warning-on-malformed pattern of ``expected_query_content``.

        Callers consuming this should NOT fall back to
        ``raw_expected_output.get("answer_type")`` — use this accessor so mypy
        catches Literal-mismatch typos at type-check time.
        """
        if self.raw_expected_output is None:
            return None
        raw_at = self.raw_expected_output.get("answer_type")
        if raw_at is None:
            return None
        if raw_at not in ANSWER_TYPES:
            logger.warning(
                "Scenario %r: raw_expected_output.answer_type %r is not a recognized "
                "AnswerType (%r) — skipping answer_type assertion",
                self.scenario_id,
                raw_at,
                ANSWER_TYPES,
            )
            return None
        return raw_at  # type: ignore[no-any-return]


def _default_prompt_timestamp(raw: dict[str, Any]) -> str | None:
    """Compute the default prompt_timestamp from any event's orders.

    Returns ``max(created_at across all events' orders) + 24h`` as an
    ISO-8601 string with Z suffix, or None when no orders carry a
    ``created_at`` field.

    Scans every event in the scenario (not just ``events[0]``) so multi-
    step fixtures whose first step is not a ``clinical_query`` still
    anchor on the orders they reference.

    The ``+24h`` offset puts the prompt's "now" comfortably after the
    last order was created, matching how a clinician would query the
    next day's worklist; it is a corpus convention, not a clinical fact.
    Fixtures that need a specific anchor must set ``prompt_timestamp``
    explicitly (which short-circuits this function).
    """
    events = raw.get("events") or []
    timestamps: list[datetime.datetime] = []
    for event in events:
        event_data = event.get("event_data") or {}
        orders = event_data.get("orders") or []
        for order in orders:
            if not isinstance(order, dict):
                continue
            raw_ts = order.get("created_at")
            if not isinstance(raw_ts, str):
                continue
            try:
                ts = datetime.datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
            except ValueError:
                logger.warning(
                    "Skipping order with malformed created_at=%r in scenario %r",
                    raw_ts,
                    raw.get("scenario_id"),
                )
                continue
            timestamps.append(ts)
    if not timestamps:
        return None
    max_ts = max(timestamps) + datetime.timedelta(hours=24)
    return max_ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_step(raw_event: dict[str, Any]) -> ScenarioStep:
    expected = raw_event["expected_output"]
    return ScenarioStep(
        step_index=raw_event["step"],
        event_type=raw_event["event_type"],
        event_data=raw_event["event_data"],
        expected_next_state=expected["next_state"],
        expected_applied_rules=tuple(expected.get("applied_rules") or []),
        expected_flags=tuple(expected.get("flags") or []),
        expected_routing_path=expected.get("routing_path"),
        # GH-184: llm_disposition is optional; only llm_review steps carry it.
        llm_disposition=expected.get("llm_disposition"),
    )


def _parse_scenario(path: Path, raw: dict[str, Any]) -> Scenario:
    """Parse a Scenario from an already-parsed dict and the source path.

    *raw* is the dict obtained from json.loads — callers must not pass the path
    for re-reading.  *path* is used only for the category (parent dir name).

    Fields the engine does not consume are deliberately not parsed. The
    following top-level keys in upstream samantha-public fixtures are
    informational only and are NOT mirrored into Scenario / ScenarioStep:

      - ``tier``: a 1–5 difficulty annotation on query fixtures (1 = simplest
        single-key filter, 5 = multi-key reasoning or cross-order aggregation).
        External-tool metadata for harness/article analysis, not consumed by the
        runtime engine. Tier-aware harness reporting is a deferred follow-up
        (audit doc F-12, 2026-05-13).
      - ``database_state``: a snapshot of the orders table the upstream
        query harness consumes directly. The engine reads ``orders`` from
        ``events[0].event_data.orders``; the top-level copy is retained
        for upstream parity but not consumed here.
      - ``query`` (top-level): the query string. The engine reads the
        copy in ``events[0].event_data.query``.
      - ``expected_output`` (top-level, on query fixtures): the upstream
        answer-key block. The engine compares against
        ``events[].expected_output``.

    Adding a new Scenario / ScenarioStep field is the right move if any of
    these become engine-relevant; silent drop is the current intent.

    GH-184: ``raw_expected_output`` is now also preserved. Query fixtures
    carry a top-level ``expected_output`` block with ``answer_type`` and
    ``order_ids`` that the engine content gate (GH-194) consumes to score
    query/llm_review responses. Workflow scenarios without a top-level
    ``expected_output`` key leave the field as None.
    """
    category = path.parent.name
    steps = tuple(sorted((_parse_step(e) for e in raw["events"]), key=lambda s: s.step_index))
    # GH-184 fix-review M9: preserve empty dict as {} (not coerced to None).
    # raw_expected_output=None means "expected_output key absent from fixture".
    # raw_expected_output={} means "key present but empty — no assertion defined yet".
    # The prior `raw_eo if raw_eo else None` silently coerced {} to None, hiding
    # fixtures where the author intended to fill in order_ids later.
    raw_eo: dict[str, Any] | None = raw.get("expected_output")
    # GH-233: explicit prompt_timestamp wins; fall back to default computation.
    # Validate explicit values as ISO-8601 here so a fixture typo surfaces as
    # a malformed-scenario file rather than silently corrupting the prompt
    # and the receipt's prompt_timestamp_hash downstream.
    explicit_ts: str | None = raw.get("prompt_timestamp")
    if explicit_ts is not None:
        if not isinstance(explicit_ts, str):
            raise ValueError(
                f"prompt_timestamp must be a string (got {type(explicit_ts).__name__})"
            )
        try:
            datetime.datetime.fromisoformat(explicit_ts.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(
                f"prompt_timestamp is not a valid ISO-8601 timestamp: {explicit_ts!r}"
            ) from exc
    prompt_timestamp: str | None = (
        explicit_ts if explicit_ts is not None else _default_prompt_timestamp(raw)
    )
    # GH-227: explicit user_role; validate against VALID_USER_ROLES.
    # Adjacent to prompt_timestamp handling for symmetry.
    # PR243 S8: when BOTH scenario-level user_role AND a step's event_data.user_role
    # are present, raise if they conflict (idempotent match is allowed).
    raw_user_role = raw.get("user_role")
    user_role: UserRole | None = None
    if raw_user_role is not None:
        if not isinstance(raw_user_role, str) or raw_user_role not in VALID_USER_ROLES:
            raise ValueError(
                f"user_role must be one of: accessioner|histotech|pathologist|lab_manager "
                f"(got {type(raw_user_role).__name__})"
            )
        user_role = raw_user_role  # type: ignore[assignment]
        # Check each step for a conflicting event_data.user_role annotation.
        for step in steps:
            step_role = step.event_data.get("user_role")
            if step_role is not None and step_role != user_role:
                raise ValueError(
                    f"user_role conflict: scenario-level user_role={user_role!r} conflicts "
                    f"with step {step.step_index} event_data.user_role "
                    f"(type={type(step_role).__name__})"
                )

    return Scenario(
        scenario_id=raw["scenario_id"],
        category=category,
        description=raw.get("description", ""),
        steps=steps,
        raw_expected_output=raw_eo,
        prompt_timestamp=prompt_timestamp,
        user_role=user_role,
    )


def _is_recognized_non_workflow(raw: dict[str, Any]) -> bool:
    """Return True if *raw* looks like a recognized non-workflow file shape.

    Currently: any file that has at least one key from ``_NON_WORKFLOW_KEYS``
    but lacks an ``events`` key is considered a recognized non-workflow file
    (e.g., query-scenario fixtures).
    """
    return bool(_NON_WORKFLOW_KEYS & raw.keys())


def load_scenarios(directory: Path) -> list[Scenario]:
    """Load all JSON scenario files from *directory* recursively.

    Each JSON file becomes one Scenario.  The category is derived from the
    immediate parent directory name.  Returns an empty list when *directory*
    contains no JSON files and no malformed files.

    Raises
    ------
    ScenarioCorpusError
        When *directory* does not exist, or when one or more files could not
        be parsed and ``SCENARIO_LOADER_TOLERATE_MALFORMED`` is not ``"true"``.
        ``malformed_files`` is empty for the missing-directory case.

    Notes
    -----
    Set ``SCENARIO_LOADER_TOLERATE_MALFORMED=true`` to downgrade malformed-file
    errors to ``WARNING`` logs and skip those files instead of raising.
    """
    if not directory.exists():
        raise ScenarioCorpusError(directory=directory, malformed_files=())

    tolerate = os.environ.get("SCENARIO_LOADER_TOLERATE_MALFORMED", "false").lower() == "true"

    scenarios: list[Scenario] = []
    malformed: list[Path] = []

    for path in sorted(directory.rglob("*.json")):
        if path.name.startswith("."):
            continue

        # --- Parse JSON ---
        try:
            raw: dict[str, Any] = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            logger.warning("Malformed JSON in %s: %s", path, exc)
            malformed.append(path)
            continue

        # --- Discriminate file shape ---
        if "events" not in raw:
            if _is_recognized_non_workflow(raw):
                logger.debug("Skipping non-workflow file %s (recognized alternative schema)", path)
            else:
                logger.warning("Malformed scenario file %s: missing 'events' key", path)
                malformed.append(path)
            continue

        # --- Parse workflow scenario ---
        try:
            scenarios.append(_parse_scenario(path, raw))
        except (KeyError, ValueError) as exc:
            logger.warning("Failed to parse scenario in %s: %s", path, exc)
            malformed.append(path)

    if malformed and not tolerate:
        # Tolerate-mode: already logged at WARNING above; just skip them.
        # Default mode: fail loud per the documented contract.
        raise ScenarioCorpusError(
            directory=directory,
            malformed_files=tuple(malformed),
        )

    return scenarios
