"""Project-root conftest — placeholder secrets for test session."""

from __future__ import annotations

import collections.abc
import contextlib
import os
import pathlib
import tempfile as _tempfile
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from samantha_server.models.context import SpecimenContext

# Sentinel placeholders for the two required-secret env vars. These
# are 32-byte hex strings constructed from obviously-fake patterns.
# Production import of samantha_server.config with these values must
# fail (see _is_test_sentinel guard in samantha_server/config.py).
_TEST_RECEIPT_KEY = "DEADBEEF" * 8  # 64 hex chars = 32 bytes
# Sentinel-prefixed (BADC0FFEE...) but with rich byte content so the
# entropy-floor smoke test (>= 16 distinct bytes) passes. Recognisably
# fake via leading sentinel + readable hex words: feedface, deadbeef,
# c0ffee. 64 hex chars = 32 bytes.
_TEST_PHI_SALT = (
    "BADC0FFEE0" + "123456789ABCDEF0" + "FEDCBA9876543210" + "FEEDFACEDEADBEEF" + "C0FFEE"
)
# RBAC_HMAC_KEY test sentinel. Uses a distinct prefix from
# _TEST_RECEIPT_KEY (CAFEBABE vs DEADBEEF) so the two sentinel classes are
# independently identifiable. Not prefixed with _TEST_RBAC_KEY_PREFIXES
# (DEADBEEF) intentionally — RBAC key sentinel uses a different pattern
# so the two key slots cannot be confused by a careless copy-paste.
# 64 hex chars = 32 bytes; starts with CAFEBABE... (clearly test-only).
_TEST_RBAC_KEY = "CAFEBABE" + "DEADBEEF" * 6 + "CAFEBABE"  # 64 hex chars = 32 bytes

os.environ.setdefault("RECEIPT_SIGNING_KEY", _TEST_RECEIPT_KEY)
os.environ.setdefault("PHI_HASH_SALT", _TEST_PHI_SALT)
os.environ.setdefault("RBAC_HMAC_KEY", _TEST_RBAC_KEY)

# Set a per-session temp path for the receipts DB so tests don't pollute
# the developer's home directory. Each test session gets a unique path
# under /tmp. The init_store call on the first receipt write creates the file.
_SESSION_RECEIPTS_DB = os.path.join(
    _tempfile.gettempdir(), f"samantha_test_receipts_{os.getpid()}.db"
)
os.environ.setdefault("RECEIPTS_DB_PATH", _SESSION_RECEIPTS_DB)


# Pin LANGFUSE_ENABLED=false as the test-session default *before*
# loading `.env`. Many existing tests assert the default-off behavior
# (e.g., readyz reports langfuse.enabled=False, /events trace_url=None);
# letting `.env`'s LANGFUSE_ENABLED=true leak into those tests would
# regress them. The live_otel_provider fixture explicitly opts in to
# Langfuse export by reading LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY /
# LANGFUSE_BASE_URL from cfg (which still get loaded from `.env`).
os.environ.setdefault("LANGFUSE_ENABLED", "false")

# Auto-load `.env` from the project root so live_llm runs (and any other
# test that needs LLM_*, LANGFUSE_*, OTEL_* values) work without an
# explicit `source .env` step. `override=False` is load-bearing: the
# sentinel `setdefault` calls above must keep precedence for the
# security-critical keys (RECEIPT_SIGNING_KEY, PHI_HASH_SALT,
# RBAC_HMAC_KEY) so a developer with a real production key in their
# `.env` cannot accidentally have it adopted by the test session.
from dotenv import dotenv_values, load_dotenv  # noqa: E402

_ENV_PATH = pathlib.Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH, override=False)

# Env-var precedence chain for the test session, top-to-bottom:
#
#   1. Sentinel-key SETDEFAULT (above): RECEIPT_SIGNING_KEY,
#      PHI_HASH_SALT, RBAC_HMAC_KEY, RECEIPTS_DB_PATH, LANGFUSE_ENABLED.
#      These are placed before any `.env` read so a developer with
#      production keys in `.env` cannot accidentally have them adopted
#      by the test session — the security-critical fail-closed contract.
#
#   2. load_dotenv(override=False): every NON-Langfuse variable from
#      `.env` is applied where the shell hasn't already set it.
#      Shell wins over `.env` for these (LLM_*, OTEL_*, etc.) so an
#      operator can override a single value at the command line.
#
#   3. Force-override LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY /
#      LANGFUSE_BASE_URL from `.env` over any shell value. A developer
#      running multiple Langfuse-traced projects (e.g., Claude Code's
#      own tracing) often has `LANGFUSE_PUBLIC_KEY` exported in their
#      shell pointing at a different project. With load_dotenv
#      override=False, that shell value would silently win, routing
#      the project's test traces into the wrong project's dashboard.
#      Project-credential routing must be deterministic from `.env`,
#      not the developer's session state.
#
# Adding new LANGFUSE_* env vars: extend the loop's tuple. Adding
# new security-critical sentinel keys: add a setdefault above. The
# config-attribute names live in samantha_server/config.py.
_DOTENV_VALUES = dotenv_values(_ENV_PATH)
for _lf_key in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL"):
    # Use `in` (not truthiness) so an explicitly-blanked `.env` value
    # (e.g. `LANGFUSE_PUBLIC_KEY=`) overrides a shell-exported value
    # rather than silently falling through to it. Empty-string
    # override → live_otel_provider treats it as "credentials missing"
    # and skips, which is what the developer asked for by blanking it.
    if _lf_key in _DOTENV_VALUES:
        os.environ[_lf_key] = _DOTENV_VALUES[_lf_key] or ""


# Register the ``--models`` CLI flag at the rootdir conftest
# so pytest sees it as a top-level option. The fixture and the
# generate-tests hook are re-exported in ``tests/scenarios/conftest.py``.
from samantha_server.scenarios.sweep import pytest_addoption  # noqa: E402,F401


@pytest.fixture
def receipts_test_isolation(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> collections.abc.Generator[None, None, None]:
    """Reset module-level caches so per-test RECEIPTS_DB_PATH overrides take effect.

    Any test that sets its own RECEIPTS_DB_PATH (e.g., via monkeypatch.setenv)
    must request this fixture to ensure the cached write connection is closed
    and re-opened against the new path. Resets the canonical cache in
    receipts/store.py (H-04) and also resets signing-key caches (H-10) to
    prevent cross-test key leakage.
    """
    import samantha_server.config as _cfg
    import samantha_server.receipts.store as _store

    # Point to a fresh per-test DB
    db_path = str(tmp_path / "receipts.db")
    monkeypatch.setenv("RECEIPTS_DB_PATH", db_path)
    # Also update the already-imported config module so _get_write_conn sees the new path
    monkeypatch.setattr(_cfg, "RECEIPTS_DB_PATH", db_path)

    # Reset the canonical connection cache in store.py (H-04 canonical location)
    old_store_conn = _store._write_conn
    monkeypatch.setattr(_store, "_write_conn", None)
    monkeypatch.setattr(_store, "_write_conn_path", None)

    # H-10: reset signing-key caches so monkeypatched signing keys take effect
    import samantha_server.receipts.signing as _signing

    monkeypatch.setattr(_signing, "_signing_key_cache", {})
    monkeypatch.setattr(_signing, "_verify_key_cache", {})

    # H-02: clear discover() cache so per-test custom SKILL.md trees are picked up
    from samantha_server.skills.loader import discover as _discover

    _discover.cache_clear()

    yield

    # Restore discover() cache state after the test
    _discover.cache_clear()

    # Cleanup: close any connection opened during the test (monkeypatch restores attrs)
    if _store._write_conn is not None and _store._write_conn is not old_store_conn:
        with contextlib.suppress(Exception):
            _store._write_conn.close()


# ---------------------------------------------------------------------------
# Hypothesis profiles (G14)
#
# ci:    derandomize=True  — fully deterministic; activate via
#            HYPOTHESIS_PROFILE=ci (e.g. in CI env)
# local: deadline=None     — default for developer sessions (random seed)
#
# The CI workflow does not yet set HYPOTHESIS_PROFILE; the default is
# "local".  When the LLM-tier grows Hypothesis-based assertions, the
# workflow can add `env: HYPOTHESIS_PROFILE: ci` to the pytest step.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# M-11: shared context factory (reduces duplication across test files)
#
# Many test files define their own _make_ctx / _make_order helpers.
# New tests should use make_test_context() from this module instead.
# Existing per-file helpers will be retired in a follow-up cleanup once
# all call sites are migrated.
# ---------------------------------------------------------------------------


def make_test_context(
    state: str = "ACCESSIONING",
    event_type: str = "order_received",
    event_data: dict | None = None,
    flags: frozenset[str] | None = None,
    *,
    order_id: str = "TEST-CTX-001",
    patient_name: str | None = None,
    patient_sex: str = "F",
    age: int = 45,
    specimen_type: str = "biopsy",
    anatomic_site: str = "breast",
    fixative: str = "formalin",
    fixation_time_hours: float = 8.0,
    ordered_tests: tuple[str, ...] = ("HER2",),
    priority: str = "ROUTINE",
    billing_info_present: bool = True,
) -> SpecimenContext:
    """Build a SpecimenContext for tests with sensible defaults.

    Centralizes the repeated _make_order + SpecimenContext construction pattern
    that appears in 4+ test files. Default parameters trigger ACC-001 (patient_name=None).
    """
    import samantha_server.models.context as _mc

    order = _mc.Order(
        order_id=order_id,
        patient_name=patient_name,
        patient_sex=patient_sex,
        age=age,
        specimen_type=specimen_type,
        anatomic_site=anatomic_site,
        fixative=fixative,
        fixation_time_hours=fixation_time_hours,
        ordered_tests=ordered_tests,
        priority=priority,
        billing_info_present=billing_info_present,
    )
    return _mc.SpecimenContext(
        order=order,
        current_state=state,
        flags=flags or frozenset(),
        event=_mc.Event(
            event_type=event_type,
            event_data=event_data or {},
            step_index=0,
        ),
    )


from hypothesis import settings as _hypothesis_settings  # noqa: E402

_hypothesis_settings.register_profile("ci", derandomize=True, deadline=None)
_hypothesis_settings.register_profile("local", deadline=None)
_hypothesis_settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "local"))
