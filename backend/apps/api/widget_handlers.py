"""Handler for the flat widget document at ``GET /api/v1/stats/widget``.

Gathers a handful of small in-memory snapshots and hands them to the pure
builder in ``apps.orchestration.widget_snapshot``. It deliberately never calls
the heavy ``get_status()`` methods (the checker's alone is ~2.7 MB) and does no
network I/O. The only database reads are the one-time seed of the last-check
register and the next-check lookup, which is cached for 15 seconds.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from flask import jsonify

from apps.core.api_responses import error_response
from apps.core.logging_config import setup_logging
from apps.orchestration.widget_snapshot import (
    NEXT_CHECK_TOLERANCE_SECONDS,
    UPCOMING_MAX,
    build_widget_snapshot,
    to_local_aware,
)
from apps.telemetry import last_check_register

logger = setup_logging(__name__)

NEXT_CHECK_CACHE_SECONDS = 15
LOOKAHEAD_HOURS = 24
LOOKAHEAD_MAX_EVENTS = 50

_next_cache_lock = threading.Lock()
_next_cache: Dict[str, Any] = {"at": None, "candidates": []}


def reset_next_check_cache() -> None:
    """Forget the cached next-check candidates (test helper)."""
    with _next_cache_lock:
        _next_cache["at"] = None
        _next_cache["candidates"] = []


def _safe(name: str, getter: Callable[[], Any]) -> Any:
    try:
        return getter()
    except Exception as exc:
        logger.debug("Widget endpoint could not collect %s: %s", name, exc)
        return None


def _upcoming(items: List[Dict[str, Any]], time_key: str, now: datetime, limit: int) -> List[Dict[str, Any]]:
    """Return the first ``limit`` items at or after ``now - tolerance``, in list order."""
    found: List[Dict[str, Any]] = []
    for item in items:
        at = to_local_aware(item.get(time_key))
        if at is not None and (now - at).total_seconds() <= NEXT_CHECK_TOLERANCE_SECONDS:
            found.append(item)
            if len(found) >= limit:
                break
    return found


def _automation_candidates(
    get_automation_config_manager: Callable[[], Any],
    get_events_scheduler: Callable[[], Any],
    now: datetime,
) -> List[Dict[str, Any]]:
    settings = get_automation_config_manager().get_global_settings() or {}
    if not settings.get("regular_automation_enabled"):
        return []
    scheduler = get_events_scheduler()
    snapshot = scheduler.get_cached_events_snapshot(LOOKAHEAD_HOURS, LOOKAHEAD_MAX_EVENTS) or {}
    events = snapshot.get("events") or []
    if not events or snapshot.get("stale"):
        # Recomputes at most once per the scheduler's own cache window.
        refreshed = scheduler.get_cached_events(LOOKAHEAD_HOURS, LOOKAHEAD_MAX_EVENTS) or {}
        events = refreshed.get("events") or []
    events = sorted(
        (e for e in events if isinstance(e, dict)),
        key=lambda e: (to_local_aware(e.get("time")) or now).timestamp(),
    )
    return [
        {"type": "automation", "name": event.get("period_name"), "channel": None, "at": event.get("time")}
        for event in _upcoming(events, "time", now, UPCOMING_MAX)
    ]


def _scheduled_event_candidates(
    get_scheduling_service: Callable[[], Any],
    now: datetime,
) -> List[Dict[str, Any]]:
    events = [
        e for e in get_scheduling_service().get_scheduled_events()
        if isinstance(e, dict) and e.get("schedule_type", "check") == "check"
    ]
    return [
        {
            "type": "scheduled_event",
            "name": event.get("program_title"),
            "channel": event.get("channel_name"),
            "at": event.get("check_time"),
        }
        for event in _upcoming(events, "check_time", now, UPCOMING_MAX)
    ]


def _cached_next_candidates(
    *,
    get_automation_config_manager: Callable[[], Any],
    get_events_scheduler: Callable[[], Any],
    get_scheduling_service: Callable[[], Any],
    now: datetime,
) -> List[Dict[str, Any]]:
    with _next_cache_lock:
        cached_at = _next_cache["at"]
        if cached_at is not None and (time.monotonic() - cached_at) < NEXT_CHECK_CACHE_SECONDS:
            return list(_next_cache["candidates"])

    candidates: List[Dict[str, Any]] = []
    candidates.extend(
        _safe(
            "next automation periods",
            lambda: _automation_candidates(get_automation_config_manager, get_events_scheduler, now),
        )
        or []
    )
    candidates.extend(
        _safe(
            "next scheduled events",
            lambda: _scheduled_event_candidates(get_scheduling_service, now),
        )
        or []
    )

    with _next_cache_lock:
        _next_cache["at"] = time.monotonic()
        _next_cache["candidates"] = list(candidates)
    return candidates


def get_widget_stats_response(
    *,
    get_automation_manager: Callable[[], Any],
    get_stream_checker_service: Callable[[], Any],
    get_teamarr_preflight_service: Callable[[], Any],
    get_scheduling_service: Callable[[], Any],
    get_events_scheduler: Callable[[], Any],
    get_automation_config_manager: Callable[[], Any],
    now: Optional[datetime] = None,
):
    try:
        now = (now or datetime.now()).astimezone()

        automation = _safe("automation", lambda: get_automation_manager().get_widget_run_state())
        preflight = _safe("preflight", lambda: get_teamarr_preflight_service().get_widget_snapshot()) or {}
        direct_checks = list(preflight.get("active_checks") or [])
        event_checks = _safe(
            "event checks",
            lambda: get_stream_checker_service().get_widget_event_checks(
                extra_channel_ids=[c["channel_id"] for c in direct_checks if c.get("channel_id") is not None]
            ),
        ) or {"entries": [], "progress_by_channel": {}, "stale_after_seconds": None}
        event_checks = {**event_checks, "entries": list(event_checks.get("entries") or []) + direct_checks}

        last_check_register.ensure_seeded()
        candidates = _cached_next_candidates(
            get_automation_config_manager=get_automation_config_manager,
            get_events_scheduler=get_events_scheduler,
            get_scheduling_service=get_scheduling_service,
            now=now,
        )
        preflight_upcoming = preflight.get("upcoming_checks")
        if preflight_upcoming is None and preflight.get("next_check"):
            preflight_upcoming = [preflight["next_check"]]
        candidates = candidates + list(preflight_upcoming or [])

        snapshot = build_widget_snapshot(
            automation=automation,
            event_checks=event_checks,
            last_check=last_check_register.latest(),
            next_candidates=candidates,
            now=now,
        )
        response = jsonify(snapshot)
        response.headers["Cache-Control"] = "no-store"
        return response, 200
    except Exception as exc:
        logger.error("Error building widget stats: %s", exc, exc_info=True)
        return error_response("Internal Server Error", status_code=500, code="internal_error")
