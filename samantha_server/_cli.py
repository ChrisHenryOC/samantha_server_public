"""Shared CLI entry-point helpers.

This module lives outside the deterministic-purity scan paths
(see ``tests/architectural/test_deterministic_purity.py``) on purpose:
it carries the one piece of CLI-only convenience (``.env`` loading)
that the engine core must never depend on. Importing it from engine
modules would be a regression — keep it called from ``main()``s only.
"""

from __future__ import annotations


def load_dotenv_for_cli() -> None:
    """Populate ``os.environ`` from ``.env`` for CLI invocations.

    Walks up from the **current working directory** (``usecwd=True``)
    looking for a ``.env`` file. ``usecwd=True`` is load-bearing: the
    default ``find_dotenv()`` mode walks up from the caller's ``.py``
    file location, which for an installed package points at
    ``site-packages`` — there is no ``.env`` there. CWD-based lookup
    finds the operator's project ``.env`` instead.

    When ``.env`` is found, sets any variable that is not already in
    the environment — ``override=False`` is deliberate so a
    shell-exported value (e.g. ``LLM_MODEL_NAME=foo python -m
    samantha_server.scenarios.replay``) still wins over the file.
    When no ``.env`` exists in any ancestor of CWD, this is a no-op;
    the caller's config import will surface the missing-secret error
    as usual.

    Call this as the *first* line of every CLI ``main()`` — before
    any ``import samantha_server.config`` — because ``config.py``
    reads ``os.environ`` at module-import time. If the config import
    lands before ``load_dotenv_for_cli()`` runs, the file's values
    arrive too late.

    NOT intended for use by library/test code. Pytest sessions load
    ``.env`` themselves via ``tests/conftest.py`` (with sentinel keys
    set first via ``setdefault`` so production secrets cannot leak
    into the test session). Tests that import a CLI's ``main`` and
    call it will also trigger this loader; that is harmless because
    ``override=False`` keeps the conftest's sentinels in place.

    Search semantics caveats:
    - ``find_dotenv(usecwd=True)`` walks from CWD up to the filesystem
      root with no depth cap. A hostile ancestor directory containing
      a `.env` would be picked up. Operators should invoke CLIs from
      the project root (or pre-export critical keys in the shell) to
      avoid this. Security-critical keys (`RECEIPT_SIGNING_KEY`,
      `PHI_HASH_SALT`, `RBAC_HMAC_KEY`) carry shape-based sentinel
      rejection in ``config.py`` as a backstop.
    - A typo in the filename (`.evn`, `.env.local`) is silently
      ignored — ``find_dotenv`` returns "" and the missing-secret
      error from ``config.py`` is the user-visible signal. This
      matches dotenv's documented behaviour.
    - ``find_dotenv`` can raise ``OSError`` if the process's CWD has
      been deleted (uncommon outside container teardown). Catch it
      defensively so the CLI's missing-secret guard surfaces a
      clearer message than the raw OSError.
    """
    from dotenv import find_dotenv, load_dotenv

    try:
        found = find_dotenv(usecwd=True)
    except OSError:
        # CWD was deleted out from under us, or another I/O error blocked
        # the walk. Fall through to the caller's config import, which
        # surfaces the canonical MisconfiguredEnvironmentError.
        return
    if found:
        load_dotenv(dotenv_path=found, override=False)
