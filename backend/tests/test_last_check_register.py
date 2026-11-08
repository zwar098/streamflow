"""Last-check register: recording, seeding from run history, and the hooks."""

import threading
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from apps.telemetry import last_check_register as register


@pytest.fixture(autouse=True)
def clean_register():
    register.reset()
    yield
    register.reset()


def row(run_type, *, outcome="completed", details=None, minutes_ago=5, duration=12.0):
    import json

    return SimpleNamespace(
        run_type=run_type,
        job_outcome=outcome,
        raw_details=json.dumps(details) if details is not None else None,
        timestamp=datetime.now() - timedelta(minutes=minutes_ago),
        duration_seconds=duration,
    )


class TestRecording:
    def test_latest_is_the_newest_finish_across_types(self):
        now = datetime.now().astimezone()
        register.record(check_type="automation", name="Hourly", finished_at=now - timedelta(minutes=10))
        register.record(check_type="preflight", name="NHL", channel="NHL | A @ B", finished_at=now - timedelta(minutes=1))
        register.record(check_type="scheduled_event", name="Show", finished_at=now - timedelta(minutes=5))

        latest = register.latest()

        assert latest["type"] == "preflight"
        assert latest["channel"] == "NHL | A @ B"
        assert set(register.latest_by_type()) == {"automation", "preflight", "scheduled_event"}

    def test_empty_register_has_no_latest(self):
        assert register.latest() is None

    @pytest.mark.parametrize("result", ["skipped", "queued", "deferred", "running", ""])
    def test_only_finished_results_are_recorded(self, result):
        assert register.record(check_type="preflight", name="x", result=result) is False
        assert register.latest() is None

    def test_unknown_check_types_are_rejected(self):
        assert register.record(check_type="monitoring", name="x") is False
        assert register.record(check_type="manual", name="x") is False

    def test_duration_is_rounded_and_never_negative(self):
        register.record(check_type="automation", name="a", duration_seconds=181.6)
        assert register.latest()["duration_seconds"] == 182
        register.record(check_type="automation", name="a", duration_seconds=-5)
        assert register.latest()["duration_seconds"] == 0
        register.record(check_type="automation", name="a", duration_seconds="oops")
        assert register.latest()["duration_seconds"] is None

    def test_stored_time_is_timezone_aware(self):
        register.record(check_type="automation", name="a", finished_at=datetime.now())
        assert register.latest()["finished_at"].tzinfo is not None

    def test_concurrent_recording_is_safe(self):
        def worker(index):
            for _ in range(50):
                register.record(check_type="preflight", name=f"n{index}")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert register.latest()["type"] == "preflight"


class TestRunRowMapping:
    def test_classifies_rows_by_run_type_and_details(self):
        rows = [
            row("automation_run", details={"period_name": "Overnight Automation"}, minutes_ago=1),
            row("single_channel_check", details={"run_mode": "teamarr_preflight", "program_name": "CHI at CAR",
                                                 "channel_name": "NHL | CHI @ CAR"}, minutes_ago=2),
            row("single_channel_check", details={"program_name": "Goal Rush", "channel_name": "Goal Rush"}, minutes_ago=3),
            row("single_channel_check", details={"channel_name": "Manual"}, minutes_ago=4),
            row("playlist_refresh", details={}, minutes_ago=5),
        ]

        by_type = {item["type"]: item for item in register.records_from_runs(rows)}

        assert set(by_type) == {"automation", "preflight", "scheduled_event"}
        assert by_type["automation"]["name"] == "Overnight Automation"
        assert by_type["preflight"]["channel"] == "NHL | CHI @ CAR"
        assert by_type["scheduled_event"]["name"] == "Goal Rush"

    def test_skipped_and_unknown_outcomes_are_not_checks(self):
        rows = [row("automation_run", outcome="skipped"), row("automation_run", outcome="unknown")]

        assert register.records_from_runs(rows) == []

    def test_failed_outcome_is_kept(self):
        rows = [row("automation_run", outcome="failed")]

        assert register.records_from_runs(rows)[0]["result"] == "failed"

    def test_malformed_details_do_not_break_mapping(self):
        bad = SimpleNamespace(run_type="single_channel_check", job_outcome="completed",
                              raw_details="{not json", timestamp=datetime.now(), duration_seconds=1)

        assert register.records_from_runs([bad]) == []


class TestSeeding:
    def test_seeds_once_from_the_loader(self):
        loader = Mock(return_value=[row("automation_run", details={"period_name": "Hourly"})])

        register.ensure_seeded(loader)
        register.ensure_seeded(loader)

        loader.assert_called_once_with()
        assert register.latest()["name"] == "Hourly"

    def test_a_newer_live_record_beats_older_history(self):
        register.record(check_type="automation", name="Live", finished_at=datetime.now().astimezone())
        loader = lambda: [row("automation_run", details={"period_name": "Old"}, minutes_ago=30)]

        register.ensure_seeded(loader)

        assert register.latest_by_type()["automation"]["name"] == "Live"

    def test_history_fills_types_that_have_no_live_record(self):
        register.record(check_type="preflight", name="Live preflight")
        loader = lambda: [row("automation_run", details={"period_name": "Hourly"}, minutes_ago=30)]

        register.ensure_seeded(loader)

        assert set(register.latest_by_type()) == {"preflight", "automation"}

    def test_newest_history_row_per_type_wins(self):
        loader = lambda: [
            row("automation_run", details={"period_name": "Newest"}, minutes_ago=1),
            row("automation_run", details={"period_name": "Older"}, minutes_ago=60),
        ]

        register.ensure_seeded(loader)

        assert register.latest_by_type()["automation"]["name"] == "Newest"

    def test_loader_failure_is_swallowed_and_not_retried(self):
        loader = Mock(side_effect=RuntimeError("db down"))

        register.ensure_seeded(loader)
        register.ensure_seeded(loader)

        loader.assert_called_once_with()
        assert register.latest() is None

    def test_seeds_from_real_run_rows(self):
        from apps.telemetry.telemetry_db import save_generic_telemetry

        save_generic_telemetry(
            "single_channel_check",
            {"channel_id": 7, "success": True, "run_mode": "teamarr_preflight",
             "program_name": "CHI at CAR", "channel_name": "NHL | CHI @ CAR", "duration_seconds": 30},
            timestamp=(datetime.now() - timedelta(minutes=3)).isoformat(),
        )
        save_generic_telemetry(
            "automation_run",
            {"total_channels": 4, "success": True, "duration_seconds": 100},
            timestamp=(datetime.now() - timedelta(minutes=9)).isoformat(),
        )

        register.ensure_seeded()

        by_type = register.latest_by_type()
        assert by_type["preflight"]["name"] == "CHI at CAR"
        assert by_type["preflight"]["duration_seconds"] == 30
        assert by_type["automation"]["result"] == "completed"
        assert register.latest()["type"] == "preflight"


class TestAutomationHook:
    """AutomatedStreamManager._finish_run_status feeds the register."""

    @pytest.fixture
    def manager(self):
        from apps.automation.automated_stream_manager import AutomatedStreamManager

        manager = AutomatedStreamManager()
        manager._store_run_snapshot = Mock()
        manager._start_run_status()
        return manager

    def reach_work_stage(self, manager, periods):
        manager._update_run_status(stage="m3u_refresh", counts={"active_periods": len(periods)})
        with manager._run_status_lock:
            manager._run_status["run_snapshot"] = {
                "effective_profiles": [{"period_id": pid, "period_name": name} for pid, name in periods]
            }

    def test_completed_run_that_did_work_is_recorded(self, manager):
        self.reach_work_stage(manager, [("p1", "Hourly Automation")])

        manager._finish_run_status(state="completed", message="done")

        latest = register.latest()
        assert latest["type"] == "automation"
        assert latest["name"] == "Hourly Automation"
        assert latest["result"] == "completed"

    @pytest.mark.parametrize("state", ["failed", "aborted", "completed_degraded"])
    def test_other_finished_results_are_recorded(self, manager, state):
        self.reach_work_stage(manager, [("p1", "Hourly")])

        manager._finish_run_status(state=state, message="x")

        assert register.latest()["result"] == state

    def test_skipped_tick_is_never_recorded(self, manager):
        manager._finish_run_status(state="skipped", message="nothing due")

        assert register.latest() is None

    def test_run_that_never_found_work_is_not_recorded(self, manager):
        manager._finish_run_status(state="failed", message="crashed before discovery")

        assert register.latest() is None

    def test_forced_run_is_named_for_its_period(self, manager):
        with manager._run_status_lock:
            manager._run_status["forced_period_id"] = "p2"
        self.reach_work_stage(manager, [("p1", "Hourly"), ("p2", "Overnight")])

        manager._finish_run_status(state="completed", message="done")

        assert register.latest()["name"] == "Overnight"


class TestPreflightHook:
    @pytest.fixture
    def service(self):
        from apps.stream.teamarr_preflight_service import TeamarrPreflightService

        service = TeamarrPreflightService.__new__(TeamarrPreflightService)
        service._lock = threading.RLock()
        service._events = __import__("collections").deque(maxlen=120)
        service._inspections = {}
        service.clock = lambda: 1_000.0
        service._match_evidence = lambda event: {}
        return service

    EVENT = {"event_name": "Chicago Blackhawks at Carolina Hurricanes", "channel_name": "NHL | CHI @ CAR",
             "dispatcharr_channel_id": 7, "identity": "x"}

    def test_completed_preflight_is_recorded_with_its_duration(self, service):
        service._record_event("preflight_completed", dict(self.EVENT), {"duration_seconds": 182.0})

        latest = register.latest()
        assert latest["type"] == "preflight"
        assert latest["name"] == self.EVENT["event_name"]
        assert latest["channel"] == "NHL | CHI @ CAR"
        assert latest["result"] == "completed"
        assert latest["duration_seconds"] == 182

    def test_failed_preflight_is_recorded_as_failed(self, service):
        service._record_event("preflight_failed", dict(self.EVENT), {})

        assert register.latest()["result"] == "failed"

    @pytest.mark.parametrize("event_type", ["preflight_deferred", "preflight_skipped", "no_streams_yet", "concurrency_limit"])
    def test_non_checks_are_never_recorded(self, service, event_type):
        service._record_event(event_type, dict(self.EVENT), {})

        assert register.latest() is None

    def test_seconds_since_handles_a_missing_start(self, service):
        assert service._seconds_since(None) is None
        assert service._seconds_since(400.0) == 600.0
