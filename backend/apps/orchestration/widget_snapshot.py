"""Pure builder for the flat ``GET /api/v1/stats/widget`` document.

Everything here takes plain dicts and returns the JSON body, so every selection
rule (what counts as an active check, which one is primary, what "next" is) is
testable without Flask or any service. The handler in ``apps.api.widget_handlers``
only gathers the inputs.

Input contracts
---------------
``automation`` (or ``None``): ``thread_alive``, ``state``, ``active``, ``stage``,
``stage_label``, ``progress`` (``current``/``total``/``percent``), ``started_at``,
``forced_period_id`` and ``periods`` (``[{"id", "name"}]``).

``event_checks`` (or ``None``): ``entries`` - one dict per check that owns a queue
slot or a direct thread, each with ``source``, ``scheduled_event_id``,
``channel_id``, ``program_name``, ``event_name``, ``team_name``,
``channel_name`` and ``started_at`` - plus ``progress_by_channel`` and
``stale_after_seconds``.

``last_check``: the newest register record or ``None``.
``next_candidates``: ``[{"type", "name", "channel", "at"}]``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

SCHEMA_VERSION = 1
MAX_TEXT_LENGTH = 80
NEXT_CHECK_TOLERANCE_SECONDS = 90

# Same order as AutomatedStreamManager.RUN_STAGES.
RUN_STAGE_ORDER: Tuple[str, ...] = (
    "settings",
    "period_discovery",
    "m3u_refresh",
    "cache_sync",
    "stream_matching",
    "quality_queueing",
    "quality_checking",
    "finalizing",
)
# Stages that only exist once period discovery has found work. The stages
# before them (starting/settings/period_discovery) are passed through by every
# scheduler tick, due or not.
WORK_STAGES = frozenset(
    {
        "m3u_refresh",
        "cache_sync",
        "stream_matching",
        "quality_queueing",
        "quality_checking",
        "finalizing",
    }
)
# automation first, then preflight, then scheduled events.
TYPE_PRIORITY = {"automation": 0, "preflight": 1, "scheduled_event": 2}

_EMPTY_ACTIVE = {
    "type": None,
    "name": None,
    "channel_name": None,
    "stage": None,
    "step": None,
    "steps": None,
    "current": None,
    "total": None,
    "percent": None,
    "started_at": None,
    "started_epoch": None,
}


def to_local_aware(value: Any) -> Optional[datetime]:
    """Normalise naive-local, aware or epoch values to a local-offset datetime."""
    if value is None or value == "":
        return None
    try:
        if isinstance(value, datetime):
            return value.astimezone()
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value)).astimezone()
        text = str(value).strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text).astimezone()
    except (OverflowError, OSError, TypeError, ValueError):
        return None


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat(timespec="seconds") if value else None


def _epoch(value: Optional[datetime]) -> Optional[int]:
    return int(value.timestamp()) if value else None


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > MAX_TEXT_LENGTH:
        return text[: MAX_TEXT_LENGTH - 1] + "…"
    return text


def _int_or_none(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


# ---------------------------------------------------------------------------
# Active checks
# ---------------------------------------------------------------------------

def period_display_name(periods: Any, forced_period_id: Any = None) -> str:
    """Name an automation run: the forced period, else the first period plus ``+N``."""
    periods = [p for p in (periods or []) if isinstance(p, dict) and p.get("name")]
    forced_id = forced_period_id
    if forced_id is not None:
        for period in periods:
            if str(period.get("id")) == str(forced_id):
                return str(period["name"])
    names: List[str] = []
    for period in periods:
        if period["name"] not in names:
            names.append(period["name"])
    if not names:
        return "Automation"
    if len(names) == 1:
        return names[0]
    return f"{names[0]} +{len(names) - 1}"


def _automation_name(automation: Dict[str, Any]) -> str:
    return period_display_name(automation.get("periods"), automation.get("forced_period_id"))


def _automation_active(automation: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Return the active-check block for a real automation run, else None."""
    if not isinstance(automation, dict):
        return None
    stage = automation.get("stage")
    if not (
        automation.get("thread_alive")
        and automation.get("state") == "running"
        and automation.get("active") is True
        and stage in WORK_STAGES
    ):
        return None

    progress = _as_dict(automation.get("progress"))
    total = _int_or_none(progress.get("total"))
    known = total is not None
    started = to_local_aware(automation.get("started_at"))
    return {
        "type": "automation",
        "name": _text(_automation_name(automation)),
        "channel_name": None,
        "stage": _text(automation.get("stage_label") or stage),
        "step": RUN_STAGE_ORDER.index(stage) + 1,
        "steps": len(RUN_STAGE_ORDER),
        "current": _int_or_none(progress.get("current")) if known else None,
        "total": total,
        "percent": _int_or_none(progress.get("percent")) if known else None,
        "started_at": _iso(started),
        "started_epoch": _epoch(started),
        "_started": started,
    }


def _classify_entry(entry: Dict[str, Any]) -> Optional[str]:
    if entry.get("source") == "teamarr_preflight":
        return "preflight"
    if entry.get("scheduled_event_id") is not None or entry.get("source") == "auto_create":
        return "scheduled_event"
    return None


def _event_active(
    entry: Dict[str, Any],
    check_type: str,
    progress_by_channel: Dict[Any, Any],
    stale_after_seconds: Optional[float],
    now: datetime,
) -> Dict[str, Any]:
    if check_type == "preflight":
        name = entry.get("event_name") or entry.get("team_name") or entry.get("program_name")
    else:
        name = entry.get("program_name")

    progress = _as_dict(progress_by_channel.get(entry.get("channel_id")))
    stage: Optional[str] = "Checking"
    current = total = percent = None
    if progress and not progress.get("stale"):
        stamp = to_local_aware(progress.get("timestamp"))
        fresh = stamp is not None and (
            stale_after_seconds is None or (now - stamp).total_seconds() <= stale_after_seconds
        )
        if fresh:
            stage = _text(progress.get("step") or progress.get("status")) or "Checking"
            current = _int_or_none(progress.get("current_stream"))
            total = _int_or_none(progress.get("total_streams"))
            percent = _int_or_none(progress.get("percentage"))

    started = to_local_aware(entry.get("started_at"))
    return {
        "type": check_type,
        "name": _text(name),
        "channel_name": _text(entry.get("channel_name")),
        "stage": stage,
        "step": None,
        "steps": None,
        "current": current,
        "total": total,
        "percent": percent,
        "started_at": _iso(started),
        "started_epoch": _epoch(started),
        "_started": started,
    }


def _event_actives(event_checks: Optional[Dict[str, Any]], now: datetime) -> List[Dict[str, Any]]:
    if not isinstance(event_checks, dict):
        return []
    progress_by_channel = _as_dict(event_checks.get("progress_by_channel"))
    stale_after = event_checks.get("stale_after_seconds")
    seen = set()
    actives: List[Dict[str, Any]] = []
    for entry in event_checks.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        check_type = _classify_entry(entry)
        if check_type is None:
            continue
        identity = (check_type, entry.get("channel_id"))
        if entry.get("channel_id") is not None:
            if identity in seen:
                continue
            seen.add(identity)
        actives.append(_event_active(entry, check_type, progress_by_channel, stale_after, now))
    return actives


def _primary(actives: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not actives:
        return None

    def sort_key(item: Dict[str, Any]):
        started = item.get("_started")
        return (
            TYPE_PRIORITY.get(item["type"], 99),
            started.timestamp() if started else float("inf"),
        )

    return min(actives, key=sort_key)


# ---------------------------------------------------------------------------
# Last / next
# ---------------------------------------------------------------------------

def _last_check_block(last_check: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    block = {
        "last_check_type": None,
        "last_check_name": None,
        "last_check_channel": None,
        "last_check_at": None,
        "last_check_epoch": None,
        "last_check_result": None,
        "last_check_duration_seconds": None,
    }
    if not isinstance(last_check, dict):
        return block
    finished = to_local_aware(last_check.get("finished_at"))
    block.update(
        {
            "last_check_type": last_check.get("type"),
            "last_check_name": _text(last_check.get("name")),
            "last_check_channel": _text(last_check.get("channel")),
            "last_check_at": _iso(finished),
            "last_check_epoch": _epoch(finished),
            "last_check_result": last_check.get("result"),
            "last_check_duration_seconds": _int_or_none(last_check.get("duration_seconds")),
        }
    )
    return block


def _next_check_block(candidates: Iterable[Dict[str, Any]], now: datetime) -> Dict[str, Any]:
    block = {
        "next_check_type": None,
        "next_check_name": None,
        "next_check_channel": None,
        "next_check_at": None,
        "next_check_epoch": None,
    }
    best: Optional[Tuple[Tuple[float, int], Dict[str, Any], datetime]] = None
    for candidate in candidates or []:
        if not isinstance(candidate, dict) or candidate.get("type") not in TYPE_PRIORITY:
            continue
        at = to_local_aware(candidate.get("at"))
        if at is None or (now - at).total_seconds() > NEXT_CHECK_TOLERANCE_SECONDS:
            continue
        key = (at.timestamp(), TYPE_PRIORITY[candidate["type"]])
        if best is None or key < best[0]:
            best = (key, candidate, at)
    if best is None:
        return block
    _, candidate, at = best
    block.update(
        {
            "next_check_type": candidate["type"],
            "next_check_name": _text(candidate.get("name")),
            "next_check_channel": _text(candidate.get("channel")),
            "next_check_at": _iso(at),
            "next_check_epoch": _epoch(at),
        }
    )
    return block


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build_widget_snapshot(
    *,
    automation: Optional[Dict[str, Any]],
    event_checks: Optional[Dict[str, Any]],
    last_check: Optional[Dict[str, Any]],
    next_candidates: Iterable[Dict[str, Any]],
    now: datetime,
) -> Dict[str, Any]:
    now = now.astimezone()

    actives: List[Dict[str, Any]] = []
    automation_block = _automation_active(automation)
    if automation_block:
        actives.append(automation_block)
    actives.extend(_event_actives(event_checks, now))
    primary = _primary(actives)

    snapshot: Dict[str, Any] = {
        "schema": SCHEMA_VERSION,
        "generated_at": _iso(now),
        "generated_epoch": _epoch(now),
        "active": primary is not None,
        "active_count": len(actives),
    }
    snapshot.update(
        {key: (primary or {}).get(key, default) for key, default in _EMPTY_ACTIVE.items()}
    )
    snapshot["queued"] = bool(isinstance(automation, dict) and automation.get("state") == "queued")
    snapshot.update(_last_check_block(last_check))
    snapshot.update(_next_check_block(next_candidates, now))
    return snapshot
