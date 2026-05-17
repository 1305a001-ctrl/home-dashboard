"""Pull state from upstream services + assemble /api/state response.

Phase 1: returns mock data only. Each upstream-pull function returns
None on failure, and the caller falls back to the mock shape.

Phase 2: wire to Redis (oracle prices), Postgres (positions),
container health (docker socket), strategy-runners + liquidation-bot
/internal/state endpoints.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from app import mocks
from app.settings import settings

log = logging.getLogger(__name__)


# Simple in-process cache to absorb the 2-second polling burst from
# multiple clients without re-aggregating every cycle.
_CACHE: dict[str, Any] = {"ts": 0.0, "state": None}


async def fetch_state() -> dict:
    """Async: return the full /api/state payload.

    Caches for `state_cache_ttl_sec` seconds so /api/state under load
    only triggers one upstream aggregation per cache window.
    """
    now = time.time()
    if _CACHE["state"] is not None and (now - _CACHE["ts"]) < settings.state_cache_ttl_sec:
        return _CACHE["state"]

    state = await _aggregate()
    _CACHE["ts"] = now
    _CACHE["state"] = state
    return state


async def _aggregate() -> dict:
    """Pull from each upstream + assemble. Phase 1 returns mocks."""
    # Phase 2 fills these in; for now we return the full mock shape so
    # the frontend renders identically against fake or real data.
    state = mocks.mock_state()

    # Phase 2 hook: capital + trades from Postgres
    # capital = await _fetch_capital_from_postgres()
    # if capital is not None: state["capital"] = capital

    # Phase 2 hook: strategies from strategy-runners + liquidation-bot
    # strats = await _fetch_strategies()
    # if strats is not None: state["strategies"] = strats

    # Phase 2 hook: system health from docker socket + Redis health pings
    # system = await _fetch_system_health()
    # if system is not None: state["system"] = system

    return state


async def fetch_positions() -> list[dict]:
    """Detailed positions list. Phase 1 returns the mock subset."""
    state = await fetch_state()
    return state.get("positions", [])


__all__ = ["fetch_state", "fetch_positions"]
