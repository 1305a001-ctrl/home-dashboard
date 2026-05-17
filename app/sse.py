"""Server-Sent Events activity feed.

Phase 1: emits one synthetic event every 5 seconds so the frontend can
verify reconnection + rendering.
Phase 3: subscribes to Redis pub/sub channels (exec.* / sig.* /
warn.* / err.*) and surfaces real upstream events.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

from app import mocks

log = logging.getLogger(__name__)


def format_sse(event: str, data: dict) -> str:
    """Pure: render a single SSE message text block."""
    body = json.dumps(data, separators=(",", ":"))
    return f"event: {event}\ndata: {body}\n\n"


async def mock_stream() -> AsyncGenerator[str, None]:
    """Async generator: emit one mock event every 5s. Used in Phase 1."""
    while True:
        event = mocks.mock_activity_event()
        yield format_sse("activity", event)
        await asyncio.sleep(5.0)


__all__ = ["format_sse", "mock_stream"]
