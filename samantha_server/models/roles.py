"""GH-227: User role literal type, valid-role set, and extraction helper.

user_role is NOT PHI — pass through verbatim, no HMAC hashing.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Final, Literal, get_args

UserRole = Literal["accessioner", "histotech", "pathologist", "lab_manager"]
VALID_USER_ROLES: Final[frozenset[str]] = frozenset(get_args(UserRole))

if TYPE_CHECKING:
    from samantha_server.observability.counters import CounterRegistry


def parse_user_role(
    event_data: Mapping[str, Any],
    *,
    logger: logging.Logger,
    counters: CounterRegistry | None = None,
) -> tuple[UserRole | None, bool]:
    """Extract and validate user_role from event_data.

    Returns (role, was_coercion_failure).

    - raw value missing → (None, False)
    - valid role string → (role, False)
    - invalid type or unknown string → log WARNING (never the raw value),
      increment counters.user_role_coercion_failures if counters is non-None,
      return (None, True)
    """
    raw_role = event_data.get("user_role")
    if raw_role is None:
        return None, False
    if isinstance(raw_role, str) and raw_role in VALID_USER_ROLES:
        return raw_role, False  # type: ignore[return-value]
    logger.warning(
        "event_data['user_role'] is invalid (type=%s); treating as absent.",
        type(raw_role).__name__,
    )
    if counters is not None:
        counters.user_role_coercion_failures.increment()
    return None, True


__all__ = ["UserRole", "VALID_USER_ROLES", "parse_user_role"]
