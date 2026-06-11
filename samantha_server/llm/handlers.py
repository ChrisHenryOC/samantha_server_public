"""LLM action handlers for the query routing path.

This module owns prompt assembly, PHI safe-context application, and the
LLMClient call. It returns an EngineDecision — it never calls evaluate().

Design reference:
- G11: decision_traces typed union — QueryTrace for query_response outcomes;
  RefusalTrace for all error/refusal outcomes.
- G17: HMAC-SHA256 for PHI-bearing event_data hash — query_text_hash uses
  phi._hmac_hex(phi._canonical_event_data(...)) not bare SHA-256.
- G18: PHI boundary via phi_safe() — PHIBoundaryError is caught here and
  converted to a STAGE_PRE_PHI_BOUNDARY refusal receipt (never escapes).

Architectural invariants honored here:
- PHI boundary (G18): every prompt construction routes through phi_safe(ctx)
  before any LLMClient.complete call. One deliberate exception (
  2026-05-11 PHI clarification): event_data["query"] is extracted from the
  raw SpecimenContext and embedded in the prompt under the local-LLM +
  self-hosted-Langfuse topology. The receipt-bearing query_text_hash on
  QueryTrace remains computed from the canonical event_data, so the audit
  trail is unaffected. All other PHI fields continue to route through
  phi_safe() exclusively.
- No rule firing: queries do not call evaluate() — applied_rule_id is
  always None on the returned EngineDecision.
- Receipt emission is the router's responsibility (router.py), not this
  module's.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Mapping
from typing import Annotated, Final, Literal
from xml.sax.saxutils import escape as saxutils_escape

from opentelemetry import trace as _otel_trace
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic import ValidationError as PydanticValidationError

from samantha_server import config
from samantha_server.canonicalization import PICK_LISTS, canonicalize
from samantha_server.engine.decision import (
    _DISPOSITION_TO_TRACE_VERB,
    ClarificationTrace,
    EngineDecision,
    LLMReviewTrace,
    QueryTrace,
    RefusalTrace,
    compute_event_input_hash,
)
from samantha_server.errors import LLMClientError, PHIBoundaryError
from samantha_server.llm import phi as _phi
from samantha_server.llm.client import ChatMessage, LLMClient
from samantha_server.llm.phi import SafeContext, phi_safe
from samantha_server.llm.preflight import PreflightMissing
from samantha_server.llm.schemas import (
    QUERY_RESPONSE_V1_JSON_SCHEMA,
    SPECIMEN_REVIEW_RESPONSE_V1_JSON_SCHEMA,
    QueryResponseV1,
    SpecimenReviewResponseV1,
)
from samantha_server.models.context import SpecimenContext
from samantha_server.models.roles import UserRole, parse_user_role
from samantha_server.observability.counters import CounterRegistry
from samantha_server.observability.otel import (
    llm_complete_json_with_span,
    llm_complete_with_span,
    set_span_attribute,
)
from samantha_server.skills.loader import SkillLoaderError, SkillSpec
from samantha_server.skills.loader import load as load_skill

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Query-block rendering
# ---------------------------------------------------------------------------

_MAX_QUERY_TEXT_LEN: Final = 1024  # per-field cap; security review §F-1
_NO_QUERY_SENTINEL: Final = "(no query provided)"
# Trojan-Source defense: strip C0/C1 controls + Unicode bidi overrides (U+202A-U+202E)
# + isolate range (U+2066-U+2069) before XML-escaping.
_QUERY_TEXT_NOISE_RE: Final = re.compile(r"[\x00-\x1f\x7f‪-‮⁦-⁩]")


def _render_query_block(query_text: str) -> str:
    """Render the <query>...</query> block for the clinical-query LLM prompt.

    Empty / whitespace-only / all-noise → sentinel. Otherwise strip control
    + bidi characters (Trojan-Source defense), truncate to _MAX_QUERY_TEXT_LEN
    (context-window defense), XML-escape (fence-breakout defense).
    """
    if not query_text:
        return f"<query>{_NO_QUERY_SENTINEL}</query>"
    cleaned = _QUERY_TEXT_NOISE_RE.sub("", query_text)
    if len(cleaned) > _MAX_QUERY_TEXT_LEN:
        _logger.warning(
            "Query text exceeds %d chars (got %d); truncating for prompt.",
            _MAX_QUERY_TEXT_LEN,
            len(cleaned),
        )
        cleaned = cleaned[:_MAX_QUERY_TEXT_LEN]
    if not cleaned:
        return f"<query>{_NO_QUERY_SENTINEL}</query>"
    return f"<query>{saxutils_escape(cleaned)}</query>"


# Disposition class and parse_disposition() deleted (hard cutover).
# The specimen-review path now always uses _handle_pending_llm_review_json() which
# parses SpecimenReviewResponseV1 JSON. There is no free-text fallback.


def _sha256_hex(value: str) -> str:
    """Return lowercase hex SHA-256 of a UTF-8 string.

    Used for non-PHI-bearing content hashes (skill body, LLM response text).
    PHI-bearing event_data is hashed via phi._hmac_hex (G17).
    """
    return hashlib.sha256(value.encode()).hexdigest()


def _strip_markdown_fences(text: str) -> str:
    """Strip markdown code fences from *text*, returning the content between them.

    Handles three failure modes the old anchored regex missed:
    (a) Prose before the fence ("Here is your answer:\\n```json{...}```").
    (b) Missing trailing newline before the closing fence ("```json{...}```").
    (c) Single-line fence with no internal newline ("```json{...}```").

    Algorithm:
    1. Find the FIRST occurrence of ``` (optionally followed by "json").
    2. Find the LAST occurrence of ``` after the first.
    3. Return the substring between them, stripped.
    4. If no fence pair is found, return *text* unchanged.

    The model should not add fences (the skill prompt says "no markdown fences"),
    but defensive stripping prevents a systematic parse failure when the model
    wraps anyway.
    """
    # Find the opening fence (``` optionally followed by "json" and optional whitespace).
    open_start = text.find("```")
    if open_start == -1:
        return text

    # Skip past the opening ```, the optional "json" tag, and any immediately
    # following whitespace/newline so the extracted content starts at the JSON.
    after_open = open_start + 3
    if text[after_open : after_open + 4] == "json":
        after_open += 4
    # Strip a single leading newline or space after the tag.
    if after_open < len(text) and text[after_open] in ("\n", " "):
        after_open += 1

    # Find the LAST occurrence of ```.
    close_start = text.rfind("```")
    if close_start <= open_start:
        # No closing fence found after the opening fence.
        return text

    content = text[after_open:close_start].strip()
    return content


def _canonical_response_hash(model: QueryResponseV1 | SpecimenReviewResponseV1) -> str:
    """Return sha256 hex of the canonical JSON of a parsed structured response model.

    Canonical form: json.dumps(model.model_dump(), sort_keys=True, separators=(",",":")).
    This is the response_text_hash stored in the trace on successful parse.
    Used by both the clinical-query path (QueryResponseV1) and the specimen-review
    path (SpecimenReviewResponseV1).
    """
    canonical = json.dumps(model.model_dump(), sort_keys=True, separators=(",", ":"))
    return _sha256_hex(canonical)


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


class LLMOrderBlock(BaseModel, frozen=True):
    """Canonical, frozen, length-bounded projection of one order for the prompt.

    Review notes:
    - Frozen Pydantic model (replaces `tuple[Mapping[str, object], ...]`):
      caller-supplied dicts are validated at construction; missing required
      keys raise loudly, oversized strings are rejected, and unknown keys
      are dropped (extra="ignore").
    - `flags` is normalized to a sorted tuple so set/frozenset inputs are
      JSON-serializable and produce the same canonical form regardless of
      iteration order (review M-3, M-4).
    - String-field length caps mirror `FIELD_MAX_LENGTHS` from
      `models.context` (M-7).

    `fixation_time_hours` and `ordered_tests` were added so the
    `<orders>` prompt block carries the fields QR-023 needs (HER2 fixation
    timing). Their bounds are NOT sourced from `FIELD_MAX_LENGTHS`:

    - `fixation_time_hours` is bounded `[0, 200]` h. The clinical operating
      range for HER2 IHC is `[6, 72]` h per `samantha_server/rules/specs/
      ACC-006.yaml`; the `le=200` ceiling is a defensive data-quality guard
      at the LLM-prompt boundary against malformed LIS payloads (e.g., a
      day-vs-hour unit mix-up). The bound is intentionally wider than the
      clinical range so a legitimate long-fixation outlier (e.g., a
      weekend-soaked specimen at 96-120 h) is still surfaced to the LLM
      rather than silently dropped by `_coerce_orders`.
    - `ordered_tests` is a tuple of strings, capped at 50 items and 100
      chars per item — sized for IHC panel listings (real-world max is
      ~30 markers) with headroom.

    PHI classification: `order_id` and `created_at` on this payload are
    **not** PHI in this project's domain — they are synthetic LIS
    identifiers and operational timestamps, not patient identifiers. Do
    not conflate with the per-event `Order.order_id` surface in
    `phi.py`'s `_ORDER_PASS_THROUGH`, which is a separate (verbatim port
    from samantha-public) field with different provenance. (Per that
    per-event `order_id` is now a pass-through too, not hashed.) `ordered_tests`
    (e.g., "ER", "PR", "HER2 IHC") names panel components — also not PHI.
    """

    order_id: str = Field(min_length=1, max_length=100)
    current_state: str | None = Field(default=None, max_length=50)
    specimen_type: str | None = Field(default=None, max_length=100)
    anatomic_site: str | None = Field(default=None, max_length=100)
    priority: str | None = Field(default=None, max_length=20)
    flags: tuple[str, ...] = ()
    created_at: str | None = Field(default=None, max_length=40)
    fixation_time_hours: float | None = Field(default=None, ge=0, le=200)
    ordered_tests: Annotated[
        tuple[Annotated[str, Field(max_length=100)], ...], Field(max_length=50)
    ] = ()

    model_config = ConfigDict(extra="ignore")

    @field_validator("flags", mode="before")
    @classmethod
    def _normalize_flags(cls, value: object) -> tuple[str, ...]:
        """Coerce list/tuple/set/frozenset of strings to a sorted tuple.

        Without this, a `set` value reaches `json.dumps` and raises
        TypeError, breaking the handler's "never raises" contract.
        """
        if value is None:
            return ()
        if isinstance(value, (list, tuple, set, frozenset)):
            return tuple(sorted(str(x) for x in value))
        raise ValueError(f"flags must be a list/tuple/set; got {type(value).__name__}")


def _coerce_orders(raw: object) -> tuple[LLMOrderBlock, ...]:
    """Validate and canonicalize an `event_data['orders']` payload.

    Returns a tuple of LLMOrderBlock sorted by `order_id`. Invalid items
    (non-Mapping, missing required `order_id`, oversized fields) are skipped
    individually with a WARNING log so a JSON-schema mismatch surfaces in
    logs instead of silently producing an all-null prompt block (review
    M-5, M-6).

    Top-level non-list/tuple values (a bare dict, a string) collapse to ()
    with a WARNING — the caller payload was malformed.
    """
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)):
        _logger.warning(
            "event_data['orders'] is not a list/tuple (type=%s); ignoring orders payload",
            type(raw).__name__,
        )
        return ()
    blocks: list[LLMOrderBlock] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            _logger.warning(
                "event_data['orders'][%d] is not a Mapping (type=%s); skipping",
                index,
                type(item).__name__,
            )
            continue
        try:
            blocks.append(LLMOrderBlock.model_validate(item))
        except PydanticValidationError as exc:
            _logger.warning(
                "event_data['orders'][%d] failed validation; skipping: %s",
                index,
                exc.errors(include_url=False, include_input=False),
            )
    blocks.sort(key=lambda b: b.order_id)
    return tuple(blocks)


def _orders_canonical_json(orders: tuple[LLMOrderBlock, ...]) -> str:
    """Deterministic JSON encoding of an order tuple (review M-1, M-2).

    The encoding is a function of the *input list order*: keys within each
    dict are sorted, but the list itself is rendered as given. So callers
    control the resulting bytes by choosing which iteration order they pass.

    Used at two call sites in ``handle_clinical_query`` with two distinct
    input orderings:

    - **Hash path** — input is the canonical (order_id-sorted) tuple from
      ``_coerce_orders``. The resulting JSON is HMAC'd into the receipt's
      ``database_state_hash``. The hash is therefore a set-fingerprint:
      different fixture orderings of the same order set produce the same
      hash.

    - **Prompt path** — input is the LIS-priority-sorted tuple
      from ``_orders_priority_sorted(canonical_orders)``. The resulting
      JSON is rendered verbatim into the prompt's ``<orders>`` block so
      the LLM sees orders pre-sorted the way a real LIS query would emit
      them.

    Audit-recovery contract: given a receipt's ``database_state_hash`` and
    the prompt's ``<orders>`` JSON (captured on the LLM span via
    ``gen_ai.prompt``), a verifier reconstructs the hash by:

      1. Parse the prompt JSON into a list of order dicts.
      2. Pass through ``_coerce_orders`` — which re-canonicalises by
         ``order_id``, undoing the priority sort.
      3. Re-encode via this function, HMAC, and compare against the
         receipt hash.

    The legacy invariant "rendered block and ``database_state_hash``
    cannot diverge" is intentionally relaxed; the two derive from the
    same order set but with different list orderings, and the recovery
    path above proves equivalence at audit time.
    """
    return json.dumps(
        [b.model_dump() for b in orders],
        sort_keys=True,
        separators=(",", ":"),
    )


# Priority-rank map for the LIS-style sort used by the prompt's
# `<orders>` block. Mirrors the 3-key sort documented in
# `samantha_server/skills/specs/query_routing/SKILL.md` § prioritized_list:
# key 1 (priority DESC), key 2 (flags-present DESC), key 3 (created_at ASC).
# Values: higher number = higher precedence in the sort. Unknown priority
# strings (including the None case for missing fields) get rank 0, sorting
# below the explicit-priority orders.
_PRIORITY_RANK: Final[dict[str, int]] = {
    "rush": 2,
    "routine": 1,
}


def _orders_priority_sorted(
    orders: tuple[LLMOrderBlock, ...],
) -> tuple[LLMOrderBlock, ...]:
    """Re-sort orders by LIS-style 3-key priority for the prompt's `<orders>` block.

    Sort keys (mirrors the SKILL.md `### prioritized_list` § specification):

    1. **Priority DESC**: rush > routine > unknown (None). Higher tier first.
    2. **Flags-present DESC**: any non-empty `flags` tuple > empty tuple.
       Flagged orders surface first within their priority tier.
    3. **Created-at ASC**: older `created_at` first. Lexicographic on
       ISO-8601 strings (older string compares as "less", which sorts first
       under ascending order — correct for "oldest first").

     background: cross-model failures on QR-020 / QR-021 (every non-
    Gemma candidate in the 2026-05-15 model retest failed these sequence-
    ordering queries) traced back to the corpus testing the LLM at
    SQL-equivalent ORDER BY + LIMIT work that a real LIS would do server-
    side. Pre-sorting at scaffolding time matches production semantics:
    the LLM walks the pre-sorted block top-down and applies the query's
    state filter / top-N cutoff, but does not re-sort.

    The function preserves the input length and content; only the order
    of the tuple changes.
    """
    return tuple(
        sorted(
            orders,
            key=lambda o: (
                -_PRIORITY_RANK.get(o.priority or "", 0),
                -1 if o.flags else 0,
                o.created_at or "",
            ),
        )
    )


def build_query_prompt(
    skill_body: str,
    orders_json: str = "",
    *,
    query_text: str = "",
    user_role: UserRole | None = None,
    prompt_timestamp: str | None = None,
) -> str:
    """Assemble the LLM prompt for a clinical query.

    Layout (stable, documented order):
    1. <query> block — the literal query text; sentinel when absent.
    2. <user_role> block — the role of the requesting user;
       only when ``user_role`` is non-None.
    3. <prompt_timestamp> block — "now" anchor for temporal reasoning;
       only when ``prompt_timestamp`` is non-None.
    4. <skill> block — the verbatim skill body.
    5. <orders> block — the canonical JSON of database_state.orders, only
       when ``orders_json`` is non-empty.

    <similar_scenarios> and <safe_context> blocks removed.
    similar_scenarios always rendered '(none available)' (production index
    empty + the corpus guard returns ()). safe_context block removed from
    clinical_query path (ctx.order is a harness artifact for query events;
    audit role served by receipts + Langfuse span metadata).

    Parameters
    ----------
    skill_body:
        The verbatim body text of the query-routing skill.
    orders_json:
        Pre-canonicalized JSON encoding of the orders payload, **rendered
        verbatim into the prompt's `<orders>` block**. Empty string omits
        the block. This is the LIS-priority-sorted encoding
        (`_orders_canonical_json(_orders_priority_sorted(...))`), NOT the
        canonical-(order_id) JSON used to derive the receipt's
        ``database_state_hash``. The two encodings derive from the same
        order set but apply different list orderings — see
        ``_orders_canonical_json`` for the audit-recovery contract. (The
        legacy invariant "rendered block and `database_state_hash`
        cannot diverge" is intentionally relaxed.)
    query_text:
        The literal query string from event_data. Must be
        extracted from the raw SpecimenContext BEFORE phi_safe() runs.
        XML-escaped via saxutils to prevent fence breakout. Empty string
        emits a ``(no query provided)`` sentinel.
    user_role:
        The role of the requesting user. When non-None, emitted as
        ``<user_role>…</user_role>`` between ``<query>`` and
        ``<prompt_timestamp>``. None omits the block entirely.
    prompt_timestamp:
        ISO-8601 "now" anchor for temporal-reasoning queries.
        When non-None, emitted as ``<prompt_timestamp>…</prompt_timestamp>``
        between ``<user_role>`` (or ``<query>``) and ``<skill>``. None omits
        the block entirely.

    Returns
    -------
    str
        The assembled prompt string; deterministic for identical inputs.
    """
    # --- 1. Query block ---
    query_block = _render_query_block(query_text)

    blocks = [query_block]

    # --- 2. User-role block ---
    # XML-escape for fence-breakout defense; valid roles contain only
    # lowercase ASCII letters and underscores so this is a no-op in practice.
    if user_role is not None:
        escaped_role = saxutils_escape(user_role)
        blocks.append(f"<user_role>{escaped_role}</user_role>")

    # --- 3. Prompt-timestamp block ---
    # XML-escape for fence-breakout defense, mirroring _render_query_block.
    # No-op for well-formed ISO-8601 inputs.
    if prompt_timestamp is not None:
        escaped_ts = saxutils_escape(prompt_timestamp)
        blocks.append(f"<prompt_timestamp>{escaped_ts}</prompt_timestamp>")

    # --- 4. Skill block ---
    skill_block = f"<skill>\n{skill_body}\n</skill>"
    blocks.append(skill_block)

    # --- 5. Database-state orders block ---
    if orders_json:
        blocks.append(f"<orders>\n{orders_json}\n</orders>")
    return "\n\n".join(blocks)


# ---------------------------------------------------------------------------
# Clinical query handler
# ---------------------------------------------------------------------------


def handle_clinical_query(
    ctx: SpecimenContext,
    llm_client: LLMClient,
    skills_index: Mapping[str, SkillSpec],
    *,
    prompt_timestamp: str | None = None,
    counters: CounterRegistry | None = None,
) -> EngineDecision:
    """Handle a clinical_query event via the LLM path.

    Does NOT call evaluate(). The EngineDecision
    returned always has applied_rule_id=None and next_state=ctx.current_state.
    Receipt emission is the caller's (router's) responsibility.

    Error contract:
    - SkillLoaderError → EngineDecision with RefusalTrace(
          refusal_reason="STAGE_PRE_SKILL_UNAVAILABLE",
          refusal_stage="PRE", judge_verdict=None, alternatives=()
      ) and outcome="refused_skill_unavailable".
    - LLMClientError → EngineDecision with RefusalTrace(
          refusal_reason="STAGE_PRE_LLM_UNAVAILABLE",
          refusal_stage="PRE", judge_verdict=None, alternatives=()
      ) and outcome="refused_llm_unavailable".

    Parameters
    ----------
    ctx:
        The current evaluation context.
    llm_client:
        An LLMClient implementation; must be initialized.
    skills_index:
        A mapping of skill_name → SkillSpec for skill lookup.
    prompt_timestamp:
        ISO-8601 "now" anchor for temporal-reasoning queries.
        When non-None, emitted as ``<prompt_timestamp>`` in the prompt
        and hashed onto ``QueryTrace.prompt_timestamp_hash``.
    counters:
        Optional CounterRegistry. When provided,
        ``counters.user_role_coercion_failures`` is incremented on
        invalid user_role values (PR243 review S4).

    Returns
    -------
    EngineDecision
        Always returns a decision (never raises). Error paths return
        a RefusalTrace decision.
    """
    event_hash = compute_event_input_hash(ctx)
    # G17: event_data is PHI-bearing in production. Use keyed HMAC-SHA256 so an
    # attacker with receipts-DB read access cannot enumerate query strings offline
    # by brute-forcing an unkeyed SHA-256.
    # NOTE: despite the variable name, this hash covers the entire canonical
    # event_data dict (per _canonical_event_data) — not just the "query" field.
    # The name is preserved because it is also the public schema name on
    # QueryTrace.query_text_hash. Do NOT narrow this to query_text alone; that
    # would silently break the receipt audit contract.
    query_text_hash = _phi._hmac_hex(_phi._canonical_event_data(ctx.event.event_data))
    # Extract query text from the raw context BEFORE phi_safe() strips it.
    # The 2026-05-11 PHI clarification permits query text in the prompt (local LLM
    # + self-hosted Langfuse). The query_text_hash on QueryTrace is unchanged.
    # Mirror _coerce_orders: event_data values may be any JSON type; only accept str.
    #
    # RESIDUAL-RISK CALLOUT: `query_text` is the ONLY free-text
    # user-controlled content in the clinical_query prompt (<safe_context> was
    # removed). The exposure is bounded by two preconditions that MUST hold:
    #   1. Local-LLM topology (no third-party hosted inference).
    #   2. Self-hosted Langfuse (or any tracing/OTel sink) inside the trust and
    #      compliance boundary — no external SaaS that would receive the raw query.
    # The prompt (including verbatim clinical query text) is stamped to
    # Langfuse BY DEFAULT. Precondition #3 (SAMANTHA_STAMP_PROMPT defaults to off)
    # no longer holds; it has been removed as a guard. The sole PHI guard is the
    # self-hosted Langfuse topology (precondition #2 above). To disable stamping,
    # set SAMANTHA_STAMP_PROMPT=0/false/no/off. `_render_query_block` only
    # XML-escapes for fence-breakout defense and does NOT perform PHI scanning or
    # redaction — the boundary topology is the only guard.
    raw_query = ctx.event.event_data.get("query")
    if raw_query is None:
        query_text = ""
    elif isinstance(raw_query, str):
        query_text = raw_query
    else:
        _logger.warning(
            "event_data['query'] is not a string (type=%s); treating as absent.",
            type(raw_query).__name__,
        )
        query_text = ""

    # Extract user_role from event_data via the canonical helper (PR243 S4).
    # parse_user_role logs WARNING (never the raw value) and increments the counter
    # on invalid input — single authoritative extraction site.
    user_role, user_role_coercion_failure = parse_user_role(
        ctx.event.event_data, logger=_logger, counters=counters
    )

    try:
        skill_body = load_skill("query-routing", index=skills_index)
    except SkillLoaderError as exc:
        _logger.warning("Skill 'query-routing' unavailable; returning refused_skill_unavailable.")
        _logger.debug("Skill 'query-routing' unavailable (full traceback).", exc_info=exc)
        return _make_refusal_decision(
            "STAGE_PRE_SKILL_UNAVAILABLE",
            "refused_skill_unavailable",
            event_hash=event_hash,
            current_state=ctx.current_state,
        )

    # scenarios_cited hardcoded to () — production index is empty and the
    # similar-scenario retrieval helper was deleted.
    scenarios_cited: tuple[str, ...] = ()

    # PHIBoundaryError must be caught here and converted to a refusal receipt.
    # Allowing it to escape would produce zero audit-trail record for
    # age > 89 queries — a silent failure on the HIPAA Safe Harbor guard.
    # The SafeContext return value is no longer threaded into the
    # prompt builders (no <safe_context> block on the clinical_query path).
    # phi_safe(ctx) is retained as a side-effecting boundary check.
    try:
        phi_safe(ctx) # PHI boundary guard — do not remove
    except PHIBoundaryError:
        # Log at WARNING without exc_info: exception chain may contain PHI.
        _logger.warning("PHIBoundaryError during clinical query; returning refused_phi_boundary.")
        return _make_refusal_decision(
            "STAGE_PRE_PHI_BOUNDARY",
            "refused_phi_boundary",
            event_hash=event_hash,
            current_state=ctx.current_state,
        )

    # Extract database_state.orders from event_data and validate.
    # Hash and prompt-block now derive from different orderings of
    # the same canonical order set — hash from order_id-sorted JSON (set-
    # fingerprint, audit-stable); prompt block from LIS-priority-sorted
    # JSON (production-realistic ordering for the model). The two-JSON
    # divergence is intentional; see `_orders_canonical_json` docstring
    # for the audit-recovery contract.
    # The order_id and created_at fields are NOT PHI in this project's
    # domain (per LLMOrderBlock docstring); they reach the prompt verbatim
    # by design.
    canonical_orders = _coerce_orders(ctx.event.event_data.get("orders"))
    if canonical_orders:
        # Hash side: canonical (order_id) encoding.
        canonical_json = _orders_canonical_json(canonical_orders)
        database_state_hash = _phi._hmac_hex(canonical_json.encode())
        # Prompt side: LIS-priority-sorted encoding.
        prompt_orders_json = _orders_canonical_json(_orders_priority_sorted(canonical_orders))
    else:
        prompt_orders_json = ""
        database_state_hash = ""

    skill_doc_hash = _sha256_hex(skill_body)

    # Hash the prompt_timestamp for the receipt; use the same HMAC
    # key as database_state_hash (phi._hmac_hex) so the value is keyed.
    prompt_timestamp_hash = (
        _phi._hmac_hex(prompt_timestamp.encode()) if prompt_timestamp is not None else ""
    )

    if config.SAMANTHA_LLM_OUTPUT_MODE == "json":
        return _handle_clinical_query_json(
            ctx=ctx,
            llm_client=llm_client,
            skill_body=skill_body,
            orders_json=prompt_orders_json,
            event_hash=event_hash,
            query_text_hash=query_text_hash,
            query_text=query_text,
            skill_doc_hash=skill_doc_hash,
            scenarios_cited=scenarios_cited,
            database_state_hash=database_state_hash,
            prompt_timestamp=prompt_timestamp,
            prompt_timestamp_hash=prompt_timestamp_hash,
            user_role=user_role,
            user_role_coercion_failure=user_role_coercion_failure,
        )

    # free_text mode: existing path
    prompt = build_query_prompt(
        skill_body,
        orders_json=prompt_orders_json,
        query_text=query_text,
        user_role=user_role,
        prompt_timestamp=prompt_timestamp,
    )

    try:
        response = llm_complete_with_span(llm_client, prompt)
    except LLMClientError as exc:
        _logger.warning("LLMClient.complete() failed; returning refused_llm_unavailable.")
        _logger.debug("LLMClient.complete() failed (full traceback).", exc_info=exc)
        return _make_refusal_decision(
            "STAGE_PRE_LLM_UNAVAILABLE",
            "refused_llm_unavailable",
            event_hash=event_hash,
            current_state=ctx.current_state,
            underlying_error_type=_llm_error_literal(exc),
        )

    response_text_hash = _sha256_hex(response.text)

    return EngineDecision(
        applied_rule_id=None,
        next_state=ctx.current_state,
        flags_added=(),
        flags_cleared=(),
        outcome="query_response",
        also_matched=(),
        dispatched_rule_ids=(),
        event_input_hash=event_hash,
        primitive_traces={},
        # H-05: use the real LLM latency measurement. Refusal paths stay at 0
        # (no LLM call was made) — only the success path carries real latency.
        latency_us=response.latency_us,
        decision_traces=(
            QueryTrace(
                query_text_hash=query_text_hash,
                skill_doc_hash=skill_doc_hash,
                scenarios_cited=scenarios_cited,
                model_id=response.model_id,
                response_text_hash=response_text_hash,
                database_state_hash=database_state_hash,
                prompt_timestamp_hash=prompt_timestamp_hash,
                user_role=user_role,
                user_role_coercion_failure=user_role_coercion_failure,
            ),
        ),
    )


def _build_query_messages(
    skill_body: str,
    orders_json: str,
    *,
    query_text: str = "",
    user_role: UserRole | None = None,
    prompt_timestamp: str | None = None,
) -> list[ChatMessage]:
    """Build the messages array for complete_json (JSON mode).

    The system message carries the skill body verbatim (sets the output contract).
    The user message carries the grounding context: query, optional user_role,
    optional prompt_timestamp, and orders.

    <similar_scenarios> and <safe_context> blocks removed from the
    clinical_query path. PHI-boundary enforcement lives upstream in
    handle_clinical_query's phi_safe(ctx) call.

    <user_role> block emitted in the user message when non-None,
    between <query> and <prompt_timestamp>.

    <prompt_timestamp> block emitted in the user message when non-None,
    between <user_role> (or <query>) and <orders>.

    This split follows the JSON query plan: system = skill doc, user = context + query.
    """
    # System: the skill body defines the JSON contract.
    system_content = skill_body

    # User: query block first (most important grounding — model reads it first),
    # then optional user_role, then optional prompt_timestamp anchor, then orders.
    query_block = _render_query_block(query_text)

    parts = [query_block]
    if user_role is not None:
        escaped_role = saxutils_escape(user_role)
        parts.append(f"<user_role>{escaped_role}</user_role>")
    if prompt_timestamp is not None:
        escaped_ts = saxutils_escape(prompt_timestamp)
        parts.append(f"<prompt_timestamp>{escaped_ts}</prompt_timestamp>")
    if orders_json:
        parts.append(f"<orders>\n{orders_json}\n</orders>")

    user_content = "\n\n".join(parts)

    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
    ]


def _handle_clinical_query_json(
    *,
    ctx: SpecimenContext,
    llm_client: LLMClient,
    skill_body: str,
    orders_json: str,
    event_hash: str,
    query_text_hash: str,
    query_text: str,
    skill_doc_hash: str,
    scenarios_cited: tuple[str, ...],
    database_state_hash: str,
    prompt_timestamp: str | None = None,
    prompt_timestamp_hash: str = "",
    user_role: UserRole | None = None,
    user_role_coercion_failure: bool = False,
) -> EngineDecision:
    """JSON-mode branch for handle_clinical_query.

    Calls complete_json() with temperature=0.0. Defensively strips markdown
    fences. Parses the response as QueryResponseV1. On parse failure, returns
    a STAGE_PRE_UNPARSEABLE refusal so the receipt records the
    failure honestly — mirrors the specimen-review parse-failure path.
    """
    messages = _build_query_messages(
        skill_body,
        orders_json,
        query_text=query_text,
        user_role=user_role,
        prompt_timestamp=prompt_timestamp,
    )

    try:
        response = llm_complete_json_with_span(
            llm_client,
            messages,
            schema=QUERY_RESPONSE_V1_JSON_SCHEMA,
            schema_name="QueryResponseV1",
            temperature=0.0,
        )
    except LLMClientError as exc:
        # Include str(exc) so a permanent capability gap (e.g.
        # LLMInferenceError from MLXClient.complete_json) is
        # distinguishable from a transient failure at default log levels.
        _logger.warning(
            "LLMClient.complete_json() failed; returning refused_llm_unavailable. error=%s",
            exc,
        )
        _logger.debug("LLMClient.complete_json() failed (full traceback).", exc_info=exc)
        return _make_refusal_decision(
            "STAGE_PRE_LLM_UNAVAILABLE",
            "refused_llm_unavailable",
            event_hash=event_hash,
            current_state=ctx.current_state,
            underlying_error_type=_llm_error_literal(exc),
        )

    raw_text = response.text
    stripped = _strip_markdown_fences(raw_text)

    # Attempt parse — defensive: model may produce non-JSON or schema-violating output.
    try:
        parsed = QueryResponseV1.model_validate_json(stripped)
    except PydanticValidationError as exc:
        # Pydantic v2 model_validate_json wraps json.JSONDecodeError in ValidationError
        # with error type "json_invalid". Distinguish the two failure modes for the
        # gen_ai.parse_failure span attribute and the logs.
        errors = exc.errors(include_url=False)
        is_json_decode = any(e.get("type") == "json_invalid" for e in errors)
        parse_failure = "json_decode" if is_json_decode else "schema_violation"
        _logger.warning("JSON parse failure in clinical query handler: %s", parse_failure)
        _logger.debug("JSON parse failure body fragment (first 120 chars): %r", raw_text[:120])
        # Set gen_ai.parse_failure on the active OTel span so operators can detect
        # parse-failure rates via span attributes (rather than only WARNING logs).
        set_span_attribute(_otel_trace.get_current_span(), "gen_ai.parse_failure", parse_failure)
        # Return a refusal so the receipt records the failure honestly.
        # Mirrors the specimen-review parse-failure path (_handle_pending_llm_review_json).
        # order_id is intentionally omitted here (None): the clinical-query path is
        # multi-order; there is no single subject order_id to stamp — matches the
        # LLM-unavailable refusal above, which also omits order_id. No response
        # hash is carried: RefusalTrace deliberately stores no response-derived
        # fields (an earlier draft computed a raw-text hash here
        # that was never forwarded into the receipt).
        return _make_refusal_decision(
            "STAGE_PRE_UNPARSEABLE",
            "refused_unparseable_response",
            event_hash=event_hash,
            current_state=ctx.current_state,
            latency_us=response.latency_us,
        )

    return EngineDecision(
        applied_rule_id=None,
        next_state=ctx.current_state,
        flags_added=(),
        flags_cleared=(),
        outcome="query_response",
        also_matched=(),
        dispatched_rule_ids=(),
        event_input_hash=event_hash,
        primitive_traces={},
        latency_us=response.latency_us,
        decision_traces=(
            QueryTrace(
                query_text_hash=query_text_hash,
                skill_doc_hash=skill_doc_hash,
                scenarios_cited=scenarios_cited,
                model_id=response.model_id,
                response_text_hash=_canonical_response_hash(parsed),
                database_state_hash=database_state_hash,
                prompt_timestamp_hash=prompt_timestamp_hash,
                parsed_order_ids=parsed.order_ids,
                parsed_answer_type=parsed.answer_type,
                user_role=user_role,
                user_role_coercion_failure=user_role_coercion_failure,
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Specimen-review handler (PENDING_LLM_REVIEW state)
# ---------------------------------------------------------------------------

# Maps SpecimenReviewResponseV1.disposition values to the next workflow state.
_DISPOSITION_TO_STATE: Final[Mapping[str, str]] = {
    "accepted": "ACCEPTED",
    "rejected": "DO_NOT_PROCESS",
    "escalated": "PENDING_HUMAN_REVIEW",
}

# _DISPOSITION_TO_TRACE_VERB is imported from samantha_server.engine.decision
# (canonical source co-located with LLMReviewTrace) to avoid drift from
# handlers.py.

# Anatomic-site lists used to infer whether ACC-011 (anatomic_site
# LLM-review band) fired. These mirror ACC-003.yaml (blacklist) and ACC-011.yaml
# (whitelist). TestAnatomicSiteNoDrift.test_handler_blacklist_constant_matches_acc003_yaml
# and test_handler_whitelist_constant_matches_acc011_yaml enforce drift-freedom:
# any edit to either constant without updating the corresponding YAML (or vice
# versa) causes those tests to fail.
_ACC003_ANATOMIC_SITE_BLACKLIST: Final[frozenset[str]] = frozenset(
    {"lung", "liver", "colon", "brain", "prostate"}
)
_ACC011_ANATOMIC_SITE_WHITELIST: Final[frozenset[str]] = frozenset(
    {"breast", "left breast", "right breast", "axillary lymph node"}
)


def _infer_trigger_rule_id(ctx: SpecimenContext) -> str | None:
    """Infer which rule sent the order into PENDING_LLM_REVIEW.

    Returns "ACC-011" if anatomic_site is non-null and in the LLM-review band
    (neither blacklist nor whitelist). Returns None otherwise (ACC-010 path or
    unknown trigger — caller uses the specimen_type prompt block).

    NOTE: when an order has both specimen_type in ACC-010's middle band AND
    anatomic_site in ACC-011's middle band, this function always returns
    'ACC-011' (anatomic_site is checked first). The actual triggering rule
    may be ACC-010 (glob-order tie-break). See
    test_handle_pending_llm_review_dual_ambiguity_returns_anatomic_site_prompt
    for the pinned behavior. Future fix: stash trigger_rule_id on EngineDecision
    instead of inferring.
    """
    site = ctx.order.anatomic_site
    if site is None:
        return None
    site_lower = site.casefold()
    if site_lower in _ACC003_ANATOMIC_SITE_BLACKLIST:
        return None
    if site_lower in _ACC011_ANATOMIC_SITE_WHITELIST:
        return None
    return "ACC-011"


def _build_specimen_review_messages(
    skill_body: str,
    safe_ctx: SafeContext,
    trigger_rule_id: str | None = None,
) -> list[ChatMessage]:
    """Build the messages array for complete_json (JSON mode, specimen review).

    The system message carries the skill body verbatim (sets the JSON output contract).
    The user message carries the PHI-stripped context.

    trigger_rule_id selects the XML fence used in the instruction block:
    - "ACC-011" → anatomic_site_under_review (anatomic site LLM-review path)
    - anything else / None → specimen_type_under_review (default, ACC-010 path)

    Mirrors _build_query_messages for the query path.
    """
    # System: the skill body defines the JSON contract.
    system_content = skill_body

    # User: safe context JSON.
    safe_ctx_json = safe_ctx.model_dump_json()
    context_block = f"<safe_context>\n{safe_ctx_json}\n</safe_context>"

    if trigger_rule_id == "ACC-011":
        # anatomic_site LLM-review path (ACC-011 fired).
        # anatomic_site is not PHI per project memory. XML-escape as a
        # prompt-injection defence (same pattern as specimen_type).
        raw_anatomic_site = (
            safe_ctx.order.anatomic_site if safe_ctx.order.anatomic_site is not None else "unknown"
        )
        anatomic_site = saxutils_escape(raw_anatomic_site)
        instruction_block = (
            f"The anatomic site under review is: "
            f"<anatomic_site_under_review>{anatomic_site}</anatomic_site_under_review>\n\n"
            "Based on the skill definition and clinical context above, determine whether "
            "this anatomic site is appropriate for the breast histology workflow. "
            "Respond with a JSON object matching the schema in the system prompt."
        )
    else:
        # Default: specimen_type LLM-review path (ACC-010 fired or unknown trigger).
        # ACC-010 routes null specimen_type to PENDING_LLM_REVIEW (unknown-specimen path);
        # render an explicit "unknown" sentinel rather than letting the f-string produce "None".
        raw_specimen_type = (
            safe_ctx.order.specimen_type if safe_ctx.order.specimen_type is not None else "unknown"
        )
        # XML-fence the specimen_type to prevent prompt injection. saxutils.escape
        # encodes `<`, `>`, and `&` so a LIS-supplied value containing the closing
        # tag (or any other XML metacharacter) cannot break out of the fence and
        # smuggle new instructions into the user message. (PR204 review #2.)
        specimen_type = saxutils_escape(raw_specimen_type)
        instruction_block = (
            f"The specimen type under review is: "
            f"<specimen_type_under_review>{specimen_type}</specimen_type_under_review>\n\n"
            "Based on the skill definition and clinical context above, determine whether "
            "this specimen is processable in the histology workflow. "
            "Respond with a JSON object matching the schema in the system prompt."
        )

    user_content = "\n\n".join([context_block, instruction_block])

    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
    ]


def _handle_pending_llm_review_json(
    *,
    ctx: SpecimenContext,
    llm_client: LLMClient,
    skill_body: str,
    safe_ctx: SafeContext,
    event_hash: str,
    trigger_rule_id: str | None = None,
) -> EngineDecision:
    """JSON-mode handler for handle_pending_llm_review.

    Hard cutover — always uses JSON path regardless of SAMANTHA_LLM_OUTPUT_MODE.
    (unlike the clinical-query path, specimen-review has no
    free_text fallback. The config.py startup guard only rejects LLM_PROVIDER=mlx
    + SAMANTHA_LLM_OUTPUT_MODE=json — because of the hard cutover, a
    guard-permitted mlx + free_text deployment still reaches complete_json here,
    so the typed LLMInferenceError from MLXClient is the primary fix on this
    path: the except below catches it and returns
    refused_llm_unavailable with a signed receipt, satisfying the
    receipt-emission invariant.)

    Calls complete_json() with temperature=0.0. Defensively strips markdown fences.
    Parses the response as SpecimenReviewResponseV1. On parse failure, returns a
    STAGE_PRE_UNPARSEABLE refusal (hard cutover: no fallback to line-scan).
    """
    messages = _build_specimen_review_messages(
        skill_body, safe_ctx, trigger_rule_id=trigger_rule_id
    )

    try:
        response = llm_complete_json_with_span(
            llm_client,
            messages,
            schema=SPECIMEN_REVIEW_RESPONSE_V1_JSON_SCHEMA,
            schema_name="SpecimenReviewResponseV1",
            temperature=0.0,
        )
    except LLMClientError as exc:
        # Include str(exc) — see the clinical-query catch site.
        _logger.warning(
            "LLMClient.complete_json() failed during specimen review; "
            "returning refused_llm_unavailable. error=%s",
            exc,
        )
        _logger.debug(
            "LLMClient.complete_json() failed during specimen review (full traceback).",
            exc_info=exc,
        )
        return _make_refusal_decision(
            "STAGE_PRE_LLM_UNAVAILABLE",
            "refused_llm_unavailable",
            event_hash=event_hash,
            current_state=ctx.current_state,
            underlying_error_type=_llm_error_literal(exc),
            order_id=ctx.order.order_id,
        )

    raw_text = response.text
    stripped = _strip_markdown_fences(raw_text)

    try:
        parsed = SpecimenReviewResponseV1.model_validate_json(stripped)
    except PydanticValidationError as exc:
        # Pydantic v2 model_validate_json wraps json.JSONDecodeError in ValidationError
        # with error type "json_invalid". Distinguish the two failure modes for the
        # parse_failure field in LLMReviewTrace.
        errors = exc.errors(include_url=False)
        is_json_decode = any(e.get("type") == "json_invalid" for e in errors)
        parse_failure = "json_decode" if is_json_decode else "schema_violation"
        # PR204 review #9/#16: log the post-strip fragment that was actually fed to
        # the parser (matches no-fence responses verbatim, and surfaces the JSON
        # body for fence-wrapped responses rather than the fence header).
        _logger.warning("JSON parse failure in specimen review handler: %s", parse_failure)
        _logger.debug("JSON parse failure parsed fragment (first 120 chars): %r", stripped[:120])
        set_span_attribute(_otel_trace.get_current_span(), "gen_ai.parse_failure", parse_failure)
        return _make_refusal_decision(
            "STAGE_PRE_UNPARSEABLE",
            "refused_unparseable_response",
            event_hash=event_hash,
            current_state=ctx.current_state,
            latency_us=response.latency_us,
            order_id=ctx.order.order_id,
        )

    # Map past-tense disposition to the verb form used in LLMReviewTrace.
    parsed_disposition = _DISPOSITION_TO_TRACE_VERB[parsed.disposition]
    response_text_hash = _canonical_response_hash(parsed)
    skill_doc_hash = _sha256_hex(skill_body)
    next_state = _DISPOSITION_TO_STATE[parsed.disposition]
    outcome = f"{parsed.disposition}_llm_review"

    return EngineDecision(
        applied_rule_id=None,
        next_state=next_state,
        flags_added=(),
        # Clear LLM_REVIEW_REQUESTED on terminal disposition: the order has moved
        # to its next state (ACCEPTED / DO_NOT_PROCESS / PENDING_HUMAN_REVIEW) and
        # no longer needs retry. Refusal paths leave the flag for retry.
        flags_cleared=("LLM_REVIEW_REQUESTED",),
        outcome=outcome,
        also_matched=(),
        dispatched_rule_ids=(),
        event_input_hash=event_hash,
        primitive_traces={},
        latency_us=response.latency_us,
        order_id=ctx.order.order_id,
        decision_traces=(
            LLMReviewTrace(
                skill_doc_hash=skill_doc_hash,
                model_id=response.model_id,
                response_text_hash=response_text_hash,
                disposition=parsed_disposition,
                parsed_disposition=parsed_disposition,
                parse_failure=None,
            ),
        ),
    )


_LLM_ERROR_NAMES: Final = frozenset({"LLMTimeoutError", "LLMInferenceError", "LLMModelLoadError"})


def _llm_error_literal(
    exc: LLMClientError,
) -> Literal["LLMTimeoutError", "LLMInferenceError", "LLMModelLoadError"]:
    """Narrow ``type(exc).__name__`` to the Literal accepted by RefusalTrace.

    Raises AssertionError if the name is not in the known set (explicit raise
    — survives python -O): a future LLMClientError subclass that isn't
    listed here fails loudly at the refusal handler rather than leaking through
    Pydantic with a different type name. Update the Literal and
    ``_LLM_ERROR_NAMES`` in lockstep when adding subclasses.
    """
    name = type(exc).__name__
    # AssertionError marks a programming error (the
    # _LLM_ERROR_NAMES registry out of sync with the LLMClientError hierarchy),
    # distinct from ValueError used for data-invariant violations.
    if name not in _LLM_ERROR_NAMES:
        raise AssertionError(
            f"unhandled LLMClientError subclass: {name}; "
            "update RefusalTrace.underlying_error_type Literal and "
            "_LLM_ERROR_NAMES together."
        )
    return name  # type: ignore[return-value]  # narrowed by guard above


def _make_refusal_decision(
    reason: str,
    outcome: str,
    *,
    event_hash: str,
    current_state: str,
    latency_us: int = 0,
    underlying_error_type: (
        Literal["LLMTimeoutError", "LLMInferenceError", "LLMModelLoadError"] | None
    ) = None,
    order_id: str | None = None,
) -> EngineDecision:
    """Build a refusal EngineDecision with a single RefusalTrace.

    Centralises the boilerplate that was repeated for each refusal path in
    handle_pending_llm_review and handle_clinical_query. All refusal decisions
    share the same shape: applied_rule_id=None, flags_added=(), flags_cleared=(),
    also_matched=(), dispatched_rule_ids=(), primitive_traces={}.

    Parameters
    ----------
    reason:
        The STAGE_PRE_* refusal reason (must be a valid _REFUSAL_REASONS literal).
    outcome:
        The outcome string (e.g. 'refused_skill_unavailable').
    event_hash:
        The SHA-256 hex event_input_hash for this context.
    current_state:
        The workflow state at the time of refusal (next_state stays the same).
    latency_us:
        LLM latency in microseconds. Zero on pre-LLM refusals; non-zero when
        the refusal happens after an LLM call (e.g. unparseable response).
    order_id:
        The LIS order ID for single-order paths (LLM review, clarification).
        None for the multi-order query path, which keeps parsed_order_ids instead.
    """
    return EngineDecision(
        applied_rule_id=None,
        next_state=current_state,
        flags_added=(),
        flags_cleared=(),
        outcome=outcome,
        also_matched=(),
        dispatched_rule_ids=(),
        event_input_hash=event_hash,
        primitive_traces={},
        latency_us=latency_us,
        order_id=order_id,
        decision_traces=(
            RefusalTrace(
                refusal_reason=reason,  # type: ignore[arg-type]
                refusal_stage="PRE",
                judge_verdict=None,
                alternatives=(),
                underlying_error_type=underlying_error_type,
            ),
        ),
    )


def handle_pending_llm_review(
    ctx: SpecimenContext,
    llm_client: LLMClient,
    skills_index: Mapping[str, SkillSpec],
) -> EngineDecision:
    """Handle a PENDING_LLM_REVIEW state via the specimen-review skill.

    Called by the router when ctx.current_state == "PENDING_LLM_REVIEW".
    Always uses the JSON path (hard cutover). Unlike handle_clinical_query
, there is no SAMANTHA_LLM_OUTPUT_MODE branch here — the issue
    demands no bifurcation for the specimen-review path.

    Returns an EngineDecision with:
    - Disposition accepted → next_state="ACCEPTED", outcome="accepted_llm_review"
    - Disposition rejected → next_state="DO_NOT_PROCESS", outcome="rejected_llm_review"
    - Disposition escalated → next_state="PENDING_HUMAN_REVIEW", outcome="escalated_llm_review"
    - JSON parse failure → RefusalTrace with STAGE_PRE_UNPARSEABLE

    Does NOT call evaluate(). applied_rule_id is always None.
    Receipt emission is the caller's (router's) responsibility.

    Error contract:
    - SkillLoaderError → RefusalTrace(STAGE_PRE_SKILL_UNAVAILABLE)
    - LLMClientError (and subclasses) → RefusalTrace(STAGE_PRE_LLM_UNAVAILABLE)
    - PHIBoundaryError → RefusalTrace(STAGE_PRE_PHI_BOUNDARY)
    - JSON parse failure → RefusalTrace(STAGE_PRE_UNPARSEABLE)
    """
    event_hash = compute_event_input_hash(ctx)

    try:
        skill_body = load_skill("specimen-review", index=skills_index)
    except SkillLoaderError as exc:
        _logger.warning("Skill 'specimen-review' unavailable; returning refused_skill_unavailable.")
        _logger.debug("Skill 'specimen-review' unavailable (full traceback).", exc_info=exc)
        return _make_refusal_decision(
            "STAGE_PRE_SKILL_UNAVAILABLE",
            "refused_skill_unavailable",
            event_hash=event_hash,
            current_state=ctx.current_state,
            order_id=ctx.order.order_id,
        )

    try:
        safe_ctx = phi_safe(ctx)
    except PHIBoundaryError:
        # Log at WARNING without exc_info: the exception chain may contain PHI
        # (age/patient fields). The refusal receipt is the authoritative record.
        _logger.warning("PHIBoundaryError during specimen review; returning refused_phi_boundary.")
        return _make_refusal_decision(
            "STAGE_PRE_PHI_BOUNDARY",
            "refused_phi_boundary",
            event_hash=event_hash,
            current_state=ctx.current_state,
            order_id=ctx.order.order_id,
        )

    trigger_rule_id = _infer_trigger_rule_id(ctx)
    if trigger_rule_id is None:
        # Log only the type of anatomic_site (never the raw value) to avoid
        # inadvertent PHI leakage in logs. The conservative pattern in
        # handlers.py logs types rather than values for LIS-supplied fields.
        _logger.debug(
            "trigger_rule_id inference returned None; defaulting to specimen_type prompt. "
            "anatomic_site type=%s",
            type(ctx.order.anatomic_site).__name__,
        )
    return _handle_pending_llm_review_json(
        ctx=ctx,
        llm_client=llm_client,
        skill_body=skill_body,
        safe_ctx=safe_ctx,
        event_hash=event_hash,
        trigger_rule_id=trigger_rule_id,
    )


# ---------------------------------------------------------------------------
# Clarification handler
# ---------------------------------------------------------------------------

# Cap per LLM-supplied suggestion value before storage in the signed receipt.
# 256 chars is generous for any canonical pick-list value (the longest current
# entry is ~30 chars) and well under SQLite row limits. The cap protects
# downstream consumers from unbounded LLM output landing in receipts.
_MAX_SUGGESTED_VALUE_LEN: Final[int] = 256


def _parse_clarification_response(
    text: str,
    fields_to_clarify: tuple[str, ...],
) -> dict[str, str]:
    """Parse LLM response for clarification suggestions.

    Parses 'field=value' lines from the LLM response. Only lines where the
    field name is in fields_to_clarify are kept; unrecognized lines are
    silently dropped. Each value is canonicalized via canonicalize(field,
    value) and dropped if was_unknown=True (the LLM hallucinated a value
    not in the pick list). Values are length-capped at
    _MAX_SUGGESTED_VALUE_LEN before storage. Duplicate field lines use
    first-write-wins (later lines for the same field are ignored) — first-
    write-wins is more conservative under hypothetical prefix-injection.

    Note on garbage-parse: a healthy LLM producing an unparseable response
    (no 'field=value' lines, or all lines dropped) returns {} with
    llm_failed=False. This is deliberately not distinguished from a
    successful empty parse — llm_failed is scoped to LLMClientError
    (transport failure), not to content-level parse quality.

    Parameters
    ----------
    text:
        Raw LLM response text. Untrusted.
    fields_to_clarify:
        The sorted union of missing_fields and unknown_canonical_fields.
        Only field names in this set are retained.
    """
    result: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if "=" not in line:
            continue
        field, _, value = line.partition("=")
        field = field.strip()
        if field not in fields_to_clarify or field in result:
            continue
        value = value.strip()[:_MAX_SUGGESTED_VALUE_LEN]
        # Re-canonicalize: the LLM may have suggested a value that isn't in the
        # canonical pick list at all. Drop those — keeping them in the signed
        # receipt would invite a future Phase 4 orchestrator to apply them.
        canon = canonicalize(field, value)
        if canon.was_unknown:
            continue
        result[field] = canon.canonical
    return result


_PICK_LIST_RENDER_CAP: Final[int] = 20


def _render_canonical_values_block(
    fields_to_clarify: tuple[str, ...],
    pick_lists: Mapping[str, frozenset[str]],
) -> str:
    """Render the `<canonical_values>` scaffolding block for the prompt.

    For each field in fields_to_clarify that has an entry in pick_lists,
    emit a line `  field: v1, v2, ...`. Values are sorted for deterministic
    output; pick lists longer than _PICK_LIST_RENDER_CAP are truncated with
    a `...+N more` suffix to bound token cost on large enumerations.

    Returns an empty string when no field has a pick list — the caller
    omits the block entirely rather than emitting an empty contradictory
    element that would conflict with the instruction prose.
    """
    lines: list[str] = []
    for field in fields_to_clarify:
        values = pick_lists.get(field)
        # `is None` distinguishes "field absent from pick_lists" (intentional —
        # free-text field) from "field present with empty frozenset" (data-file
        # authoring error). An empty list still renders as `  field: ` so the
        # authoring error is visible in the prompt rather than silently treated
        # the same as a missing entry.
        if values is None:
            continue
        sorted_values = sorted(values)
        if len(sorted_values) > _PICK_LIST_RENDER_CAP:
            shown = sorted_values[:_PICK_LIST_RENDER_CAP]
            remainder = len(sorted_values) - _PICK_LIST_RENDER_CAP
            rendered = ", ".join(shown) + f", ...+{remainder} more"
        else:
            rendered = ", ".join(sorted_values)
        lines.append(f"  {field}: {rendered}")
    if not lines:
        return ""
    body = "\n".join(lines)
    return f"<canonical_values>\n{body}\n</canonical_values>"


def build_clarification_prompt(
    safe_ctx: SafeContext,
    fields_to_clarify: tuple[str, ...],
    pick_lists: Mapping[str, frozenset[str]],
) -> str:
    """Assemble the LLM prompt for a clarification request.

    Layout (deterministic order):
    1. <fields_to_clarify> block — list of the union of missing/unknown fields.
    2. <canonical_values> block — per-field canonical pick list rendered from
       `pick_lists`. Fields with no pick_lists entry are skipped (they appear
       in <fields_to_clarify> but carry no canonical-form constraint).
    3. <safe_context> block — canonical JSON of safe_ctx (PHI-stripped).
    4. Instruction telling the model to choose from <canonical_values> and
       omit fields that cannot be mapped.

    Parameters
    ----------
    safe_ctx:
        PHI-stripped context from phi_safe(ctx). Raw SpecimenContext MUST
        NOT be passed here. (Type signature enforces this.)
    fields_to_clarify:
        Sorted tuple of field names needing clarification.
    pick_lists:
        Mapping from field name to its canonical-value set. Passed in (rather
        than read from `samantha_server.canonicalization.PICK_LISTS` directly)
        so a future role-scoping layer can plug in narrower lists without
        modifying this helper.
    """
    fields_content = "\n".join(f"  - {f}" for f in fields_to_clarify)
    fields_block = f"<fields_to_clarify>\n{fields_content}\n</fields_to_clarify>"
    canonical_block = _render_canonical_values_block(fields_to_clarify, pick_lists)
    safe_ctx_json = safe_ctx.model_dump_json()
    context_block = f"<safe_context>\n{safe_ctx_json}\n</safe_context>"
    # When no field has a pick list, the `<canonical_values>` block is omitted
    # entirely — the instruction must also drop the
    # reference to that block, otherwise the model is pointed at scaffolding
    # that doesn't exist.
    if canonical_block:
        directive = (
            "For each field, choose the canonical form from the corresponding pick list shown in "
            "<canonical_values>. If no pick-list value plausibly maps to the user's input for a "
            "field, omit that field from your response."
        )
    else:
        directive = (
            "For each field, provide the canonical form of the value. If you cannot determine a "
            "canonical form for a field, omit that field from your response."
        )
    instruction = (
        "The above order has missing or unrecognized values for the fields listed above. "
        f"{directive}\n\n"
        "Respond with one line per field in the format:\n"
        "  field_name=canonical_value\n\n"
        "Respond only with field=value lines. One line per field."
    )
    # Filter empty blocks so an all-free-text fields_to_clarify (no pick_lists
    # entries for any field) doesn't leave a double-blank gap in the prompt.
    blocks = [fields_block, canonical_block, context_block, instruction]
    return "\n\n".join(b for b in blocks if b)


def handle_clarification(
    ctx: SpecimenContext,
    *,
    preflight_result: PreflightMissing,
    llm_client: LLMClient,
) -> EngineDecision:
    """Handle a preflight-detected degenerate input via the clarification path.

    Does NOT call evaluate(). The EngineDecision
    returned always has applied_rule_id=None and next_state=ctx.current_state.
    Receipt emission is the caller's (router's) responsibility.

    The trace records both `missing_fields` (required-field nulls) and
    `unknown_canonical_fields` (values outside the canonical pick list)
    separately so replay can reconstruct why each field was flagged.

    Error contract:
    - PHIBoundaryError → RefusalTrace(STAGE_PRE_PHI_BOUNDARY, outcome="refused_phi_boundary").
    - LLMClientError → clarification still emits with suggested_values={},
      model_id="" and latency_us=0. The missing/unknown field lists are the
      load-bearing data; the LLM suggestion is advisory.

    Parameters
    ----------
    ctx:
        The current evaluation context.
    preflight_result:
        The PreflightMissing instance returned by preflight(ctx). Carries
        the missing_fields and unknown_canonical_fields tuples directly.
    llm_client:
        An initialized LLMClient instance.

    Returns
    -------
    EngineDecision
        Always returns a decision (never raises). Error paths return a
        RefusalTrace decision (PHI boundary) or a ClarificationTrace
        decision with empty suggested_values (LLM error).
    """
    event_hash = compute_event_input_hash(ctx)

    # PHIBoundaryError: convert to a refusal receipt (mirror handle_clinical_query).
    # Allowing it to escape would produce zero audit-trail record for age > 89 orders.
    try:
        safe_ctx = phi_safe(ctx)
    except PHIBoundaryError:
        # Log at WARNING without exc_info: exception chain may contain PHI.
        _logger.warning(
            "PHIBoundaryError during clarification handling; returning refused_phi_boundary."
        )
        return _make_refusal_decision(
            "STAGE_PRE_PHI_BOUNDARY",
            "refused_phi_boundary",
            event_hash=event_hash,
            current_state=ctx.current_state,
            order_id=ctx.order.order_id,
        )

    # Sorted union of all fields needing clarification (for the prompt).
    fields_to_clarify = tuple(
        sorted(
            set(preflight_result.missing_fields) | set(preflight_result.unknown_canonical_fields)
        )
    )

    prompt = build_clarification_prompt(safe_ctx, fields_to_clarify, PICK_LISTS)

    latency_us = 0
    suggested: dict[str, str] = {}
    model_id = ""
    # Track whether the LLM call itself failed so the receipt can
    # distinguish a genuine failure from a healthy LLM that returned no usable
    # suggestions (both produce empty suggested_values without this flag).
    llm_failed = False

    try:
        response = llm_complete_with_span(llm_client, prompt, temperature=0.0)
    except LLMClientError as exc:
        # Log at WARNING without exc_info on the parent line (exception chain
        # may carry PHI in some implementations); attach the traceback at DEBUG
        # for operators who increase verbosity, mirroring the pattern in
        # handle_clinical_query and handle_pending_llm_review.
        _logger.warning(
            "LLMClient.complete() failed during clarification; proceeding with empty suggestions."
        )
        _logger.debug(
            "LLMClient.complete() failed during clarification (full traceback).", exc_info=exc
        )
        llm_failed = True
    else:
        latency_us = response.latency_us
        model_id = response.model_id
        suggested = _parse_clarification_response(response.text, fields_to_clarify)

    return EngineDecision(
        applied_rule_id=None,
        next_state=ctx.current_state,
        flags_added=(),
        flags_cleared=(),
        outcome="needs_clarification",
        also_matched=(),
        dispatched_rule_ids=(),
        event_input_hash=event_hash,
        primitive_traces={},
        latency_us=latency_us,
        order_id=ctx.order.order_id,
        decision_traces=(
            ClarificationTrace(
                missing_fields=preflight_result.missing_fields,
                unknown_canonical_fields=preflight_result.unknown_canonical_fields,
                suggested_values=suggested,
                model_id=model_id,
                llm_failed=llm_failed,
            ),
        ),
    )


__all__ = [
    "build_clarification_prompt",
    "build_query_prompt",
    "handle_clarification",
    "handle_clinical_query",
    "handle_pending_llm_review",
]
