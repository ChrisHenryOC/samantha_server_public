#!/bin/bash
# PostToolUse hook: run markdownlint-cli2 --fix on Markdown files after Edit/Write.
# Advisory: applies safe auto-fixes via .markdownlint.json at the project root,
# prints any remaining issues, and never blocks (exit 0 always).

if ! command -v jq &>/dev/null; then
  echo "run-markdownlint: jq is required but not installed" >&2
  exit 0
fi

if ! command -v markdownlint-cli2 &>/dev/null; then
  # markdownlint-cli2 not installed — no-op rather than failing the whole hook chain.
  exit 0
fi

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

# Skip non-Markdown files
if [[ "$FILE_PATH" != *.md ]]; then
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

# Run markdownlint-cli2 --fix on the specific file (-- prevents option injection via filenames).
# It picks up .markdownlint.json from the project root automatically.
OUTPUT=$(markdownlint-cli2 --fix -- "$FILE_PATH" 2>&1)
EXIT_CODE=$?

if [[ $EXIT_CODE -ne 0 ]]; then
  echo "$OUTPUT"
fi

exit 0
