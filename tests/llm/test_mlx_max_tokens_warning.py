"""Tests for GH-321 review #2: WARNING log when MLXClient hits max_tokens.

The numeric guard (`output_tokens >= max_tok`) is protocol-agnostic and
applies to MLXClient symmetrically with OMLXClient. `finish_reason` is
unavailable from `mlx_lm.generate()` per the upstream library — that
field stays `None` on MLX-path LLMResponse objects — but the
token-count truncation signal is still observable and worth surfacing.

These tests use `sys.modules` injection to stub `mlx_lm`, so they do
not require the `local-llm` dependency group and are NOT decorated
``@pytest.mark.local_mlx``.
"""

from __future__ import annotations

import logging
import sys
from typing import Any
from unittest.mock import MagicMock

import pytest


class _FakeTokenizer:
    """Tokenizer stub that returns a configurable encode() length.

    encode() returns a list of integers whose length matches the
    desired token count. `complete()` calls encode(prompt) and
    encode(generated_text); we control both via the *prompt_tokens*
    and *output_tokens* attributes.
    """

    def __init__(self, prompt_tokens: int, output_tokens: int) -> None:
        self._prompt_tokens = prompt_tokens
        self._output_tokens = output_tokens

    def encode(self, text: str) -> list[int]:
        # Distinguish prompt-encode vs output-encode by the literal text.
        # MLXClient calls encode(prompt) then encode(generated_text); the
        # prompt is the caller-supplied string, the output is a sentinel.
        if text == "<<output>>":
            return list(range(self._output_tokens))
        return list(range(self._prompt_tokens))


def _install_fake_mlx_lm(
    monkeypatch: pytest.MonkeyPatch,
    *,
    output_tokens: int,
    prompt_tokens: int = 4,
) -> None:
    """Stub `mlx_lm` in sys.modules so MLXClient(__init__/complete) succeed."""
    fake_module = MagicMock()
    fake_module.load = MagicMock(
        return_value=(MagicMock(), _FakeTokenizer(prompt_tokens, output_tokens))
    )
    fake_module.generate = MagicMock(return_value="<<output>>")
    monkeypatch.setitem(sys.modules, "mlx_lm", fake_module)


def _make_client(monkeypatch: pytest.MonkeyPatch, output_tokens: int) -> Any:
    """Build a MLXClient with fake mlx_lm and a controlled output_tokens count."""
    _install_fake_mlx_lm(monkeypatch, output_tokens=output_tokens)
    from samantha_server.llm.mlx_client import MLXClient

    return MLXClient(model_path="fake-path")


def test_mlx_complete_warns_when_output_tokens_equals_max_tokens(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """MLXClient.complete() emits WARNING when output_tokens == max_tok."""
    max_tok = 16
    client = _make_client(monkeypatch, output_tokens=max_tok)

    with caplog.at_level(logging.WARNING, logger="samantha_server.llm.mlx_client"):
        client.complete("ping", max_tokens=max_tok)

    warning_msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any(f"LLM output reached max_tokens={max_tok}" in m for m in warning_msgs), (
        f"Expected WARNING containing 'LLM output reached max_tokens={max_tok}'; "
        f"got: {warning_msgs}"
    )


def test_mlx_complete_warns_when_output_tokens_exceeds_max_tokens(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """MLXClient.complete() emits WARNING on overflow (output_tokens > max_tok).

    Defensive: mlx_lm's tokenizer can in principle return a larger count
    than max_tok depending on how tokens are post-counted. `>=` catches
    that case just like the OMLXClient path.
    """
    max_tok = 16
    client = _make_client(monkeypatch, output_tokens=max_tok + 1)

    with caplog.at_level(logging.WARNING, logger="samantha_server.llm.mlx_client"):
        client.complete("ping", max_tokens=max_tok)

    warning_msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any(f"LLM output reached max_tokens={max_tok}" in m for m in warning_msgs), (
        f"Expected WARNING on overflow; got: {warning_msgs}"
    )


def test_mlx_complete_no_warning_when_output_tokens_below_max_tokens(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """MLXClient.complete() does NOT emit max_tokens WARNING when below cap."""
    max_tok = 16
    client = _make_client(monkeypatch, output_tokens=5)

    with caplog.at_level(logging.WARNING, logger="samantha_server.llm.mlx_client"):
        client.complete("ping", max_tokens=max_tok)

    warning_msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert not any("LLM output reached max_tokens" in m for m in warning_msgs), (
        f"Unexpected WARNING; got: {warning_msgs}"
    )
