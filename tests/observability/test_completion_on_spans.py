"""gen_ai.completion and gen_ai.prompt on OTel spans.

Local-LLM + self-hosted Langfuse topology allows LLM completions on spans for
operator diagnostics. This module drives all four slices through
red-green-refactor:

Slice 1 — gen_ai.completion allowlisted (unconditional)
Slice 2 — completion stamped on both llm_complete_with_span / llm_complete_json_with_span
Slice 3 — gen_ai.prompt default-on; disable with falsy SAMANTHA_STAMP_PROMPT
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from samantha_server.llm.client import LLMResponse

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_provider_and_exporter() -> tuple[TracerProvider, InMemorySpanExporter]:
    """Return a fresh isolated TracerProvider + InMemorySpanExporter pair."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider, exporter


def _patch_get_tracer(monkeypatch: pytest.MonkeyPatch, provider: TracerProvider) -> None:
    """Redirect `otel_trace.get_tracer(...)` to the given test-isolated provider.

    PR207 Low #8: extracted to remove the three-line inner-function +
    monkeypatch.setattr boilerplate that previously repeated in 5+ tests.
    """
    from opentelemetry import trace as otel_trace

    monkeypatch.setattr(otel_trace, "get_tracer", lambda name: provider.get_tracer(name))


def _make_response(text: str = "ok") -> LLMResponse:
    return LLMResponse(
        text=text,
        input_tokens=10,
        output_tokens=5,
        model_id="test-model-v1",
        latency_us=1000,
    )


def _make_llm_client(text: str = "ok") -> Any:
    mock = MagicMock()
    mock.model_id = "test-model"
    response = _make_response(text)
    mock.complete.return_value = response
    mock.complete_json.return_value = response
    return mock


# ---------------------------------------------------------------------------
# Slice 1 — gen_ai.completion on allowlist; set_span_attribute accepts it
# ---------------------------------------------------------------------------


def test_slice1_gen_ai_completion_is_on_allowlist() -> None:
    """gen_ai.completion must be in _ALLOWED_ATTRIBUTES."""
    from samantha_server.observability.otel import _ALLOWED_ATTRIBUTES

    assert "gen_ai.completion" in _ALLOWED_ATTRIBUTES, (
        "gen_ai.completion must be allowlisted for lab-host Langfuse diagnostics"
    )


def test_slice1_set_span_attribute_accepts_gen_ai_completion() -> None:
    """set_span_attribute does NOT raise for gen_ai.completion."""
    from samantha_server.observability.otel import set_span_attribute

    provider, _ = _make_provider_and_exporter()
    tracer = provider.get_tracer("test")
    with tracer.start_as_current_span("t") as span:
        # Must not raise ValueError.
        set_span_attribute(span, "gen_ai.completion", "some text")


# ---------------------------------------------------------------------------
# Slice 2 — completion stamped on both wrappers (unconditional, success path)
# ---------------------------------------------------------------------------


def test_slice2_llm_complete_with_span_stamps_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """llm_complete_with_span stamps gen_ai.completion = response.text."""
    from opentelemetry import trace as otel_trace

    import samantha_server.config as cfg
    from samantha_server.observability.otel import (
        LLM_CHILD_SPAN_NAME,
        llm_complete_with_span,
    )

    provider, exporter = _make_provider_and_exporter()
    monkeypatch.setattr(otel_trace, "get_tracer_provider", lambda: provider)

    _patch_get_tracer(monkeypatch, provider)

    completion_text = "The answer is 42."
    mock_llm = _make_llm_client(completion_text)

    monkeypatch.setattr(cfg, "LLM_PROVIDER", "test-provider")
    monkeypatch.setattr(cfg, "LLM_MODEL_NAME", "test-model-v1")
    monkeypatch.setattr(cfg, "LLM_TEMPERATURE", 0.0)
    monkeypatch.setattr(cfg, "LLM_MAX_TOKENS", 512)

    llm_complete_with_span(mock_llm, "test prompt")

    spans = exporter.get_finished_spans()
    child = next((s for s in spans if s.name == LLM_CHILD_SPAN_NAME), None)
    assert child is not None, f"LLM child span not found; spans={[s.name for s in spans]}"

    attrs = dict(child.attributes or {})
    assert attrs.get("gen_ai.completion") == completion_text, (
        f"gen_ai.completion must equal response.text; attrs={attrs}"
    )


def test_slice2_llm_complete_json_with_span_stamps_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """llm_complete_json_with_span stamps gen_ai.completion = response.text."""

    import samantha_server.config as cfg
    from samantha_server.observability.otel import (
        LLM_CHILD_SPAN_NAME,
        llm_complete_json_with_span,
    )

    provider, exporter = _make_provider_and_exporter()

    _patch_get_tracer(monkeypatch, provider)

    completion_text = '{"answer_type":"no_orders","order_ids":[]}'
    mock_llm = _make_llm_client(completion_text)

    monkeypatch.setattr(cfg, "LLM_PROVIDER", "test-provider")
    monkeypatch.setattr(cfg, "LLM_MODEL_NAME", "test-model-v1")
    monkeypatch.setattr(cfg, "LLM_TEMPERATURE", 0.0)
    monkeypatch.setattr(cfg, "LLM_MAX_TOKENS", 512)

    schema: dict[str, Any] = {"type": "object"}
    llm_complete_json_with_span(
        mock_llm,
        [{"role": "user", "content": "test"}],
        schema=schema,
        schema_name="TestSchema",
    )

    spans = exporter.get_finished_spans()
    child = next((s for s in spans if s.name == LLM_CHILD_SPAN_NAME), None)
    assert child is not None, f"LLM child span not found; spans={[s.name for s in spans]}"

    attrs = dict(child.attributes or {})
    assert attrs.get("gen_ai.completion") == completion_text, (
        f"gen_ai.completion must equal response.text; attrs={attrs}"
    )


# ---------------------------------------------------------------------------
# Slice 3 — gen_ai.prompt default-on; suppressed by explicit falsy SAMANTHA_STAMP_PROMPT
# ---------------------------------------------------------------------------


def test_slice3_gen_ai_prompt_is_on_allowlist() -> None:
    """gen_ai.prompt must be in _ALLOWED_ATTRIBUTES."""
    from samantha_server.observability.otel import _ALLOWED_ATTRIBUTES

    assert "gen_ai.prompt" in _ALLOWED_ATTRIBUTES, (
        "gen_ai.prompt must be allowlisted for prompt diagnostics"
    )


def test_slice3_prompt_stamped_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With SAMANTHA_STAMP_PROMPT unset, gen_ai.prompt is present (default-on)."""

    import samantha_server.config as cfg
    from samantha_server.observability.otel import (
        LLM_CHILD_SPAN_NAME,
        llm_complete_with_span,
    )

    # Ensure env var is absent — unset means default-on.
    monkeypatch.delenv("SAMANTHA_STAMP_PROMPT", raising=False)

    provider, exporter = _make_provider_and_exporter()

    _patch_get_tracer(monkeypatch, provider)

    mock_llm = _make_llm_client()
    monkeypatch.setattr(cfg, "LLM_PROVIDER", "test-provider")
    monkeypatch.setattr(cfg, "LLM_MODEL_NAME", "test-model-v1")
    monkeypatch.setattr(cfg, "LLM_TEMPERATURE", 0.0)
    monkeypatch.setattr(cfg, "LLM_MAX_TOKENS", 512)

    prompt_text = "stamp this by default"
    llm_complete_with_span(mock_llm, prompt_text)

    spans = exporter.get_finished_spans()
    child = next((s for s in spans if s.name == LLM_CHILD_SPAN_NAME), None)
    assert child is not None

    attrs = dict(child.attributes or {})
    assert attrs.get("gen_ai.prompt") == prompt_text, (
        "gen_ai.prompt must be present when SAMANTHA_STAMP_PROMPT is unset (default-on)"
    )


def test_slice3_prompt_stamped_when_env_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With SAMANTHA_STAMP_PROMPT=1, gen_ai.prompt is stamped."""

    import samantha_server.config as cfg
    from samantha_server.observability.otel import (
        LLM_CHILD_SPAN_NAME,
        llm_complete_with_span,
    )

    monkeypatch.setenv("SAMANTHA_STAMP_PROMPT", "1")

    provider, exporter = _make_provider_and_exporter()

    _patch_get_tracer(monkeypatch, provider)

    mock_llm = _make_llm_client()
    monkeypatch.setattr(cfg, "LLM_PROVIDER", "test-provider")
    monkeypatch.setattr(cfg, "LLM_MODEL_NAME", "test-model-v1")
    monkeypatch.setattr(cfg, "LLM_TEMPERATURE", 0.0)
    monkeypatch.setattr(cfg, "LLM_MAX_TOKENS", 512)

    prompt_text = "stamp this prompt"
    llm_complete_with_span(mock_llm, prompt_text)

    spans = exporter.get_finished_spans()
    child = next((s for s in spans if s.name == LLM_CHILD_SPAN_NAME), None)
    assert child is not None

    attrs = dict(child.attributes or {})
    assert attrs.get("gen_ai.prompt") == prompt_text, (
        f"gen_ai.prompt must equal prompt when SAMANTHA_STAMP_PROMPT=1; attrs={attrs}"
    )


def test_slice3_json_prompt_stamped_as_canonical_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SAMANTHA_STAMP_PROMPT=1, json wrapper stamps prompt as json.dumps."""

    import samantha_server.config as cfg
    from samantha_server.observability.otel import (
        LLM_CHILD_SPAN_NAME,
        llm_complete_json_with_span,
    )

    monkeypatch.setenv("SAMANTHA_STAMP_PROMPT", "1")

    provider, exporter = _make_provider_and_exporter()

    _patch_get_tracer(monkeypatch, provider)

    mock_llm = _make_llm_client()
    monkeypatch.setattr(cfg, "LLM_PROVIDER", "test-provider")
    monkeypatch.setattr(cfg, "LLM_MODEL_NAME", "test-model-v1")
    monkeypatch.setattr(cfg, "LLM_TEMPERATURE", 0.0)
    monkeypatch.setattr(cfg, "LLM_MAX_TOKENS", 512)

    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "q"}]
    schema: dict[str, Any] = {"type": "object"}
    llm_complete_json_with_span(
        mock_llm,
        messages,
        schema=schema,
        schema_name="TestSchema",
    )

    spans = exporter.get_finished_spans()
    child = next((s for s in spans if s.name == LLM_CHILD_SPAN_NAME), None)
    assert child is not None

    attrs = dict(child.attributes or {})
    expected = json.dumps(messages)
    assert attrs.get("gen_ai.prompt") == expected, (
        f"gen_ai.prompt must equal json.dumps(messages) "
        f"when SAMANTHA_STAMP_PROMPT=1; attrs={attrs}"
    )


# ---------------------------------------------------------------------------
# PR207 review #6: regression coverage on three production paths
# ---------------------------------------------------------------------------


def test_pr207_completion_absent_on_raised_llm_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PR207 review #6 (a): when llm_client.complete() raises, the span does
    NOT carry gen_ai.completion. Production code stamps the attribute AFTER
    the try/except returns; a regression that moved the stamp before the
    call would silently expose attacker-controlled or partial completion
    text on failed spans.
    """

    import samantha_server.config as cfg
    from samantha_server.observability.otel import LLM_CHILD_SPAN_NAME, llm_complete_with_span

    provider, exporter = _make_provider_and_exporter()

    _patch_get_tracer(monkeypatch, provider)

    mock_llm = MagicMock()
    mock_llm.complete.side_effect = RuntimeError("simulated LLM timeout")

    monkeypatch.setattr(cfg, "LLM_PROVIDER", "test-provider")
    monkeypatch.setattr(cfg, "LLM_MODEL_NAME", "test-model-v1")
    monkeypatch.setattr(cfg, "LLM_TEMPERATURE", 0.0)
    monkeypatch.setattr(cfg, "LLM_MAX_TOKENS", 512)

    with pytest.raises(RuntimeError, match="simulated LLM timeout"):
        llm_complete_with_span(mock_llm, "the prompt")

    spans = exporter.get_finished_spans()
    child = next((s for s in spans if s.name == LLM_CHILD_SPAN_NAME), None)
    assert child is not None, "child span must be exported even on raise"

    attrs = dict(child.attributes or {})
    assert "gen_ai.completion" not in attrs, (
        f"PR207 review #6: gen_ai.completion must NOT appear on a failed-call "
        f"span (no completion to record); attrs={attrs}"
    )


@pytest.mark.parametrize(
    "env_value",
    ["1", "true", "yes", "TRUE", "Yes", " 1 ", "true\n"],
)
def test_pr207_stamp_prompt_env_truthy_values(
    monkeypatch: pytest.MonkeyPatch, env_value: str
) -> None:
    """PR207 review #6 (b) + #2: all advertised truthy values (case-insensitive,
    whitespace-tolerant) enable prompt stamping. Pins the `_stamp_prompt_enabled`
    helper against regressions that would silently disable `true` or `yes`,
    or fail-closed on `"yes "` (the misconfigured-operator scenario).
    """

    import samantha_server.config as cfg
    from samantha_server.observability.otel import LLM_CHILD_SPAN_NAME, llm_complete_with_span

    monkeypatch.setenv("SAMANTHA_STAMP_PROMPT", env_value)

    provider, exporter = _make_provider_and_exporter()

    _patch_get_tracer(monkeypatch, provider)

    mock_llm = _make_llm_client()
    monkeypatch.setattr(cfg, "LLM_PROVIDER", "test-provider")
    monkeypatch.setattr(cfg, "LLM_MODEL_NAME", "test-model-v1")
    monkeypatch.setattr(cfg, "LLM_TEMPERATURE", 0.0)
    monkeypatch.setattr(cfg, "LLM_MAX_TOKENS", 512)

    prompt_text = "What orders are ready?"
    llm_complete_with_span(mock_llm, prompt_text)

    spans = exporter.get_finished_spans()
    child = next((s for s in spans if s.name == LLM_CHILD_SPAN_NAME), None)
    assert child is not None
    attrs = dict(child.attributes or {})
    assert attrs.get("gen_ai.prompt") == prompt_text, (
        f"PR207: env_value={env_value!r} should enable prompt stamping; attrs={attrs}"
    )


@pytest.mark.parametrize(
    "env_value",
    ["0", "false", "no", "FALSE", "off"],
)
def test_pr207_stamp_prompt_env_falsy_values(
    monkeypatch: pytest.MonkeyPatch, env_value: str
) -> None:
    """Explicit falsy values (0/false/no/off, case-insensitive) disable prompt stamping.

    Blank/whitespace-only values are no longer treated as falsy; they map to the
    default-on behavior. Only the four explicit opt-out tokens suppress stamping.
    """

    import samantha_server.config as cfg
    from samantha_server.observability.otel import LLM_CHILD_SPAN_NAME, llm_complete_with_span

    monkeypatch.setenv("SAMANTHA_STAMP_PROMPT", env_value)

    provider, exporter = _make_provider_and_exporter()

    _patch_get_tracer(monkeypatch, provider)

    mock_llm = _make_llm_client()
    monkeypatch.setattr(cfg, "LLM_PROVIDER", "test-provider")
    monkeypatch.setattr(cfg, "LLM_MODEL_NAME", "test-model-v1")
    monkeypatch.setattr(cfg, "LLM_TEMPERATURE", 0.0)
    monkeypatch.setattr(cfg, "LLM_MAX_TOKENS", 512)

    llm_complete_with_span(mock_llm, "the prompt")

    spans = exporter.get_finished_spans()
    child = next((s for s in spans if s.name == LLM_CHILD_SPAN_NAME), None)
    assert child is not None
    attrs = dict(child.attributes or {})
    assert "gen_ai.prompt" not in attrs, (
        f"PR207: env_value={env_value!r} should NOT enable prompt stamping; attrs={attrs}"
    )


def test_pr207_stamp_prompt_main_restores_env_on_return(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PR207 review #5: main(['--stamp-prompt', ...]) restores the prior
    SAMANTHA_STAMP_PROMPT value on function exit. Pins the try/finally
    cleanup so an in-process caller doesn't leak the env flag into
    subsequent invocations.
    """
    import json as _json

    monkeypatch.delenv("SAMANTHA_STAMP_PROMPT", raising=False)
    fixture = {
        "scenario_id": "SC-PR207-RESTORE",
        "category": "rule_coverage",
        "description": "Deterministic fixture; no LLM call",
        "events": [
            {
                "step": 1,
                "event_type": "order_received",
                "event_data": {
                    "patient_name": "T, X",
                    "age": 45,
                    "sex": "F",
                    "specimen_type": "biopsy",
                    "anatomic_site": "breast",
                    "fixative": "formalin",
                    "fixation_time_hours": 24.0,
                    "ordered_tests": ["Breast IHC Panel"],
                    "priority": "routine",
                    "billing_info_present": True,
                },
                "expected_output": {
                    "next_state": "ACCEPTED",
                    "applied_rules": ["ACC-008"],
                    "flags": [],
                    "routing_path": "deterministic",
                },
            }
        ],
    }
    subdir = tmp_path / "rule_coverage"
    subdir.mkdir()
    (subdir / "sc-pr207-restore.json").write_text(_json.dumps(fixture))

    from samantha_server.scenarios.replay import main

    exit_code = main([str(tmp_path), "--stamp-prompt"])
    assert exit_code == 0, "deterministic fixture should pass"

    import os as _os

    assert _os.environ.get("SAMANTHA_STAMP_PROMPT") is None, (
        "PR207 review #5: SAMANTHA_STAMP_PROMPT must be unset after main() "
        "returns when it was unset before (no leak into subsequent in-process callers)"
    )


# ---------------------------------------------------------------------------
# Stamp-on-error: prompt is recorded even when the LLM call raises.
#
# Pre-fix, gen_ai.prompt was stamped after llm_client.complete() returned —
# so a timeout / connection-refused / any other error path produced a span
# with no prompt attribute even when SAMANTHA_STAMP_PROMPT=1. This blinded
# its diagnosis: the issue body claimed "input prompt was never
# recorded in Langfuse... call stuck pre-send," but the missing prompt was
# actually a function of stamp-after-success ordering, not pre-send hang.
# Move the stamp above the try-block so error-path traces still carry
# input.
# ---------------------------------------------------------------------------


def test_prompt_stamped_on_error_path_text_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing llm_client.complete() call must still leave gen_ai.prompt on the span."""
    import samantha_server.config as cfg
    from samantha_server.observability.otel import (
        LLM_CHILD_SPAN_NAME,
        llm_complete_with_span,
    )

    monkeypatch.setenv("SAMANTHA_STAMP_PROMPT", "1")

    provider, exporter = _make_provider_and_exporter()
    _patch_get_tracer(monkeypatch, provider)

    # `RuntimeError` stands in for the real production error taxonomy
    # (`LLMTimeoutError` / `LLMClientError`); the wrapper's catch is
    # `except Exception` so behavior is identical for any subclass.
    failing = MagicMock()
    failing.model_id = "test-model"
    failing.complete.side_effect = RuntimeError("simulated LLM timeout")

    monkeypatch.setattr(cfg, "LLM_PROVIDER", "test-provider")
    monkeypatch.setattr(cfg, "LLM_MODEL_NAME", "test-model-v1")
    monkeypatch.setattr(cfg, "LLM_TEMPERATURE", 0.0)
    monkeypatch.setattr(cfg, "LLM_MAX_TOKENS", 512)

    prompt_text = "this prompt must survive the error path"
    with pytest.raises(RuntimeError, match="simulated LLM timeout"):
        llm_complete_with_span(failing, prompt_text)

    spans = exporter.get_finished_spans()
    child = next((s for s in spans if s.name == LLM_CHILD_SPAN_NAME), None)
    assert child is not None, "child span must be emitted even on error"

    attrs = dict(child.attributes or {})
    assert attrs.get("gen_ai.prompt") == prompt_text, (
        "prompt must be stamped before the try-block so error/timeout paths "
        f"still capture input; attrs={attrs}"
    )
    # Pin the error status + recorded-exception contract.
    # Without these, a refactor that dropped `set_status` / `record_exception`
    # would still green-light this test on the prompt-stamp alone.
    assert child.status.status_code == StatusCode.ERROR, (
        "error path must set the span status to ERROR"
    )
    event_names = [e.name for e in child.events]
    assert "exception" in event_names, (
        f"error path must record the exception as a span event; events={event_names!r}"
    )


def test_prompt_stamped_on_error_path_json_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing llm_client.complete_json() call must still leave gen_ai.prompt on the span.

    Mirrors the text-endpoint test against the JSON wrapper used by
    PENDING_LLM_REVIEW (LR-* fixtures). The prompt is rendered as
    json.dumps(messages) per the existing stamp-on-success contract.
    """
    import samantha_server.config as cfg
    from samantha_server.observability.otel import (
        LLM_CHILD_SPAN_NAME,
        llm_complete_json_with_span,
    )

    monkeypatch.setenv("SAMANTHA_STAMP_PROMPT", "1")

    provider, exporter = _make_provider_and_exporter()
    _patch_get_tracer(monkeypatch, provider)

    # `RuntimeError` stands in for the real production error taxonomy
    # (`LLMTimeoutError` / `LLMClientError`); wrapper catch is `except Exception`.
    failing = MagicMock()
    failing.model_id = "test-model"
    failing.complete_json.side_effect = RuntimeError("simulated LLM timeout")

    monkeypatch.setattr(cfg, "LLM_PROVIDER", "test-provider")
    monkeypatch.setattr(cfg, "LLM_MODEL_NAME", "test-model-v1")
    monkeypatch.setattr(cfg, "LLM_TEMPERATURE", 0.0)
    monkeypatch.setattr(cfg, "LLM_MAX_TOKENS", 512)

    messages = [{"role": "user", "content": "this prompt must survive too"}]
    with pytest.raises(RuntimeError, match="simulated LLM timeout"):
        llm_complete_json_with_span(
            failing, messages, schema={"type": "object"}, schema_name="test"
        )

    spans = exporter.get_finished_spans()
    child = next((s for s in spans if s.name == LLM_CHILD_SPAN_NAME), None)
    assert child is not None, "child span must be emitted even on error"

    attrs = dict(child.attributes or {})
    assert attrs.get("gen_ai.prompt") == json.dumps(messages), (
        "prompt must be stamped before the try-block so error/timeout paths "
        f"still capture input; attrs={attrs}"
    )
    # Pin the error status + recorded-exception contract.
    assert child.status.status_code == StatusCode.ERROR, (
        "error path must set the span status to ERROR"
    )
    event_names = [e.name for e in child.events]
    assert "exception" in event_names, (
        f"error path must record the exception as a span event; events={event_names!r}"
    )


def test_prompt_not_stamped_on_error_path_when_env_off_text_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit SAMANTHA_STAMP_PROMPT=0 suppresses gen_ai.prompt on error path.

    Without this, a regression removing the `_stamp_prompt_enabled()` gate at
    the pre-try stamp site would always-stamp on error and wouldn't be caught.
    Uses an explicit falsy value (0) rather than delenv; unset now means default-on.
    """
    import samantha_server.config as cfg
    from samantha_server.observability.otel import (
        LLM_CHILD_SPAN_NAME,
        llm_complete_with_span,
    )

    monkeypatch.setenv("SAMANTHA_STAMP_PROMPT", "0")

    provider, exporter = _make_provider_and_exporter()
    _patch_get_tracer(monkeypatch, provider)

    failing = MagicMock()
    failing.model_id = "test-model"
    failing.complete.side_effect = RuntimeError("simulated LLM timeout")

    monkeypatch.setattr(cfg, "LLM_PROVIDER", "test-provider")
    monkeypatch.setattr(cfg, "LLM_MODEL_NAME", "test-model-v1")
    monkeypatch.setattr(cfg, "LLM_TEMPERATURE", 0.0)
    monkeypatch.setattr(cfg, "LLM_MAX_TOKENS", 512)

    with pytest.raises(RuntimeError, match="simulated LLM timeout"):
        llm_complete_with_span(failing, "must NOT be stamped on error when env=0")

    spans = exporter.get_finished_spans()
    child = next((s for s in spans if s.name == LLM_CHILD_SPAN_NAME), None)
    assert child is not None

    attrs = dict(child.attributes or {})
    assert "gen_ai.prompt" not in attrs, (
        "gen_ai.prompt must be absent on the error path when SAMANTHA_STAMP_PROMPT=0; "
        f"attrs={attrs}"
    )


def test_prompt_not_stamped_on_error_path_when_env_off_json_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit SAMANTHA_STAMP_PROMPT=0 suppresses gen_ai.prompt on error path (JSON)."""
    import samantha_server.config as cfg
    from samantha_server.observability.otel import (
        LLM_CHILD_SPAN_NAME,
        llm_complete_json_with_span,
    )

    monkeypatch.setenv("SAMANTHA_STAMP_PROMPT", "0")

    provider, exporter = _make_provider_and_exporter()
    _patch_get_tracer(monkeypatch, provider)

    failing = MagicMock()
    failing.model_id = "test-model"
    failing.complete_json.side_effect = RuntimeError("simulated LLM timeout")

    monkeypatch.setattr(cfg, "LLM_PROVIDER", "test-provider")
    monkeypatch.setattr(cfg, "LLM_MODEL_NAME", "test-model-v1")
    monkeypatch.setattr(cfg, "LLM_TEMPERATURE", 0.0)
    monkeypatch.setattr(cfg, "LLM_MAX_TOKENS", 512)

    messages = [{"role": "user", "content": "must NOT be stamped on error when env=0"}]
    with pytest.raises(RuntimeError, match="simulated LLM timeout"):
        llm_complete_json_with_span(
            failing, messages, schema={"type": "object"}, schema_name="test"
        )

    spans = exporter.get_finished_spans()
    child = next((s for s in spans if s.name == LLM_CHILD_SPAN_NAME), None)
    assert child is not None

    attrs = dict(child.attributes or {})
    assert "gen_ai.prompt" not in attrs, (
        "gen_ai.prompt must be absent on the error path when SAMANTHA_STAMP_PROMPT=0; "
        f"attrs={attrs}"
    )


# ---------------------------------------------------------------------------
# S1 — startup misconfiguration warning for unrecognized SAMANTHA_STAMP_PROMPT
# ---------------------------------------------------------------------------


def test_s1_misconfigured_value_emits_warning(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """S1: an unrecognized opt-out value (e.g. 'disabled') must emit a WARNING at startup.

    The warning must name the offending value and remind the operator of
    accepted opt-out tokens (0/false/no/off), because under default-on a
    fat-fingered disable silently fails open to PHI stamping.
    """
    import logging

    from samantha_server.observability.otel import _warn_if_stamp_prompt_misconfigured

    monkeypatch.setenv("SAMANTHA_STAMP_PROMPT", "disabled")

    with caplog.at_level(logging.WARNING, logger="samantha_server.observability.otel"):
        _warn_if_stamp_prompt_misconfigured()

    warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("disabled" in m for m in warning_messages), (
        f"S1: warning must name the offending value 'disabled'; warnings={warning_messages}"
    )
    assert any("0" in m or "false" in m or "no" in m or "off" in m for m in warning_messages), (
        f"S1: warning must list accepted opt-out tokens; warnings={warning_messages}"
    )


def test_s1_typo_value_emits_warning(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """S1: a typo value ('flase') also triggers a warning."""
    import logging

    from samantha_server.observability.otel import _warn_if_stamp_prompt_misconfigured

    monkeypatch.setenv("SAMANTHA_STAMP_PROMPT", "flase")

    with caplog.at_level(logging.WARNING, logger="samantha_server.observability.otel"):
        _warn_if_stamp_prompt_misconfigured()

    warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("flase" in m for m in warning_messages), (
        f"S1: warning must name the offending typo value 'flase'; warnings={warning_messages}"
    )


def test_s1_recognized_falsy_no_warning(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """S1: a recognized falsy value ('off') must NOT emit a warning."""
    import logging

    from samantha_server.observability.otel import _warn_if_stamp_prompt_misconfigured

    monkeypatch.setenv("SAMANTHA_STAMP_PROMPT", "off")

    with caplog.at_level(logging.WARNING, logger="samantha_server.observability.otel"):
        _warn_if_stamp_prompt_misconfigured()

    warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert not warning_messages, (
        f"S1: recognized falsy 'off' must not produce a warning; warnings={warning_messages}"
    )


def test_s1_recognized_truthy_no_warning(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """S1: a recognized truthy value ('true') must NOT emit a warning."""
    import logging

    from samantha_server.observability.otel import _warn_if_stamp_prompt_misconfigured

    monkeypatch.setenv("SAMANTHA_STAMP_PROMPT", "true")

    with caplog.at_level(logging.WARNING, logger="samantha_server.observability.otel"):
        _warn_if_stamp_prompt_misconfigured()

    warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert not warning_messages, (
        f"S1: recognized truthy 'true' must not produce a warning; warnings={warning_messages}"
    )


def test_s1_unset_no_warning(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """S1: when SAMANTHA_STAMP_PROMPT is unset, no warning is emitted."""
    import logging

    from samantha_server.observability.otel import _warn_if_stamp_prompt_misconfigured

    monkeypatch.delenv("SAMANTHA_STAMP_PROMPT", raising=False)

    with caplog.at_level(logging.WARNING, logger="samantha_server.observability.otel"):
        _warn_if_stamp_prompt_misconfigured()

    warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert not warning_messages, (
        f"S1: unset SAMANTHA_STAMP_PROMPT must not produce a warning; warnings={warning_messages}"
    )


# ---------------------------------------------------------------------------
# T1 — error-path default-on: gen_ai.prompt present when env var is UNSET
# ---------------------------------------------------------------------------


def test_prompt_stamped_on_error_path_default_on_text_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T1: when SAMANTHA_STAMP_PROMPT is unset (default-on), gen_ai.prompt is
    present on the span even when llm_client.complete() raises.

    Regression guard: if the stamp were moved inside the try-block, this test
    would fail because the prompt attribute would be absent on the error path.
    """
    import samantha_server.config as cfg
    from samantha_server.observability.otel import (
        LLM_CHILD_SPAN_NAME,
        llm_complete_with_span,
    )

    # Unset = default-on; distinct from the existing test that uses setenv("1").
    monkeypatch.delenv("SAMANTHA_STAMP_PROMPT", raising=False)

    provider, exporter = _make_provider_and_exporter()
    _patch_get_tracer(monkeypatch, provider)

    failing = MagicMock()
    failing.model_id = "test-model"
    failing.complete.side_effect = RuntimeError("simulated LLM timeout")

    monkeypatch.setattr(cfg, "LLM_PROVIDER", "test-provider")
    monkeypatch.setattr(cfg, "LLM_MODEL_NAME", "test-model-v1")
    monkeypatch.setattr(cfg, "LLM_TEMPERATURE", 0.0)
    monkeypatch.setattr(cfg, "LLM_MAX_TOKENS", 512)

    prompt_text = "default-on: this prompt must survive the error path"
    with pytest.raises(RuntimeError, match="simulated LLM timeout"):
        llm_complete_with_span(failing, prompt_text)

    spans = exporter.get_finished_spans()
    child = next((s for s in spans if s.name == LLM_CHILD_SPAN_NAME), None)
    assert child is not None, "child span must be emitted even on error"

    attrs = dict(child.attributes or {})
    assert attrs.get("gen_ai.prompt") == prompt_text, (
        "T1: default-on — gen_ai.prompt must be present on error path when "
        f"SAMANTHA_STAMP_PROMPT is unset; attrs={attrs}"
    )
    assert child.status.status_code == StatusCode.ERROR
    event_names = [e.name for e in child.events]
    assert "exception" in event_names


def test_prompt_stamped_on_error_path_default_on_json_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T1: when SAMANTHA_STAMP_PROMPT is unset (default-on), gen_ai.prompt is
    present on the span even when llm_client.complete_json() raises.
    """
    import samantha_server.config as cfg
    from samantha_server.observability.otel import (
        LLM_CHILD_SPAN_NAME,
        llm_complete_json_with_span,
    )

    monkeypatch.delenv("SAMANTHA_STAMP_PROMPT", raising=False)

    provider, exporter = _make_provider_and_exporter()
    _patch_get_tracer(monkeypatch, provider)

    failing = MagicMock()
    failing.model_id = "test-model"
    failing.complete_json.side_effect = RuntimeError("simulated LLM timeout")

    monkeypatch.setattr(cfg, "LLM_PROVIDER", "test-provider")
    monkeypatch.setattr(cfg, "LLM_MODEL_NAME", "test-model-v1")
    monkeypatch.setattr(cfg, "LLM_TEMPERATURE", 0.0)
    monkeypatch.setattr(cfg, "LLM_MAX_TOKENS", 512)

    messages = [{"role": "user", "content": "default-on: must survive error path too"}]
    with pytest.raises(RuntimeError, match="simulated LLM timeout"):
        llm_complete_json_with_span(
            failing, messages, schema={"type": "object"}, schema_name="test"
        )

    spans = exporter.get_finished_spans()
    child = next((s for s in spans if s.name == LLM_CHILD_SPAN_NAME), None)
    assert child is not None, "child span must be emitted even on error"

    attrs = dict(child.attributes or {})
    assert attrs.get("gen_ai.prompt") == json.dumps(messages), (
        "T1: default-on — gen_ai.prompt must be present on error path when "
        f"SAMANTHA_STAMP_PROMPT is unset; attrs={attrs}"
    )
    assert child.status.status_code == StatusCode.ERROR
    event_names = [e.name for e in child.events]
    assert "exception" in event_names


# ---------------------------------------------------------------------------
# T2 — JSON-endpoint default-on success-path symmetry
# ---------------------------------------------------------------------------


def test_slice3_json_prompt_stamped_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T2: with SAMANTHA_STAMP_PROMPT unset (default-on), gen_ai.prompt is
    present on the JSON endpoint success path as json.dumps(messages).

    Mirrors test_slice3_prompt_stamped_by_default for the text endpoint.
    The existing test_slice3_json_prompt_stamped_as_canonical_json uses
    explicit SAMANTHA_STAMP_PROMPT=1; this test covers the unset (default)
    operator path.
    """
    import samantha_server.config as cfg
    from samantha_server.observability.otel import (
        LLM_CHILD_SPAN_NAME,
        llm_complete_json_with_span,
    )

    monkeypatch.delenv("SAMANTHA_STAMP_PROMPT", raising=False)

    provider, exporter = _make_provider_and_exporter()
    _patch_get_tracer(monkeypatch, provider)

    mock_llm = _make_llm_client()
    monkeypatch.setattr(cfg, "LLM_PROVIDER", "test-provider")
    monkeypatch.setattr(cfg, "LLM_MODEL_NAME", "test-model-v1")
    monkeypatch.setattr(cfg, "LLM_TEMPERATURE", 0.0)
    monkeypatch.setattr(cfg, "LLM_MAX_TOKENS", 512)

    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "q"}]
    schema: dict[str, Any] = {"type": "object"}
    llm_complete_json_with_span(
        mock_llm,
        messages,
        schema=schema,
        schema_name="TestSchema",
    )

    spans = exporter.get_finished_spans()
    child = next((s for s in spans if s.name == LLM_CHILD_SPAN_NAME), None)
    assert child is not None

    attrs = dict(child.attributes or {})
    expected = json.dumps(messages)
    assert attrs.get("gen_ai.prompt") == expected, (
        "T2: gen_ai.prompt must equal json.dumps(messages) when "
        f"SAMANTHA_STAMP_PROMPT is unset (default-on); attrs={attrs}"
    )
