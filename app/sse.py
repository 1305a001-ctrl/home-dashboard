"""Server-Sent Events activity feed.

Subscribes to Redis pub/sub channels (``exec.*``, ``sig.*``, ``warn.*``,
``err.*``) via ``PSUBSCRIBE``. Each upstream pub-sub message is
normalised to ``{ts, kind, strategy, message}`` and emitted as one SSE
``event: activity`` block.

A bounded asyncio queue (max 50) holds messages between the Redis task
and the SSE generator — when the queue fills, the oldest entry is
dropped so we keep the most-recent traffic.

If Redis is unreachable, ``activity_stream`` falls back to a mock
generator so the dashboard keeps rendering.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator
from datetime import datetime, timezone

import redis.asyncio as redis_asyncio

from app import mocks
from app.settings import settings

log = logging.getLogger(__name__)

# Kind classification from the Redis channel prefix.
_KIND_PREFIXES: tuple[tuple[str, str], ...] = (
    ("exec", "exec"),
    ("sig", "sig"),
    ("warn", "warn"),
    ("err", "err"),
)


def format_sse(event: str, data: dict) -> str:
    """Pure: render a single SSE message text block."""
    body = json.dumps(data, separators=(",", ":"))
    return f"event: {event}\ndata: {body}\n\n"


def classify_kind(channel: str) -> str:
    """Pure: map a Redis channel like ``sig.poly.entry`` → ``sig``."""
    head = channel.split(".", 1)[0].split(":", 1)[0]
    for prefix, kind in _KIND_PREFIXES:
        if head == prefix:
            return kind
    return "sig"


def parse_pubsub_message(channel: str, payload: str) -> dict:
    """Normalise a raw Redis pub/sub payload → SSE-friendly dict.

    Accepts JSON or plain string payloads. Always returns a dict with
    keys ``ts``, ``kind``, ``strategy``, ``message``.
    """
    kind = classify_kind(channel)
    strategy = ""
    message = payload
    parsed: dict | None = None
    try:
        parsed = json.loads(payload)
    except (ValueError, TypeError):
        parsed = None

    if isinstance(parsed, dict):
        strategy = str(parsed.get("strategy", "") or parsed.get("strat", ""))
        message = str(parsed.get("message", "") or parsed.get("msg", "") or payload)
        kind = str(parsed.get("kind", kind))
    return {
        "ts": _iso_now(),
        "kind": kind,
        "strategy": strategy,
        "message": message,
    }


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class BoundedActivityBuffer:
    """An asyncio.Queue with a 'drop oldest on overflow' policy."""

    def __init__(self, maxsize: int) -> None:
        self.maxsize = max(1, maxsize)
        self._q: asyncio.Queue[dict] = asyncio.Queue(maxsize=self.maxsize)

    def put_nowait(self, item: dict) -> None:
        if self._q.full():
            try:
                self._q.get_nowait()
            except asyncio.QueueEmpty:
                pass
        self._q.put_nowait(item)

    async def get(self) -> dict:
        return await self._q.get()

    def qsize(self) -> int:
        return self._q.qsize()


async def _psubscribe_loop(
    redis: redis_asyncio.Redis,
    buffer: BoundedActivityBuffer,
    channels: list[str],
    stop: asyncio.Event,
) -> None:
    """Pump Redis PSUBSCRIBE messages into the bounded buffer."""
    pubsub = redis.pubsub()
    try:
        await pubsub.psubscribe(*channels)
        log.info("sse.psubscribe channels=%s", channels)
        while not stop.is_set():
            msg = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=1.0
            )
            if msg is None:
                continue
            channel = _decode(msg.get("channel"))
            payload = _decode(msg.get("data"))
            buffer.put_nowait(parse_pubsub_message(channel, payload))
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.warning("sse.psubscribe_failed err=%s", e)
    finally:
        try:
            await pubsub.punsubscribe()
            await pubsub.aclose()
        except Exception:
            pass


def _decode(value: object) -> str:
    """Decode bytes/str safely."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", errors="replace")
        except Exception:
            return ""
    return str(value)


async def real_stream(
    redis: redis_asyncio.Redis,
    *,
    channels: list[str] | None = None,
    buffer_size: int | None = None,
) -> AsyncGenerator[str, None]:
    """Yield SSE blocks for each Redis pub/sub message we receive."""
    ch = channels or settings.sse_channels
    buf = BoundedActivityBuffer(buffer_size or settings.sse_buffer_size)
    stop = asyncio.Event()
    task = asyncio.create_task(_psubscribe_loop(redis, buf, ch, stop))
    try:
        # Emit one keep-alive immediately so EventSource fires `onopen`.
        yield format_sse(
            "activity",
            {"ts": _iso_now(), "kind": "sig", "strategy": "", "message": "stream connected"},
        )
        while True:
            item = await buf.get()
            yield format_sse("activity", item)
    except asyncio.CancelledError:
        raise
    finally:
        stop.set()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


async def mock_stream() -> AsyncGenerator[str, None]:
    """Mock fallback: one synthetic event every 5s."""
    while True:
        event = mocks.mock_activity_event()
        yield format_sse("activity", event)
        await asyncio.sleep(5.0)


async def activity_stream(
    redis: redis_asyncio.Redis | None = None,
) -> AsyncGenerator[str, None]:
    """Top-level entry: real stream if Redis is reachable, mock otherwise."""
    if redis is None:
        log.warning("sse.activity_stream.no_redis_fallback_mock")
        async for chunk in mock_stream():
            yield chunk
        return

    # Probe Redis with a 1s ping before opening pubsub — fail-OPEN.
    try:
        await asyncio.wait_for(redis.ping(), timeout=1.0)
    except Exception as e:
        log.warning("sse.activity_stream.redis_ping_failed err=%s fallback_mock", e)
        async for chunk in mock_stream():
            yield chunk
        return

    async for chunk in real_stream(redis):
        yield chunk


__all__ = [
    "format_sse",
    "classify_kind",
    "parse_pubsub_message",
    "BoundedActivityBuffer",
    "mock_stream",
    "real_stream",
    "activity_stream",
]
