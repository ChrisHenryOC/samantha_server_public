"""Per-model chat-template kwargs lookup.

Some models accept (and require) extra arguments passed through the
chat template at request time. The canonical example: Qwen3.5 and
Qwen3.6 emit `<think>` reasoning tokens before the structured JSON
unless the chat template is invoked with `enable_thinking=False`.
Without that kwarg, schema-constrained outputs from those models
will bleed reasoning content into the response — producing parse
failures that have nothing to do with the underlying disposition
gap we're testing for (GH-271 background).

The map is keyed on a model-family substring so the same entry
matches the full HF path, a bare quantized name, and the family stem
without duplication. Substring matching is intentional:
the model path varies across local caches, HF mirrors, and custom
repos, but the family identifier (Qwen3.5-27B) is stable.

Default-off semantics: unknown models return None — no kwargs are
applied. This preserves the in-flight Gemma baseline's request shape
and any model we haven't explicitly catalogued.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

# Family-substring → kwargs dict. Order matters: we iterate and
# short-circuit on the first substring match. With only two entries
# that share no prefix the ordering is currently inert, but when a
# third entry is added the maintainer must place the more-specific
# substring first (e.g., "Qwen3.5-27B" before "Qwen3.5") to avoid
# the family-stem entry shadowing variant-specific entries.
_PER_MODEL_KWARGS: Final[tuple[tuple[str, Mapping[str, Any]], ...]] = (
    # Qwen3.5/3.6 thinking mode emits `<think>` reasoning that bleeds
    # into structured JSON. Must disable for schema-constrained tasks.
    # Source: model-selection shortlist research.
    ("Qwen3.5-27B", {"enable_thinking": False}),
    ("Qwen3.6-27B", {"enable_thinking": False}),
)


def chat_template_kwargs_for(model_id: str) -> dict[str, Any] | None:
    """Return per-model chat-template kwargs, or None if no entry matches.

    Matching is family-substring based: the first entry whose key appears
    as a substring of `model_id` wins. Returns a fresh dict per call so
    mutation by the caller cannot poison the table.
    """
    for family, kwargs in _PER_MODEL_KWARGS:
        if family in model_id:
            return dict(kwargs)
    return None
