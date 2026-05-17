"""Aggregator tests — mocks shape, fallbacks, and per-fetcher units."""
from __future__ import annotations

import pytest

from app import aggregator, mocks
from app.settings import settings


# ─── Shape contracts (full-aggregator) ───────────────────────────────


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


# ─── Fail-OPEN: None on upstream error ───────────────────────────────


@pytest.mark.asyncio
async def test_fetch_capital_and_trades_none_when_pool_none():
    out = await aggregator._fetch_capital_and_trades(None)
    assert out is None


@pytest.mark.asyncio
async def test_fetch_positions_none_when_pool_none():
    out = await aggregator._fetch_positions(None)
    assert out is None


@pytest.mark.asyncio
async def test_fetch_pnl_series_none_when_pool_none():
    out = await aggregator._fetch_pnl_series(None)
    assert out is None


@pytest.mark.asyncio
async def test_fetch_strategies_none_when_no_pool_or_redis():
    out = await aggregator._fetch_strategies(None, None)
    assert out is None


@pytest.mark.asyncio
async def test_fetch_capital_and_trades_returns_none_on_query_error(monkeypatch):
    """If asyncpg pool.acquire raises, fetcher returns None (fail-open)."""

    class BadPool:
        def acquire(self):
            raise RuntimeError("connection refused")

    out = await aggregator._fetch_capital_and_trades(BadPool())
    assert out is None


@pytest.mark.asyncio
async def test_fetch_positions_returns_none_on_query_error():
    class BadPool:
        def acquire(self):
            raise RuntimeError("oops")

    out = await aggregator._fetch_positions(BadPool())
    assert out is None


# ─── System health ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_oracle_freshness_counts_keys(fake_redis):
    await fake_redis.set("chainlink:btc:latest", "1")
    await fake_redis.set("chainlink:eth:latest", "1")
    monkey_assets = ["btc", "eth", "sol"]
    out = await aggregator._oracle_freshness(fake_redis, prefix="chainlink", assets=monkey_assets)
    assert out["active"] == 2
    assert out["total"] == 3
    assert out["ok"] is False


@pytest.mark.asyncio
async def test_oracle_freshness_all_ok(fake_redis):
    for asset in ("btc", "eth"):
        await fake_redis.set(f"pyth:{asset}:latest", "1")
    out = await aggregator._oracle_freshness(fake_redis, prefix="pyth", assets=["btc", "eth"])
    assert out["ok"] is True
    assert out["active"] == 2


@pytest.mark.asyncio
async def test_oracle_freshness_returns_none_on_error():
    class BadRedis:
        async def mget(self, keys):
            raise RuntimeError("oops")

    out = await aggregator._oracle_freshness(BadRedis(), prefix="cl", assets=["btc"])
    assert out is None


def test_endpoint_label_extracts_port():
    assert aggregator._endpoint_label("http://ocde:8014") == ":8014"
    assert aggregator._endpoint_label("http://oms-gateway:8003/health") == ":8003"


# ─── Liquidation card via Redis streams ──────────────────────────────


@pytest.mark.asyncio
async def test_build_liquidation_card_counts_streams(fake_redis):
    await fake_redis.xadd("liquidation:eval_log", {"reason": "would_fire"})
    await fake_redis.xadd("liquidation:eval_log", {"reason": "ok"})
    await fake_redis.xadd("gmx:execution:paper_log", {"x": "1"})
    out = await aggregator._build_liquidation_card(fake_redis)
    assert out["id"] == "liquidation"
    assert out["evaluations_24h"] >= 2
    assert out["detected_liquidatable_24h"] == 1
    # would_execute = gmx paper + detected
    assert out["would_execute_24h"] >= 2
    assert out["actually_executed_24h"] == 0


@pytest.mark.asyncio
async def test_build_liquidation_card_gate_shape(fake_redis):
    out = await aggregator._build_liquidation_card(fake_redis)
    assert out["gate"]["total"] == 4
    assert len(out["gate"]["flags"]) == 4


# ─── Strategy assembly ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_strategies_polymarket_falls_back_when_pool_none(fake_redis):
    """No PG pool: poly card falls back to mock, liquidation built from Redis."""
    out = await aggregator._fetch_strategies(None, fake_redis)
    # If poly fails AND liq builds, the function still returns the pair
    # (we fall back to the mock poly card).
    assert out is not None
    ids = {s["id"] for s in out}
    assert "polymarket" in ids
    assert "liquidation" in ids


# ─── End-to-end fallback behaviour ───────────────────────────────────


@pytest.mark.asyncio
async def test_aggregate_marks_mock_when_pg_missing(fake_redis):
    """With no Postgres but Redis up, state['mock'] flips to True."""
    aggregator._CACHE["state"] = None
    aggregator._pg_pool = None   # explicit
    state = await aggregator._aggregate()
    assert state.get("mock") is True
    # System health still includes the oracle-pip data from Redis.
    assert "system" in state
    assert "cl_streams" in state["system"]


@pytest.mark.asyncio
async def test_close_is_idempotent():
    """Calling close() twice should not raise."""
    await aggregator.close()
    await aggregator.close()


# ─── Settings smoke ───────────────────────────────────────────────────


def test_settings_oracle_lists():
    assert "btc" in settings.cl_assets
    assert len(settings.pyth_assets) >= 5
    assert settings.sse_buffer_size >= 1
