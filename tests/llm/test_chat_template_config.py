"""Tests for samantha_server.llm.chat_template_config."""

from __future__ import annotations

import pytest


def test_qwen35_27b_returns_enable_thinking_false() -> None:
    """GH-271: Qwen3.5-27B must be mapped to enable_thinking=False.

    Without this, `<think>` reasoning tokens bleed into structured JSON
    output during LR-006/QR-024 retest, producing false-negative results
    that have nothing to do with the actual disposition gap.
    """
    from samantha_server.llm.chat_template_config import chat_template_kwargs_for

    kwargs = chat_template_kwargs_for("mlx-community/Qwen3.5-27B-4bit")  # nosec: model-id
    assert kwargs == {"enable_thinking": False}


def test_qwen36_27b_returns_enable_thinking_false() -> None:
    """GH-271: Qwen3.6-27B must be mapped to enable_thinking=False.

    Same thinking-mode caveat as Qwen3.5-27B per the 2026-05-15 wiki
    research source.
    """
    from samantha_server.llm.chat_template_config import chat_template_kwargs_for

    kwargs = chat_template_kwargs_for("mlx-community/Qwen3.6-27B-4bit")  # nosec: model-id
    assert kwargs == {"enable_thinking": False}


def test_qwen3_next_80b_returns_none() -> None:
    """Qwen3-Next-80B-A3B has no thinking mode at all — no kwargs needed.

    Documenting this as an explicit test prevents future maintainers from
    'helpfully' adding enable_thinking=False to all Qwen-family entries.
    """
    from samantha_server.llm.chat_template_config import chat_template_kwargs_for

    qwen_next_80b = "mlx-community/Qwen3-Next-80B-A3B-Instruct-4bit"  # nosec: model-id
    kwargs = chat_template_kwargs_for(qwen_next_80b)
    assert kwargs is None


def test_baseline_gemma_returns_none() -> None:
    """The current production baseline must not receive any chat_template_kwargs.

    Locks in that adding the helper doesn't accidentally change the
    in-flight Gemma baseline's request shape.
    """
    from samantha_server.llm.chat_template_config import chat_template_kwargs_for

    kwargs = chat_template_kwargs_for("gemma-4-26B-A4B-it-MLX-4bit")
    assert kwargs is None


def test_mistral_small_returns_none() -> None:
    """Mistral-Small-3.2 has no thinking mode — no kwargs."""
    from samantha_server.llm.chat_template_config import chat_template_kwargs_for

    mistral_small = "mlx-community/Mistral-Small-3.2-24B-Instruct-2506-4bit"  # nosec: model-id
    kwargs = chat_template_kwargs_for(mistral_small)
    assert kwargs is None


def test_llama_33_70b_returns_none() -> None:
    """Llama-3.3-70B has no thinking mode — no kwargs."""
    from samantha_server.llm.chat_template_config import chat_template_kwargs_for

    llama_33 = "mlx-community/Llama-3.3-70B-Instruct-4bit"  # nosec: model-id
    kwargs = chat_template_kwargs_for(llama_33)
    assert kwargs is None


def test_unknown_model_returns_none() -> None:
    """Unrecognized models default to None — no chat_template_kwargs applied.

    Default-off is the safe posture: a model we haven't catalogued runs
    with whatever the chat template's built-in defaults are.
    """
    from samantha_server.llm.chat_template_config import chat_template_kwargs_for

    kwargs = chat_template_kwargs_for("some-arbitrary-model-id-2099")
    assert kwargs is None


@pytest.mark.parametrize(
    "model_id",
    [
        "mlx-community/Qwen3.5-27B-4bit",  # nosec: model-id
        "Qwen3.5-27B-4bit",
        "Qwen3.5-27B",
    ],
)
def test_qwen35_match_is_substring_tolerant(model_id: str) -> None:
    """The lookup matches the family substring, not the full model path.

    The same model can show up under different prefixes (mlx-community/,
    bare name, or a custom local repo). Matching on the family substring
    (`Qwen3.5-27B`) keeps the config table compact and tolerates path
    variation.
    """
    from samantha_server.llm.chat_template_config import chat_template_kwargs_for

    kwargs = chat_template_kwargs_for(model_id)
    assert kwargs == {"enable_thinking": False}


def test_empty_model_id_returns_none() -> None:
    """PR #272 review #3: empty model_id must return None (not match every entry).

    With the substring-matching rule (`family in model_id`), an empty model_id
    can never contain a non-empty family substring, so the function returns
    None. Pin this contract explicitly so a future refactor (e.g., to dict
    lookup, or to `.startswith()`) doesn't quietly change the semantics.
    """
    from samantha_server.llm.chat_template_config import chat_template_kwargs_for

    assert chat_template_kwargs_for("") is None


def test_extended_suffix_match_returns_family_kwargs() -> None:
    """PR #272 review #3: hypothetical extended-suffix model still matches the family.

    The substring-matching rule is intentional: any variant of the family
    (e.g., `Qwen3.5-27B-Plus-Custom`, a quantization or finetune of the
    base Qwen3.5-27B) inherits the family's chat-template kwargs. Document
    that this is a feature, not a coincidence — and lock it in via test so
    a future regex tightening doesn't silently break the contract.
    """
    from samantha_server.llm.chat_template_config import chat_template_kwargs_for

    kwargs = chat_template_kwargs_for("Qwen3.5-27B-Plus-Custom")
    assert kwargs == {"enable_thinking": False}, (
        "extended-suffix model variants must inherit family kwargs by substring match"
    )


def test_returned_kwargs_are_independent_copies() -> None:
    """Successive calls return distinct dicts — mutating one must not affect the next.

    The internal config table is small and immutable from the caller's
    perspective. Returning a fresh dict per call prevents a buggy caller
    from poisoning the table.
    """
    from samantha_server.llm.chat_template_config import chat_template_kwargs_for

    a = chat_template_kwargs_for("Qwen3.5-27B")
    b = chat_template_kwargs_for("Qwen3.5-27B")
    assert a is not None
    assert b is not None
    a["mutated"] = True
    assert "mutated" not in b
