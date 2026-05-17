"""SSE tests — format helpers, parsing, buffer policy, and a small
end-to-end loop against fakeredis pub/sub."""
from __future__ import annotations

import asyncio
import json

import pytest

from app import sse


# ─── Pure helpers ─────────────────────────────────────────────────────


def test_format_sse_basic():
    out = sse.format_sse("activity", {"a": 1})
    assert out.startswith("event: activity\n")
    assert "data:" in out
    assert out.endswith("\n\n")


def test_format_sse_uses_compact_json():
    out = sse.format_sse("activity", {"a": 1, "b": "x"})
    # compact json: no spaces between separators
    assert '"a":1' in out
    assert ", " not in out  # no separator spaces


def test_classify_kind_known_prefixes():
    assert sse.classify_kind("exec.poly.fill") == "exec"
    assert sse.classify_kind("sig.btc.entry") == "sig"
    assert sse.classify_kind("warn.risk.dd") == "warn"
    assert sse.classify_kind("err.adapter.binance") == "err"


def test_classify_kind_default():
    assert sse.classify_kind("unknown.channel") == "sig"
    assert sse.classify_kind("") == "sig"


def test_parse_pubsub_message_string_payload():
    out = sse.parse_pubsub_message("sig.poly", "hello world")
    assert out["kind"] == "sig"
    assert out["message"] == "hello world"
    assert out["strategy"] == ""
    assert "ts" in out


def test_parse_pubsub_message_json_payload():
    payload = json.dumps({
        "strategy": "polymarket",
        "message": "entered BTC>=104k",
        "kind": "exec",
    })
    out = sse.parse_pubsub_message("exec.poly", payload)
    assert out["strategy"] == "polymarket"
    assert out["message"] == "entered BTC>=104k"
    assert out["kind"] == "exec"


def test_parse_pubsub_message_json_payload_fallback_kind():
    """If JSON omits 'kind', fall back to channel prefix."""
    payload = json.dumps({"strategy": "liquidation", "message": "ping"})
    out = sse.parse_pubsub_message("warn.liq", payload)
    assert out["kind"] == "warn"


def test_parse_pubsub_message_bad_json_keeps_raw():
    out = sse.parse_pubsub_message("sig.x", "{bad json")
    assert out["message"] == "{bad json"


def test_parse_pubsub_message_alt_keys():
    """Accepts ``strat`` / ``msg`` aliases."""
    payload = json.dumps({"strat": "polymarket", "msg": "alt-keys"})
    out = sse.parse_pubsub_message("sig.x", payload)
    assert out["strategy"] == "polymarket"
    assert out["message"] == "alt-keys"


# ─── Bounded buffer policy ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bounded_buffer_drops_oldest_on_overflow():
    buf = sse.BoundedActivityBuffer(maxsize=3)
    for i in range(5):
        buf.put_nowait({"i": i})
    # qsize is capped at maxsize
    assert buf.qsize() == 3
    items = [await buf.get() for _ in range(3)]
    # We kept the most-recent three: 2, 3, 4
    assert [it["i"] for it in items] == [2, 3, 4]


@pytest.mark.asyncio
async def test_bounded_buffer_normal_flow():
    buf = sse.BoundedActivityBuffer(maxsize=5)
    buf.put_nowait({"i": 1})
    buf.put_nowait({"i": 2})
    assert (await buf.get())["i"] == 1
    assert (await buf.get())["i"] == 2


@pytest.mark.asyncio
async def test_bounded_buffer_min_size_one():
    """maxsize=0 should be coerced to at least 1."""
    buf = sse.BoundedActivityBuffer(maxsize=0)
    buf.put_nowait({"i": 1})
    assert buf.qsize() == 1


# ─── Stream wiring against fakeredis ──────────────────────────────────


@pytest.mark.asyncio
async def test_activity_stream_no_redis_falls_back_to_mock():
    """When redis is None, activity_stream yields mock events."""
    gen = sse.activity_stream(None)
    first = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
    assert first.startswith("event: activity\n")
    assert "data:" in first
    await gen.aclose()


@pytest.mark.asyncio
async def test_activity_stream_real_stream_emits_keepalive(fake_redis):
    """First yield is the 'stream connected' keepalive."""
    gen = sse.activity_stream(fake_redis)
    first = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
    assert "stream connected" in first
    await gen.aclose()


@pytest.mark.asyncio
async def test_real_stream_picks_up_publish(fake_redis):
    """Publish to a matching channel and verify the stream yields it."""
    gen = sse.real_stream(fake_redis, channels=["exec.*"], buffer_size=10)
    # consume the initial keepalive
    await asyncio.wait_for(gen.__anext__(), timeout=2.0)

    # Give the subscriber a moment to attach, then publish.
    await asyncio.sleep(0.05)
    await fake_redis.publish(
        "exec.poly.fill",
        json.dumps({"strategy": "polymarket", "message": "filled"}),
    )

    second = await asyncio.wait_for(gen.__anext__(), timeout=3.0)
    assert "filled" in second
    assert "polymarket" in second
    await gen.aclose()
