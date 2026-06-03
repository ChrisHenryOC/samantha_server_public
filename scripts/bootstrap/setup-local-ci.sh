#!/usr/bin/env bash
# One-time per-clone setup for the local CI gate (replaces GitHub Actions).
#
# - Points git at the tracked .githooks/ directory so .githooks/pre-push fires.
# - Ensures the non-Python gate tools (gitleaks, markdownlint-cli2) are present.
#
# Safe to re-run. Run from anywhere inside the repo.
set -euo pipefail

REPO_ROOT="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
cd "$REPO_ROOT"

echo "==> Enabling tracked git hooks (core.hooksPath=.githooks)"
git config core.hooksPath .githooks
chmod +x .githooks/pre-push scripts/ci/run.sh

if ! command -v gitleaks >/dev/null 2>&1; then
  echo "==> Installing gitleaks"
  if command -v brew >/dev/null 2>&1; then
    brew install gitleaks
  else
    echo "    Homebrew not found. Install gitleaks manually: https://github.com/gitleaks/gitleaks#installing" >&2
  fi
else
  echo "==> gitleaks present: $(gitleaks version 2>/dev/null || echo unknown)"
fi

if ! command -v markdownlint-cli2 >/dev/null 2>&1; then
  echo "==> Installing markdownlint-cli2"
  if command -v npm >/dev/null 2>&1; then
    npm install -g markdownlint-cli2
  else
    echo "    npm not found. Install markdownlint-cli2 manually: npm install -g markdownlint-cli2" >&2
  fi
else
  echo "==> markdownlint-cli2 present"
fi

echo "==> Done. The full gate now runs on every 'git push' (bypass: --no-verify)."
echo "    Run it on demand any time: scripts/ci/run.sh"
