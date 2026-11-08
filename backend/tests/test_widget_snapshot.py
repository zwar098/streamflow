"""Pure builder behind GET /api/v1/stats/widget."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from apps.orchestration.widget_snapshot import (
    MAX_TEXT_LENGTH,
    build_widget_snapshot,
    period_display_name,
    to_local_aware,
)

NOW = datetime.now().astimezone().replace(microsecond=0)

ALL_KEYS = {
    "schema", "generated_at", "generated_epoch", "active", "active_count",
    "type", "name", "channel_name", "stage", "step", "steps", "current", "total",
    "percent", "started_at", "started_epoch", "queued",
    "last_check_type", "last_check_name", "last_check_channel", "last_check_at",
    "last_check_epoch", "last_check_result", "last_check_duration_seconds",
    "next_check_type", "next_check_name", "next_check_channel", "next_check_at",
    "next_check_epoch", "upcoming",
}


def naive_local(dt):
    return dt.astimezone().replace(tzinfo=None).isoformat()


def automation(**overrides):
    base = {
        "thread_alive": True,
        "state": "running",
        "active": True,
        "stage": "m3u_refresh",
        "stage_label": "Refreshing M3U",
        "progress": {"current": 2, "total": 4, "percent": 50},
        "started_at": naive_local(NOW - timedelta(seconds=53)),
        "forced_period_id": None,
        "periods": [{"id": "p1", "name": "Hourly Automation"}],
    }
    base.update(overrides)
    return base


def queue_entry(source="teamarr_preflight", channel_id=1, started_offset=60, **overrides):
    entry = {
        "channel_id": channel_id,
        "source": source,
        "scheduled_event_id": None,
        "program_name": None,
        "event_name": "Tampa Bay Lightning at Minnesota Wild",
        "team_name": None,
        "channel_name": "NHL | TB @ MIN",
        "started_at": naive_local(NOW - timedelta(seconds=started_offset)),
    }
    entry.update(overrides)
    return entry


def checks(*entries, progress=None, stale_after=300):
    return {
        "entries": list(entries),
        "progress_by_channel": progress or {},
        "stale_after_seconds": stale_after,
    }


def progress_for(channel_id, age_seconds=5, **overrides):
    value = {
        "channel_name": "NHL | TB @ MIN",
        "step": "Analyzing streams",
        "status": "checking",
        "current_stream": 2,
        "total_streams": 6,
        "percentage": 33.3,
        "timestamp": naive_local(NOW - timedelta(seconds=age_seconds)),
    }
    value.update(overrides)
    return {channel_id: value}


def build(**kwargs):
    kwargs.setdefault("automation", None)
    kwargs.setdefault("event_checks", None)
    kwargs.setdefault("last_check", None)
    kwargs.setdefault("next_candidates", [])
    kwargs.setdefault("now", NOW)
    return build_widget_snapshot(**kwargs)


class TestIdle:
    def test_every_key_present_with_nulls(self):
        snapshot = build()

        assert set(snapshot) == ALL_KEYS
        assert snapshot["schema"] == 1
        assert snapshot["active"] is False
        assert snapshot["active_count"] == 0
        assert snapshot["queued"] is False
        assert snapshot["upcoming"] == []
        for key in ALL_KEYS - {"schema", "generated_at", "generated_epoch", "active", "active_count", "queued",
                               "upcoming"}:
            assert snapshot[key] is None, key
        assert snapshot["generated_epoch"] == int(NOW.timestamp())


class TestAutomationActivity:
    def test_queued_run_is_never_active_but_sets_queued(self):
        snapshot = build(automation=automation(state="queued", active=False, stage="queued"))

        assert snapshot["active"] is False
        assert snapshot["queued"] is True

    @pytest.mark.parametrize("stage", ["starting", "settings", "period_discovery"])
    def test_pre_work_stages_are_not_active(self, stage):
        assert build(automation=automation(stage=stage))["active"] is False

    @pytest.mark.parametrize("state", ["skipped", "completed", "completed_degraded", "failed", "aborted"])
    def test_terminal_states_are_not_active(self, state):
        assert build(automation=automation(state=state, active=False))["active"] is False

    def test_requires_a_live_automation_thread(self):
        assert build(automation=automation(thread_alive=False))["active"] is False

    def test_real_run_uses_nested_progress_and_stage_index(self):
        snapshot = build(automation=automation())

        assert snapshot["active"] is True
        assert snapshot["type"] == "automation"
        assert snapshot["name"] == "Hourly Automation"
        assert snapshot["channel_name"] is None
        assert snapshot["stage"] == "Refreshing M3U"
        assert (snapshot["step"], snapshot["steps"]) == (3, 8)
        assert (snapshot["current"], snapshot["total"], snapshot["percent"]) == (2, 4, 50)
        assert snapshot["started_epoch"] == int((NOW - timedelta(seconds=53)).timestamp())

    def test_unknown_total_reports_null_progress(self):
        snapshot = build(automation=automation(progress={"current": 0, "total": None, "percent": 0}))

        assert snapshot["active"] is True
        assert (snapshot["current"], snapshot["total"], snapshot["percent"]) == (None, None, None)

    def test_forced_run_is_named_for_its_period(self):
        snapshot = build(
            automation=automation(
                forced_period_id="p2",
                periods=[{"id": "p1", "name": "Hourly"}, {"id": "p2", "name": "Overnight"}],
            )
        )

        assert snapshot["name"] == "Overnight"

    def test_scheduled_run_with_two_periods_gets_a_plus_n_suffix(self):
        snapshot = build(
            automation=automation(
                periods=[{"id": "p1", "name": "Hourly Automation"}, {"id": "p2", "name": "Overnight"}],
            )
        )

        assert snapshot["name"] == "Hourly Automation +1"

    def test_duplicate_period_names_are_collapsed(self):
        assert period_display_name([{"id": 1, "name": "A"}, {"id": 2, "name": "A"}]) == "A"

    def test_no_period_names_falls_back_to_automation(self):
        assert build(automation=automation(periods=[]))["name"] == "Automation"


class TestEventActivity:
    def test_preflight_from_the_queue(self):
        snapshot = build(
            event_checks=checks(queue_entry(), progress=progress_for(1)),
        )

        assert snapshot["active"] is True
        assert snapshot["type"] == "preflight"
        assert snapshot["name"] == "Tampa Bay Lightning at Minnesota Wild"
        assert snapshot["channel_name"] == "NHL | TB @ MIN"
        assert snapshot["stage"] == "Analyzing streams"
        assert (snapshot["current"], snapshot["total"], snapshot["percent"]) == (2, 6, 33)

    def test_preflight_falls_back_to_team_then_program_name(self):
        entry = queue_entry(event_name=None, team_name="Wild", program_name="Fallback")
        assert build(event_checks=checks(entry))["name"] == "Wild"
        entry = queue_entry(event_name=None, team_name=None, program_name="Fallback")
        assert build(event_checks=checks(entry))["name"] == "Fallback"

    def test_direct_thread_preflight_uses_an_epoch_start(self):
        started = (NOW - timedelta(seconds=30)).timestamp()
        entry = queue_entry(started_at=started)

        snapshot = build(event_checks=checks(entry))

        assert snapshot["type"] == "preflight"
        assert snapshot["started_epoch"] == int(started)

    def test_scheduled_event(self):
        entry = queue_entry(
            source="auto_create",
            scheduled_event_id="evt-1",
            event_name=None,
            program_name="Premier League Goal Rush",
            channel_name="Goal Rush",
        )

        snapshot = build(event_checks=checks(entry))

        assert snapshot["type"] == "scheduled_event"
        assert snapshot["name"] == "Premier League Goal Rush"
        assert snapshot["channel_name"] == "Goal Rush"

    def test_manual_single_channel_check_is_out_of_scope(self):
        entry = queue_entry(source=None, event_name=None, program_name="Manual")

        assert build(event_checks=checks(entry))["active"] is False

    def test_three_parallel_preflights_report_the_earliest_as_primary(self):
        snapshot = build(
            event_checks=checks(
                queue_entry(channel_id=1, event_name="Second", started_offset=40),
                queue_entry(channel_id=2, event_name="First", started_offset=90),
                queue_entry(channel_id=3, event_name="Third", started_offset=10),
            )
        )

        assert snapshot["active_count"] == 3
        assert snapshot["name"] == "First"

    def test_same_channel_is_not_counted_twice(self):
        snapshot = build(event_checks=checks(queue_entry(channel_id=1), queue_entry(channel_id=1)))

        assert snapshot["active_count"] == 1

    def test_automation_outranks_a_concurrent_preflight(self):
        snapshot = build(automation=automation(), event_checks=checks(queue_entry()))

        assert snapshot["active_count"] == 2
        assert snapshot["type"] == "automation"

    def test_preflight_outranks_a_scheduled_event_even_if_later(self):
        scheduled = queue_entry(
            source="auto_create", scheduled_event_id="e", channel_id=9,
            event_name=None, program_name="Show", started_offset=300,
        )
        snapshot = build(event_checks=checks(scheduled, queue_entry(channel_id=1, started_offset=5)))

        assert snapshot["type"] == "preflight"

    def test_stale_progress_keeps_the_check_active_but_hides_numbers(self):
        snapshot = build(
            event_checks=checks(queue_entry(), progress=progress_for(1, age_seconds=900), stale_after=300),
        )

        assert snapshot["active"] is True
        assert snapshot["stage"] == "Checking"
        assert (snapshot["current"], snapshot["total"], snapshot["percent"]) == (None, None, None)

    def test_progress_flagged_stale_hides_numbers(self):
        snapshot = build(event_checks=checks(queue_entry(), progress=progress_for(1, stale=True)))

        assert snapshot["stage"] == "Checking"
        assert snapshot["percent"] is None

    def test_progress_without_ownership_never_makes_a_check_active(self):
        snapshot = build(event_checks=checks(progress=progress_for(1)))

        assert snapshot["active"] is False


class TestNextCheck:
    def candidate(self, check_type, at, name="x"):
        return {"type": check_type, "name": name, "channel": None, "at": at}

    def test_earliest_candidate_wins(self):
        snapshot = build(
            next_candidates=[
                self.candidate("automation", NOW + timedelta(minutes=50), "auto"),
                self.candidate("preflight", NOW + timedelta(minutes=5), "pre"),
                self.candidate("scheduled_event", NOW + timedelta(minutes=20), "sched"),
            ]
        )

        assert (snapshot["next_check_type"], snapshot["next_check_name"]) == ("preflight", "pre")
        assert snapshot["next_check_epoch"] == int((NOW + timedelta(minutes=5)).timestamp())

    def test_ties_prefer_automation_then_preflight(self):
        at = NOW + timedelta(minutes=5)
        snapshot = build(
            next_candidates=[
                self.candidate("scheduled_event", at),
                self.candidate("preflight", at),
                self.candidate("automation", at),
            ]
        )

        assert snapshot["next_check_type"] == "automation"

    def test_just_overdue_items_still_show_but_old_ones_are_dropped(self):
        recent = self.candidate("preflight", NOW - timedelta(seconds=60), "recent")
        old = self.candidate("automation", NOW - timedelta(seconds=120), "old")

        assert build(next_candidates=[recent, old])["next_check_name"] == "recent"
        assert build(next_candidates=[old])["next_check_type"] is None

    def test_unknown_types_and_unparseable_times_are_ignored(self):
        snapshot = build(
            next_candidates=[
                self.candidate("monitoring", NOW + timedelta(minutes=1)),
                self.candidate("preflight", "not a time"),
                {"type": "automation"},
            ]
        )

        assert snapshot["next_check_type"] is None


class TestUpcoming:
    @staticmethod
    def cand(kind, minutes, name=None, channel=None):
        return {"type": kind, "name": name or f"{kind}-{minutes}", "channel": channel,
                "at": NOW + timedelta(minutes=minutes)}

    def test_no_candidates_gives_an_empty_list_with_the_key_present(self):
        snapshot = build(next_candidates=[])

        assert snapshot["upcoming"] == []

    def test_the_three_earliest_win_in_order_whatever_the_input_order(self):
        snapshot = build(next_candidates=[
            self.cand("automation", 50), self.cand("preflight", 5), self.cand("scheduled_event", 30),
            self.cand("automation", 10), self.cand("preflight", 40),
        ])

        assert [(i["type"], i["name"]) for i in snapshot["upcoming"]] == [
            ("preflight", "preflight-5"), ("automation", "automation-10"), ("scheduled_event", "scheduled_event-30"),
        ]

    def test_fewer_than_three_candidates_gives_a_shorter_list(self):
        assert len(build(next_candidates=[self.cand("automation", 5), self.cand("preflight", 6)])["upcoming"]) == 2

    def test_equal_times_order_automation_then_preflight_then_scheduled_event(self):
        snapshot = build(next_candidates=[
            self.cand("scheduled_event", 5), self.cand("preflight", 5), self.cand("automation", 5),
        ])

        assert [i["type"] for i in snapshot["upcoming"]] == ["automation", "preflight", "scheduled_event"]

    def test_overdue_candidates_are_dropped_but_just_late_ones_stay(self):
        late = self.cand("automation", 0, name="just late")
        late["at"] = NOW - timedelta(seconds=30)
        old = self.cand("preflight", 0, name="too old")
        old["at"] = NOW - timedelta(seconds=300)

        snapshot = build(next_candidates=[old, late])

        assert [i["name"] for i in snapshot["upcoming"]] == ["just late"]

    def test_exact_duplicates_appear_once(self):
        snapshot = build(next_candidates=[
            self.cand("automation", 5, name="Hourly"), self.cand("automation", 5, name="Hourly"),
            self.cand("automation", 65, name="Hourly"),
        ])

        assert [i["epoch"] for i in snapshot["upcoming"]] == [
            int((NOW + timedelta(minutes=5)).timestamp()), int((NOW + timedelta(minutes=65)).timestamp()),
        ]

    def test_same_name_at_different_times_fills_several_slots(self):
        snapshot = build(next_candidates=[self.cand("automation", m, name="Hourly") for m in (5, 65, 125, 185)])

        assert [i["name"] for i in snapshot["upcoming"]] == ["Hourly"] * 3

    def test_first_item_is_the_next_check(self):
        snapshot = build(next_candidates=[
            self.cand("scheduled_event", 5, channel="Goal Rush"), self.cand("preflight", 5, channel="NHL"),
            self.cand("automation", 9),
        ])

        first = snapshot["upcoming"][0]
        assert (first["type"], first["name"], first["channel"], first["at"], first["epoch"]) == (
            snapshot["next_check_type"], snapshot["next_check_name"], snapshot["next_check_channel"],
            snapshot["next_check_at"], snapshot["next_check_epoch"],
        )

    def test_items_have_exactly_the_documented_keys_and_formats(self):
        item = build(next_candidates=[self.cand("preflight", 5, channel="NHL | X")])["upcoming"][0]

        assert set(item) == {"type", "name", "channel", "at", "epoch"}
        parsed = datetime.fromisoformat(item["at"])
        assert parsed.utcoffset() is not None
        assert int(parsed.timestamp()) == item["epoch"]

    def test_names_are_truncated_like_the_other_fields(self):
        item = build(next_candidates=[self.cand("preflight", 5, name="x" * 300, channel="y" * 300)])["upcoming"][0]

        assert len(item["name"]) <= MAX_TEXT_LENGTH and item["name"].endswith("…")
        assert len(item["channel"]) <= MAX_TEXT_LENGTH

    def test_invalid_candidates_are_skipped(self):
        snapshot = build(next_candidates=[
            {"type": "manual", "name": "x", "channel": None, "at": NOW + timedelta(minutes=1)},
            {"type": "preflight", "name": "x", "channel": None, "at": "garbage"},
            "not a dict",
            self.cand("automation", 9),
        ])

        assert [i["type"] for i in snapshot["upcoming"]] == ["automation"]


class TestLastCheck:
    def test_passes_through_the_register_record(self):
        finished = NOW - timedelta(minutes=2)
        snapshot = build(
            last_check={
                "type": "preflight",
                "name": "Chicago at Carolina",
                "channel": "NHL | CHI @ CAR",
                "finished_at": finished,
                "result": "completed",
                "duration_seconds": 182.4,
            }
        )

        assert snapshot["last_check_type"] == "preflight"
        assert snapshot["last_check_channel"] == "NHL | CHI @ CAR"
        assert snapshot["last_check_epoch"] == int(finished.timestamp())
        assert snapshot["last_check_result"] == "completed"
        assert snapshot["last_check_duration_seconds"] == 182


class TestTimestamps:
    EXPECTED = datetime(2026, 11, 7, 19, 58, 40, tzinfo=timezone.utc)

    @pytest.mark.parametrize(
        "value",
        [
            "2026-11-07T19:58:40Z",
            "2026-11-07T19:58:40+00:00",
            "2026-11-07T14:58:40-05:00",
            EXPECTED.timestamp(),
            EXPECTED,
        ],
        ids=["z", "utc-offset", "other-offset", "epoch", "datetime"],
    )
    def test_aware_and_epoch_inputs_are_the_same_instant(self, value):
        assert to_local_aware(value).timestamp() == self.EXPECTED.timestamp()

    def test_naive_values_are_read_as_server_local_time(self):
        naive = datetime(2026, 11, 7, 19, 58, 40)

        assert to_local_aware(naive.isoformat()).timestamp() == naive.astimezone().timestamp()

    def test_output_iso_and_epoch_agree_and_carry_an_offset(self):
        snapshot = build(next_candidates=[{"type": "preflight", "name": "n", "channel": None,
                                           "at": "2026-11-07T19:58:40Z"}], now=datetime(2026, 11, 7, 19, 0, tzinfo=timezone.utc))

        parsed = datetime.fromisoformat(snapshot["next_check_at"])
        assert parsed.tzinfo is not None
        assert int(parsed.timestamp()) == snapshot["next_check_epoch"]

    @pytest.mark.parametrize("bad", [None, "", "garbage", True])
    def test_unusable_values_become_none(self, bad):
        assert to_local_aware(bad) is None


class TestSize:
    def test_worst_case_payload_stays_under_two_kilobytes(self):
        long_text = "é" * 200
        snapshot = build(
            automation=automation(periods=[{"id": "p", "name": long_text}], stage_label=long_text),
            last_check={"type": "preflight", "name": long_text, "channel": long_text,
                        "finished_at": NOW, "result": "completed", "duration_seconds": 99999},
            next_candidates=[{"type": "preflight", "name": long_text, "channel": long_text,
                              "at": NOW + timedelta(hours=1)}],
        )

        # Three upcoming items carry two names each, so the cap is 3 KB.
        assert len(json.dumps(snapshot, ensure_ascii=False).encode("utf-8")) < 3072
        for key in ("name", "stage", "last_check_name", "last_check_channel", "next_check_name", "next_check_channel"):
            assert len(snapshot[key]) <= MAX_TEXT_LENGTH
        assert snapshot["name"].endswith("…")
