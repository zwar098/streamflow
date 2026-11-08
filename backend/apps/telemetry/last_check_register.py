"""In-memory register of the most recent finished check of each type.

The widget endpoint needs "what was the last check that finished, of any type"
without touching the database on every poll. Nothing existing answers that:
automation runs are only persisted when they did channel work, preflight
results live in a bounded in-memory deque, and scheduled-event checks leave no
dedicated record. Services call :func:`record` when a real check finishes; the
register is seeded once, lazily, from the persisted ``Run`` rows so a restart
does not start from nothing.

Only real checks belong here. Skipped no-op ticks and deferred, expired or
skipped preflights are not checks and must never be recorded.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, List, Optional

from apps.core.logging_config import setup_logging

logger = setup_logging(__name__)

CHECK_TYPES = ("automation", "scheduled_event", "preflight")
FINISHED_RESULTS = {"completed", "completed_degraded", "failed", "aborted"}

_lock = threading.Lock()
_records: Dict[str, Dict[str, Any]] = {}
_seeded = False


def _aware(value: Optional[datetime]) -> datetime:
    if value is None:
        return datetime.now().astimezone()
    return value.astimezone()


def _store_locked(record: Dict[str, Any], *, only_if_newer: bool) -> bool:
    existing = _records.get(record["type"])
    if only_if_newer and existing and existing["finished_at"] >= record["finished_at"]:
        return False
    _records[record["type"]] = record
    return True


def _build_record(
    check_type: str,
    name: Optional[str],
    channel: Optional[str],
    finished_at: Optional[datetime],
    result: str,
    duration_seconds: Optional[float],
) -> Optional[Dict[str, Any]]:
    if check_type not in CHECK_TYPES or result not in FINISHED_RESULTS:
        return None
    try:
        duration = None if duration_seconds is None else max(0, int(round(float(duration_seconds))))
    except (TypeError, ValueError):
        duration = None
    return {
        "type": check_type,
        "name": name or None,
        "channel": channel or None,
        "finished_at": _aware(finished_at),
        "result": result,
        "duration_seconds": duration,
    }


def record(
    *,
    check_type: str,
    name: Optional[str] = None,
    channel: Optional[str] = None,
    finished_at: Optional[datetime] = None,
    result: str = "completed",
    duration_seconds: Optional[float] = None,
) -> bool:
    """Record a finished check. Returns False when the input is not a real check."""
    built = _build_record(check_type, name, channel, finished_at, result, duration_seconds)
    if built is None:
        return False
    with _lock:
        return _store_locked(built, only_if_newer=False)


def latest_by_type() -> Dict[str, Dict[str, Any]]:
    with _lock:
        return {key: dict(value) for key, value in _records.items()}


def latest() -> Optional[Dict[str, Any]]:
    """Return the newest finished check across all types, or None."""
    with _lock:
        if not _records:
            return None
        return dict(max(_records.values(), key=lambda item: item["finished_at"]))


def reset() -> None:
    """Forget everything, including the seeded flag (test helper)."""
    global _seeded
    with _lock:
        _records.clear()
        _seeded = False


def _parse_details(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def records_from_runs(rows: Iterable[Any]) -> List[Dict[str, Any]]:
    """Map persisted ``Run`` rows (newest first) to register records.

    ``automation_run`` rows are automation. ``single_channel_check`` rows are a
    preflight when ``run_mode`` is ``teamarr_preflight`` and a scheduled event
    when a ``program_name`` is present; anything else is a manual single-channel
    check and out of scope.
    """
    mapped: List[Dict[str, Any]] = []
    for row in rows:
        run_type = getattr(row, "run_type", None)
        details = _parse_details(getattr(row, "raw_details", None))
        outcome = str(getattr(row, "job_outcome", None) or "completed")
        if outcome not in FINISHED_RESULTS:
            continue
        if run_type == "automation_run":
            check_type = "automation"
            name = details.get("period_name") or details.get("profile_name") or "Automation"
            channel = None
        elif run_type == "single_channel_check":
            if details.get("run_mode") == "teamarr_preflight":
                check_type = "preflight"
            elif details.get("program_name"):
                check_type = "scheduled_event"
            else:
                continue
            name = details.get("program_name")
            channel = details.get("channel_name")
        else:
            continue
        built = _build_record(
            check_type,
            name,
            channel,
            getattr(row, "timestamp", None),
            outcome,
            getattr(row, "duration_seconds", None),
        )
        if built:
            mapped.append(built)
    return mapped


def _load_recent_runs() -> List[Any]:
    from apps.telemetry.telemetry_db import Run, get_session

    session = get_session()
    try:
        rows = (
            session.query(Run)
            .filter(Run.run_type.in_(("automation_run", "single_channel_check")))
            .order_by(Run.timestamp.desc())
            .limit(20)
            .all()
        )
        for row in rows:
            session.expunge(row)
        return rows
    finally:
        session.close()


def ensure_seeded(loader: Optional[Callable[[], Iterable[Any]]] = None) -> None:
    """Seed from persisted runs once. Live records that are newer win."""
    global _seeded
    with _lock:
        if _seeded:
            return
        _seeded = True
    try:
        seeded = records_from_runs((loader or _load_recent_runs)())
    except Exception as exc:
        logger.debug("Could not seed last-check register from run history: %s", exc)
        return
    with _lock:
        for item in seeded:
            _store_locked(item, only_if_newer=True)
