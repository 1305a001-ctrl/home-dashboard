"""Kill switch tests — pure helpers, dry-run, and real Redis writes."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import kill_switch as ks
from app.settings import settings


# ─── Pure helpers ─────────────────────────────────────────────────────


def test_confirm_token_known_targets():
    assert ks.confirm_token_for("all") == "HALT"
    assert ks.confirm_token_for("polymarket") == "PAUSE-POLY"
    assert ks.confirm_token_for("liquidation") == "PAUSE-LIQ"


def test_confirm_token_unknown_returns_empty():
    assert ks.confirm_token_for("unknown") == ""


def test_is_confirm_valid_happy_path():
    assert ks.is_confirm_valid("all", "HALT") is True
    assert ks.is_confirm_valid("polymarket", "PAUSE-POLY") is True


def test_is_confirm_valid_wrong_token():
    assert ks.is_confirm_valid("all", "PAUSE-POLY") is False
    assert ks.is_confirm_valid("polymarket", "HALT") is False


def test_is_confirm_valid_empty():
    assert ks.is_confirm_valid("all", "") is False


def test_is_confirm_valid_unknown_target_default_deny():
    """Unknown target should NOT accept any confirmation token."""
    assert ks.is_confirm_valid("unknown", "HALT") is False
    assert ks.is_confirm_valid("", "HALT") is False


def test_make_log_id_format():
    lid = ks.make_log_id("master-kill")
    assert lid.startswith("audit-")
    assert "master-kill" in lid


def test_idempotent_token_lookup():
    """Calling confirm_token_for twice gives the same answer."""
    assert ks.confirm_token_for("all") == ks.confirm_token_for("all")


def test_halt_key_format():
    assert ks.halt_key("polymarket") == "system:halt:strategy:polymarket"
    assert ks.master_halt_key() == "system:halt"


# ─── Dry-run paths (no Redis writes) ─────────────────────────────────


@pytest.mark.asyncio
async def test_kill_all_dry_run():
    result = await ks.kill_all(dry_run=True)
    assert result.ok is True
    assert len(result.actions) == 2
    assert all(a.disarmed for a in result.actions)
    assert "audit-" in result.log_id


@pytest.mark.asyncio
async def test_kill_strategy_dry_run():
    result = await ks.kill_strategy("polymarket", dry_run=True)
    assert result.ok is True
    assert result.actions[0].strategy == "polymarket"


@pytest.mark.asyncio
async def test_pause_strategy_dry_run():
    result = await ks.pause_strategy("liquidation", dry_run=True)
    assert result.ok is True
    assert result.actions[0].disarmed is False   # pause is not disarm


# ─── Real Redis writes (against fakeredis) ───────────────────────────


@pytest.mark.asyncio
async def test_kill_all_writes_halt_keys(fake_redis):
    result = await ks.kill_all(dry_run=False)
    assert result.ok is True
    # Master halt key set
    assert await fake_redis.get("system:halt") is not None
    # Per-strategy halt keys set
    for strategy in settings.halt_strategies:
        assert await fake_redis.get(f"system:halt:strategy:{strategy}") is not None


@pytest.mark.asyncio
async def test_kill_all_emits_xadd_event(fake_redis):
    await ks.kill_all(dry_run=False)
    entries = await fake_redis.xrange("risk:strategy_halt_events", min="-", max="+")
    # One entry per strategy in halt_strategies
    assert len(entries) >= len(settings.halt_strategies)
    last_id, fields = entries[-1]
    assert "log_id" in fields
    assert fields["reason"] == "kill_all"


@pytest.mark.asyncio
async def test_kill_all_idempotent_second_call_noop(fake_redis):
    """Calling kill_all twice — second call records noop per-strategy."""
    r1 = await ks.kill_all(dry_run=False)
    r2 = await ks.kill_all(dry_run=False)
    assert r1.ok is True
    assert r2.ok is True
    assert any(a.noop for a in r2.actions)
    # The halt key value should still be r1's log_id (we don't overwrite).
    val = await fake_redis.get("system:halt:strategy:polymarket")
    assert val == r1.log_id


@pytest.mark.asyncio
async def test_kill_all_ttl_set(fake_redis):
    await ks.kill_all(dry_run=False)
    ttl = await fake_redis.ttl("system:halt:strategy:polymarket")
    # TTL between 1 day and the configured 7-day window
    assert ttl > 86400


@pytest.mark.asyncio
async def test_kill_strategy_writes_key(fake_redis):
    result = await ks.kill_strategy("polymarket", dry_run=False)
    assert result.ok is True
    assert await fake_redis.get("system:halt:strategy:polymarket") is not None
    # liquidation should not be halted
    assert await fake_redis.get("system:halt:strategy:liquidation") is None


@pytest.mark.asyncio
async def test_kill_strategy_idempotent(fake_redis):
    r1 = await ks.kill_strategy("polymarket", dry_run=False)
    r2 = await ks.kill_strategy("polymarket", dry_run=False)
    assert r1.ok and r2.ok
    assert r2.actions[0].noop is True


@pytest.mark.asyncio
async def test_pause_strategy_writes_key(fake_redis):
    result = await ks.pause_strategy("liquidation", dry_run=False)
    assert result.ok is True
    assert await fake_redis.get("system:halt:strategy:liquidation") is not None


@pytest.mark.asyncio
async def test_pause_strategy_xadd_uses_pause_reason(fake_redis):
    await ks.pause_strategy("liquidation", dry_run=False)
    entries = await fake_redis.xrange("risk:strategy_halt_events", min="-", max="+")
    assert any(fields["reason"] == "pause" for _id, fields in entries)


@pytest.mark.asyncio
async def test_kill_strategy_redis_unavailable(monkeypatch):
    """If Redis is None, kill_strategy returns ok=False with error."""
    ks._set_redis_for_test(None)
    # also make _get_redis a no-op so it returns None
    async def fake_get_redis():
        return None
    monkeypatch.setattr(ks, "_get_redis", fake_get_redis)
    result = await ks.kill_strategy("polymarket", dry_run=False)
    assert result.ok is False
    assert result.error == "redis_unavailable"


@pytest.mark.asyncio
async def test_kill_all_writes_audit_log(fake_redis, tmp_path):
    audit_file = Path(settings.audit_log_path)
    result = await ks.kill_all(dry_run=False)
    contents = audit_file.read_text().splitlines()
    assert len(contents) >= 1
    parsed = json.loads(contents[-1])
    assert parsed["action"] == "kill_all"
    assert parsed["target"] == "all"
    assert parsed["log_id"] == result.log_id


@pytest.mark.asyncio
async def test_kill_strategy_writes_audit_log(fake_redis):
    audit_file = Path(settings.audit_log_path)
    result = await ks.kill_strategy("polymarket", dry_run=False)
    contents = audit_file.read_text().splitlines()
    parsed = json.loads(contents[-1])
    assert parsed["action"] == "kill_strategy"
    assert parsed["target"] == "polymarket"
    assert parsed["log_id"] == result.log_id


@pytest.mark.asyncio
async def test_pause_strategy_writes_audit_log(fake_redis):
    audit_file = Path(settings.audit_log_path)
    await ks.pause_strategy("polymarket", dry_run=False)
    parsed = json.loads(audit_file.read_text().splitlines()[-1])
    assert parsed["action"] == "pause_strategy"
