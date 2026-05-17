"""Aggregator tests — Phase 1 verifies mock shape matches spec."""
from __future__ import annotations

import pytest

from app import aggregator, mocks


@pytest.mark.asyncio
async def test_fetch_state_returns_all_required_keys():
    state = await aggregator.fetch_state()
    for key in ("ts", "capital", "trades", "strategies", "pnl_series_30d",
                "positions", "recommendations", "system"):
        assert key in state, f"missing {key}"


@pytest.mark.asyncio
async def test_fetch_state_capital_shape():
    state = await aggregator.fetch_state()
    cap = state["capital"]
    for key in ("working_capital_usd", "open_notional_usd", "pnl_24h_usd",
                "pnl_7d_usd", "pnl_30d_usd", "pnl_alltime_usd"):
        assert key in cap


@pytest.mark.asyncio
async def test_fetch_state_strategies_count():
    state = await aggregator.fetch_state()
    assert len(state["strategies"]) >= 2
    ids = {s["id"] for s in state["strategies"]}
    assert "polymarket" in ids
    assert "liquidation" in ids


@pytest.mark.asyncio
async def test_fetch_state_caches():
    """Subsequent calls within TTL should return the same object."""
    aggregator._CACHE["state"] = None   # reset
    s1 = await aggregator.fetch_state()
    s2 = await aggregator.fetch_state()
    assert s1 is s2  # same object reference (cache hit)


@pytest.mark.asyncio
async def test_fetch_positions_returns_list():
    positions = await aggregator.fetch_positions()
    assert isinstance(positions, list)
    if positions:
        assert "id" in positions[0]
        assert "source" in positions[0]
        assert "pnl_usd" in positions[0]


def test_mock_state_shape_matches_spec():
    state = mocks.mock_state()
    assert state["capital"]["working_capital_usd"] == 4247.83
    assert len(state["strategies"]) == 2
    assert state["strategies"][1]["gate"]["total"] == 4


def test_mock_pnl_series_30_points():
    state = mocks.mock_state()
    assert len(state["pnl_series_30d"]) == 30
    point = state["pnl_series_30d"][0]
    for key in ("t", "combined", "polymarket", "liquidation"):
        assert key in point


def test_mock_activity_event_shape():
    ev = mocks.mock_activity_event()
    assert "ts" in ev
    assert "kind" in ev
    assert ev["kind"] in ("exec", "sig", "warn", "err")
