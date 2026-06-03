"""S1 GH-227: Tests for samantha_server.models.roles."""

from __future__ import annotations

import logging
from typing import get_args

import pytest


def test_user_role_import() -> None:
    """UserRole and VALID_USER_ROLES are importable from models.roles."""
    from samantha_server.models.roles import VALID_USER_ROLES, UserRole  # noqa: F401


def test_valid_user_roles_has_exactly_four_members() -> None:
    """VALID_USER_ROLES contains exactly the four expected role strings."""
    from samantha_server.models.roles import VALID_USER_ROLES

    assert frozenset({"accessioner", "histotech", "pathologist", "lab_manager"}) == VALID_USER_ROLES


def test_valid_user_roles_is_frozenset() -> None:
    """VALID_USER_ROLES is a frozenset (immutable)."""
    from samantha_server.models.roles import VALID_USER_ROLES

    assert isinstance(VALID_USER_ROLES, frozenset)


def test_valid_user_roles_derived_from_user_role_literal() -> None:
    """VALID_USER_ROLES must equal frozenset(get_args(UserRole)) — no duplication (L1)."""
    from samantha_server.models.roles import VALID_USER_ROLES, UserRole

    assert frozenset(get_args(UserRole)) == VALID_USER_ROLES


# ---------------------------------------------------------------------------
# parse_user_role tests (S1)
# ---------------------------------------------------------------------------


def test_parse_user_role_absent_returns_none_false() -> None:
    """Missing user_role key → (None, False)."""
    from samantha_server.models.roles import parse_user_role

    role, coerced = parse_user_role({}, logger=logging.getLogger("t"))
    assert role is None
    assert coerced is False


@pytest.mark.parametrize("valid_role", ["accessioner", "histotech", "pathologist", "lab_manager"])
def test_parse_user_role_valid_roles(valid_role: str) -> None:
    """Each of the four valid roles → (role, False)."""
    from samantha_server.models.roles import parse_user_role

    role, coerced = parse_user_role({"user_role": valid_role}, logger=logging.getLogger("t"))
    assert role == valid_role
    assert coerced is False


def test_parse_user_role_unknown_string_returns_none_true(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unknown string → (None, True) + WARNING logged."""
    from samantha_server.models.roles import parse_user_role

    logger = logging.getLogger("samantha_server.models.roles")
    with caplog.at_level(logging.WARNING, logger="samantha_server.models.roles"):
        role, coerced = parse_user_role({"user_role": "nurse"}, logger=logger)

    assert role is None
    assert coerced is True
    assert any(r.levelno >= logging.WARNING for r in caplog.records)


def test_parse_user_role_non_str_returns_none_true(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Non-string user_role → (None, True) + WARNING logged."""
    from samantha_server.models.roles import parse_user_role

    logger = logging.getLogger("samantha_server.models.roles")
    with caplog.at_level(logging.WARNING, logger="samantha_server.models.roles"):
        role, coerced = parse_user_role({"user_role": 42}, logger=logger)

    assert role is None
    assert coerced is True
    assert any(r.levelno >= logging.WARNING for r in caplog.records)


def test_parse_user_role_counter_incremented_on_failure() -> None:
    """Counter is incremented when coercion fails and counters is provided."""
    from samantha_server.models.roles import parse_user_role
    from samantha_server.observability.counters import CounterRegistry

    counters = CounterRegistry()
    logger = logging.getLogger("samantha_server.models.roles")
    parse_user_role({"user_role": "nurse"}, logger=logger, counters=counters)
    assert counters.user_role_coercion_failures.value == 1


def test_parse_user_role_counter_not_incremented_on_valid() -> None:
    """Counter is NOT incremented when role is valid."""
    from samantha_server.models.roles import parse_user_role
    from samantha_server.observability.counters import CounterRegistry

    counters = CounterRegistry()
    logger = logging.getLogger("samantha_server.models.roles")
    parse_user_role({"user_role": "pathologist"}, logger=logger, counters=counters)
    assert counters.user_role_coercion_failures.value == 0


def test_parse_user_role_counter_not_incremented_when_absent() -> None:
    """Counter is NOT incremented when user_role is absent."""
    from samantha_server.models.roles import parse_user_role
    from samantha_server.observability.counters import CounterRegistry

    counters = CounterRegistry()
    logger = logging.getLogger("samantha_server.models.roles")
    parse_user_role({}, logger=logger, counters=counters)
    assert counters.user_role_coercion_failures.value == 0
