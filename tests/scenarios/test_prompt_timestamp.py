"""Scenario.prompt_timestamp field and default computation.

Slice 1 tests: loader reads explicit prompt_timestamp from JSON and
computes the default from max(orders.created_at) + 24h when absent.
"""

from __future__ import annotations

import json
from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE_STEP = {
    "step": 1,
    "event_type": "clinical_query",
    "event_data": {
        "query": "Which orders should I do first?",
    },
    "expected_output": {
        "next_state": "ACCESSIONING",
        "applied_rules": [],
        "flags": [],
        "routing_path": "llm",
    },
}

_ORDER_WITH_CREATED_AT = {
    "order_id": "ORD-001",
    "current_state": "ACCEPTED",
    "specimen_type": "biopsy",
    "anatomic_site": "breast",
    "priority": "routine",
    "flags": [],
    "created_at": "2025-01-15T10:00:00Z",
}

_ORDER_OLDER = {
    "order_id": "ORD-002",
    "current_state": "ACCEPTED",
    "specimen_type": "excision",
    "anatomic_site": "breast",
    "priority": "rush",
    "flags": [],
    "created_at": "2025-01-14T08:00:00Z",
}


def _write_fixture(tmp_path: Path, raw: dict) -> Path:
    """Write a JSON fixture to a temp file and return the path."""
    p = tmp_path / "test_scenario.json"
    p.write_text(json.dumps(raw))
    return p


def _make_scenario_raw(*, prompt_timestamp: str | None = None, orders: list | None = None) -> dict:
    """Build a minimal scenario dict."""
    step = dict(_BASE_STEP)
    if orders is not None:
        step["event_data"] = dict(_BASE_STEP["event_data"]) | {"orders": orders}

    raw: dict = {
        "scenario_id": "QR-TEST",
        "category": "query",
        "description": "Test scenario",
        "events": [step],
    }
    if prompt_timestamp is not None:
        raw["prompt_timestamp"] = prompt_timestamp
    return raw


# ---------------------------------------------------------------------------
# Slice 1 — explicit prompt_timestamp field
# ---------------------------------------------------------------------------


def test_scenario_carries_explicit_prompt_timestamp(tmp_path: Path) -> None:
    """Scenario loaded from a JSON with prompt_timestamp carries it verbatim."""
    from samantha_server.scenarios.loader import _parse_scenario

    raw = _make_scenario_raw(prompt_timestamp="2025-01-16T08:00:00Z")
    path = _write_fixture(tmp_path, raw)
    scenario = _parse_scenario(path, raw)

    assert scenario.prompt_timestamp == "2025-01-16T08:00:00Z"


def test_scenario_prompt_timestamp_none_when_absent_and_no_orders(tmp_path: Path) -> None:
    """Without prompt_timestamp key and without orders, the field is None."""
    from samantha_server.scenarios.loader import _parse_scenario

    raw = _make_scenario_raw()
    path = _write_fixture(tmp_path, raw)
    scenario = _parse_scenario(path, raw)

    assert scenario.prompt_timestamp is None


# ---------------------------------------------------------------------------
# Slice 1 — default computation from max(created_at) + 24h
# ---------------------------------------------------------------------------


def test_scenario_default_prompt_timestamp_from_orders(tmp_path: Path) -> None:
    """Without explicit prompt_timestamp, default is max(created_at) + 24h."""
    from samantha_server.scenarios.loader import _parse_scenario

    orders = [_ORDER_WITH_CREATED_AT, _ORDER_OLDER]
    raw = _make_scenario_raw(orders=orders)
    path = _write_fixture(tmp_path, raw)
    scenario = _parse_scenario(path, raw)

    # max(created_at) is 2025-01-15T10:00:00Z; + 24h = 2025-01-16T10:00:00Z
    assert scenario.prompt_timestamp == "2025-01-16T10:00:00Z"


def test_scenario_default_prompt_timestamp_uses_max_not_first(tmp_path: Path) -> None:
    """Default uses max(created_at), not the first order in the list."""
    from samantha_server.scenarios.loader import _parse_scenario

    # Reverse order: older first, newer second
    orders = [_ORDER_OLDER, _ORDER_WITH_CREATED_AT]
    raw = _make_scenario_raw(orders=orders)
    path = _write_fixture(tmp_path, raw)
    scenario = _parse_scenario(path, raw)

    # max is still 2025-01-15T10:00:00Z (from _ORDER_WITH_CREATED_AT)
    assert scenario.prompt_timestamp == "2025-01-16T10:00:00Z"


def test_scenario_explicit_overrides_default(tmp_path: Path) -> None:
    """Explicit prompt_timestamp wins even when orders with created_at are present."""
    from samantha_server.scenarios.loader import _parse_scenario

    orders = [_ORDER_WITH_CREATED_AT]
    raw = _make_scenario_raw(prompt_timestamp="2025-02-01T00:00:00Z", orders=orders)
    path = _write_fixture(tmp_path, raw)
    scenario = _parse_scenario(path, raw)

    assert scenario.prompt_timestamp == "2025-02-01T00:00:00Z"


def test_scenario_default_none_when_orders_lack_created_at(tmp_path: Path) -> None:
    """Orders without created_at fields produce prompt_timestamp=None."""
    from samantha_server.scenarios.loader import _parse_scenario

    order_no_ts = {
        "order_id": "ORD-X",
        "current_state": "ACCEPTED",
        "specimen_type": "biopsy",
        "anatomic_site": "breast",
        "priority": "routine",
        "flags": [],
    }
    raw = _make_scenario_raw(orders=[order_no_ts])
    path = _write_fixture(tmp_path, raw)
    scenario = _parse_scenario(path, raw)

    assert scenario.prompt_timestamp is None


def test_default_skips_orders_with_malformed_created_at(tmp_path: Path) -> None:
    """Malformed created_at is skipped (logged warning),
    valid sibling orders are still considered for the max() anchor.
    """
    from samantha_server.scenarios.loader import _parse_scenario

    bad_order = dict(_ORDER_WITH_CREATED_AT) | {"order_id": "ORD-BAD", "created_at": "not-a-date"}
    raw = _make_scenario_raw(orders=[bad_order, _ORDER_WITH_CREATED_AT])
    path = _write_fixture(tmp_path, raw)
    scenario = _parse_scenario(path, raw)

    # The valid order's max + 24h survives; the bad one is skipped.
    assert scenario.prompt_timestamp == "2025-01-16T10:00:00Z"


def test_default_returns_none_when_only_malformed_created_at(tmp_path: Path) -> None:
    """When ALL orders have malformed created_at,
    no usable timestamps exist and the result is None.
    """
    from samantha_server.scenarios.loader import _parse_scenario

    bad = dict(_ORDER_WITH_CREATED_AT) | {"created_at": "garbage"}
    raw = _make_scenario_raw(orders=[bad])
    path = _write_fixture(tmp_path, raw)
    scenario = _parse_scenario(path, raw)

    assert scenario.prompt_timestamp is None


def test_explicit_malformed_prompt_timestamp_raises(tmp_path: Path) -> None:
    """Explicit prompt_timestamp not parseable as ISO-8601
    raises ValueError (loader treats as malformed scenario).
    """
    import pytest

    from samantha_server.scenarios.loader import _parse_scenario

    raw = _make_scenario_raw(prompt_timestamp="not-a-date")
    path = _write_fixture(tmp_path, raw)
    with pytest.raises(ValueError, match="not a valid ISO-8601"):
        _parse_scenario(path, raw)


# ---------------------------------------------------------------------------
# Slice 4 — QR-020 and QR-021 fixture prompt_timestamp values
# ---------------------------------------------------------------------------


def test_qr020_carries_explicit_prompt_timestamp() -> None:
    """QR-020 fixture has an explicit prompt_timestamp that the loader preserves."""
    from pathlib import Path

    from samantha_server.scenarios.loader import load_scenarios

    fixture_dir = Path(__file__).parent.parent / "fixtures" / "scenarios" / "query"
    scenarios = load_scenarios(fixture_dir)
    by_id = {s.scenario_id: s for s in scenarios}
    assert "QR-020" in by_id, "QR-020 fixture not loaded"
    # max(created_at) for QR-020 orders is 2025-01-15T10:00:00Z (ORD-2002);
    # +24h = 2025-01-16T10:00:00Z. The fixture pins this explicitly so the
    # temporal anchor is deterministic regardless of default-computation changes.
    assert by_id["QR-020"].prompt_timestamp == "2025-01-16T10:00:00Z"


def test_qr021_carries_explicit_prompt_timestamp() -> None:
    """QR-021 fixture has an explicit prompt_timestamp that the loader preserves."""
    from pathlib import Path

    from samantha_server.scenarios.loader import load_scenarios

    fixture_dir = Path(__file__).parent.parent / "fixtures" / "scenarios" / "query"
    scenarios = load_scenarios(fixture_dir)
    by_id = {s.scenario_id: s for s in scenarios}
    assert "QR-021" in by_id, "QR-021 fixture not loaded"
    # max(created_at) for QR-021 orders is 2025-01-15T09:00:00Z (ORD-2106);
    # +24h = 2025-01-16T09:00:00Z. The fixture pins this explicitly.
    assert by_id["QR-021"].prompt_timestamp == "2025-01-16T09:00:00Z"
