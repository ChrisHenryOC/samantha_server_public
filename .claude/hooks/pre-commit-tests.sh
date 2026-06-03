#!/bin/bash
# PreToolUse hook: run pytest before git commit commands
# Note: matcher in settings.json is "Bash" (broadest available); this script
# filters internally to only intercept git commit commands.

if ! command -v jq &>/dev/null; then
  echo "pre-commit-tests: jq is required but not installed" >&2
  exit 2
fi

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')

# Only intercept commands that run git commit (anchored match, not substring)
if [[ ! "$COMMAND" =~ (^|[[:space:];&|])git[[:space:]]+commit([[:space:]]|$) ]]; then
  exit 0
fi

if [[ -z "$CLAUDE_PROJECT_DIR" ]]; then
  echo "pre-commit-tests: CLAUDE_PROJECT_DIR is not set, blocking commit" >&2
  exit 2
fi
cd "$CLAUDE_PROJECT_DIR" || { echo "pre-commit-tests: cannot cd to $CLAUDE_PROJECT_DIR" >&2; exit 2; }

# Skip cleanly when the Python project hasn't been initialized yet (pre-Phase-1).
# The hook activates automatically once `pyproject.toml` and `tests/` exist.
if [[ ! -f "pyproject.toml" || ! -d "tests" ]]; then
  exit 0
fi

# Run pytest
OUTPUT=$(uv run pytest --tb=short -q 2>&1)
EXIT_CODE=$?

if [[ $EXIT_CODE -ne 0 ]]; then
  # Block the commit - exit code 2 blocks the tool
  echo "$OUTPUT" >&2
  exit 2
fi

exit 0
