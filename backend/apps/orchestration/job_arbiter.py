"""Read-only V6 job arbiter snapshot.

This module deliberately starts as an observation layer.  Existing services keep
their current queues and busy guards, while the arbiter exposes one consistent
view of active work and the policy result a new job request would hit.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Set


REQUEST_CATEGORIES = (
    "full_run",
    "single_channel",
    "event_check",
    "manual_probe",
    "provider_refresh",
    "shadow_monitor",
)

BLOCKING_CONFLICTS = {
    "full_run": {"stream_checker", "single_channel", "event_check", "manual_probe", "provider_refresh"},
    "single_channel": {"full_run", "stream_checker", "single_channel"},
    "manual_probe": {"full_run", "stream_checker", "single_channel", "manual_probe"},
    "provider_refresh": {"full_run", "provider_refresh"},
}

QUEUE_CONFLICTS = {
    "event_check": {"full_run", "stream_checker", "single_channel"},
}


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _is_running_state(value: Any) -> bool:
    state = str(value or "").strip().lower()
    return state in {"running", "active", "checking", "refreshing", "in_progress"}


def _job(
    *,
    category: str,
    source: str,
    state: str,
    label: str,
    active: bool = True,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "category": category,
        "source": source,
        "state": state,
        "label": label,
        "active": bool(active),
        "details": details or {},
    }


def _automation_jobs(status: Dict[str, Any]) -> List[Dict[str, Any]]:
    run_status = _as_dict(status.get("run_status") or status.get("run_progress"))
    if not run_status:
        return []

    state = str(run_status.get("state") or run_status.get("status") or "").lower()
    # A run waiting behind the Stream Checker owns nothing yet. Report it so it
    # stays visible, but never as an active job: counting it blocked every other
    # request for as long as it waited, even when no full run would ever start.
    queued = state == "queued"
    active = not queued and (bool(run_status.get("active")) or _is_running_state(state))
    if not active and not queued:
        return []

    # The top-level percent lags: only the nested progress block is kept current.
    progress = _as_dict(run_status.get("progress"))
    percent = progress.get("percent") if progress.get("percent") is not None else run_status.get("percent")

    return [
        _job(
            category="full_run",
            source="automation",
            state=state or "running",
            label=str(run_status.get("stage_label") or run_status.get("stage") or "Full Run"),
            active=active,
            details={
                "run_id": run_status.get("run_id"),
                "stage": run_status.get("stage"),
                "percent": percent,
                "message": run_status.get("message"),
            },
        )
    ]


def _stream_checker_jobs(status: Dict[str, Any]) -> List[Dict[str, Any]]:
    progress = _as_dict(status.get("progress"))
    queue = _as_dict(status.get("queue"))
    queue_size = _as_int(queue.get("queue_size"))
    in_progress = _as_int(queue.get("in_progress"))
    active = (
        bool(status.get("stream_checking_mode"))
        or bool(status.get("checking"))
        or queue_size > 0
        or in_progress > 0
        or queue.get("current_channel") is not None
    )
    if not active:
        return []

    is_single = bool(progress.get("is_single_channel_check"))
    category = "single_channel" if is_single else "stream_checker"
    label = "Single Channel Check" if is_single else "Stream Checker"

    return [
        _job(
            category=category,
            source="stream_checker",
            state="running" if status.get("stream_checking_mode") or status.get("checking") else "queued",
            label=label,
            details={
                "queue_size": queue_size,
                "in_progress": in_progress,
                "current_channel": queue.get("current_channel") or progress.get("current_channel"),
                "percent": progress.get("percentage") or progress.get("percent"),
            },
        )
    ]


def _shadow_jobs(status: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not status.get("running"):
        return []

    watched_count = _as_int(status.get("watched_count"))
    active = watched_count > 0
    return [
        _job(
            category="shadow_monitor",
            source="shadow_monitor",
            state="watching" if active else "idle",
            label="Shadow Monitor",
            active=active,
            details={
                "watched_count": watched_count,
                "recent_event_count": len(status.get("recent_events") or []),
                "last_scan": status.get("last_scan"),
            },
        )
    ]


def _count(value: Any) -> int:
    return len(value) if isinstance(value, list) else _as_int(value)


def _teamarr_jobs(status: Dict[str, Any]) -> List[Dict[str, Any]]:
    # Three places hold preflight work: direct-thread checks (``active_checks``),
    # checks the stream-checker queue is running (``queue_active_checks``) and
    # checks still waiting in it (``queued_checks``). Only the first used to be
    # read, so queued preflight work looked like a generic stream_checker job.
    direct_count = _count(status.get("active_checks") or status.get("active_event_checks"))
    queue_active_count = _count(status.get("queue_active_checks"))
    queued_count = _count(status.get("queued_checks"))
    if direct_count + queue_active_count + queued_count == 0:
        return []

    running_count = direct_count + queue_active_count
    return [
        _job(
            category="event_check",
            source="teamarr_preflight",
            state="running" if running_count > 0 else "queued",
            label="Event Check",
            details={
                "active_checks": running_count,
                "direct_checks": direct_count,
                "queue_active_checks": queue_active_count,
                "queue_size": queued_count,
            },
        )
    ]


def collect_active_jobs(
    *,
    automation_status: Optional[Dict[str, Any]] = None,
    stream_checker_status: Optional[Dict[str, Any]] = None,
    shadow_status: Optional[Dict[str, Any]] = None,
    teamarr_status: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    jobs: List[Dict[str, Any]] = []
    jobs.extend(_automation_jobs(_as_dict(automation_status)))
    jobs.extend(_stream_checker_jobs(_as_dict(stream_checker_status)))
    jobs.extend(_shadow_jobs(_as_dict(shadow_status)))
    jobs.extend(_teamarr_jobs(_as_dict(teamarr_status)))
    return jobs


def _matching_conflicts(active_jobs: Iterable[Dict[str, Any]], categories: Set[str]) -> List[Dict[str, Any]]:
    return [
        {
            "category": job.get("category"),
            "source": job.get("source"),
            "state": job.get("state"),
            "label": job.get("label"),
        }
        for job in active_jobs
        if job.get("active") and job.get("category") in categories
    ]


def build_request_policies(active_jobs: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    policies: Dict[str, Dict[str, Any]] = {}
    for category in REQUEST_CATEGORIES:
        blocking = _matching_conflicts(active_jobs, BLOCKING_CONFLICTS.get(category, set()))
        queueing = _matching_conflicts(active_jobs, QUEUE_CONFLICTS.get(category, set()))
        if blocking:
            policies[category] = {
                "state": "blocked",
                "reason": "active_conflict",
                "conflicts": blocking,
            }
        elif queueing:
            policies[category] = {
                "state": "queue",
                "reason": "active_conflict_queueable",
                "conflicts": queueing,
            }
        else:
            policies[category] = {
                "state": "allowed",
                "reason": "no_conflict",
                "conflicts": [],
            }
    return policies


def build_job_arbiter_snapshot(
    *,
    automation_status: Optional[Dict[str, Any]] = None,
    stream_checker_status: Optional[Dict[str, Any]] = None,
    shadow_status: Optional[Dict[str, Any]] = None,
    teamarr_status: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    active_jobs = collect_active_jobs(
        automation_status=automation_status,
        stream_checker_status=stream_checker_status,
        shadow_status=shadow_status,
        teamarr_status=teamarr_status,
    )
    active_blocking_jobs = [job for job in active_jobs if job.get("active")]
    request_policies = build_request_policies(active_jobs)

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "active_jobs": active_jobs,
        "active_job_count": len(active_blocking_jobs),
        "request_policies": request_policies,
        "summary": {
            "busy": bool(active_blocking_jobs),
            "blocking_categories": sorted({job.get("category") for job in active_blocking_jobs if job.get("category")}),
        },
    }
