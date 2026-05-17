"""Kill / pause logic.

Phase 1: writes audit-log entries + returns mock action results.
Phase 4: actually disarms strategies via Redis halt keys + OMS Gateway control channel.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from typing import Literal

from app.settings import settings

log = logging.getLogger(__name__)


KillKind = Literal["all", "strategy"]


@dataclass(frozen=True)
class KillAction:
    """Per-strategy outcome of a kill/pause."""
    strategy: str
    disarmed: bool
    cancelled_orders: int = 0
    flattened_positions: int = 0


@dataclass(frozen=True)
class KillResult:
    """Full response shape for /api/kill/*"""
    ok: bool
    ts: str
    actions: list[KillAction]
    log_id: str


# Expected confirmation tokens per endpoint. Must match the X-Confirm header.
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


def _audit_log_path() -> str:
    return settings.audit_log_path


def write_audit_log(*, log_id: str, action: str, target: str, payload: dict) -> None:
    """Write a single JSON line to the audit log. Best-effort — caller
    proceeds even if log write fails (log to stderr instead)."""
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
    """Pure-ish (uses time): audit log identifier."""
    ts_compact = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    return f"audit-{ts_compact}-{kind}"


async def kill_all(*, dry_run: bool = False) -> KillResult:
    """Disarm every strategy + cancel open orders + flatten paper book.

    Phase 1: dry-run only — no real disarm. Phase 4 wires the Redis
    halt key + OMS Gateway control channel calls.
    """
    log_id = make_log_id("master-kill")
    actions = [
        KillAction(
            strategy="polymarket",
            disarmed=True,
            cancelled_orders=12,
            flattened_positions=12,
        ),
        KillAction(
            strategy="liquidation",
            disarmed=True,
            cancelled_orders=0,
            flattened_positions=0,
        ),
    ]
    write_audit_log(
        log_id=log_id, action="kill_all", target="all",
        payload={"dry_run": dry_run, "actions": [asdict(a) for a in actions]},
    )
    log.info("kill_all log_id=%s dry_run=%s", log_id, dry_run)
    return KillResult(
        ok=True,
        ts=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        actions=actions,
        log_id=log_id,
    )


async def kill_strategy(strategy: str, *, dry_run: bool = False) -> KillResult:
    """Disarm one strategy. Phase 1: dry-run."""
    log_id = make_log_id(f"halt-{strategy}")
    action = KillAction(
        strategy=strategy,
        disarmed=True,
        cancelled_orders=0,
        flattened_positions=0,
    )
    write_audit_log(
        log_id=log_id, action="kill_strategy", target=strategy,
        payload={"dry_run": dry_run, "action": asdict(action)},
    )
    return KillResult(
        ok=True,
        ts=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        actions=[action],
        log_id=log_id,
    )


async def pause_strategy(strategy: str, *, dry_run: bool = False) -> KillResult:
    """Pause (temporary halt) for one strategy. Phase 1: dry-run."""
    log_id = make_log_id(f"pause-{strategy}")
    action = KillAction(strategy=strategy, disarmed=False)
    write_audit_log(
        log_id=log_id, action="pause_strategy", target=strategy,
        payload={"dry_run": dry_run},
    )
    return KillResult(
        ok=True,
        ts=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        actions=[action],
        log_id=log_id,
    )


__all__ = [
    "KillAction",
    "KillResult",
    "EXPECTED_CONFIRM",
    "confirm_token_for",
    "is_confirm_valid",
    "write_audit_log",
    "make_log_id",
    "kill_all",
    "kill_strategy",
    "pause_strategy",
]
