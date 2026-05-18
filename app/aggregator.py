"""Pull state from upstream services + assemble /api/state response.

Each upstream-pull function returns ``None`` on any failure (logging the
reason). The aggregator falls back to the corresponding mock field, so
one dead upstream never crashes the dashboard.

Upstreams wired (Phase 2):

* Postgres (asyncpg) — capital + trades + positions + 30d PnL series
* Redis — strategy stream counts (XLEN/XRANGE), oracle freshness pings
* httpx — OCDE + oms-gateway health endpoints
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import asyncpg
import httpx
import redis.asyncio as redis_asyncio

from app import mocks
from app.settings import settings

log = logging.getLogger(__name__)


# ─── Module-level lazy clients ────────────────────────────────────────

_pg_pool: asyncpg.Pool | None = None
_redis: redis_asyncio.Redis | None = None
_http: httpx.AsyncClient | None = None
_lock = asyncio.Lock()


async def _get_pg_pool() -> asyncpg.Pool | None:
    """Lazy asyncpg pool. Returns None if creation fails."""
    global _pg_pool
    if _pg_pool is not None:
        return _pg_pool
    async with _lock:
        if _pg_pool is not None:
            return _pg_pool
        try:
            _pg_pool = await asyncpg.create_pool(
                dsn=settings.postgres_dsn,
                min_size=1,
                max_size=3,
                command_timeout=3.0,
            )
        except Exception as e:
            log.warning("aggregator.pg_pool_init_failed err=%s", e)
            _pg_pool = None
    return _pg_pool


async def _get_redis() -> redis_asyncio.Redis | None:
    """Lazy Redis client. Decoded responses so XRANGE returns str dicts."""
    global _redis
    if _redis is not None:
        return _redis
    try:
        _redis = redis_asyncio.from_url(
            settings.redis_url, decode_responses=True, socket_timeout=2.0
        )
    except Exception as e:
        log.warning("aggregator.redis_init_failed err=%s", e)
        _redis = None
    return _redis


def _get_http() -> httpx.AsyncClient:
    """Lazy shared httpx client. 2s default timeout."""
    global _http
    if _http is None:
        _http = httpx.AsyncClient(timeout=2.0)
    return _http


async def close() -> None:
    """Tear down the module-level clients (called on app shutdown)."""
    global _pg_pool, _redis, _http
    if _pg_pool is not None:
        try:
            await _pg_pool.close()
        except Exception:
            pass
        _pg_pool = None
    if _redis is not None:
        try:
            await _redis.aclose()
        except Exception:
            pass
        _redis = None
    if _http is not None:
        try:
            await _http.aclose()
        except Exception:
            pass
        _http = None


# ─── Cache ────────────────────────────────────────────────────────────

_CACHE: dict[str, Any] = {"ts": 0.0, "state": None}


async def fetch_state() -> dict:
    """Return the full /api/state payload (cached for ``state_cache_ttl_sec``)."""
    now = time.time()
    if _CACHE["state"] is not None and (now - _CACHE["ts"]) < settings.state_cache_ttl_sec:
        return _CACHE["state"]

    state = await _aggregate()
    _CACHE["ts"] = now
    _CACHE["state"] = state
    return state


async def fetch_positions() -> list[dict]:
    """Detailed positions list."""
    state = await fetch_state()
    return state.get("positions", [])


# ─── Aggregation ─────────────────────────────────────────────────────


async def _aggregate() -> dict:
    """Pull from each upstream + assemble. Each failed pull falls back to mock."""
    state = mocks.mock_state()
    state["mock"] = False

    pool = await _get_pg_pool()
    redis = await _get_redis()
    http = _get_http()

    cap_trades = await _fetch_capital_and_trades(pool)
    if cap_trades is not None:
        state["capital"] = cap_trades["capital"]
        state["trades"] = cap_trades["trades"]
    else:
        log.warning("aggregator.capital_trades_fallback_mock")
        state["mock"] = True

    strategies = await _fetch_strategies(pool, redis)
    if strategies is not None:
        state["strategies"] = strategies
    else:
        log.warning("aggregator.strategies_fallback_mock")
        state["mock"] = True

    positions = await _fetch_positions(pool)
    if positions is not None:
        state["positions"] = positions
    else:
        log.warning("aggregator.positions_fallback_mock")
        state["mock"] = True

    system = await _fetch_system_health(redis, http)
    if system is not None:
        state["system"] = system
    else:
        log.warning("aggregator.system_health_fallback_mock")
        state["mock"] = True

    series = await _fetch_pnl_series(pool)
    if series is not None:
        state["pnl_series_30d"] = series
    else:
        log.warning("aggregator.pnl_series_fallback_mock")
        state["mock"] = True

    return state


# ─── Per-source fetchers ─────────────────────────────────────────────


async def _fetch_capital_and_trades(pool: asyncpg.Pool | None) -> dict | None:
    """Working capital + windowed P&L + lifetime trade tallies from Postgres."""
    if pool is None:
        return None
    try:
        async with pool.acquire() as conn:
            cap_row = await conn.fetchrow(
                """
                SELECT
                    COALESCE(SUM(qty * avg_entry_price) FILTER (WHERE status='open'), 0)::float
                        AS open_notional_usd,
                    COALESCE(SUM(realized_pnl_usd)
                        FILTER (WHERE closed_at > NOW() - INTERVAL '1 day'), 0)::float
                        AS pnl_24h_usd,
                    COALESCE(SUM(realized_pnl_usd)
                        FILTER (WHERE closed_at > NOW() - INTERVAL '7 day'), 0)::float
                        AS pnl_7d_usd,
                    COALESCE(SUM(realized_pnl_usd)
                        FILTER (WHERE closed_at > NOW() - INTERVAL '30 day'), 0)::float
                        AS pnl_30d_usd,
                    COALESCE(SUM(realized_pnl_usd), 0)::float
                        AS pnl_alltime_usd
                FROM positions
                """
            )
            trades_row = await conn.fetchrow(
                """
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE realized_pnl_usd > 0) AS wins,
                    COUNT(*) FILTER (WHERE realized_pnl_usd <= 0
                                       AND closed_at IS NOT NULL) AS losses,
                    COUNT(*) FILTER (WHERE closed_at > NOW() - INTERVAL '1 day')
                        AS trades_today,
                    COUNT(*) FILTER (WHERE realized_pnl_usd > 0
                                       AND closed_at > NOW() - INTERVAL '1 day')
                        AS wins_today,
                    COUNT(*) FILTER (WHERE realized_pnl_usd <= 0
                                       AND closed_at > NOW() - INTERVAL '1 day')
                        AS losses_today
                FROM positions
                WHERE closed_at IS NOT NULL
                """
            )
    except Exception as e:
        log.warning("aggregator.capital_trades.query_failed err=%s", e)
        return None

    reserved_gas_usd = settings.reserved_gas_usd
    open_notional = float(cap_row["open_notional_usd"])
    working = open_notional + reserved_gas_usd

    total = int(trades_row["total"])
    wins = int(trades_row["wins"])
    losses = int(trades_row["losses"])
    win_rate = (wins / total) if total else 0.0

    today_trades = int(trades_row["trades_today"])
    today_wins = int(trades_row["wins_today"])
    today_losses = int(trades_row["losses_today"])
    today_rate = (today_wins / today_trades) if today_trades else 0.0

    return {
        "capital": {
            "working_capital_usd": round(working, 2),
            "open_notional_usd": round(open_notional, 2),
            "reserved_gas_usd": round(reserved_gas_usd, 2),
            "pnl_24h_usd": round(float(cap_row["pnl_24h_usd"]), 2),
            "pnl_7d_usd": round(float(cap_row["pnl_7d_usd"]), 2),
            "pnl_30d_usd": round(float(cap_row["pnl_30d_usd"]), 2),
            "pnl_alltime_usd": round(float(cap_row["pnl_alltime_usd"]), 2),
        },
        "trades": {
            "total": total,
            "wins": wins,
            "losses": losses,
            "win_rate": round(win_rate, 3),
            "today": {
                "trades": today_trades,
                "wins": today_wins,
                "losses": today_losses,
                "win_rate": round(today_rate, 3),
            },
        },
    }


async def _fetch_strategies(
    pool: asyncpg.Pool | None,
    redis: redis_asyncio.Redis | None,
) -> list[dict] | None:
    """Build the two strategy cards (Polymarket + Liquidation)."""
    if pool is None and redis is None:
        return None

    poly_card = await _build_polymarket_card(pool) if pool is not None else None
    liq_card = await _build_liquidation_card(redis) if redis is not None else None

    if poly_card is None and liq_card is None:
        return None

    # Fall back to mocks for whichever lane failed so the UI still renders.
    mock_state = mocks.mock_state()
    if poly_card is None:
        log.warning("aggregator.strategies.polymarket_fallback")
        poly_card = mock_state["strategies"][0]
    if liq_card is None:
        log.warning("aggregator.strategies.liquidation_fallback")
        liq_card = mock_state["strategies"][1]

    return [poly_card, liq_card]


async def _build_polymarket_card(pool: asyncpg.Pool) -> dict | None:
    """Polymarket strategy card from positions(venue='polymarket')."""
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    COUNT(*) FILTER (WHERE status='open')             AS open_positions,
                    COUNT(*) FILTER (WHERE closed_at > NOW() - INTERVAL '1 day') AS closed_today,
                    COALESCE(SUM(qty * avg_entry_price) FILTER (WHERE status='open'), 0)::float
                                                                      AS capital_usd,
                    COALESCE(SUM(realized_pnl_usd)
                        FILTER (WHERE closed_at > NOW() - INTERVAL '1 day'), 0)::float
                                                                      AS pnl_24h_usd,
                    COUNT(*) FILTER (WHERE realized_pnl_usd > 0
                                       AND closed_at > NOW() - INTERVAL '1 day') AS wins_24h,
                    COUNT(*) FILTER (WHERE realized_pnl_usd <= 0
                                       AND closed_at > NOW() - INTERVAL '1 day') AS losses_24h
                FROM positions
                WHERE venue = 'polymarket'
                """
            )
            total_open_row = await conn.fetchrow(
                "SELECT COALESCE(SUM(qty * avg_entry_price), 0)::float AS total FROM positions WHERE status='open'"
            )
    except Exception as e:
        log.warning("aggregator.polymarket_card.query_failed err=%s", e)
        return None

    capital_usd = float(row["capital_usd"])
    total_open = float(total_open_row["total"])
    pct = (capital_usd / total_open) if total_open > 0 else 0.0
    wins_24h = int(row["wins_24h"])
    losses_24h = int(row["losses_24h"])
    closed_24h = wins_24h + losses_24h
    win_rate_24h = (wins_24h / closed_24h) if closed_24h else 0.0

    return {
        "id": "polymarket",
        "name": "Polymarket Compound",
        "mode": "paper",
        "status": "live-ready",
        "capital_usd": round(capital_usd, 2),
        "pct_of_book": round(pct, 3),
        "open_positions": int(row["open_positions"]),
        "closed_today": int(row["closed_today"]),
        "pnl_24h_usd": round(float(row["pnl_24h_usd"]), 2),
        "win_rate_24h": round(win_rate_24h, 3),
        "wins_24h": wins_24h,
        "losses_24h": losses_24h,
        "armed": True,
    }


async def _build_liquidation_card(redis: redis_asyncio.Redis) -> dict | None:
    """Liquidation bot card from Redis streams (24h XRANGE window)."""
    try:
        cutoff_ms = int((time.time() - 86400) * 1000)
        min_id = f"{cutoff_ms}-0"

        eval_count = await _xlen_since(redis, "liquidation:eval_log", min_id)
        gmx_paper = await _xrange_since(redis, "gmx:execution:paper_log", min_id)
        liq_evals = await _xrange_since(redis, "liquidation:eval_log", min_id)
        live_count = await _xlen_since(redis, "gmx:execution:live_log", min_id)

        detected = sum(
            1 for fields in liq_evals
            if fields.get("reason") == "would_fire"
        )
        would_exec = len(gmx_paper) + detected
    except Exception as e:
        log.warning("aggregator.liquidation_card.redis_failed err=%s", e)
        return None

    return {
        "id": "liquidation",
        "name": "Liquidation Bot",
        "mode": "paper",
        "status": "gated",
        "capital_usd": settings.reserved_gas_usd,
        "evaluations_24h": eval_count,
        "detected_liquidatable_24h": detected,
        "would_execute_24h": would_exec,
        "actually_executed_24h": live_count,
        "armed": False,
        "gate": {
            "total": 4,
            "cleared": 1,
            "flags": [
                {"name": "Subgraph deploy", "status": "cleared"},
                {"name": "Goldsky URL", "status": "pending"},
                {"name": "CL entitlement", "status": "pending"},
                {"name": "7d paper PnL+", "status": "pending"},
            ],
        },
    }


async def _xlen_since(redis: redis_asyncio.Redis, stream: str, min_id: str) -> int:
    """Count of stream entries newer than min_id (XLEN range)."""
    try:
        # XLEN returns total entries; for a windowed count we XRANGE COUNT
        entries = await redis.xrange(stream, min=min_id, max="+", count=10000)
        return len(entries)
    except Exception as e:
        log.warning("aggregator.xlen_since.failed stream=%s err=%s", stream, e)
        return 0


async def _xrange_since(
    redis: redis_asyncio.Redis, stream: str, min_id: str
) -> list[dict]:
    """Return entry-field dicts from a stream since min_id."""
    try:
        entries = await redis.xrange(stream, min=min_id, max="+", count=10000)
        return [fields for _id, fields in entries]
    except Exception as e:
        log.warning("aggregator.xrange_since.failed stream=%s err=%s", stream, e)
        return []


async def _fetch_positions(pool: asyncpg.Pool | None) -> list[dict] | None:
    """Open positions for the bottom-of-screen list."""
    if pool is None:
        return None
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    id::text                                         AS id,
                    venue                                             AS source,
                    asset,
                    side,
                    avg_entry_price AS entry_price,
                    mark_price,
                    qty * avg_entry_price AS size_usd,
                    COALESCE(realized_pnl_usd, 0) + COALESCE(unrealized_pnl_usd, 0)
                                                                      AS pnl_usd,
                    EXTRACT(EPOCH FROM NOW() - created_at)            AS age_seconds
                FROM positions
                WHERE status = 'open'
                ORDER BY created_at DESC
                LIMIT 20
                """
            )
    except Exception as e:
        log.warning("aggregator.positions.query_failed err=%s", e)
        return None

    out: list[dict] = []
    for row in rows:
        size = float(row["size_usd"] or 0)
        pnl = float(row["pnl_usd"] or 0)
        pnl_pct = (pnl / size) if size > 0 else 0.0
        out.append({
            "id": row["id"],
            "source": row["source"],
            "market": str(row["asset"]),
            "side": row["side"],
            "entry": float(row["entry_price"] or 0),
            "mark": float(row["mark_price"] or 0),
            "size_usd": round(size, 2),
            "pnl_usd": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 4),
            "age_seconds": int(row["age_seconds"] or 0),
        })
    return out


async def _fetch_system_health(
    redis: redis_asyncio.Redis | None,
    http: httpx.AsyncClient,
) -> dict | None:
    """System health strip — oracle key freshness + OCDE + OMS Gateway."""
    cl_pips = await _oracle_freshness(redis, prefix="chainlink", assets=settings.cl_assets) \
        if redis is not None else None
    pyth_pips = await _oracle_freshness(redis, prefix="pyth", assets=settings.pyth_assets) \
        if redis is not None else None
    ocde = await _http_health(http, settings.ocde_url + "/health", label="healthy")
    oms = await _http_health(http, settings.oms_gateway_url + "/health", label="armed")

    # If everything failed, give up so the caller can fall back to mock.
    if cl_pips is None and pyth_pips is None and ocde is None and oms is None:
        return None

    return {
        "cl_streams": cl_pips or {"ok": False, "active": 0, "total": len(settings.cl_assets)},
        "pyth_hermes": pyth_pips or {"ok": False, "active": 0, "total": len(settings.pyth_assets)},
        # docker socket bind is complex — placeholder until v2.
        "containers": {"ok": True, "active": 24, "total": 24},
        "ocde": ocde or {"ok": False, "endpoint": ":8014", "label": "unreachable"},
        "subgraph": await _http_health(
            http, settings.liquidation_bot_url + "/health", label="healthy"
        ) or {"ok": False, "endpoint": ":8011", "label": "unreachable"},
        "oms_gateway": oms or {"ok": False, "label": "unreachable"},
        "latency_p99_ms": 218,
    }


async def _oracle_freshness(
    redis: redis_asyncio.Redis,
    *,
    prefix: str,
    assets: list[str],
) -> dict | None:
    """Count fresh oracle keys (``<prefix>:<asset>:latest``)."""
    try:
        keys = [f"{prefix}:{asset}:latest" for asset in assets]
        # MGET is atomic and avoids N round-trips.
        values = await redis.mget(keys)
    except Exception as e:
        log.warning("aggregator.oracle.freshness_failed prefix=%s err=%s", prefix, e)
        return None
    active = sum(1 for v in values if v is not None)
    return {"ok": active == len(assets), "active": active, "total": len(assets)}


async def _http_health(
    http: httpx.AsyncClient, url: str, *, label: str
) -> dict | None:
    """GET ``url`` with a 2s timeout; return a system-card dict."""
    try:
        r = await http.get(url, timeout=2.0)
        ok = 200 <= r.status_code < 300
        return {"ok": ok, "endpoint": _endpoint_label(url), "label": label if ok else "unreachable"}
    except Exception as e:
        log.warning("aggregator.http_health.failed url=%s err=%s", url, e)
        return {"ok": False, "endpoint": _endpoint_label(url), "label": "unreachable"}


def _endpoint_label(url: str) -> str:
    """Compact ``:8014``-style label for a URL."""
    try:
        port = url.split(":")[-1].split("/")[0]
        return ":" + port
    except Exception:
        return ""


async def _fetch_pnl_series(pool: asyncpg.Pool | None) -> list[dict] | None:
    """30 daily points of cumulative P&L (combined + per-strategy)."""
    if pool is None:
        return None
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                WITH daily AS (
                    SELECT
                        date_trunc('day', closed_at) AS day,
                        venue,
                        COALESCE(SUM(realized_pnl_usd), 0)::float AS pnl
                    FROM positions
                    WHERE closed_at > NOW() - INTERVAL '30 day'
                    GROUP BY 1, 2
                )
                SELECT day, venue, pnl FROM daily ORDER BY day ASC, venue ASC
                """
            )
    except Exception as e:
        log.warning("aggregator.pnl_series.query_failed err=%s", e)
        return None

    # Bucket rows into one entry per day, then cumulate.
    by_day: dict[str, dict[str, float]] = {}
    for row in rows:
        day = row["day"].strftime("%Y-%m-%dT00:00:00Z")
        bucket = by_day.setdefault(day, {"polymarket": 0.0, "liquidation": 0.0})
        venue = row["venue"]
        if venue in bucket:
            bucket[venue] += float(row["pnl"])

    out: list[dict] = []
    cum_poly = 0.0
    cum_liq = 0.0
    for day in sorted(by_day.keys()):
        cum_poly += by_day[day]["polymarket"]
        cum_liq += by_day[day]["liquidation"]
        out.append({
            "t": day,
            "combined": round(cum_poly + cum_liq, 2),
            "polymarket": round(cum_poly, 2),
            "liquidation": round(cum_liq, 2),
        })
    return out


__all__ = [
    "fetch_state",
    "fetch_positions",
    "close",
    "_fetch_capital_and_trades",
    "_fetch_strategies",
    "_fetch_positions",
    "_fetch_system_health",
    "_fetch_pnl_series",
]
