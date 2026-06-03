#!/usr/bin/env bash
# Local CI gate — single source of truth for the validation pass.
#
# Mirrors what GitHub Actions used to run (validate + markdownlint +
# secret-scan), now enforced locally via the .githooks/pre-push hook and
# invoked by the /issue → /fix-review chain. See CLAUDE.md § "Local CI gate".
#
# Run from anywhere inside the repo; it cd's to the repo root.
set -euo pipefail

REPO_ROOT="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
cd "$REPO_ROOT"

step() { printf '\n\033[1;34m==> %s\033[0m\n' "$1"; }
fail() { printf '\n\033[1;31mLOCAL CI FAILED: %s\033[0m\n' "$1" >&2; exit 1; }

# Tools that aren't Python deps must be present on PATH.
command -v markdownlint-cli2 >/dev/null 2>&1 || fail \
  "markdownlint-cli2 not found. Install with: npm install -g markdownlint-cli2 (or run scripts/bootstrap/setup-local-ci.sh)"
command -v gitleaks >/dev/null 2>&1 || fail \
  "gitleaks not found. Install with: brew install gitleaks (or run scripts/bootstrap/setup-local-ci.sh)"

step "uv lock --locked (pyproject.toml / uv.lock in sync)"
uv lock --locked || fail "uv.lock is out of sync — run 'uv lock' and commit the result."

step "uv sync --all-extras"
uv sync --all-extras

step "ruff format --check"
uv run ruff format --check samantha_server/ tests/ || fail "ruff format: run 'uv run ruff format samantha_server/ tests/'"

step "ruff check"
uv run ruff check samantha_server/ tests/ || fail "ruff check: run 'uv run ruff check samantha_server/ tests/ --fix'"

step "mypy --strict"
uv run mypy samantha_server/

step "pytest with coverage (fail_under=100 on primitives/**)"
uv run pytest --cov=samantha_server --cov-report=term-missing

step "markdownlint-cli2"
markdownlint-cli2 "**/*.md"

step "gitleaks (secret scan, full history)"
gitleaks detect --source . --redact --verbose --exit-code 1

printf '\n\033[1;32mLOCAL CI PASSED\033[0m\n'
