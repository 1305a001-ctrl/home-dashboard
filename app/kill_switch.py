"""Kill / pause logic.

Real implementation (Phase 4):

* ``SET system:halt:strategy:<slug>`` with a 7-day TTL marks the
  strategy as disarmed. Strategy runners read this key on each tick.
* ``SET system:halt`` is the master halt (kill-all).
* ``XADD risk:strategy_halt_events`` records the event for the
  risk-watcher to subscribe to.
* An audit log line is appended to ``settings.audit_log_path``.

Idempotency: if the halt key already exists, we log a no-op and return
the same shape — the audit log still records the call but flags it as
``noop=true``. Callers can safely retry.

If Redis is unreachable the function still returns ``ok=False`` with the
upstream error string, so the dashboard can surface the failure.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Literal

import redis.asyncio as redis_asyncio

from app.settings import settings

log = logging.getLogger(__name__)


KillKind = Literal["all", "strategy"]


# ─── Result shapes ───────────────────────────────────────────────────


@dataclass(frozen=True)
class KillAction:
    """Per-strategy outcome of a kill/pause."""
    strategy: str
    disarmed: bool
    cancelled_orders: int = 0
    flattened_positions: int = 0
    noop: bool = False


@dataclass(frozen=True)
class KillResult:
    """Full response shape for /api/kill/* and /api/pause/*."""
    ok: bool
    ts: str
    actions: list[KillAction] = field(default_factory=list)
    log_id: str = ""
    error: str | None = None


# ─── Confirmation tokens ─────────────────────────────────────────────


EXPECTED_CONFIRM: dict[str, str] = {
    "all":          "HALT",
    "polymarket":   "PAUSE-POLY",
    "liquidation":  "PAUSE-LIQ",
}


def confirm_token_for(target: str) -> str:
    """Pure: which X-Confirm header value to expect for this target."""
    return EXPECTED_CONFIRM.get(target, "")


def is_confirm_valid(target: str, header_value: str) -> bool:
    """Pure: is the X-Confirm header value the expected token?"""
    expected = confirm_token_for(target)
    if not expected:
        return False
    return header_value == expected


# ─── Audit log ───────────────────────────────────────────────────────


def _audit_log_path() -> str:
    return settings.audit_log_path


def write_audit_log(*, log_id: str, action: str, target: str, payload: dict) -> None:
    """Append a JSON line. Best-effort — log to stderr on write failure."""
    try:
        os.makedirs(os.path.dirname(_audit_log_path()), exist_ok=True)
        line = json.dumps({
            "log_id": log_id,
            "ts": time.time(),
            "action": action,
            "target": target,
            "payload": payload,
        }, sort_keys=True, separators=(",", ":"))
        with open(_audit_log_path(), "a") as f:
            f.write(line + "\n")
    except Exception as e:
        log.warning("audit.write_failed err=%s", e)


def make_log_id(kind: str) -> str:
    """Audit log identifier."""
    ts_compact = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    return f"audit-{ts_compact}-{kind}"


def halt_key(strategy: str) -> str:
    """Pure: the Redis key used to disarm one strategy."""
    return f"system:halt:strategy:{strategy}"


def master_halt_key() -> str:
    """Pure: the Redis key used to disarm everything."""
    return "system:halt"


# ─── Redis client ─────────────────────────────────────────────────────


_redis: redis_asyncio.Redis | None = None


async def _get_redis() -> redis_asyncio.Redis | None:
    """Lazy Redis client for kill-switch writes."""
    global _redis
    if _redis is not None:
        return _redis
    try:
        _redis = redis_asyncio.from_url(
            settings.redis_url, decode_responses=True, socket_timeout=2.0
        )
    except Exception as e:
        log.warning("kill_switch.redis_init_failed err=%s", e)
        _redis = None
    return _redis


def _set_redis_for_test(r: redis_asyncio.Redis | None) -> None:
    """Test hook — inject a fake redis client."""
    global _redis
    _redis = r


# ─── Halt writes ──────────────────────────────────────────────────────


async def _write_halt(
    redis: redis_asyncio.Redis,
    *,
    strategy: str,
    log_id: str,
    reason: str,
) -> tuple[bool, bool, str | None]:
    """Set the halt key + emit the event.

    Returns (ok, noop, error). ``noop=True`` means the halt key was
    already set, so we skipped the SET but still recorded the event.
    """
    key = halt_key(strategy)
    try:
        existing = await redis.get(key)
        noop = existing is not None
        if not noop:
            await redis.set(key, log_id, ex=settings.halt_key_ttl_sec)
        await redis.xadd(
            "risk:strategy_halt_events",
            {
                "strategy": strategy,
                "log_id": log_id,
                "reason": reason,
                "noop": "1" if noop else "0",
                "ts": str(time.time()),
            },
        )
        return True, noop, None
    except Exception as e:
        log.warning("kill_switch.write_halt.failed strategy=%s err=%s", strategy, e)
        return False, False, str(e)


async def _write_master_halt(
    redis: redis_asyncio.Redis, *, log_id: str
) -> tuple[bool, bool, str | None]:
    """Set the master halt key (used by kill_all)."""
    key = master_halt_key()
    try:
        existing = await redis.get(key)
        noop = existing is not None
        if not noop:
            await redis.set(key, log_id, ex=settings.halt_key_ttl_sec)
        return True, noop, None
    except Exception as e:
        log.warning("kill_switch.write_master_halt.failed err=%s", e)
        return False, False, str(e)


# ─── Public actions ──────────────────────────────────────────────────


async def kill_all(*, dry_run: bool = False) -> KillResult:
    """Disarm every strategy + master halt.

    Idempotent: if the halt keys already exist, returns ``ok=True`` with
    ``noop=True`` per-action.
    """
    log_id = make_log_id("master-kill")
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    redis = await _get_redis()

    if redis is None or dry_run:
        actions = [
            KillAction(strategy=s, disarmed=True, noop=False)
            for s in settings.halt_strategies
        ]
        write_audit_log(
            log_id=log_id, action="kill_all", target="all",
            payload={
                "dry_run": dry_run, "no_redis": redis is None,
                "actions": [asdict(a) for a in actions],
            },
        )
        return KillResult(
            ok=redis is not None or dry_run,
            ts=now, actions=actions, log_id=log_id,
            error=None if dry_run else ("redis_unavailable" if redis is None else None),
        )

    actions: list[KillAction] = []
    overall_ok = True
    last_err: str | None = None

    ok, noop, err = await _write_master_halt(redis, log_id=log_id)
    overall_ok = overall_ok and ok
    last_err = err or last_err

    for strategy in settings.halt_strategies:
        ok, noop, err = await _write_halt(
            redis, strategy=strategy, log_id=log_id, reason="kill_all"
        )
        actions.append(KillAction(
            strategy=strategy, disarmed=ok, noop=noop,
        ))
        overall_ok = overall_ok and ok
        if err is not None:
            last_err = err

    write_audit_log(
        log_id=log_id, action="kill_all", target="all",
        payload={
            "dry_run": False,
            "actions": [asdict(a) for a in actions],
            "ok": overall_ok,
        },
    )
    log.info("kill_all log_id=%s ok=%s", log_id, overall_ok)
    return KillResult(
        ok=overall_ok, ts=now, actions=actions, log_id=log_id,
        error=last_err,
    )


async def kill_strategy(strategy: str, *, dry_run: bool = False) -> KillResult:
    """Disarm one strategy."""
    log_id = make_log_id(f"halt-{strategy}")
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    redis = await _get_redis()

    if redis is None or dry_run:
        action = KillAction(strategy=strategy, disarmed=True)
        write_audit_log(
            log_id=log_id, action="kill_strategy", target=strategy,
            payload={"dry_run": dry_run, "no_redis": redis is None},
        )
        return KillResult(
            ok=redis is not None or dry_run,
            ts=now, actions=[action], log_id=log_id,
            error=None if dry_run else ("redis_unavailable" if redis is None else None),
        )

    ok, noop, err = await _write_halt(
        redis, strategy=strategy, log_id=log_id, reason="kill_strategy"
    )
    action = KillAction(strategy=strategy, disarmed=ok, noop=noop)
    write_audit_log(
        log_id=log_id, action="kill_strategy", target=strategy,
        payload={"action": asdict(action), "ok": ok, "noop": noop},
    )
    return KillResult(ok=ok, ts=now, actions=[action], log_id=log_id, error=err)


async def pause_strategy(strategy: str, *, dry_run: bool = False) -> KillResult:
    """Pause one strategy. Uses the same halt key + a ``pause`` reason."""
    log_id = make_log_id(f"pause-{strategy}")
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    redis = await _get_redis()

    if redis is None or dry_run:
        action = KillAction(strategy=strategy, disarmed=False)
        write_audit_log(
            log_id=log_id, action="pause_strategy", target=strategy,
            payload={"dry_run": dry_run, "no_redis": redis is None},
        )
        return KillResult(
            ok=redis is not None or dry_run,
            ts=now, actions=[action], log_id=log_id,
            error=None if dry_run else ("redis_unavailable" if redis is None else None),
        )

    ok, noop, err = await _write_halt(
        redis, strategy=strategy, log_id=log_id, reason="pause"
    )
    action = KillAction(strategy=strategy, disarmed=False, noop=noop)
    write_audit_log(
        log_id=log_id, action="pause_strategy", target=strategy,
        payload={"action": asdict(action), "ok": ok, "noop": noop},
    )
    return KillResult(ok=ok, ts=now, actions=[action], log_id=log_id, error=err)


__all__ = [
    "KillAction",
    "KillResult",
    "EXPECTED_CONFIRM",
    "confirm_token_for",
    "is_confirm_valid",
    "write_audit_log",
    "make_log_id",
    "halt_key",
    "master_halt_key",
    "kill_all",
    "kill_strategy",
    "pause_strategy",
]
