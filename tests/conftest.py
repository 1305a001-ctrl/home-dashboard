"""Shared test fixtures.

Two main hooks:

* ``redirect_audit_log`` (autouse) — routes the audit log into pytest's
  ``tmp_path`` so tests can't poke ``/var/log/home-dashboard``.
* ``fake_redis`` — a ``fakeredis.aioredis.FakeRedis`` instance with
  ``decode_responses=True`` (mirrors production wiring). Tests can opt
  in by depending on this fixture; the autouse ``inject_fake_redis``
  fixture wires it into both ``aggregator`` and ``kill_switch``.
"""
from __future__ import annotations

import asyncio

import fakeredis.aioredis
import pytest

from app import aggregator, kill_switch
from app.settings import settings


@pytest.fixture(autouse=True)
def redirect_audit_log(tmp_path, monkeypatch):
    """Send audit-log writes to a per-test tmp file."""
    audit_file = tmp_path / "audit.log"
    monkeypatch.setattr(settings, "audit_log_path", str(audit_file))
    yield audit_file


@pytest.fixture
async def fake_redis():
    """Async fakeredis client (decode_responses to match production)."""
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        yield r
    finally:
        await r.aclose()


@pytest.fixture(autouse=True)
async def inject_fake_redis(fake_redis):
    """Wire the fakeredis client into aggregator + kill_switch.

    This runs for every test so we never hit a real Redis from a unit
    test — even tests that don't ask for ``fake_redis`` explicitly.
    """
    # Reset the aggregator cache + module-level clients to a clean state.
    aggregator._CACHE["state"] = None
    aggregator._CACHE["ts"] = 0.0
    aggregator._redis = fake_redis
    aggregator._pg_pool = None
    aggregator._http = None
    kill_switch._set_redis_for_test(fake_redis)
    yield
    aggregator._redis = None
    aggregator._pg_pool = None
    aggregator._http = None
    kill_switch._set_redis_for_test(None)
