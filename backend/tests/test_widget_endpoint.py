"""GET /api/v1/stats/widget through the real Flask app with stubbed services."""

from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest

import apps.api.web_api as web_api
from apps.api import widget_handlers
from apps.api.middleware import InMemoryRateLimiter
from apps.telemetry import last_check_register

NOW = datetime.now().astimezone().replace(microsecond=0)


def heavy_status_bomb():
    return Mock(side_effect=AssertionError("get_status() must not be called by the widget endpoint"))


class Services:
    """Stub services whose heavy get_status() methods blow up if called."""

    def __init__(self):
        self.automation = Mock()
        self.automation.get_status = heavy_status_bomb()
        self.automation.get_widget_run_state.return_value = {
            "thread_alive": True, "state": "idle", "active": False, "stage": "idle",
            "stage_label": "Idle", "progress": {}, "started_at": None,
            "forced_period_id": None, "periods": [],
        }

        self.checker = Mock()
        self.checker.get_status = heavy_status_bomb()
        self.checker.get_widget_event_checks.return_value = {
            "entries": [], "progress_by_channel": {}, "stale_after_seconds": 300,
        }

        self.preflight = Mock()
        self.preflight.get_status = heavy_status_bomb()
        self.preflight.get_widget_snapshot.return_value = {
            "enabled": True, "running": True, "active_checks": [], "next_check": None,
        }

        self.scheduling = Mock()
        self.scheduling.get_status = heavy_status_bomb()
        self.scheduling.get_scheduled_events.return_value = []

        self.events_scheduler = Mock()
        self.events_scheduler.get_cached_events_snapshot.return_value = {"events": [], "stale": False}
        self.events_scheduler.get_cached_events.return_value = {"events": []}

        self.config = Mock()
        self.config.get_global_settings.return_value = {"regular_automation_enabled": True}


@pytest.fixture
def services(monkeypatch):
    stubs = Services()
    monkeypatch.setattr(web_api, "get_automation_manager", lambda: stubs.automation)
    monkeypatch.setattr(web_api, "get_stream_checker_service", lambda: stubs.checker)
    monkeypatch.setattr(web_api, "get_teamarr_preflight_service", lambda: stubs.preflight)
    monkeypatch.setattr(web_api, "get_scheduling_service", lambda: stubs.scheduling)
    monkeypatch.setattr(web_api, "get_events_scheduler", lambda: stubs.events_scheduler)
    monkeypatch.setattr(web_api, "get_automation_config_manager", lambda: stubs.config)
    monkeypatch.setattr(web_api, "API_RATE_LIMIT_ENABLED", False)
    widget_handlers.reset_next_check_cache()
    last_check_register.reset()
    last_check_register.ensure_seeded(lambda: [])
    yield stubs
    widget_handlers.reset_next_check_cache()
    last_check_register.reset()


@pytest.fixture
def client():
    return web_api.app.test_client()


@pytest.mark.parametrize("path", ["/api/v1/stats/widget", "/api/stats/widget"])
def test_both_routes_return_flat_json_with_no_store(client, services, path):
    response = client.get(path)

    assert response.status_code == 200
    assert response.is_json
    assert response.headers["Cache-Control"] == "no-store"
    body = response.get_json()
    assert body["schema"] == 1
    assert body["active"] is False
    assert "next_check_epoch" in body and "last_check_epoch" in body


def test_never_calls_any_heavy_get_status(client, services):
    response = client.get("/api/v1/stats/widget")

    assert response.status_code == 200
    for stub in (services.automation, services.checker, services.preflight, services.scheduling):
        stub.get_status.assert_not_called()


def test_reports_an_active_automation_run(client, services):
    services.automation.get_widget_run_state.return_value = {
        "thread_alive": True, "state": "running", "active": True, "stage": "m3u_refresh",
        "stage_label": "Refreshing M3U", "progress": {"current": 2, "total": 4, "percent": 50},
        "started_at": (NOW - timedelta(seconds=50)).replace(tzinfo=None).isoformat(),
        "forced_period_id": None, "periods": [{"id": "p1", "name": "Hourly Automation"}],
    }

    body = client.get("/api/v1/stats/widget").get_json()

    assert body["active"] is True
    assert (body["type"], body["name"], body["percent"]) == ("automation", "Hourly Automation", 50)


def test_direct_preflight_checks_are_merged_and_their_progress_is_requested(client, services):
    services.preflight.get_widget_snapshot.return_value = {
        "enabled": True, "running": True, "next_check": None,
        "active_checks": [{
            "source": "teamarr_preflight", "channel_id": 7, "event_name": "CHI at CAR",
            "team_name": None, "channel_name": "NHL | CHI @ CAR", "started_at": NOW.timestamp() - 20,
        }],
    }

    body = client.get("/api/v1/stats/widget").get_json()

    assert (body["type"], body["name"]) == ("preflight", "CHI at CAR")
    services.checker.get_widget_event_checks.assert_called_once_with(extra_channel_ids=[7])


def test_a_failing_source_is_nulled_not_fatal(client, services):
    services.automation.get_widget_run_state.side_effect = RuntimeError("boom")
    services.checker.get_widget_event_checks.side_effect = RuntimeError("boom")
    services.preflight.get_widget_snapshot.side_effect = RuntimeError("boom")

    response = client.get("/api/v1/stats/widget")

    assert response.status_code == 200
    assert response.get_json()["active"] is False


def test_builder_failure_returns_a_500(client, services, monkeypatch):
    monkeypatch.setattr(widget_handlers, "build_widget_snapshot", Mock(side_effect=RuntimeError("bug")))

    response = client.get("/api/v1/stats/widget")

    assert response.status_code == 500
    assert response.get_json()["code"] == "internal_error"


def test_last_check_comes_from_the_register(client, services):
    last_check_register.record(
        check_type="preflight", name="CHI at CAR", channel="NHL | CHI @ CAR",
        finished_at=NOW - timedelta(minutes=2), duration_seconds=182,
    )

    body = client.get("/api/v1/stats/widget").get_json()

    assert body["last_check_type"] == "preflight"
    assert body["last_check_duration_seconds"] == 182


class TestNextCheck:
    def test_earliest_across_sources(self, client, services):
        later = (NOW + timedelta(minutes=50)).replace(tzinfo=None).isoformat()
        services.events_scheduler.get_cached_events_snapshot.return_value = {
            "events": [{"time": later, "period_name": "Hourly Automation"}], "stale": False,
        }
        services.scheduling.get_scheduled_events.return_value = [
            {"schedule_type": "check", "check_time": (NOW + timedelta(minutes=30)).isoformat(),
             "program_title": "Show", "channel_name": "Goal Rush"},
        ]
        services.preflight.get_widget_snapshot.return_value = {
            "enabled": True, "running": True, "active_checks": [],
            "next_check": {"type": "preflight", "name": "OU at FLA", "channel": "NCAAF | OU @ FLA",
                           "at": (NOW + timedelta(minutes=5)).isoformat()},
        }

        assert client.get("/api/v1/stats/widget").get_json()["next_check_name"] == "OU at FLA"

        services.preflight.get_widget_snapshot.return_value["next_check"] = None
        widget_handlers.reset_next_check_cache()
        assert client.get("/api/v1/stats/widget").get_json()["next_check_name"] == "Show"

        services.scheduling.get_scheduled_events.return_value = []
        widget_handlers.reset_next_check_cache()
        body = client.get("/api/v1/stats/widget").get_json()
        assert (body["next_check_type"], body["next_check_name"]) == ("automation", "Hourly Automation")

    def test_disabled_automation_has_no_automation_candidate(self, client, services):
        services.config.get_global_settings.return_value = {"regular_automation_enabled": False}
        services.events_scheduler.get_cached_events_snapshot.return_value = {
            "events": [{"time": (NOW + timedelta(minutes=5)).replace(tzinfo=None).isoformat(),
                        "period_name": "Hourly"}],
            "stale": False,
        }

        assert client.get("/api/v1/stats/widget").get_json()["next_check_type"] is None
        services.events_scheduler.get_cached_events_snapshot.assert_not_called()

    def test_monitoring_scheduled_events_are_ignored(self, client, services):
        services.scheduling.get_scheduled_events.return_value = [
            {"schedule_type": "monitoring", "check_time": (NOW + timedelta(minutes=5)).isoformat(),
             "program_title": "Monitor me", "channel_name": "X"},
        ]

        assert client.get("/api/v1/stats/widget").get_json()["next_check_type"] is None

    def test_empty_or_stale_snapshot_recomputes_once(self, client, services):
        services.events_scheduler.get_cached_events_snapshot.return_value = {"events": [], "stale": True}
        services.events_scheduler.get_cached_events.return_value = {"events": [
            {"time": (NOW + timedelta(minutes=5)).replace(tzinfo=None).isoformat(), "period_name": "Hourly"},
        ]}

        body = client.get("/api/v1/stats/widget").get_json()

        assert body["next_check_name"] == "Hourly"
        services.events_scheduler.get_cached_events.assert_called_once()

    def test_candidates_are_cached_for_a_few_seconds(self, client, services):
        client.get("/api/v1/stats/widget")
        client.get("/api/v1/stats/widget")
        client.get("/api/v1/stats/widget")

        assert services.config.get_global_settings.call_count == 1
        assert services.scheduling.get_scheduled_events.call_count == 1

    def test_one_failing_next_source_does_not_hide_the_other(self, client, services):
        services.config.get_global_settings.side_effect = RuntimeError("db down")
        services.scheduling.get_scheduled_events.return_value = [
            {"schedule_type": "check", "check_time": (NOW + timedelta(minutes=30)).isoformat(),
             "program_title": "Show", "channel_name": "Goal Rush"},
        ]

        assert client.get("/api/v1/stats/widget").get_json()["next_check_name"] == "Show"


def iso_in(minutes):
    return (NOW + timedelta(minutes=minutes)).isoformat()


def naive_in(minutes):
    return (NOW + timedelta(minutes=minutes)).replace(tzinfo=None).isoformat()


class TestUpcoming:
    def test_four_future_automation_events_give_three_items(self, client, services):
        services.events_scheduler.get_cached_events_snapshot.return_value = {
            "events": [{"time": naive_in(m), "period_name": "Hourly Automation"} for m in (5, 65, 125, 185)],
            "stale": False,
        }

        body = client.get("/api/v1/stats/widget").get_json()

        assert [i["type"] for i in body["upcoming"]] == ["automation"] * 3
        assert [i["epoch"] for i in body["upcoming"]] == [
            int((NOW + timedelta(minutes=m)).timestamp()) for m in (5, 65, 125)
        ]
        assert body["upcoming"][0]["epoch"] == body["next_check_epoch"]

    def test_monitoring_scheduled_events_are_left_out_of_the_list(self, client, services):
        services.scheduling.get_scheduled_events.return_value = [
            {"schedule_type": "check", "check_time": iso_in(10), "program_title": "Show", "channel_name": "Goal Rush"},
            {"schedule_type": "monitoring", "check_time": iso_in(4), "program_title": "Monitor me", "channel_name": "X"},
        ]
        services.events_scheduler.get_cached_events_snapshot.return_value = {
            "events": [{"time": naive_in(20), "period_name": "Hourly"}], "stale": False,
        }

        body = client.get("/api/v1/stats/widget").get_json()

        assert [(i["type"], i["name"]) for i in body["upcoming"]] == [
            ("scheduled_event", "Show"), ("automation", "Hourly"),
        ]

    def test_preflight_list_is_merged_with_the_other_sources_in_time_order(self, client, services):
        services.events_scheduler.get_cached_events_snapshot.return_value = {
            "events": [{"time": naive_in(30), "period_name": "Hourly"}], "stale": False,
        }
        services.preflight.get_widget_snapshot.return_value = {
            "enabled": True, "running": True, "active_checks": [],
            "next_check": {"type": "preflight", "name": "A", "channel": "NHL | A", "at": iso_in(5)},
            "upcoming_checks": [
                {"type": "preflight", "name": "A", "channel": "NHL | A", "at": iso_in(5)},
                {"type": "preflight", "name": "B", "channel": "NHL | B", "at": iso_in(45)},
            ],
        }

        body = client.get("/api/v1/stats/widget").get_json()

        assert [(i["type"], i["name"]) for i in body["upcoming"]] == [
            ("preflight", "A"), ("automation", "Hourly"), ("preflight", "B"),
        ]

    def test_a_preflight_snapshot_with_only_next_check_still_contributes(self, client, services):
        services.preflight.get_widget_snapshot.return_value = {
            "enabled": True, "running": True, "active_checks": [],
            "next_check": {"type": "preflight", "name": "A", "channel": "NHL | A", "at": iso_in(5)},
        }

        body = client.get("/api/v1/stats/widget").get_json()

        assert [i["name"] for i in body["upcoming"]] == ["A"]

    def test_one_failing_source_still_lets_the_others_fill_the_list(self, client, services):
        services.config.get_global_settings.side_effect = RuntimeError("db down")
        services.scheduling.get_scheduled_events.return_value = [
            {"schedule_type": "check", "check_time": iso_in(m), "program_title": f"Show {m}", "channel_name": "C"}
            for m in (10, 20)
        ]

        body = client.get("/api/v1/stats/widget").get_json()

        assert [i["name"] for i in body["upcoming"]] == ["Show 10", "Show 20"]

    def test_nothing_scheduled_gives_an_empty_list(self, client, services):
        assert client.get("/api/v1/stats/widget").get_json()["upcoming"] == []

    def test_the_candidate_cache_still_holds_with_the_longer_lists(self, client, services):
        services.events_scheduler.get_cached_events_snapshot.return_value = {
            "events": [{"time": naive_in(m), "period_name": "Hourly"} for m in (5, 65, 125)], "stale": False,
        }

        for _ in range(3):
            client.get("/api/v1/stats/widget")

        assert services.config.get_global_settings.call_count == 1
        assert services.scheduling.get_scheduled_events.call_count == 1

    def test_response_stays_small_with_a_full_list(self, client, services):
        services.events_scheduler.get_cached_events_snapshot.return_value = {
            "events": [{"time": naive_in(m), "period_name": "Evening Automation"} for m in (5, 65, 125)],
            "stale": False,
        }

        assert len(client.get("/api/v1/stats/widget").data) < 2048


def test_rate_limiting_applies_normally(client, services, monkeypatch):
    monkeypatch.setattr(web_api, "API_RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(
        web_api,
        "api_rate_limiter",
        InMemoryRateLimiter(max_requests=2, window_seconds=60, max_buckets=16),
    )

    statuses = [client.get("/api/v1/stats/widget").status_code for _ in range(3)]

    assert statuses == [200, 200, 429]
    assert client.get("/api/v1/stats/widget").get_json()["code"] == "rate_limited"
