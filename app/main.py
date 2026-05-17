"""FastAPI app — home-dashboard endpoints.

Routes:
  GET  /                       static index.html
  GET  /api/state              full snapshot for the dashboard
  GET  /api/positions          detailed positions list
  POST /api/kill/all           master halt (requires X-Confirm: HALT)
  POST /api/kill/<strategy>    per-strategy halt
  POST /api/pause/<strategy>   per-strategy pause
  GET  /api/stream             SSE activity feed
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import structlog
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app import aggregator, kill_switch, sse
from app.settings import settings

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ]
)
log = structlog.get_logger(__name__)


app = FastAPI(title="home-dashboard", version="0.1.0")


STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ─── Root: serve dashboard.html ───────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def root() -> str:
    """Serve the single-page dashboard."""
    return (STATIC_DIR / "index.html").read_text()


# ─── State endpoints ──────────────────────────────────────────────────


@app.get("/api/state")
async def api_state() -> dict[str, Any]:
    return await aggregator.fetch_state()


@app.get("/api/positions")
async def api_positions() -> dict[str, Any]:
    positions = await aggregator.fetch_positions()
    return {"count": len(positions), "positions": positions}


@app.get("/api/health")
async def api_health() -> dict[str, Any]:
    return {"ok": True, "version": "0.1.0"}


# ─── Kill / pause endpoints ───────────────────────────────────────────


@app.post("/api/kill/all")
async def api_kill_all(
    x_confirm: str = Header("", alias="X-Confirm"),
) -> dict[str, Any]:
    if not kill_switch.is_confirm_valid("all", x_confirm):
        raise HTTPException(
            status_code=400,
            detail=f"X-Confirm header required (expected '{kill_switch.confirm_token_for('all')}')",
        )
    result = await kill_switch.kill_all(dry_run=False)
    return {
        "ok": result.ok,
        "ts": result.ts,
        "actions": [a.__dict__ for a in result.actions],
        "log_id": result.log_id,
    }


@app.post("/api/kill/{strategy}")
async def api_kill_strategy(
    strategy: str,
    x_confirm: str = Header("", alias="X-Confirm"),
) -> dict[str, Any]:
    if not kill_switch.is_confirm_valid(strategy, x_confirm):
        raise HTTPException(
            status_code=400,
            detail=f"X-Confirm header required (expected '{kill_switch.confirm_token_for(strategy)}')",
        )
    result = await kill_switch.kill_strategy(strategy, dry_run=False)
    return {
        "ok": result.ok,
        "ts": result.ts,
        "actions": [a.__dict__ for a in result.actions],
        "log_id": result.log_id,
    }


@app.post("/api/pause/{strategy}")
async def api_pause_strategy(
    strategy: str,
    x_confirm: str = Header("", alias="X-Confirm"),
) -> dict[str, Any]:
    if not kill_switch.is_confirm_valid(strategy, x_confirm):
        raise HTTPException(
            status_code=400,
            detail=f"X-Confirm header required (expected '{kill_switch.confirm_token_for(strategy)}')",
        )
    result = await kill_switch.pause_strategy(strategy, dry_run=False)
    return {
        "ok": result.ok,
        "ts": result.ts,
        "actions": [a.__dict__ for a in result.actions],
        "log_id": result.log_id,
    }


# ─── SSE activity feed ────────────────────────────────────────────────


@app.get("/api/stream")
async def api_stream() -> StreamingResponse:
    """Phase 1: mock stream (one event every 5s)."""
    return StreamingResponse(
        sse.mock_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # nginx/caddy: don't buffer
            "Connection": "keep-alive",
        },
    )


# ─── Entrypoint ───────────────────────────────────────────────────────


def main() -> None:
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.http_host,
        port=settings.http_port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
