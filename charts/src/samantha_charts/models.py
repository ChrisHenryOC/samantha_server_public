"""Shared model metadata for samantha_charts.

This module is the single source of truth for display names and related
model-level constants used across multiple chart modules.
"""

from __future__ import annotations

# Canonical display name mapping for the four candidate models.
# Import this in chart modules instead of redeclaring _DISPLAY_NAMES locally.
MODEL_DISPLAY_NAMES: dict[str, str] = {
    "Qwen3-Next-80B-A3B-Instruct-4bit": "Qwen3-Next-80B",
    "Qwen3.5-35B-A3B-8bit": "Qwen3.5-35B",
    "gemma-4-26B-A4B-it-MLX-4bit": "gemma-4-26B",
    "Qwen2.5-Coder-32B-Instruct-MLX-4bit": "Qwen2.5-Coder-32B",
}
