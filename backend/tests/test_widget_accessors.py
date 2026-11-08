"""The small read-only accessors behind the widget endpoint, and the queue hook."""

import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from apps.stream.queue_execution import StreamCheckQueueExecutionMixin
from apps.stream.teamarr_preflight_service import TeamarrPreflightService
from apps.telemetry import last_check_register


@pytest.fixture(autouse=True)
def clean_register():
    last_check_register.reset()
    yield
    last_check_register.reset()


# ---------------------------------------------------------------------------
# StreamCheckerService.get_widget_event_checks
# ---------------------------------------------------------------------------

@pytest.fixture
def checker(tmp_path, monkeypatch):
    from apps.stream import stream_checker_service as scs

    monkeypatch.setattr(scs, "CONFIG_DIR", tmp_path)
    return scs.StreamCheckerService()


def put_in_progress(checker, channel_id, metadata):
    checker.check_queue.in_progress_metadata[channel_id] = metadata
    checker.check_queue.channel_start_times[channel_id] = datetime.now()


class TestCheckerEventChecks:
    def test_idle_returns_nothing_and_skips_the_progress_read(self, checker, monkeypatch):
        progress_get = Mock()
        monkeypatch.setattr(checker.progress, "get", progress_get)

        result = checker.get_widget_event_checks()

        assert result["entries"] == []
        assert result["progress_by_channel"] == {}
        progress_get.assert_not_called()

    def test_preflight_and_scheduled_entries_are_returned_with_names(self, checker, monkeypatch):
        put_in_progress(checker, 1, {
            "source": "teamarr_preflight",
            "program_name": "ignored",
            "event": {"event_name": "TB at MIN", "team_name": "Lightning", "channel_name": "NHL | TB @ MIN"},
        })
        put_in_progress(checker, 2, {
            "source": "auto_create", "scheduled_event_id": "evt-1", "program_name": "Goal Rush",
        })
        monkeypatch.setattr(checker.progress, "get", Mock(return_value=None))
        udi = Mock()
        udi.get_channel_by_id.return_value = {"name": "Goal Rush"}
        monkeypatch.setattr("apps.stream.stream_checker_service.get_udi_manager", lambda: udi)

        entries = {e["channel_id"]: e for e in checker.get_widget_event_checks()["entries"]}

        assert entries[1]["event_name"] == "TB at MIN"
        assert entries[1]["channel_name"] == "NHL | TB @ MIN"
        assert entries[2]["program_name"] == "Goal Rush"
        assert entries[2]["scheduled_event_id"] == "evt-1"
        assert entries[2]["channel_name"] == "Goal Rush"
        assert isinstance(entries[1]["started_at"], datetime)

    def test_manual_and_foreign_queue_work_is_excluded(self, checker, monkeypatch):
        put_in_progress(checker, 3, {"source": "manual", "program_name": "Manual check"})
        put_in_progress(checker, 4, {})
        monkeypatch.setattr(checker.progress, "get", Mock(return_value=None))

        assert checker.get_widget_event_checks()["entries"] == []

    def test_progress_is_trimmed_and_keyed_by_channel(self, checker, monkeypatch):
        put_in_progress(checker, 1, {"source": "teamarr_preflight", "event": {"event_name": "A"}})
        monkeypatch.setattr(checker.progress, "get", Mock(return_value={
            "channel_id": 1, "channel_name": "NHL | A", "step": "Analyzing streams", "status": "checking",
            "current_stream": 2, "total_streams": 6, "percentage": 33.3,
            "timestamp": datetime.now().isoformat(), "streams_detail": [{"huge": "payload"}],
        }))

        progress = checker.get_widget_event_checks()["progress_by_channel"][1]

        assert progress["step"] == "Analyzing streams"
        assert "streams_detail" not in progress

    def test_direct_thread_channels_trigger_the_progress_read(self, checker, monkeypatch):
        progress_get = Mock(return_value={"channel_id": 7, "step": "Probing", "timestamp": datetime.now().isoformat()})
        monkeypatch.setattr(checker.progress, "get", progress_get)

        result = checker.get_widget_event_checks(extra_channel_ids=[7])

        progress_get.assert_called_once()
        assert result["progress_by_channel"][7]["step"] == "Probing"

    def test_never_builds_the_full_status(self, checker, monkeypatch):
        put_in_progress(checker, 1, {"source": "teamarr_preflight", "event": {"event_name": "A"}})
        monkeypatch.setattr(checker.progress, "get", Mock(return_value=None))
        monkeypatch.setattr(checker, "get_status", Mock(side_effect=AssertionError("get_status")))
        monkeypatch.setattr(checker.check_queue, "get_status", Mock(side_effect=AssertionError("queue status")))

        checker.get_widget_event_checks()


# ---------------------------------------------------------------------------
# TeamarrPreflightService.get_widget_snapshot
# ---------------------------------------------------------------------------

def make_preflight(items=(), active=None, enabled=True, running=True):
    service = TeamarrPreflightService.__new__(TeamarrPreflightService)
    service._lock = threading.RLock()
    service._config = {"enabled": enabled}
    service._thread = Mock(is_alive=Mock(return_value=running))
    service._active_checks = active or {}
    service._preflight_items = list(items)
    return service


def item(name, minutes_ahead, state="scheduled", channel="NHL | X"):
    stamp = (datetime.now(timezone.utc) + timedelta(minutes=minutes_ahead)).isoformat()
    return {"event_name": name, "channel_name": channel, "state": state,
            "next_automatic_check": {"timestamp": stamp, "label": "Next auto check"}}


class TestPreflightSnapshot:
    def test_earliest_scheduled_item_is_the_next_check(self):
        service = make_preflight([item("Later", 40), item("Soonest", 5), item("Middle", 20)])

        snapshot = service.get_widget_snapshot()

        assert snapshot["next_check"]["name"] == "Soonest"
        assert snapshot["next_check"]["type"] == "preflight"
        assert snapshot["next_check"]["channel"] == "NHL | X"

    def test_already_attempted_items_count_but_due_and_other_states_do_not(self):
        due = {"event_name": "Due", "state": "due",
               "next_automatic_check": {"label": "Due now", "timestamp": None}}
        service = make_preflight([due, item("Done", 1, state="completed"), item("Retry", 15, state="already_attempted")])

        assert service.get_widget_snapshot()["next_check"]["name"] == "Retry"

    def test_static_team_items_fall_back_to_the_team_name(self):
        team = item(None, 5)
        team["team_name"] = "Chicago Blackhawks"

        assert make_preflight([team]).get_widget_snapshot()["next_check"]["name"] == "Chicago Blackhawks"

    @pytest.mark.parametrize("kwargs", [{"enabled": False}, {"running": False}], ids=["disabled", "stopped"])
    def test_disabled_or_stopped_service_has_no_next_check(self, kwargs):
        assert make_preflight([item("x", 5)], **kwargs).get_widget_snapshot()["next_check"] is None

    def test_direct_thread_checks_are_listed_with_source_and_channel(self):
        active = {"k": {"dispatcharr_channel_id": 7, "event_name": "CHI at CAR", "team_name": None,
                        "channel_name": "NHL | CHI @ CAR", "started_at": 1_000.0, "bucket": "5m"}}

        checks = make_preflight(active=active).get_widget_snapshot()["active_checks"]

        assert checks == [{"source": "teamarr_preflight", "channel_id": 7, "event_name": "CHI at CAR",
                           "team_name": None, "channel_name": "NHL | CHI @ CAR", "started_at": 1_000.0}]

    def test_does_not_build_the_full_status(self):
        service = make_preflight([item("x", 5)])
        service.get_status = Mock(side_effect=AssertionError("get_status"))

        service.get_widget_snapshot()


# ---------------------------------------------------------------------------
# AutomatedStreamManager.get_widget_run_state
# ---------------------------------------------------------------------------

class TestAutomationRunState:
    def test_returns_only_the_small_fields_the_widget_needs(self):
        from apps.automation.automated_stream_manager import AutomatedStreamManager

        manager = AutomatedStreamManager()
        manager._start_run_status(forced=True, forced_period_id="p2")
        manager._update_run_status(stage="m3u_refresh", progress={"current": 2, "total": 4})
        with manager._run_status_lock:
            manager._run_status["run_snapshot"] = {"effective_profiles": [
                {"period_id": "p1", "period_name": "Hourly"},
                {"period_id": "p1", "period_name": "Hourly"},
                {"period_id": "p2", "period_name": "Overnight"},
            ], "huge": "x" * 10_000}

        state = manager.get_widget_run_state()

        assert set(state) == {"thread_alive", "state", "active", "stage", "stage_label", "progress",
                              "started_at", "forced_period_id", "periods"}
        assert state["state"] == "running" and state["active"] is True
        assert state["stage"] == "m3u_refresh"
        assert state["progress"] == {"current": 2, "total": 4, "percent": 50}
        assert state["forced_period_id"] == "p2"
        assert state["periods"] == [{"id": "p1", "name": "Hourly"}, {"id": "p2", "name": "Overnight"}]
        assert state["thread_alive"] is False


# ---------------------------------------------------------------------------
# Scheduled-event register hook in the queue executor
# ---------------------------------------------------------------------------

def run_queue_entry(metadata, result):
    executor = StreamCheckQueueExecutionMixin()
    executor.lock = threading.RLock()
    executor.abort_current_check = threading.Event()
    executor._should_isolate_teamarr_connectivity_abort = Mock(return_value=False)
    executor.check_single_channel = Mock(return_value=result)
    executor._fail_channel_check = Mock(side_effect=lambda cid, error, callback, **kw: callback())
    executor._complete_channel_check = Mock(side_effect=lambda cid, callback, **kw: callback())
    executor._run_specialized_queue_entry_owned({"channel_id": 9, "queue_entry_token": 1, "metadata": metadata})
    return executor


class TestScheduledEventHook:
    META = {"source": "auto_create", "scheduled_event_id": "evt-1", "program_name": "Goal Rush"}

    def test_successful_scheduled_check_is_recorded(self):
        run_queue_entry(dict(self.META), {"success": True, "channel_name": "Goal Rush"})

        latest = last_check_register.latest()
        assert (latest["type"], latest["name"], latest["channel"], latest["result"]) == (
            "scheduled_event", "Goal Rush", "Goal Rush", "completed")
        assert latest["duration_seconds"] is not None

    def test_failed_scheduled_check_is_recorded_as_failed(self):
        run_queue_entry(dict(self.META), {"success": False, "error": "no streams"})

        assert last_check_register.latest()["result"] == "failed"

    def test_skipped_results_are_not_checks(self):
        run_queue_entry(dict(self.META), {"success": True, "skipped": True, "skip_reason": "profile_disabled"})

        assert last_check_register.latest() is None

    def test_entries_without_a_scheduled_event_id_are_not_recorded(self):
        run_queue_entry({"source": "manual", "program_name": "x"}, {"success": True})

        assert last_check_register.latest() is None
