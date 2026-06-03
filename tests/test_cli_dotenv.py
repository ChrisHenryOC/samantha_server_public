"""Tests for samantha_server._cli — `.env` auto-load on CLI entry."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

# Test-sentinel values mirrored from tests/conftest.py so the subprocess
# helper test can exercise the .env-loading path with values config.py
# will accept under the test bypass (inherited PYTEST_CURRENT_TEST). 64
# hex chars = 32 bytes for each key. Hoisted to module level so future
# subprocess tests can reuse without re-derivation.
_TEST_RECEIPT_KEY = "DEADBEEF" * 8
_TEST_PHI_SALT = "BADC0FFEE0" + "123456789ABCDEF0" * 2 + "FEEDFACEDEADBEEF" + "C0FFEE"
_TEST_RBAC_KEY = "CAFEBABE" + "DEADBEEF" * 6 + "CAFEBABE"


def test_load_dotenv_for_cli_populates_env_from_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `.env` in CWD becomes accessible via os.environ after the helper runs."""
    import os

    from samantha_server._cli import load_dotenv_for_cli

    (tmp_path / ".env").write_text("SAMANTHA_CLI_TEST_VAR=hello\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SAMANTHA_CLI_TEST_VAR", raising=False)

    load_dotenv_for_cli()

    assert os.environ["SAMANTHA_CLI_TEST_VAR"] == "hello"


def test_load_dotenv_for_cli_does_not_override_shell_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Shell-exported values win; the helper is opt-in for unset vars only."""
    import os

    from samantha_server._cli import load_dotenv_for_cli

    (tmp_path / ".env").write_text("SAMANTHA_CLI_TEST_VAR=from_dotenv\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SAMANTHA_CLI_TEST_VAR", "from_shell")

    load_dotenv_for_cli()

    assert os.environ["SAMANTHA_CLI_TEST_VAR"] == "from_shell"


def test_load_dotenv_for_cli_is_noop_when_dotenv_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No `.env` in any ancestor directory is fine — function returns silently."""
    from samantha_server._cli import load_dotenv_for_cli

    monkeypatch.chdir(tmp_path)
    load_dotenv_for_cli()  # must not raise


def test_replay_cli_subprocess_auto_loads_dotenv(tmp_path: Path) -> None:
    """End-to-end: invoke the replay CLI with a fresh `.env` in CWD and a
    subprocess env that has *no* RECEIPT_SIGNING_KEY pre-set. The replay
    must read the key from `.env` (via load_dotenv_for_cli) and fail on
    the missing directory — *not* on the missing-secret guard.

    Regression cover for the friction that motivated this feature: prior
    to _cli.load_dotenv_for_cli, every CLI invocation required the user
    to `set -a; source .env; set +a` first. This test fails if a future
    refactor drops the dotenv load or moves it after the config import.

    --help cannot be used here: argparse short-circuits before main()'s
    load_dotenv call, so a passing --help proves nothing about the wiring.
    """
    import os

    # Test-only sentinel values: config.py rejects them at module-import
    # time UNLESS PYTEST_CURRENT_TEST is set in os.environ. The subprocess
    # inherits PYTEST_CURRENT_TEST from this pytest session, which is what
    # makes the bypass fire. (There is no `_is_test_sentinel` predicate;
    # see config.py's `_reject_test_sentinel` for the actual mechanism.)
    env_path = tmp_path / ".env"
    env_path.write_text(
        f"RECEIPT_SIGNING_KEY={_TEST_RECEIPT_KEY}\n"
        f"PHI_HASH_SALT={_TEST_PHI_SALT}\n"
        f"RBAC_HMAC_KEY={_TEST_RBAC_KEY}\n"
    )

    # Inherit parent env so the venv-resolved python finds samantha_server,
    # then strip exactly the keys the test needs absent. Conftest sets these
    # via setdefault in the pytest session; without removal the subprocess
    # would inherit them and never need .env.
    clean_env = {k: v for k, v in os.environ.items()}
    for k in ("RECEIPT_SIGNING_KEY", "PHI_HASH_SALT", "RBAC_HMAC_KEY", "RECEIPTS_DB_PATH"):
        clean_env.pop(k, None)

    # The project is importable only via CWD=repo root (no editable .pth in
    # site-packages — uv-style install). Set PYTHONPATH so the subprocess
    # finds samantha_server while CWD points at the .env-bearing tmp dir.
    repo_root = Path(__file__).resolve().parents[1]
    clean_env["PYTHONPATH"] = str(repo_root)

    nonexistent_dir = tmp_path / "no-such-corpus"
    result = subprocess.run(
        [sys.executable, "-m", "samantha_server.scenarios.replay", str(nonexistent_dir)],
        cwd=tmp_path,
        env=clean_env,
        capture_output=True,
        text=True,
        check=False,
    )

    combined = result.stdout + result.stderr
    # Negative guards in this order: rule out import-path breakage first
    # (would otherwise let the env-guard check pass vacuously), then
    # rule out the env-guard itself. Together these prove the subprocess
    # reached the corpus loader, which is the point this test verifies.
    assert "ModuleNotFoundError" not in combined, (
        "subprocess could not import samantha_server — PYTHONPATH wiring "
        "broken, test result is meaningless.\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "ImportError" not in combined, (
        "subprocess hit an ImportError before reaching the corpus loader.\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "RECEIPT_SIGNING_KEY" not in combined, (
        "config.py raised the missing-secret guard — .env was not auto-loaded.\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    # Sanity: the subprocess should have reached the corpus loader and
    # failed there. We don't pin the exact wording (loader errors evolve);
    # we just confirm it isn't the env-guard.
    assert result.returncode != 0, f"expected non-zero exit (nonexistent dir); got 0.\n{combined}"
