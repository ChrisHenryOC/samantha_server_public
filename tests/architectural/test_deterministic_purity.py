"""Architectural test: deterministic-path import purity.

Per CLAUDE.md "Architectural invariants":

> Deterministic-path purity — code in `rules_engine.py` and
> `samantha_server/rules/` must not import the LLM client.

This test extends that contract to the full deterministic core
(`primitives/`, `rules/`, `engine/`, `models/`, `scenarios/`,
`receipts/`, `skills/`, `tools/`) and forbids broader categories
of impurity:

- LLM clients (`anthropic`, `openai`, `mlx_lm`, `litellm`).
- Network surface (`httpx`, `requests`, `aiohttp`, `urllib3`,
  `socket`).
- Non-determinism vectors (`datetime`, `random`, `secrets`).
- Time access (`time`) — allowlisted for `evaluator.py`'s
  `perf_counter_ns` use only.
- Cross-boundary imports into `samantha_server.llm`.

The test walks each module's AST and asserts no banned `import` /
`from … import` statement is present at module scope. Imports
inside ``if TYPE_CHECKING:`` blocks are skipped — those have no
runtime effect (they're erased under
``from __future__ import annotations``) and forbidding them would
prevent legitimate type-only imports of, e.g., ``datetime`` for
parameter annotations.

Allowlist additions live in `_ALLOWED` below; each entry must be
reviewable in the PR diff.

Path coverage is split between:
- `_REQUIRED_PATHS`: must exist on disk. A directory rename or
  removal is a hard failure rather than a silent shrink.
- `_FORWARD_LOOKING_PATHS`: may not exist yet (Phase 2 placeholders).
  Skipped silently when absent; scanned when present.

When this test fires:
1. The error names the offending file, line, and module.
2. If the import is genuinely necessary, add the (module, name) tuple
   to `_ALLOWED` and cite the GH issue authorizing it in a comment.
3. If the import shouldn't be there, fix the code rather than the
   allowlist.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Paths that must exist today and are subject to the purity invariant.
# A renamed/missing entry here is a hard failure (caught by the
# `test_required_paths_exist` test below).
_REQUIRED_PATHS: tuple[str, ...] = (
    "samantha_server/primitives",
    "samantha_server/rules",
    "samantha_server/engine",
    "samantha_server/models",
    "samantha_server/scenarios",
    "samantha_server/receipts",
    "samantha_server/skills",
    # tools/ exists today and contains only deterministic code
    # (`tools/fixture_impact.py` explicitly says "No LLM imports.
    # Deterministic path only."). When Phase 2 lands LLM-bearing tool
    # wrappers, the protocol-spike PR should either: (a) move them into
    # `samantha_server/llm/tools/` (preferred), or (b) move this entry
    # to `_FORWARD_LOOKING_PATHS` with a comment explaining why the
    # carve-out is needed.
    "samantha_server/tools",
    # queue/ is the Phase 3 Step 2 priority event queue. It must not
    # import LLM clients, network libs, or cross-boundary into
    # samantha_server.{api,llm,observability}. The consumer task
    # (Step 2 placeholder, Step 4 dispatch) lives in api/app.py which
    # is explicitly outside the deterministic core.
    #
    "samantha_server/queue",
)

# Paths that may not exist yet (Phase 2 placeholders). Skipped when
# absent; scanned when present.
_FORWARD_LOOKING_PATHS: tuple[str, ...] = ()

_DETERMINISTIC_PATHS = _REQUIRED_PATHS + _FORWARD_LOOKING_PATHS

# Modules whose mere import inside the deterministic core is a
# violation. Matches both `import X` and `from X import …` (and
# `from X.subpkg import …`).
_BANNED_PREFIXES: frozenset[str] = frozenset(
    {
        # LLM clients
        "anthropic",
        "openai",
        "mlx_lm",
        "litellm",
        # Network
        "httpx",
        "requests",
        "aiohttp",
        "urllib3",
        "socket",
        # Non-determinism vectors. `secrets` is included even though
        # it's used today by engine/dispatcher.py for HMAC nonce
        # generation — that use is allowlisted explicitly so the
        # exception is reviewer-visible.
        "datetime",
        "random",
        "secrets",
        # Real-time
        "time",
        # Cross-boundary into the Phase 2 LLM module
        "samantha_server.llm",
        # Cross-boundary into Phase 3 orchestrator and observability
        # surfaces. The deterministic core must
        # stay independent of FastAPI-side wiring and the OTel/Langfuse
        # exporter pipeline.
        "samantha_server.api",
        "samantha_server.observability",
    }
)

# Per-module allowlist entries: `(qualified_module, imported_module)`.
#
# The imported_module is the exact module string from the import statement
# (e.g. "samantha_server.api.routing" for `from samantha_server.api.routing import …`).
# Using specific submodule names rather than top-level package prefixes prevents
# a future `from samantha_server.api.anything import …` from slipping through
# silently — only the listed submodules are approved. (Phase 3 Step 6 fix-review #15).
#
# Add an entry here only with reviewer sign-off. Each entry should
# carry a comment naming the function or behavior it enables and the
# GH issue (if any) authorizing it. Allowlist edits are visible in
# the PR diff by design.
_ALLOWED: frozenset[tuple[str, str]] = frozenset(
    {
        # evaluator.evaluate() uses time.perf_counter_ns() to measure
        # per-decision latency for the EngineDecision.latency_us field
        # (Phase 1 Step 11 contract). perf_counter_ns is monotonic and
        # not a wall-clock dependency, so it does not break the
        # determinism invariant.
        ("samantha_server.engine.evaluator", "time"),
        # priority.py uses time.monotonic_ns() to capture enqueue_monotonic_ns
        # on each _QueueItem. The timestamp is an ordering-metadata anchor for
        # Step 4's queue_wait_us measurement (orchestrator-owned; never touches
        # EngineDecision). monotonic_ns() is not a wall-clock read — it is a
        # strictly increasing counter with no epoch dependency. Conceptually
        # equivalent to the evaluator's perf_counter_ns allowlist entry.
        #
        ("samantha_server.queue.priority", "time"),
        # dispatcher.list_applicable_rules generates an HMAC nonce per
        # call (random.token_bytes via the secrets module) for the
        # dispatch boundary token. The token verifies that the caller
        # of evaluator.evaluate received its DispatchResult from this
        # process — see the dispatcher docstring. The randomness is
        # confined to the token; rule selection itself is deterministic
        # given (ctx, index).
        ("samantha_server.engine.dispatcher", "secrets"),
        # dispatcher.list_applicable_rules uses time.time() to compute
        # expires_at = time.time() + ttl_sec for the dispatch token TTL.
        # dispatcher.verify_dispatch uses time.time() to check expires_at.
        # Both are TTL-enforcement wall-clock reads — not used for rule
        # selection, which remains fully deterministic. (Phase 3 Step 5).
        ("samantha_server.engine.dispatcher", "time"),
        # signing.py generates ULID timestamps via time.time() (ms
        # granularity for the monotonic 48-bit ULID timestamp component).
        # The ULID's random component (80 bits from os.urandom) is the
        # primary uniqueness guarantee; the timestamp is ordering metadata.
        # (Phase 2 Step 6): receipt signing is inherently a
        # wall-clock operation — each receipt carries a signed_at_utc.
        ("samantha_server.receipts.signing", "time"),
        # signing.py uses datetime.now(tz=timezone.utc) for signed_at_utc
        # on SignedReceipt. Receipt timestamps are intentional non-determinism
        # (each signing operation records when it happened).
        #
        ("samantha_server.receipts.signing", "datetime"),
        # audit.py uses datetime for UTC validation of query window parameters
        # (fetch_in_window) and for rehydrating stored ISO-8601 timestamps back
        # into tz-aware datetime objects. The UTC sentinel is imported for
        # comparison only; no new wall-clock reads occur in audit.
        #
        ("samantha_server.receipts.audit", "datetime"),
        # replay.py imports _build_llm_client from samantha_server.api.lifespan inside
        # _build_llm_client_for_replay() to build a real LLM client for the
        # production-CLI path. The import is lazy (inside a function body) so
        # deterministic-only corpora never load MLX. (Phase 3 Step 6 fix-review #1).
        ("samantha_server.scenarios.replay", "samantha_server.api.lifespan"),
        # replay.py imports ReceiptWriter from samantha_server.api.receipt_writer
        # inside _ReplayHarness.__aenter__() to build the in-memory or file-backed
        # SQLite store for replay receipts. (Phase 3 Step 6).
        ("samantha_server.scenarios.replay", "samantha_server.api.receipt_writer"),
        # replay.py imports CounterRegistry from samantha_server.observability.counters
        # inside _ReplayHarness.__aenter__() (for AppState) and inside
        # replay_with_langfuse_export() (for counting OTLP export failures).
        #
        ("samantha_server.scenarios.replay", "samantha_server.observability.counters"),
        # replay.py imports make_counting_exporter (and STAMP_PROMPT_ENV) from
        # samantha_server.observability.otel inside replay_with_langfuse_export()
        # and main() respectively (lazy function-body imports). The import is
        # deferred and confined to the Langfuse/CLI path; the deterministic engine
        # core (engine/, rules/, primitives/) is unchanged.
        ("samantha_server.scenarios.replay", "samantha_server.observability.otel"),
        # replay.py's _make_stub_llm_client imports LLMClient and LLMResponse from
        # samantha_server.llm.client inside the function body (lazy import, not at module
        # scope) to build the no-op stub used for deterministic-only replay runs where
        # no real LLM is needed. The import is deferred and confined to that helper;
        # the deterministic engine core is unchanged.
        ("samantha_server.scenarios.replay", "samantha_server.llm.client"),
        # replay.py imports `time` inside _replay_all_async (lazy function-body
        # import) solely to measure elapsed seconds for the optional --progress
        # stdout line. The `import time` statement runs on every call to
        # _replay_all_async, but `time.monotonic()` reads are gated behind
        # `if progress:` so they never execute in normal (non-CLI-progress)
        # replays. The reading is presentational only — it is not stamped on
        # receipts, spans, or AccuracyReport fields, so it cannot leak
        # non-determinism into engine outputs. The deterministic engine core
        # (engine/, rules/, primitives/) is unchanged.
        ("samantha_server.scenarios.replay", "time"),
        # engine/decision.py imports AnswerType (a TypeAlias) from samantha_server.llm.schemas
        # to derive QueryTrace.parsed_answer_type's Literal from the single-source-of-truth
        # ANSWER_TYPES tuple. This is a pure type/constant import: ANSWER_TYPES is a Final
        # tuple of strings and AnswerType is a TypeAlias — no LLM inference is possible.
        # The import was added in fix-review Cluster B to eliminate Literal drift
        # risk when widens the answer_type set. fix-review.
        ("samantha_server.engine.decision", "samantha_server.llm.schemas"),
        # scenarios/loader.py imports ANSWER_TYPES and AnswerType from samantha_server.llm.schemas
        # for the Scenario.expected_answer_type typed accessor (fix-review Cluster D).
        # Same justification as the engine.decision entry above: purely declarative constants,
        # no LLM inference path.
        ("samantha_server.scenarios.loader", "samantha_server.llm.schemas"),
        # scenarios/loader.py imports datetime to compute the default prompt_timestamp
        # from max(orders.created_at) + 24h. The arithmetic is purely
        # deterministic given fixed fixture inputs — no wall-clock reads occur;
        # fromisoformat() + timedelta is a pure function of the fixture JSON.
        # Same rationale as the receipts.audit allowlist entry.
        ("samantha_server.scenarios.loader", "datetime"),
        # replay.py's _ReplayHarness imports httpx inside __aenter__ (lazy function-body
        # import) to build an AsyncClient with ASGITransport for the in-process endpoint
        # path. Phase B Step 5: replay routes every step through the real /events
        # endpoint; httpx is the HTTP client layer. The import is deferred and confined
        # to _ReplayHarness; the deterministic engine core is unchanged.
        ("samantha_server.scenarios.replay", "httpx"),
        # replay.py's _ReplayHarness imports RequestIDMiddleware, _BodyCapMiddleware,
        # and _consume from samantha_server.api.app inside __aenter__ (lazy import) to
        # wire the in-process FastAPI app used by the endpoint path.
        ("samantha_server.scenarios.replay", "samantha_server.api.app"),
        # replay.py's _ReplayHarness imports register_events_routes from
        # samantha_server.api.events inside __aenter__ (lazy import) to mount the
        # /events route on the in-process FastAPI app.
        ("samantha_server.scenarios.replay", "samantha_server.api.events"),
        # replay.py's _ReplayHarness imports register_rbac_exception_handlers and
        # _sign_token from samantha_server.api.rbac inside __aenter__ and
        # make_auth_headers (lazy imports) to configure RBAC on the in-process app
        # and to sign auth tokens for harness requests.
        ("samantha_server.scenarios.replay", "samantha_server.api.rbac"),
        # replay.py's _ReplayHarness imports make_langfuse_stub_probe from
        # samantha_server.observability.cached_probe inside __aenter__ (lazy import)
        # to provide a no-op Langfuse probe for the AppState built by the harness.
        # Phase B Step 5.
        ("samantha_server.scenarios.replay", "samantha_server.observability.cached_probe"),
    }
)


def _iter_python_files(root: Path) -> list[Path]:
    """Yield .py files under root, skipping __pycache__."""
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _module_qualname(repo_root: Path, file_path: Path) -> str:
    """Return the dotted module name for a file under the repo root.

    e.g. /repo/samantha_server/engine/evaluator.py
       → "samantha_server.engine.evaluator"
    """
    rel = file_path.relative_to(repo_root).with_suffix("")
    parts = rel.parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _is_type_checking_guard(node: ast.If) -> bool:
    """True if *node* is `if TYPE_CHECKING:` or `if typing.TYPE_CHECKING:`."""
    test = node.test
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute):
        return test.attr == "TYPE_CHECKING"
    return False


def _imported_names(tree: ast.AST) -> list[tuple[str, int]]:
    """Return list of (imported module, lineno) tuples for runtime imports.

    For `import X.Y.Z` returns `("X.Y.Z", lineno)` (the full dotted
    name; `_matches_banned_prefix` does prefix matching downstream).

    For `from X.Y import Z` returns `("X.Y", lineno)` (the package
    the symbol comes from, not the symbol itself).

    Imports inside `if TYPE_CHECKING:` blocks are skipped — those have
    no runtime effect under `from __future__ import annotations`.
    """
    out: list[tuple[str, int]] = []

    # We can't use ast.walk for the whole tree because it descends into
    # TYPE_CHECKING blocks. Instead: walk top-level + recurse only into
    # branches that aren't TYPE_CHECKING-guarded.
    def visit(parent: ast.AST) -> None:
        for child in ast.iter_child_nodes(parent):
            if isinstance(child, ast.Import):
                for alias in child.names:
                    out.append((alias.name, child.lineno))
            elif isinstance(child, ast.ImportFrom) and child.module is not None:
                out.append((child.module, child.lineno))
            elif isinstance(child, ast.If) and _is_type_checking_guard(child):
                # Skip the entire body and orelse — these are type-only.
                continue
            else:
                visit(child)

    visit(tree)
    return out


def _matches_banned_prefix(module: str) -> str | None:
    """Return the banned prefix that matches `module`, or None."""
    for prefix in _BANNED_PREFIXES:
        if module == prefix or module.startswith(prefix + "."):
            return prefix
    return None


def _repo_root() -> Path:
    """Locate the repo root from this test file's location."""
    return Path(__file__).resolve().parents[2]


def _existing_paths() -> list[Path]:
    """Return the deterministic paths that exist on disk.

    Required paths missing on disk fail `test_required_paths_exist`,
    not this helper, so by the time `_violations` runs the missing
    requireds have already been surfaced.
    """
    repo_root = _repo_root()
    paths: list[Path] = []
    for rel in _DETERMINISTIC_PATHS:
        root = repo_root / rel
        if root.is_dir():
            paths.append(root)
    return paths


def _violations() -> list[str]:
    """Walk the deterministic core and return human-readable violations.

    Allowlist lookup uses (qualname, actual_imported_module) — the specific
    submodule, not the banned top-level prefix. This enforces that only the
    listed submodules are approved; a future import from a sibling submodule
    would require its own explicit allowlist entry.
    """
    repo_root = _repo_root()
    violations: list[str] = []
    for root in _existing_paths():
        for py_file in _iter_python_files(root):
            qualname = _module_qualname(repo_root, py_file)
            try:
                tree = ast.parse(py_file.read_text())
            except SyntaxError as exc:  # pragma: no cover - hard fail
                violations.append(f"{py_file}: SyntaxError parsing for purity check: {exc}")
                continue
            for module, lineno in _imported_names(tree):
                banned = _matches_banned_prefix(module)
                if banned is None:
                    continue
                # Check against specific imported module (not just the banned prefix).
                if (qualname, module) in _ALLOWED:
                    continue
                violations.append(
                    f"{py_file}:{lineno}: deterministic-purity violation — "
                    f"`{qualname}` imports `{module}` (banned prefix: `{banned}`). "
                    f"Either remove the import or add ('{qualname}', '{module}') "
                    f"to _ALLOWED with a reviewer-visible justification."
                )
    return violations


def test_required_paths_exist() -> None:
    """A missing required path is a hard failure, not a silent skip.

    Catches accidental directory renames or removals that would
    otherwise shrink the purity guard's coverage without any signal.
    """
    repo_root = _repo_root()
    missing = [rel for rel in _REQUIRED_PATHS if not (repo_root / rel).is_dir()]
    if missing:
        pytest.fail(
            "_REQUIRED_PATHS missing on disk (silent coverage shrink risk):\n  - "
            + "\n  - ".join(missing)
            + "\n\nIf the path was renamed, update _REQUIRED_PATHS. "
            "If it was removed deliberately, also remove from _REQUIRED_PATHS "
            "and document the architectural change."
        )


def test_deterministic_core_has_no_banned_imports() -> None:
    """Fail loudly with file:line:why for any banned import."""
    violations = _violations()
    if violations:
        pytest.fail("deterministic-path purity violations found:\n  - " + "\n  - ".join(violations))


def test_allowlist_entries_are_actually_used() -> None:
    """Guard against allowlist drift.

    Every entry in `_ALLOWED` must correspond to an actual import in
    the source. Otherwise the allowlist accumulates dead entries that
    obscure which exceptions are real.

    Complementary to `test_deterministic_core_has_no_banned_imports`:
    that test catches bad imports; this test catches stale exceptions.
    A qualname rename that desyncs an import from its allowlist entry
    fails BOTH tests — the rename leaves the bad-import test seeing an
    unexcepted import, and leaves this test seeing a stale entry.
    """
    used: set[tuple[str, str]] = set()
    repo_root = _repo_root()
    for root in _existing_paths():
        for py_file in _iter_python_files(root):
            qualname = _module_qualname(repo_root, py_file)
            try:
                tree = ast.parse(py_file.read_text())
            except SyntaxError:
                # Skip — the primary test surfaces SyntaxErrors with
                # full context. Don't double-report here.
                continue
            for module, _ in _imported_names(tree):
                banned = _matches_banned_prefix(module)
                if banned is not None and (qualname, module) in _ALLOWED:
                    used.add((qualname, module))

    stale = _ALLOWED - used
    if stale:
        pytest.fail(
            "_ALLOWED has stale entries (no matching import in the source): "
            + ", ".join(f"{m}/{p}" for m, p in sorted(stale))
        )


# ---------------------------------------------------------------------------
# Tests for the test machinery itself (TYPE_CHECKING handling, AST helpers).
# ---------------------------------------------------------------------------


def _parse(src: str) -> ast.AST:
    return ast.parse(src)


def test_imported_names_skips_type_checking_blocks() -> None:
    """Imports inside `if TYPE_CHECKING:` are excluded from the scan."""
    src = (
        "from __future__ import annotations\n"
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    import datetime\n"
        "    from anthropic import Anthropic\n"
        "import json\n"
    )
    names = [n for n, _ in _imported_names(_parse(src))]
    assert "datetime" not in names
    assert "anthropic" not in names
    assert "json" in names
    assert "typing" in names  # the TYPE_CHECKING import itself is runtime


def test_imported_names_handles_typing_typecheck_attribute() -> None:
    """`if typing.TYPE_CHECKING:` (attribute form) is also recognized."""
    src = "import typing\nif typing.TYPE_CHECKING:\n    import datetime\nimport json\n"
    names = [n for n, _ in _imported_names(_parse(src))]
    assert "datetime" not in names
    assert "json" in names


def test_imported_names_full_dotted_name_for_import_x_y_z() -> None:
    """`import X.Y.Z` returns the full dotted name (not just the head).

    Documented contract — the prefix matching in `_matches_banned_prefix`
    handles partial matches downstream.
    """
    names = [n for n, _ in _imported_names(_parse("import a.b.c\n"))]
    assert names == ["a.b.c"]
