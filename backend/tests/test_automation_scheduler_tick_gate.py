"""Scheduler ticks with nothing due must not publish a run or wait for the checker.

Every 60 seconds the automation loop calls ``run_automation_cycle``. A tick with
no due period used to publish a new run, queue behind a busy Stream Checker for
minutes (polling the full checker status), and only then end as "skipped". That
replaced the last real run's status, held the checker busy-wait, and bumped
``last_playlist_update`` although no playlist was refreshed.
"""

import copy
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest

import apps.automation.automated_stream_manager as asm
from apps.automation.automated_stream_manager import AutomatedStreamManager

INTERVAL_PERIOD = {
    "id": "p1",
    "name": "Hourly",
    "enabled": True,
    "schedule": {"type": "interval", "value": 60},
    "catch_up_missed_runs": False,
    "missed_run_grace_minutes": 0,
}


def make_config(*, enabled=True, assigned=("p1",), periods=None):
    config = Mock()
    config.get_global_settings.return_value = {"regular_automation_enabled": enabled}
    config.get_assigned_period_ids.return_value = set(assigned)
    config.get_all_periods.return_value = (
        [dict(INTERVAL_PERIOD)] if periods is None else periods
    )
    config.get_effective_configuration.return_value = None
    return config


@pytest.fixture
def env(monkeypatch):
    """A non-legacy manager wired to mock config, UDI and Stream Checker."""
    manager = AutomatedStreamManager()
    manager._explicit_config_file = False
    manager._save_state = Mock()
    manager._is_maintenance_window_active = Mock(return_value=False)
    manager._get_active_teamarr_event_window = Mock(return_value=None)
    monkeypatch.setattr(asm, "CONFIG_DIR", Path("/app/data"))
    monkeypatch.setattr("apps.automation.automated_stream_manager.time.sleep", lambda _s: None)

    checker = Mock()
    checker.is_busy.return_value = False
    checker.begin_automation_cycle_operation.return_value = True

    udi = Mock()
    udi.get_channels.return_value = []

    class Env:
        pass

    state = Env()
    state.manager = manager
    state.checker = checker
    state.udi = udi
    state.config = make_config()

    def install(config=None):
        if config is not None:
            state.config = config
        monkeypatch.setattr(
            "apps.automation.automated_stream_manager.get_automation_config_manager",
            lambda: state.config,
        )
        monkeypatch.setattr(
            "apps.automation.automation_config_manager.get_automation_config_manager",
            lambda: state.config,
        )

    install()
    monkeypatch.setattr("apps.automation.automated_stream_manager.get_udi_manager", lambda: udi)
    monkeypatch.setattr(
        "apps.automation.automated_stream_manager.get_stream_checker_service", lambda: checker
    )
    monkeypatch.setattr(
        "apps.stream.stream_checker_service.get_stream_checker_service", lambda: checker
    )
    state.install = install
    return state


def last_run_status(manager):
    with manager._run_status_lock:
        return copy.deepcopy(manager._run_status)


def make_due(manager, period_id="p1"):
    manager.period_last_run[period_id] = datetime.now() - timedelta(hours=2)


def make_not_due(manager, period_id="p1"):
    manager.period_last_run[period_id] = datetime.now() - timedelta(minutes=1)


def assert_checker_untouched(checker):
    checker.is_busy.assert_not_called()
    checker.get_status.assert_not_called()
    checker.begin_automation_cycle_operation.assert_not_called()


class TestNothingDueTick:
    def test_busy_checker_does_not_hold_or_replace_the_run(self, env):
        manager = env.manager
        make_not_due(manager)
        env.checker.is_busy.return_value = True
        before = last_run_status(manager)
        playlist_update = datetime(2026, 1, 1, 12, 0, 0)
        manager.last_playlist_update = playlist_update

        manager.run_automation_cycle()

        assert last_run_status(manager) == before
        assert_checker_untouched(env.checker)
        assert manager.last_scheduler_tick["outcome"] == "nothing_due"
        assert manager.last_playlist_update == playlist_update

    def test_previous_real_run_status_survives_noop_ticks(self, env):
        manager = env.manager
        make_due(manager)
        manager.run_automation_cycle()
        real_run = last_run_status(manager)
        assert real_run["state"] == "skipped"

        make_not_due(manager)
        manager.run_automation_cycle()
        manager.run_automation_cycle()

        assert last_run_status(manager)["run_id"] == real_run["run_id"]

    def test_get_status_exposes_last_scheduler_tick(self, env):
        env.manager.run_automation_cycle()

        tick = env.manager.get_status()["last_scheduler_tick"]
        assert tick["outcome"] == "nothing_due"
        assert tick["message"] == "No automation periods were due"
        assert tick["at"]

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"assigned": ()},
            {"periods": [{**INTERVAL_PERIOD, "enabled": False}]},
        ],
        ids=["unassigned-period", "disabled-period"],
    )
    def test_unassigned_or_disabled_due_period_does_not_open_the_gate(self, env, kwargs):
        make_due(env.manager)
        env.install(make_config(**kwargs))

        env.manager.run_automation_cycle()

        assert_checker_untouched(env.checker)
        assert env.manager.last_scheduler_tick["outcome"] == "nothing_due"

    def test_orphaned_retry_state_is_dropped_on_a_noop_tick(self, env):
        manager = env.manager
        make_not_due(manager)
        manager._scheduler_retry_state = {
            "gone": {"attempt": 1, "exhausted": False, "pending_channel_ids": [1]},
        }

        manager.run_automation_cycle()

        assert manager._scheduler_retry_state == {}
        manager._save_state.assert_called()


class TestSkippedTickReasons:
    def test_disabled(self, env):
        env.install(make_config(enabled=False))
        before = last_run_status(env.manager)

        env.manager.run_automation_cycle()

        assert env.manager.last_scheduler_tick["outcome"] == "disabled"
        assert last_run_status(env.manager) == before
        assert_checker_untouched(env.checker)

    def test_maintenance_window(self, env):
        env.manager._is_maintenance_window_active.return_value = True
        before = last_run_status(env.manager)

        env.manager.run_automation_cycle()

        assert env.manager.last_scheduler_tick["outcome"] == "maintenance"
        assert last_run_status(env.manager) == before
        assert_checker_untouched(env.checker)

    def test_teamarr_event_window(self, env):
        env.manager._get_active_teamarr_event_window.return_value = {"event_name": "NHL TB@MIN"}
        before = last_run_status(env.manager)

        env.manager.run_automation_cycle()

        tick = env.manager.last_scheduler_tick
        assert tick["outcome"] == "teamarr_window"
        assert tick["event_name"] == "NHL TB@MIN"
        assert last_run_status(env.manager) == before
        assert_checker_untouched(env.checker)


class TestDueWorkStillRuns:
    def test_due_period_with_busy_checker_queues_and_keeps_its_run_id(self, env):
        manager = env.manager
        make_due(manager)
        manager.automation_running = True
        observed = []

        def busy_then_idle():
            observed.append(last_run_status(manager))
            return len(observed) < 4

        env.checker.is_busy.side_effect = busy_then_idle

        manager.run_automation_cycle()

        # First reading happens before the run is marked queued; the next
        # two see the same queued intent, and the run then resumes.
        assert [run["state"] for run in observed[1:3]] == ["queued", "queued"]
        assert len({run["run_id"] for run in observed}) == 1
        assert last_run_status(manager)["run_id"] == observed[0]["run_id"]
        env.checker.begin_automation_cycle_operation.assert_called_once_with()
        env.checker.end_automation_cycle_operation.assert_called_once_with()
        env.checker.get_status.assert_not_called()
        assert manager.last_scheduler_tick is None

    def test_due_period_with_busy_checker_and_direct_call_stays_queued(self, env):
        manager = env.manager
        make_due(manager)
        env.checker.is_busy.return_value = True

        manager.run_automation_cycle()

        assert last_run_status(manager)["state"] == "queued"
        env.checker.begin_automation_cycle_operation.assert_not_called()
        env.checker.get_status.assert_not_called()

    @pytest.mark.parametrize(
        "kwargs",
        [{"forced": True}, {"forced": True, "forced_period_id": "p1"}, {"forced_period_id": "p1"}],
        ids=["forced", "forced-period", "period-only"],
    )
    def test_forced_run_starts_even_when_nothing_is_due(self, env, kwargs):
        manager = env.manager
        make_not_due(manager)
        before = last_run_status(manager)

        manager.run_automation_cycle(**kwargs)

        run = last_run_status(manager)
        assert run["run_id"] != before["run_id"]
        assert manager.last_scheduler_tick is None
        env.checker.begin_automation_cycle_operation.assert_called_once_with()

    def test_due_retry_passes_the_gate(self, env):
        manager = env.manager
        make_not_due(manager)
        manager._scheduler_retry_state = {
            "p1": {
                "period_name": "Hourly",
                "attempt": 1,
                "max_attempts": 3,
                "next_retry_at": (datetime.now() - timedelta(seconds=1)).isoformat(),
                "exhausted": False,
                "pending_channel_ids": [101],
            },
        }

        manager.run_automation_cycle()

        env.checker.begin_automation_cycle_operation.assert_called_once_with()
        assert manager.last_scheduler_tick is None

    def test_retry_not_yet_due_does_not_pass_the_gate(self, env):
        manager = env.manager
        make_not_due(manager)
        manager._scheduler_retry_state = {
            "p1": {
                "attempt": 1,
                "max_attempts": 3,
                "next_retry_at": (datetime.now() + timedelta(minutes=5)).isoformat(),
                "exhausted": False,
                "pending_channel_ids": [101],
            },
        }

        manager.run_automation_cycle()

        assert_checker_untouched(env.checker)
        assert manager.last_scheduler_tick["outcome"] == "nothing_due"

    def test_due_period_without_channels_does_not_touch_last_playlist_update(self, env):
        manager = env.manager
        make_due(manager)
        playlist_update = datetime(2026, 1, 1, 12, 0, 0)
        manager.last_playlist_update = playlist_update

        manager.run_automation_cycle()

        assert last_run_status(manager)["state"] == "skipped"
        assert manager.last_playlist_update == playlist_update

    def test_gate_errors_fall_back_to_running_the_cycle(self, env):
        manager = env.manager
        env.config.get_all_periods.side_effect = RuntimeError("config store unavailable")

        manager.run_automation_cycle()

        env.checker.begin_automation_cycle_operation.assert_called_once_with()
        assert manager.last_scheduler_tick is None


class TestLegacyConfigFileMode:
    def test_legacy_mode_skips_the_gate(self, env, monkeypatch):
        manager = env.manager
        manager._explicit_config_file = True
        manager.refresh_playlists = Mock(return_value=True)
        manager.discover_and_assign_streams = Mock(return_value={})
        env.config.get_all_periods.side_effect = AssertionError("gate must not run")

        manager.run_automation_cycle()

        env.checker.begin_automation_cycle_operation.assert_called_once_with()
        assert manager.last_scheduler_tick is None


class TestStreamCheckerIsBusy:
    @pytest.fixture
    def service(self, tmp_path, monkeypatch):
        from apps.stream import stream_checker_service as scs

        monkeypatch.setattr(scs, "CONFIG_DIR", tmp_path)
        return scs.StreamCheckerService()

    def test_idle_checker_is_not_busy_and_can_be_reserved(self, service):
        assert service.is_busy() is False
        assert service.begin_automation_cycle_operation() is True

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda s: setattr(s, "checking", True),
            lambda s: setattr(s, "_automation_cycle_active", True),
            lambda s: setattr(s, "_single_channel_check_active", True),
            lambda s: setattr(s, "_single_stream_check_active", True),
            lambda s: setattr(s, "_sync_batch_execution_active", True),
            lambda s: s.sync_batch_state.update({"active": True}),
            lambda s: s.check_queue.queued.update({1: {}}),
            lambda s: s.check_queue.in_progress.update({1: {}}),
            lambda s: s.check_queue.stats.update({"current_channel": 5}),
            lambda s: setattr(s, "_active_queue_entry_executions", {1: object()}),
        ],
        ids=[
            "checking",
            "automation-cycle",
            "single-channel",
            "single-stream",
            "sync-execution",
            "sync-batch",
            "queued",
            "in-progress",
            "current-channel",
            "queue-execution",
        ],
    )
    def test_busy_exactly_when_a_reservation_would_be_refused(self, service, mutate):
        mutate(service)

        assert service.is_busy() is True
        assert service.begin_automation_cycle_operation() is False

    def test_is_busy_does_not_build_the_full_status(self, service, monkeypatch):
        monkeypatch.setattr(
            service, "get_status", Mock(side_effect=AssertionError("get_status called"))
        )
        service.is_busy()


class TestAssignedPeriodIds:
    def test_union_of_channel_and_group_assignments(self):
        from apps.automation.automation_config_manager import get_automation_config_manager

        manager = get_automation_config_manager()
        manager._set_config_dict("channel_period_assignments", {"5": {"1": "profile-a"}})
        manager._set_config_dict(
            "group_period_assignments", {"9": {"2": "profile-a", "3": "profile-b"}}
        )

        assert manager.get_assigned_period_ids() == {"1", "2", "3"}

    def test_empty_when_nothing_is_assigned(self):
        from apps.automation.automation_config_manager import get_automation_config_manager

        assert get_automation_config_manager().get_assigned_period_ids() == set()
