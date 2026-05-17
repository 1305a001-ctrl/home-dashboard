"""Mock state generators — used when upstream services are unreachable.

The shape MUST match what /api/state returns, so the frontend can be
developed against mocks without any upstream dependency.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def mock_state() -> dict:
    """Full /api/state response matching the spec."""
    return {
        "ts": now_iso(),
        "mock": True,
        "capital": {
            "working_capital_usd": 4247.83,
            "open_notional_usd": 1847.00,
            "reserved_gas_usd": 200.00,
            "pnl_24h_usd": 284.31,
            "pnl_7d_usd": 1247.16,
            "pnl_30d_usd": -8447.00,
            "pnl_alltime_usd": -8201.42,
        },
        "trades": {
            "total": 247, "wins": 144, "losses": 103, "win_rate": 0.583,
            "today": {"trades": 31, "wins": 19, "losses": 12, "win_rate": 0.613},
        },
        "strategies": [
            {
                "id": "polymarket",
                "name": "Polymarket Compound",
                "mode": "paper",
                "status": "live-ready",
                "capital_usd": 1847.00,
                "pct_of_book": 0.435,
                "open_positions": 12,
                "closed_today": 28,
                "pnl_24h_usd": 284.31,
                "win_rate_24h": 0.613,
                "wins_24h": 19,
                "losses_24h": 12,
                "armed": True,
            },
            {
                "id": "liquidation",
                "name": "Liquidation Bot",
                "mode": "paper",
                "status": "gated",
                "capital_usd": 200.00,
                "evaluations_24h": 1247,
                "detected_liquidatable_24h": 8,
                "would_execute_24h": 3,
                "actually_executed_24h": 0,
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
            },
        ],
        "pnl_series_30d": _mock_pnl_series(),
        "positions": [
            {
                "id": "poly-9281",
                "source": "polymarket",
                "market": "BTC ≥ $104k @ 15:00",
                "side": "YES",
                "entry": 0.523, "mark": 0.612,
                "size_usd": 250.00, "pnl_usd": 42.51, "pnl_pct": 0.170,
                "age_seconds": 1380,
            },
            {
                "id": "poly-9282",
                "source": "polymarket",
                "market": "ETH ≥ $3.85k @ 15:00",
                "side": "NO",
                "entry": 0.482, "mark": 0.446,
                "size_usd": 200.00, "pnl_usd": -14.83, "pnl_pct": -0.074,
                "age_seconds": 660,
            },
            {
                "id": "poly-9283",
                "source": "polymarket",
                "market": "BTC ≥ $105k @ 16:00",
                "side": "YES",
                "entry": 0.401, "mark": 0.502,
                "size_usd": 150.00, "pnl_usd": 37.71, "pnl_pct": 0.251,
                "age_seconds": 180,
            },
            {
                "id": "poly-9284",
                "source": "polymarket",
                "market": "HYPE ≥ $43 @ 17:00",
                "side": "YES",
                "entry": 0.612, "mark": 0.640,
                "size_usd": 220.00, "pnl_usd": 9.88, "pnl_pct": 0.045,
                "age_seconds": 1020,
            },
            {
                "id": "liq-paper-0014",
                "source": "liquidation",
                "market": "aWETH / Morpho · paper",
                "side": "L1",
                "entry": 2184.00, "mark": 2247.00,
                "size_usd": 3184.00, "pnl_usd": 94.21, "pnl_pct": 0.0296,
                "age_seconds": 3600,
                "paper": True,
            },
        ],
        "recommendations": [
            {
                "priority": 1,
                "title": "Push poly-adapter Maker Mode",
                "body": "4 modified files + 4 test files unblock Phase 4 Maker Mode. Dispatcher already registered.",
                "action_label": "git push origin maker-mode",
                "action_kind": "shell",
            },
            {
                "priority": 2,
                "title": "Email Jonah · entitlement",
                "body": "Request USDC, USDT, WSTETH feeds — gates LIVE_ENABLED for liquidation.",
                "action_label": "Open mail",
                "action_kind": "mailto",
            },
            {
                "priority": 3,
                "title": "Goldsky deploy + restart",
                "body": "Run goldsky subgraph deploy → paste URL into /srv/secrets/liquidation-bot.env.",
                "action_label": "ssh ai-primary",
                "action_kind": "ssh",
            },
        ],
        "system": {
            "cl_streams":  {"ok": True,  "active": 7,  "total": 7},
            "pyth_hermes": {"ok": True,  "active": 31, "total": 31},
            "containers":  {"ok": True,  "active": 24, "total": 24},
            "ocde":        {"ok": True,  "endpoint": ":8014", "label": "healthy"},
            "subgraph":    {"ok": False, "label": "pending"},
            "oms_gateway": {"ok": True,  "label": "armed"},
            "latency_p99_ms": 218,
        },
    }


def _mock_pnl_series() -> list[dict]:
    """30 daily points for the chart."""
    import datetime as _dt
    series = []
    today = _dt.date.today()
    cum_poly = 0.0
    cum_liq = 0.0
    # synthetic curve: dip then recover
    daily_pnl = [
        -120, -80, -200, -50, -150, -100, 30, -40, 80, 110,
        -200, -300, -150, 60, 120, 90, 40, -30, 80, 140,
        180, 50, 110, 90, 130, 200, 80, 250, 180, 284,
    ]
    for i, pnl in enumerate(daily_pnl):
        d = today - _dt.timedelta(days=29 - i)
        cum_poly += pnl
        # liquidation lane stays near flat (paper mode)
        cum_liq += 0 if i < 25 else 3
        series.append({
            "t": f"{d.isoformat()}T00:00:00Z",
            "combined": round(cum_poly + cum_liq, 2),
            "polymarket": round(cum_poly, 2),
            "liquidation": round(cum_liq, 2),
        })
    return series


def mock_activity_event() -> dict:
    """One synthetic event for /api/stream when no upstream events flow."""
    return {
        "ts": now_iso(),
        "kind": "sig",
        "strategy": "polymarket",
        "message": "evaluated BTC≥104k YES @ 0.523 (mock)",
    }


__all__ = ["mock_state", "mock_activity_event", "now_iso"]
