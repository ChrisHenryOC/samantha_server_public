"""OpenTelemetry SDK setup + PHI-allowlisted span-attribute helper.

Step 8 (phase-3-implementation.md). Wires the OTel SDK into the FastAPI
lifespan and exposes a tracer for the orchestrator boundary and the
``llm/handlers.py`` call sites.

Attribute-name discipline
=========================
**Attribute names are string literals in this module, not resolved
from ``opentelemetry.semconv.*`` constants.** If the
``opentelemetry-semantic-conventions`` release renames any of the
``gen_ai.*`` names below, the literals here AND the table in
``docs/plans/phase-3-implementation.md § Step 8`` AND the pinned
package version in ``pyproject.toml`` must be updated **together**. The
manual checklist replaces a runtime-resolution test that would add
maintenance for a low-frequency rename event.

PHI boundary (G18 + Step 8)
===========================
``set_span_attribute`` enforces a hardcoded allowlist. New attributes
require an allowlist update *plus* an explicit no-PHI assessment in
the same PR.

``gen_ai.completion`` is unconditionally allowed. The
lab-host deployment topology (local LLM + self-hosted Langfuse)
permits LLM completions on spans for operator diagnostics. Production
deployments outside the lab-host topology must revisit before
allowing cloud LLM or cloud Langfuse.

``gen_ai.prompt`` is stamped by default. The prompt,
including verbatim clinical query text, reaches Langfuse unless
``SAMANTHA_STAMP_PROMPT`` is explicitly set to a falsy value
(``"0"``, ``"false"``, ``"no"``, or ``"off"``). The trust guard is
the self-hosted Langfuse topology (precondition #2), not
the env var default. Hashes of each live in the signed receipt
regardless of whether the full text appears on the span.

Decision-path purity
====================
This module lives under ``observability/`` and is consumed by
``api/`` and ``llm/handlers.py``. ``engine/``, ``rules/``, and
``primitives/`` must not import from here.

attribute schema
=======================
Canonical dashboard surface = ``langfuse.trace.metadata.*``.
``samantha.*`` is reserved for engine-internal fields that don't
surface on dashboards (no ``langfuse.trace.metadata.*`` duplicate).

``stamp_trace_attributes`` schema table
---------------------------------------
Source field          | samantha.*              | langfuse.trace.metadata.*
--------------------- | ----------------------- | -------------------------
session_id            | samantha.session_id     | langfuse.trace.metadata.scenario_id
                      |                         | + langfuse.trace.name
                      |                         | (only when scenario_id is None)
scenario_id | (dropped) | langfuse.trace.metadata.scenario_id
                      |                         | + langfuse.trace.name
scenario_category | (dropped) | langfuse.trace.metadata.scenario_category
                      |                         | + langfuse.trace.tags
sweep_run_id | (dropped) | langfuse.trace.metadata.sweep_run_id
                      |                         | + langfuse.release (fallback)
environment | (dropped) | langfuse.trace.metadata.environment
                      |                         | + langfuse.environment
routing_path | (dropped) | langfuse.trace.metadata.routing_path
next_state | (dropped) | langfuse.trace.metadata.next_state
outcome | (dropped) | langfuse.trace.metadata.outcome
priority              | samantha.priority       | (none)
event_input_hash      | samantha.event_input_hash | (none)
latency_us            | samantha.latency_us     | (none)
applied_rule_id       | samantha.applied_rule_id | (none)
receipt_id            | samantha.receipt_id     | (none)
order_id              | samantha.order_id       | (none)
run_id                | (none)                  | langfuse.release (wins over sweep_run_id)

Langfuse-native field overrides (NOT part of the dedupe — separate surface):
``langfuse.trace.name``, ``langfuse.environment``, ``langfuse.release``,
``langfuse.trace.tags``.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from samantha_server.observability.trace_context import TraceContext

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Span, StatusCode, Tracer
from opentelemetry.util.types import AttributeValue

from samantha_server.llm.client import ChatMessage, LLMClient, LLMResponse
from samantha_server.observability.counters import CounterRegistry

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Allowlisted attribute names
# ---------------------------------------------------------------------------

# Parent-span ``samantha.*`` attributes — engine-internal fields only.
# Canonical dashboard surface is ``langfuse.trace.metadata.*``.
# Only fields WITHOUT a ``langfuse.trace.metadata.*`` duplicate belong here.
# Dropped: samantha.scenario_id, samantha.scenario_category,
# samantha.sweep_run_id, samantha.environment, samantha.routing_path,
# samantha.next_state, samantha.outcome — all have metadata duplicates.
_SAMANTHA_ATTRS: Final[frozenset[str]] = frozenset(
    {
        "samantha.event_input_hash",
        "samantha.session_id",
        "samantha.priority",
        "samantha.applied_rule_id",
        "samantha.latency_us",
        "samantha.receipt_id",
        # Synthetic LIS order_id (not PHI, not Safe Harbor); pass-through.
        "samantha.order_id",
    }
)

# Child-span ``gen_ai.*`` attributes — populated around each
# ``LLMClient.complete()`` call.
# ``gen_ai.completion`` is unconditionally allowed (lab-host topology).
# ``gen_ai.prompt`` is stamped by default; suppress with
# SAMANTHA_STAMP_PROMPT=0/false/no/off.
_GEN_AI_ATTRS: Final[frozenset[str]] = frozenset(
    {
        "gen_ai.system",
        "gen_ai.request.model",
        "gen_ai.response.model",
        "gen_ai.usage.input_tokens",
        "gen_ai.usage.output_tokens",
        "gen_ai.request.temperature",
        "gen_ai.request.max_tokens",
        "gen_ai.response.finish_reasons",
        # Boolean flag; True iff finish_reason=="length" (token-budget hit).
        # No PHI surface — it is a boolean derived from a wire protocol enum value.
        # Name pattern `<predicate>_is_<value>` reads as a boolean predicate
        # ("did finish_reason equal 'length'?") rather than a string-length field.
        "gen_ai.response.finish_reason_is_length",
        "gen_ai.completion",
        "gen_ai.prompt",
        # JSON parse-failure literal; PHI-safe (only the error class name, no model output).
        "gen_ai.parse_failure",
    }
)

# Langfuse-native field overrides. Langfuse v3.x dashboard widget builders
# expose a fixed set of breakdown dimensions (Environment, Name, Release,
# Tags, Session Id, User Id, Version) and a filterable ``metadata`` map.
# To make our dashboard-relevant dimensions breakdown-able, we mirror them
# onto the corresponding Langfuse-native fields via the keys listed in
# ``packages/shared/src/server/otel/attributes.ts`` of the langfuse repo.
# Per-key contract:
#   - ``langfuse.trace.name``     -> "Name" breakdown / trace title (scenario_id)
#   - ``langfuse.trace.tags``     -> "Tags" breakdown (array; scenario_category)
#   - ``langfuse.release``        -> "Release" breakdown (sweep_run_id)
#   - ``langfuse.environment``    -> "Environment" breakdown (replay/parity/production)
#   - ``langfuse.trace.metadata.<key>`` -> filterable ``metadata.<key>`` map
# PHI posture: each mirror value comes from an already-allowlisted
# ``samantha.*`` attribute and carries no PHI by construction.
_LANGFUSE_FIELD_MIRRORS: Final[frozenset[str]] = frozenset(
    {
        "langfuse.trace.name",
        "langfuse.trace.tags",
        "langfuse.release",
        "langfuse.environment",
        "langfuse.trace.metadata.scenario_id",
        "langfuse.trace.metadata.scenario_category",
        "langfuse.trace.metadata.sweep_run_id",
        "langfuse.trace.metadata.environment",
        "langfuse.trace.metadata.routing_path",
        "langfuse.trace.metadata.outcome",
        "langfuse.trace.metadata.next_state",
    }
)

_ALLOWED_ATTRIBUTES: Final[frozenset[str]] = (
    _SAMANTHA_ATTRS | _GEN_AI_ATTRS | _LANGFUSE_FIELD_MIRRORS
)


# ---------------------------------------------------------------------------
# Span shape constants
# ---------------------------------------------------------------------------

PARENT_SPAN_NAME: Final[str] = "samantha_server.event"
LLM_CHILD_SPAN_NAME: Final[str] = "samantha_server.llm_call"

# Env-var that controls `gen_ai.prompt`
# stamping on LLM child spans. Default is ON (Langfuse is inside the trust
# boundary); stamping is suppressed only when explicitly set to a falsy value.
# Values are normalized via strip + lower so operator typos (`"NO "`, `" 0"`,
# `"False\n"`) don't silently fail-open. Exported so replay.py can use the
# same constant name on the CLI side rather than open-coding the string.
STAMP_PROMPT_ENV: Final[str] = "SAMANTHA_STAMP_PROMPT"
_STAMP_PROMPT_FALSY: Final[frozenset[str]] = frozenset({"0", "false", "no", "off"})
# Used only by _warn_if_stamp_prompt_misconfigured to distinguish recognized-truthy
# values from genuinely unrecognized tokens. Does NOT rewire _stamp_prompt_enabled().
_STAMP_PROMPT_TRUTHY_HINT: Final[frozenset[str]] = frozenset({"1", "true", "yes"})


def _stamp_prompt_enabled() -> bool:
    """Return True unless SAMANTHA_STAMP_PROMPT is set to an explicit falsy value.

    Default-on: an unset (or empty/whitespace) env var enables stamping.
    Disable by setting to ``0`` / ``false`` / ``no`` / ``off`` (whitespace-
    and case-tolerant). Any other value (including ``1`` / ``true`` / ``yes``)
    also enables stamping. Read per-call so monkeypatch-style test isolation
    works correctly and so a CLI invocation that sets the env after import
    is honored.
    """
    return os.environ.get(STAMP_PROMPT_ENV, "").strip().lower() not in _STAMP_PROMPT_FALSY


def _warn_if_stamp_prompt_misconfigured() -> None:
    """Emit a WARNING if SAMANTHA_STAMP_PROMPT is set to an unrecognized value.

    Called once at startup from ``configure_otel()``. NOT called per-LLM-call
    (``_stamp_prompt_enabled()`` is per-call; this helper is startup-only to
    avoid log spam).

    Under default-on semantics, an unrecognized value (e.g. ``disabled``,
    ``flase``, ``none``) silently fails open to PHI stamping. This warning
    surfaces the misconfiguration so an operator who fat-fingered the disable
    token gets immediate feedback at startup rather than discovering the leak
    in a Langfuse trace later.

    Warning text names the offending config token (never query content — the
    value is an operator-supplied config string, not PHI).
    """
    raw = os.environ.get(STAMP_PROMPT_ENV, "").strip().lower()
    if not raw or raw in _STAMP_PROMPT_FALSY or raw in _STAMP_PROMPT_TRUTHY_HINT:
        return
    _log.warning(
        "SAMANTHA_STAMP_PROMPT=%r is not a recognized value. "
        "Accepted opt-out tokens: 0 / false / no / off. "
        "Prompt stamping remains ON; gen_ai.prompt will be sent to Langfuse.",
        raw,
    )


# Canonical values for ``samantha.environment``. Dashboards and the drift
# alarm filter on these literals; production callers stamp ENVIRONMENT_PRODUCTION,
# the in-process replay harness stamps ENVIRONMENT_REPLAY, and the parity-
# replay CLI stamps ENVIRONMENT_PARITY (separate value so dashboards can
# discriminate "comparing-to-published-numbers" runs from regular replay sweeps).
ENVIRONMENT_PRODUCTION: Final[str] = "production"
ENVIRONMENT_REPLAY: Final[str] = "replay"
ENVIRONMENT_PARITY: Final[str] = "parity"

# Tracer name — used as the instrumentation library identifier on every span.
_TRACER_NAME: Final[str] = "samantha_server"


# ---------------------------------------------------------------------------
# Allowlist-enforcing setter
# ---------------------------------------------------------------------------


def set_span_attribute(span: Span, name: str, value: AttributeValue) -> None:
    """Set ``name``=``value`` on ``span`` after enforcing the PHI allowlist.

    Raises
    ------
    ValueError
        If ``name`` is not in ``_ALLOWED_ATTRIBUTES``. The error message
        names the rejected attribute but does NOT echo ``value`` (G18).
    """
    if name not in _ALLOWED_ATTRIBUTES:
        raise ValueError(
            f"OTel span attribute {name!r} is not on the allowlist. "
            "Add it to `_SAMANTHA_ATTRS`, `_GEN_AI_ATTRS`, or "
            "`_LANGFUSE_FIELD_MIRRORS` and document the no-PHI "
            "assessment in the same PR."
        )
    span.set_attribute(name, value)


# ---------------------------------------------------------------------------
# finish_reasons fallback helper
# ---------------------------------------------------------------------------


def extract_finish_reasons(response: object) -> list[str] | None:
    """Return finish reasons from *response* as a list, or ``None`` if not reported.

    Read order:
    1. ``finish_reasons`` (plural, duck-typed) — preferred; returned as-is as a
       list. Preserves duck-typed callers that set the plural attribute directly.
       An explicit empty list (``[]``) is passed through unchanged, semantically
       distinct from ``None`` ("backend reported no reasons" vs "backend did
       not report").
    2. ``finish_reason`` (singular) — populated by ``LLMResponse`` from the wire.
       When present and non-None, wrapped into a single-element list.
    3. Neither present or both None → return ``None``.

    Callers must omit the ``gen_ai.response.finish_reasons`` attribute when this
    helper returns ``None`` rather than fabricating ``["unknown"]`` or ``[]``
    (OTel semantic conventions treat absent attributes as "not reported").

    Precedence pitfall: if a duck-typed object exposes BOTH attributes, the
    plural wins and the singular value is silently shadowed. This cannot happen
    on production ``LLMResponse`` (which only declares the singular field) but
    is a real pitfall for future duck-typed callers that mix the two shapes.

    Type posture: the parameter is ``object`` rather than a ``Protocol``
    because we explicitly want to accept response objects that do
    *not* declare either attribute (production ``LLMResponse`` is the
    common case for the singular path). A ``Protocol`` requiring
    ``finish_reasons`` would exclude ``LLMResponse``, which is the
    inverse of the contract. Duck-typed via ``getattr``.
    """
    # Prefer plural attribute (duck-typed callers, e.g. SimpleNamespace fakes).
    raw_plural = getattr(response, "finish_reasons", None)
    if raw_plural is not None:
        return list(raw_plural)
    # Fall back to singular attribute (LLMResponse.finish_reason from wire).
    singular = getattr(response, "finish_reason", None)
    if singular is not None:
        return [singular]
    return None


def _stamp_finish_reason_attrs(span: Span, response: object) -> None:
    """Stamp ``gen_ai.response.finish_reasons`` + ``finish_reason_is_length`` on *span*.

    Consolidates the block that ``llm_complete_with_span`` and
    ``llm_complete_json_with_span`` would otherwise duplicate.
    Set-only behavior: each attribute is omitted when the source value is
    ``None`` / not ``"length"``, preserving OTel's "absent = not reported"
    convention so dashboard filters like ``IS SET`` give clean signals.
    """
    finish_reasons = extract_finish_reasons(response)
    if finish_reasons is None:
        return
    set_span_attribute(span, "gen_ai.response.finish_reasons", finish_reasons)
    if "length" in finish_reasons:
        set_span_attribute(span, "gen_ai.response.finish_reason_is_length", True)


# ---------------------------------------------------------------------------
# Counter-incrementing span exporter wrapper
# ---------------------------------------------------------------------------


def _summarize_exception(exc: BaseException) -> str:
    """Return a bounded ``type: message`` string for log lines.

    Avoids ``exc_info=True`` on log calls because the exception chain
    surfaced by the OTLP exporter (via ``requests``) can carry the
    outbound ``Authorization`` header from a ``PreparedRequest`` —
    Step 9 will inject ``OTEL_EXPORTER_OTLP_HEADERS=Authorization=Basic
    <encoded_keys>``, and a tracebacked log line at that point would
    leak the credential.
    """
    return f"{type(exc).__name__}: {exc}"


class _CountingSpanExporter(SpanExporter):
    """Wraps a SpanExporter; increments ``otel_export_failures`` on export errors.

    The wrapper sits at the *exporter* boundary (not the processor
    boundary) because both ``SimpleSpanProcessor`` and
    ``BatchSpanProcessor`` swallow exporter exceptions internally —
    the processor logs and continues, so a counter wired at the
    processor layer would never see a failure. Wrapping the exporter
    intercepts the raised exception before the processor's catch
    clause and records the silent-loss event in
    ``counters.otel_export_failures``.

    Per Step 8: OTLP export failures must NOT gate the decision return
    — the receipt is the contract; the trace is observability. This
    class converts a raised exception into ``SpanExportResult.FAILURE``
    so the surrounding processor's bookkeeping stays valid, while the
    counter increment surfaces in ``/readyz`` degraded JSON.
    """

    def __init__(self, inner: SpanExporter, counters: CounterRegistry) -> None:
        self._inner = inner
        self._counters = counters

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        try:
            result = self._inner.export(spans)
        except Exception as exc:
            self._counters.otel_export_failures.increment()
            _log.warning(
                "OTel span export raised; counter incremented: %s", _summarize_exception(exc)
            )
            return SpanExportResult.FAILURE
        if result != SpanExportResult.SUCCESS:
            self._counters.otel_export_failures.increment()
        return result

    def shutdown(self) -> None:
        try:
            self._inner.shutdown()
        except Exception as exc:
            self._counters.otel_export_failures.increment()
            _log.warning("OTel exporter shutdown raised: %s", _summarize_exception(exc))

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        try:
            return bool(self._inner.force_flush(timeout_millis))
        except Exception as exc:
            self._counters.otel_export_failures.increment()
            _log.warning("OTel exporter force_flush raised: %s", _summarize_exception(exc))
            return False


# ---------------------------------------------------------------------------
# SDK setup
# ---------------------------------------------------------------------------


def _configure_tracer_provider(
    counters: CounterRegistry,
    *,
    test_exporter: InMemorySpanExporter | None = None,
) -> tuple[TracerProvider, InMemorySpanExporter | None]:
    """Construct a TracerProvider and return ``(provider, in_memory_exporter)``.

    Private helper for ``configure_otel``. Not exported — production
    deployment uses ``configure_otel`` exclusively, which adds the
    reuse-existing-provider semantics needed for test isolation.
    """
    resource = Resource.create({"service.name": "samantha_server"})
    provider = TracerProvider(resource=resource)

    if test_exporter is not None:
        provider.add_span_processor(
            SimpleSpanProcessor(_CountingSpanExporter(test_exporter, counters))
        )
        return provider, test_exporter

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if endpoint:
        # Lazy import: keeps the OTLP HTTP exporter (and its requests
        # dependency stack) off the import path of every test that
        # touches observability.
        #
        # OTLPSpanExporter() with no args reads OTEL_EXPORTER_OTLP_HEADERS
        # at construction time. Step 9 writes that env var inside
        # build_app_state() *before* this call runs — the ordering is
        # load-bearing for credential injection. When Step 9 lands,
        # consider passing headers= explicitly so the dependency is
        # visible at the call site.
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        # PR207 review #4: warn when the OTLP endpoint is off-localhost.
        # The G8 PHI ban on `gen_ai.completion` was lifted for the
        # lab-host topology (local LLM + self-hosted Langfuse on the same
        # host). Exporting to a remote endpoint sends LLM completion text
        # (potentially derived from PHI-bearing context) over the network.
        # Log-only — this PR does not fail-closed, but the warning forces
        # an operator-visible reminder that the topology assumption may
        # not hold.
        if not _endpoint_is_localhost(endpoint):
            _log.warning(
                "OTEL_EXPORTER_OTLP_ENDPOINT=%r is not localhost; "
                "allows gen_ai.completion on spans under the lab-host "
                "topology (local LLM + self-hosted Langfuse). Sending "
                "completions to a remote endpoint exports text derived "
                "from PHI-bearing context. Verify the receiving Langfuse "
                "is operator-controlled before relying on this in production.",
                endpoint,
            )

        provider.add_span_processor(
            BatchSpanProcessor(_CountingSpanExporter(OTLPSpanExporter(), counters))
        )

    return provider, None


def _endpoint_is_localhost(endpoint: str) -> bool:
    """Return True iff ``endpoint`` resolves to a loopback / local host.

    Conservative scheme/host parse — flags only the trivially-local set
    (localhost, 127.0.0.0/8, ::1, unix sockets) as safe. Any other host
    (IP literal in a non-loopback range, DNS name, etc.) is treated as
    remote so the PHI-export warning fires.

    PR207 review #4. Operator-facing diagnostic only — not a security
    boundary.
    """
    from urllib.parse import urlparse

    try:
        parsed = urlparse(endpoint)
    except (ValueError, TypeError):
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        # Schemes like ``unix://`` have no hostname and are local by definition.
        return parsed.scheme in {"unix", ""}
    if host in {"localhost", "::1"}:
        return True
    return host.startswith("127.")


def configure_otel(
    counters: CounterRegistry,
    *,
    test_exporter: InMemorySpanExporter | None = None,
) -> tuple[Tracer, TracerProvider, InMemorySpanExporter | None]:
    """Set up the global OTel tracer provider and return a tracer.

    Called by the FastAPI lifespan at startup. Installs the
    constructed ``TracerProvider`` as the global provider via
    ``trace.set_tracer_provider``.

    Reuse-existing-provider behaviour: if a ``TracerProvider`` (from
    ``opentelemetry.sdk.trace``) is already installed as the global
    provider, this function does NOT replace it — it returns the
    existing provider instead. This supports two test scenarios:

    1. A session-level conftest fixture installs an
       ``InMemorySpanExporter``-backed provider once per process; the
       lifespan then calls ``configure_otel`` and reuses it.
    2. Repeated lifespan invocations within a single test session
       (``test_lifespan.py`` patterns) reuse the first installed
       provider rather than emitting OTel's "overriding tracer
       provider" warning.

    Production deployment runs ``configure_otel`` exactly once via the
    lifespan; the reuse branch is dormant in that path.

    The ``test_exporter`` parameter forces the in-memory exporter to
    be wired in regardless of whether a TracerProvider is already
    installed. In the already-installed case, a fresh
    ``SimpleSpanProcessor`` is added to the existing provider so the
    test harness can read the recorded spans without losing the
    other-test provider's processors.
    """
    _warn_if_stamp_prompt_misconfigured()

    existing = trace.get_tracer_provider()

    if test_exporter is not None and isinstance(existing, TracerProvider):
        existing.add_span_processor(
            SimpleSpanProcessor(_CountingSpanExporter(test_exporter, counters))
        )
        return trace.get_tracer(_TRACER_NAME), existing, test_exporter

    if test_exporter is None and isinstance(existing, TracerProvider):
        return trace.get_tracer(_TRACER_NAME), existing, None

    provider, in_memory = _configure_tracer_provider(counters, test_exporter=test_exporter)
    trace.set_tracer_provider(provider)
    tracer = trace.get_tracer(_TRACER_NAME)
    return tracer, provider, in_memory


# ---------------------------------------------------------------------------
# gen_ai.* child span helper around llm_client.complete()
# ---------------------------------------------------------------------------


def llm_complete_with_span(
    llm_client: LLMClient,
    prompt: str,
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> LLMResponse:
    """Wrap a single ``llm_client.complete()`` call with a ``gen_ai.*`` child span.

    The span attaches to the ambient OTel context (set by the consumer
    after dequeuing — see ``api/app.py::_consume``). Inside the
    ``samantha_server.event`` parent's context, the resulting span is a
    child; without that context, the span is a root, which is the
    correct fallback shape (no orphan-vs-root ambiguity to resolve).

    ``gen_ai.completion`` is stamped unconditionally on the success
    path. ``gen_ai.prompt`` is stamped by default; suppressed only
    when ``SAMANTHA_STAMP_PROMPT`` is explicitly ``"0"``, ``"false"``,
    ``"no"``, or ``"off"``. The prompt stamp happens BEFORE
    ``llm_client.complete()`` so error /
    timeout paths still carry input in the span — without this, a hung
    pre-send call leaves Langfuse with no input recorded
    and the diagnosis stays ambiguous.

    Error contract: when ``llm_client.complete()`` raises, the span is
    marked ``StatusCode.ERROR`` and the exception is recorded as a span
    event before re-raising. Existing ``LLMClientError`` handlers at
    the call sites still fire — wrapping does not swallow the error.

    The helper passes ``effective_temp`` / ``effective_max`` (the
    config-resolved values) into ``complete()`` so the span attribute
    matches what the model actually saw — without this, a future client
    whose ``None``-default diverges from ``cfg.LLM_TEMPERATURE`` would
    make the span lie.

    Theoretical ``ValueError`` from ``set_span_attribute``: every
    attribute name passed below is a static literal on the allowlist,
    so the raise path is unreachable today. A future refactor that
    introduces a non-literal name must keep this contract or wrap the
    call.
    """
    import samantha_server.config as cfg

    effective_temp = cfg.LLM_TEMPERATURE if temperature is None else temperature
    effective_max = cfg.LLM_MAX_TOKENS if max_tokens is None else max_tokens

    tracer = trace.get_tracer(_TRACER_NAME)
    with tracer.start_as_current_span(LLM_CHILD_SPAN_NAME) as span:
        set_span_attribute(span, "gen_ai.system", cfg.LLM_PROVIDER)
        set_span_attribute(span, "gen_ai.request.model", cfg.LLM_MODEL_NAME)
        set_span_attribute(span, "gen_ai.request.temperature", effective_temp)
        set_span_attribute(span, "gen_ai.request.max_tokens", effective_max)

        # Stamp prompt BEFORE the try-block so error / timeout paths still
        # capture input. See docstring for the diagnostic rationale.
        # Ordering note: if a future allowlist edit ever removed
        # "gen_ai.prompt" from _ALLOWED_ATTRIBUTES, this call would raise
        # ValueError BEFORE llm_client.complete() runs — silently skipping
        # the LLM invocation. The except below catches `Exception` but only
        # over the try-block; this stamp is outside it. Keep the attribute
        # allowlisted, or wrap this call if you remove it.
        if _stamp_prompt_enabled():
            set_span_attribute(span, "gen_ai.prompt", prompt)

        try:
            response = llm_client.complete(
                prompt,
                temperature=effective_temp,
                max_tokens=effective_max,
            )
        except Exception as exc:
            span.set_status(StatusCode.ERROR, type(exc).__name__)
            span.record_exception(exc)
            raise

        set_span_attribute(span, "gen_ai.response.model", response.model_id)
        set_span_attribute(span, "gen_ai.usage.input_tokens", response.input_tokens)
        set_span_attribute(span, "gen_ai.usage.output_tokens", response.output_tokens)
        set_span_attribute(span, "gen_ai.completion", response.text)

        _stamp_finish_reason_attrs(span, response)

        return response


def llm_complete_json_with_span(
    llm_client: LLMClient,
    messages: list[ChatMessage],
    *,
    schema: dict[str, Any],
    schema_name: str,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> LLMResponse:
    """Wrap a single ``llm_client.complete_json()`` call with a ``gen_ai.*`` child span.

    Mirrors ``llm_complete_with_span`` for the JSON output path.
    The span shape is identical — same attribute set, same error handling.
    ``gen_ai.completion`` is stamped on the success path only (the
    stamp call sits after the ``try/except``; a raised call propagates
    without setting it). ``gen_ai.prompt`` is stamped as
    ``json.dumps(messages)`` BEFORE the ``try/except`` by default, so
    error / timeout paths still carry input on the span. Suppress with
    ``SAMANTHA_STAMP_PROMPT=0/false/no/off``.
    """
    import samantha_server.config as cfg

    effective_temp = cfg.LLM_TEMPERATURE if temperature is None else temperature
    effective_max = cfg.LLM_MAX_TOKENS if max_tokens is None else max_tokens

    tracer = trace.get_tracer(_TRACER_NAME)
    with tracer.start_as_current_span(LLM_CHILD_SPAN_NAME) as span:
        set_span_attribute(span, "gen_ai.system", cfg.LLM_PROVIDER)
        set_span_attribute(span, "gen_ai.request.model", cfg.LLM_MODEL_NAME)
        set_span_attribute(span, "gen_ai.request.temperature", effective_temp)
        set_span_attribute(span, "gen_ai.request.max_tokens", effective_max)

        # Stamp prompt BEFORE the try-block so error / timeout paths still
        # capture input. Mirrors the text-endpoint fix; see
        # llm_complete_with_span for rationale.
        if _stamp_prompt_enabled():
            set_span_attribute(span, "gen_ai.prompt", json.dumps(messages))

        try:
            response = llm_client.complete_json(
                messages,
                schema=schema,
                schema_name=schema_name,
                temperature=effective_temp,
                max_tokens=effective_max,
            )
        except Exception as exc:
            span.set_status(StatusCode.ERROR, type(exc).__name__)
            span.record_exception(exc)
            raise

        set_span_attribute(span, "gen_ai.response.model", response.model_id)
        set_span_attribute(span, "gen_ai.usage.input_tokens", response.input_tokens)
        set_span_attribute(span, "gen_ai.usage.output_tokens", response.output_tokens)
        set_span_attribute(span, "gen_ai.completion", response.text)

        _stamp_finish_reason_attrs(span, response)

        return response


def _stamp_attribute(span: Span, name: str, value: AttributeValue) -> None:
    """Call set_span_attribute, logging a per-attribute warning on failure.

    Containment is per-attribute so a single bad key does not silence
    any other attribute in the same stamp_trace_attributes call. The broad
    second catch keeps the fail-soft contract ("observability must not gate
    the decision return") for non-ValueError SDK failures too (
    review); its log line carries the key and exception type only — never
    str(exc), whose content the SDK controls (G18: values can be PHI).
    """
    try:
        set_span_attribute(span, name, value)
    except ValueError as exc:
        _log.warning(
            "stamp_trace_attributes: allowlist rejection — %s; "
            "other attributes in this call are unaffected.",
            exc,
        )
    except Exception as exc:
        _log.warning(
            "stamp_trace_attributes: failed to stamp %r (%s); "
            "other attributes in this call are unaffected.",
            name,
            type(exc).__name__,
        )


def stamp_trace_attributes(
    span: Span,
    ctx: TraceContext,
) -> None:
    """Stamp span attributes from *ctx* per the union-mapping table.

    Emits only the attributes whose source field is non-None. Precedence
    rules mirror the union table:
    - ``scenario_id`` wins over ``session_id`` for ``langfuse.trace.name``
      and ``langfuse.trace.metadata.scenario_id``.
    - ``run_id`` wins over ``sweep_run_id`` for ``langfuse.release``.

    Fails soft on ``ValueError`` from ``set_span_attribute`` — observability
    must not gate the decision return. Each attribute is contained
    independently: a rejection on one key does not drop any
    subsequent key. Logs one warning per rejected attribute with the prefix
    ``"stamp_trace_attributes: allowlist rejection — %s"``.
    """

    if ctx.session_id is not None:
        _stamp_attribute(span, "samantha.session_id", ctx.session_id)
        if ctx.scenario_id is None:
            _stamp_attribute(span, "langfuse.trace.name", ctx.session_id)
            _stamp_attribute(span, "langfuse.trace.metadata.scenario_id", ctx.session_id)

    if ctx.scenario_id is not None:
        # samantha.scenario_id dropped; canonical surface is metadata.*
        _stamp_attribute(span, "langfuse.trace.name", ctx.scenario_id)
        _stamp_attribute(span, "langfuse.trace.metadata.scenario_id", ctx.scenario_id)

    if ctx.scenario_category is not None:
        # samantha.scenario_category dropped; canonical surface is metadata.*
        _stamp_attribute(span, "langfuse.trace.tags", [ctx.scenario_category])
        _stamp_attribute(span, "langfuse.trace.metadata.scenario_category", ctx.scenario_category)

    if ctx.sweep_run_id is not None:
        # samantha.sweep_run_id dropped; canonical surface is metadata.*
        _stamp_attribute(span, "langfuse.trace.metadata.sweep_run_id", ctx.sweep_run_id)
        if ctx.run_id is None:
            _stamp_attribute(span, "langfuse.release", ctx.sweep_run_id)

    if ctx.run_id is not None:
        _stamp_attribute(span, "langfuse.release", ctx.run_id)

    if ctx.environment is not None:
        # samantha.environment dropped; canonical surface is metadata.*
        _stamp_attribute(span, "langfuse.environment", ctx.environment)
        _stamp_attribute(span, "langfuse.trace.metadata.environment", ctx.environment)

    if ctx.priority is not None:
        _stamp_attribute(span, "samantha.priority", ctx.priority)

    if ctx.event_input_hash is not None:
        _stamp_attribute(span, "samantha.event_input_hash", ctx.event_input_hash)

    if ctx.routing_path is not None:
        # samantha.routing_path dropped; canonical surface is metadata.*
        _stamp_attribute(span, "langfuse.trace.metadata.routing_path", ctx.routing_path)

    if ctx.next_state is not None:
        # samantha.next_state dropped; canonical surface is metadata.*
        _stamp_attribute(span, "langfuse.trace.metadata.next_state", ctx.next_state)

    if ctx.outcome is not None:
        # samantha.outcome dropped; canonical surface is metadata.*
        _stamp_attribute(span, "langfuse.trace.metadata.outcome", ctx.outcome)

    if ctx.latency_us is not None:
        _stamp_attribute(span, "samantha.latency_us", ctx.latency_us)

    if ctx.applied_rule_id is not None:
        _stamp_attribute(span, "samantha.applied_rule_id", ctx.applied_rule_id)

    if ctx.receipt_id is not None:
        _stamp_attribute(span, "samantha.receipt_id", ctx.receipt_id)

    if ctx.order_id is not None:
        _stamp_attribute(span, "samantha.order_id", ctx.order_id)


def make_counting_exporter(inner: SpanExporter, counters: CounterRegistry) -> SpanExporter:
    """Return a SpanExporter that forwards to *inner* and counts export failures.

    Public factory wrapping the private ``_CountingSpanExporter`` so external
    callers (e.g., the replay harness) don't reach into a leading-underscore
    symbol.
    """
    return _CountingSpanExporter(inner, counters)


__all__ = [
    "ENVIRONMENT_PARITY",
    "ENVIRONMENT_PRODUCTION",
    "ENVIRONMENT_REPLAY",
    "LLM_CHILD_SPAN_NAME",
    "PARENT_SPAN_NAME",
    "configure_otel",
    "extract_finish_reasons",
    "llm_complete_json_with_span",
    "llm_complete_with_span",
    "make_counting_exporter",
    "set_span_attribute",
    "stamp_trace_attributes",
]
