from flask import Flask

from apps.api.job_arbiter_handlers import get_job_arbiter_status_response
from apps.orchestration.job_arbiter import build_job_arbiter_snapshot


class _Service:
    def __init__(self, status):
        self._status = status

    def get_status(self):
        return self._status


def test_full_run_blocks_single_channel_request():
    snapshot = build_job_arbiter_snapshot(
        automation_status={
            "run_status": {
                "active": True,
                "state": "running",
                "stage": "m3u_refresh",
                "stage_label": "M3U Refresh",
                "run_id": "automation-1",
            }
        }
    )

    assert snapshot["summary"]["busy"] is True
    assert snapshot["active_jobs"][0]["category"] == "full_run"
    assert snapshot["request_policies"]["single_channel"]["state"] == "blocked"
    assert snapshot["request_policies"]["single_channel"]["conflicts"][0]["category"] == "full_run"


def test_stream_checker_makes_event_checks_queueable():
    snapshot = build_job_arbiter_snapshot(
        stream_checker_status={
            "stream_checking_mode": True,
            "progress": {"is_single_channel_check": False, "percentage": 42},
            "queue": {"queue_size": 0, "in_progress": 2},
        }
    )

    assert snapshot["active_jobs"][0]["category"] == "stream_checker"
    assert snapshot["request_policies"]["full_run"]["state"] == "blocked"
    assert snapshot["request_policies"]["event_check"]["state"] == "queue"
    assert snapshot["request_policies"]["event_check"]["reason"] == "active_conflict_queueable"


def test_idle_shadow_monitor_is_visible_but_not_busy():
    snapshot = build_job_arbiter_snapshot(
        shadow_status={
            "running": True,
            "watched_count": 0,
            "recent_events": [],
        }
    )

    assert snapshot["active_jobs"][0]["category"] == "shadow_monitor"
    assert snapshot["active_jobs"][0]["active"] is False
    assert snapshot["summary"]["busy"] is False
    assert snapshot["request_policies"]["full_run"]["state"] == "allowed"


def test_job_arbiter_status_handler_returns_snapshot():
    app = Flask(__name__)
    with app.app_context():
        response, status_code = get_job_arbiter_status_response(
            get_automation_manager=lambda: _Service({}),
            get_stream_checker_service=lambda: _Service(
                {
                    "stream_checking_mode": True,
                    "progress": {"is_single_channel_check": True},
                    "queue": {"in_progress": 1},
                }
            ),
            get_shadow_monitor_service=lambda: _Service({"running": False}),
            get_teamarr_preflight_service=lambda: _Service({}),
        )

    payload = response.get_json()
    assert status_code == 200
    assert payload["active_jobs"][0]["category"] == "single_channel"
    assert payload["request_policies"]["full_run"]["state"] == "blocked"


# ---------------------------------------------------------------------------
# Queued automation intents, preflight queue work, and the nested percent
# ---------------------------------------------------------------------------

def _queued_run():
    return {
        "run_status": {
            "active": False,
            "state": "queued",
            "stage": "queued",
            "stage_label": "Queued",
            "run_id": "automation-1",
            "message": "Stream checker is active; automation run is queued",
        }
    }


def test_queued_automation_run_is_visible_but_not_an_active_job():
    snapshot = build_job_arbiter_snapshot(automation_status=_queued_run())

    job = snapshot["active_jobs"][0]
    assert (job["category"], job["state"], job["active"]) == ("full_run", "queued", False)
    assert snapshot["active_job_count"] == 0
    assert snapshot["summary"]["busy"] is False
    assert snapshot["summary"]["blocking_categories"] == []
    assert snapshot["request_policies"]["single_channel"]["state"] == "allowed"
    assert snapshot["request_policies"]["manual_probe"]["state"] == "allowed"


def test_queued_automation_does_not_block_while_the_checker_is_busy():
    """The sample: a no-op tick queued behind three preflight checks for minutes."""
    snapshot = build_job_arbiter_snapshot(
        automation_status=_queued_run(),
        stream_checker_status={
            "stream_checking_mode": True,
            "queue": {"queue_size": 0, "in_progress": 3},
        },
    )

    assert snapshot["summary"]["blocking_categories"] == ["stream_checker"]
    assert snapshot["request_policies"]["full_run"]["conflicts"] == [
        {"category": "stream_checker", "source": "stream_checker", "state": "running", "label": "Stream Checker"}
    ]


def test_running_automation_run_still_blocks():
    snapshot = build_job_arbiter_snapshot(
        automation_status={"run_status": {"active": True, "state": "running", "stage": "m3u_refresh"}}
    )

    assert snapshot["active_jobs"][0]["active"] is True
    assert snapshot["request_policies"]["single_channel"]["state"] == "blocked"


def test_terminal_automation_states_report_no_job():
    for state in ("skipped", "completed", "failed", "aborted", "idle"):
        snapshot = build_job_arbiter_snapshot(
            automation_status={"run_status": {"active": False, "state": state}}
        )
        assert snapshot["active_jobs"] == [], state


def test_automation_percent_comes_from_the_nested_progress_block():
    snapshot = build_job_arbiter_snapshot(
        automation_status={
            "run_status": {
                "active": True,
                "state": "running",
                "stage": "m3u_refresh",
                "percent": 0,
                "progress": {"current": 2, "total": 4, "percent": 50},
            }
        }
    )

    assert snapshot["active_jobs"][0]["details"]["percent"] == 50


def test_automation_percent_falls_back_to_the_top_level_value():
    snapshot = build_job_arbiter_snapshot(
        automation_status={"run_status": {"active": True, "state": "running", "percent": 30}}
    )

    assert snapshot["active_jobs"][0]["details"]["percent"] == 30


def test_queued_preflight_checks_are_reported_as_an_event_check():
    snapshot = build_job_arbiter_snapshot(
        teamarr_status={"running": True, "queued_checks": [{"channel_id": 1}, {"channel_id": 2}]}
    )

    job = snapshot["active_jobs"][0]
    assert (job["category"], job["state"], job["active"]) == ("event_check", "queued", True)
    assert job["details"]["queue_size"] == 2
    assert snapshot["request_policies"]["full_run"]["state"] == "blocked"


def test_preflight_checks_running_through_the_queue_are_running():
    snapshot = build_job_arbiter_snapshot(
        teamarr_status={"running": True, "queue_active_checks": [{"channel_id": 1}], "queued_checks": [{"channel_id": 2}]}
    )

    job = snapshot["active_jobs"][0]
    assert job["state"] == "running"
    assert job["details"] == {
        "active_checks": 1,
        "direct_checks": 0,
        "queue_active_checks": 1,
        "queue_size": 1,
    }


def test_direct_thread_preflight_checks_still_count():
    snapshot = build_job_arbiter_snapshot(
        teamarr_status={"running": True, "active_checks": [{"channel_id": 1}, {"channel_id": 2}]}
    )

    job = snapshot["active_jobs"][0]
    assert job["state"] == "running"
    assert job["details"]["direct_checks"] == 2


def test_preflight_with_no_work_reports_no_job():
    snapshot = build_job_arbiter_snapshot(
        teamarr_status={"running": True, "active_checks": [], "queue_active_checks": [], "queued_checks": []}
    )

    assert snapshot["active_jobs"] == []


def test_preflight_work_is_reported_even_if_the_scanner_has_stopped():
    snapshot = build_job_arbiter_snapshot(
        teamarr_status={"running": False, "queued_checks": [{"channel_id": 1}]}
    )

    assert snapshot["active_jobs"][0]["category"] == "event_check"
