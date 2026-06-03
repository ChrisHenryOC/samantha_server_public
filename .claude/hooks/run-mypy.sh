#!/bin/bash
# PostToolUse hook: run mypy on Python files after Edit/Write
# Advisory only — prints type errors but does not block (exit 0 always).
# Per-file check for speed; may miss cross-module errors.
# Run 'uv run mypy src/' for a full project check.

if ! command -v jq &>/dev/null; then
  echo "run-mypy: jq is required but not installed" >&2
  exit 1
fi

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

# Skip non-Python files
if [[ "$FILE_PATH" != *.py ]]; then
  exit 0
fi

# Skip if file doesn't exist (e.g. deleted)
if [[ ! -f "$FILE_PATH" ]]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR" || exit 0

# Restrict to files within the project directory
REAL_FILE=$(realpath "$FILE_PATH" 2>/dev/null) || exit 0
REAL_ROOT=$(realpath "$CLAUDE_PROJECT_DIR" 2>/dev/null) || exit 0
if [[ "$REAL_FILE" != "$REAL_ROOT"/* ]]; then
  exit 0
fi

# Run mypy on the specific file (-- prevents option injection via filenames)
OUTPUT=$(uv run mypy --no-error-summary -- "$FILE_PATH" 2>&1)
EXIT_CODE=$?

if [[ $EXIT_CODE -ne 0 ]]; then
  echo "$OUTPUT"
fi

exit 0
