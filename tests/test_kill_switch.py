"""Kill switch tests — pure helpers + dry-run paths."""
from __future__ import annotations

import pytest

from app import kill_switch as ks


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


def test_idempotent_token_lookup():
    """Calling confirm_token_for twice gives the same answer."""
    a = ks.confirm_token_for("all")
    b = ks.confirm_token_for("all")
    assert a == b
