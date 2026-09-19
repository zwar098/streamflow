"""Regression test: an M3U refresh/matching pass must never push a channel
past its resolved stream limit (channel override -> group override ->
profile's stream_checking.stream_limit -> unlimited).

Before this fix, discovery/matching had no awareness of stream_limit at all:
it kept re-adding every matching stream on each M3U refresh, only for the
checker to trim it back down on its own (much less frequent) schedule. That
oscillation is the bug the user reported ("no matter what number I set, all
streams still get assigned to a channel").
"""

from unittest.mock import Mock

from apps.automation import automated_stream_manager as asm
from apps.automation.stream_limit_config import get_stream_limit_config


def _make_manager():
    manager = asm.AutomatedStreamManager()
    manager.config.setdefault("enabled_features", {})["auto_stream_discovery"] = True
    return manager


def _base_mocks(monkeypatch, channel, all_streams, existing_stream_ids, profile):
    monkeypatch.setattr(asm, "get_channels", lambda: [channel])
    monkeypatch.setattr(asm, "get_m3u_accounts", lambda: [])
    monkeypatch.setattr(asm, "get_streams", lambda log_result=True: all_streams)

    mock_udi = Mock()
    mock_udi.get_channel_streams.return_value = [{"id": sid} for sid in existing_stream_ids]
    mock_udi.get_channel_by_id.return_value = channel
    monkeypatch.setattr(asm, "get_udi_manager", lambda: mock_udi)

    mock_automation_config = Mock()
    mock_automation_config.get_effective_configuration.return_value = {
        "profile": profile,
        "periods": [],
    }
    monkeypatch.setattr(asm, "get_automation_config_manager", lambda: mock_automation_config)

    monkeypatch.setattr(
        "apps.stream.stream_session_manager.get_session_manager",
        lambda: Mock(
            get_channels_in_active_sessions=lambda: set(),
            get_active_sessions=lambda: [],
        ),
    )


def test_channel_override_caps_new_matches_to_remaining_capacity(monkeypatch):
    channel = {"id": 500, "name": "Sky Sports Main Event", "channel_group_id": None}
    existing_stream_ids = [1, 2, 3, 4]
    all_streams = [
        {"id": i, "name": "Sky Sports Main Event FHD", "url": f"http://x/{i}", "m3u_account": 1, "is_custom": False}
        for i in range(1, 11)
    ]
    profile = {
        "id": "profile-1",
        "stream_matching": {"enabled": True, "match_priority_order": ["regex"]},
        "stream_checking": {"enabled": True, "stream_limit": 0},
    }
    _base_mocks(monkeypatch, channel, all_streams, existing_stream_ids, profile)

    manager = _make_manager()
    manager.regex_matcher.add_channel_pattern("500", "Sky Sports Main Event", ["Sky Sports Main Event"])

    # Channel override of 6 leaves room for exactly 2 more on top of the 4 already assigned.
    get_stream_limit_config().set_channel_stream_limit(500, 6)

    added_calls = []
    monkeypatch.setattr(
        asm,
        "assign_streams_to_channel",
        lambda channel_id, stream_ids, **kwargs: (added_calls.append(list(stream_ids)) or len(stream_ids)),
    )

    manager._discover_and_assign_streams_impl(force=True, skip_check_trigger=True)

    assert len(added_calls) == 1
    assert len(added_calls[0]) == 2


def test_group_override_used_when_no_channel_override(monkeypatch):
    channel = {"id": 501, "name": "ESPN", "channel_group_id": 9}
    existing_stream_ids = [10, 11]
    all_streams = [
        {"id": i, "name": "ESPN HD", "url": f"http://x/{i}", "m3u_account": 1, "is_custom": False}
        for i in range(1, 6)
    ]
    profile = {
        "id": "profile-1",
        "stream_matching": {"enabled": True, "match_priority_order": ["regex"]},
        "stream_checking": {"enabled": True, "stream_limit": 0},
    }
    _base_mocks(monkeypatch, channel, all_streams, existing_stream_ids, profile)

    manager = _make_manager()
    manager.regex_matcher.add_channel_pattern("501", "ESPN", ["ESPN"])

    get_stream_limit_config().set_group_stream_limit(9, 3)

    added_calls = []
    monkeypatch.setattr(
        asm,
        "assign_streams_to_channel",
        lambda channel_id, stream_ids, **kwargs: (added_calls.append(list(stream_ids)) or len(stream_ids)),
    )

    manager._discover_and_assign_streams_impl(force=True, skip_check_trigger=True)

    assert len(added_calls) == 1
    assert len(added_calls[0]) == 1  # 3 limit - 2 already assigned = room for 1 more


def test_no_override_and_no_profile_limit_is_unlimited(monkeypatch):
    channel = {"id": 502, "name": "BBC One", "channel_group_id": None}
    existing_stream_ids = [20]
    all_streams = [
        {"id": i, "name": "BBC One HD", "url": f"http://x/{i}", "m3u_account": 1, "is_custom": False}
        for i in range(1, 6)
    ]
    profile = {
        "id": "profile-1",
        "stream_matching": {"enabled": True, "match_priority_order": ["regex"]},
        "stream_checking": {"enabled": True, "stream_limit": 0},
    }
    _base_mocks(monkeypatch, channel, all_streams, existing_stream_ids, profile)

    manager = _make_manager()
    manager.regex_matcher.add_channel_pattern("502", "BBC One", ["BBC One"])

    added_calls = []
    monkeypatch.setattr(
        asm,
        "assign_streams_to_channel",
        lambda channel_id, stream_ids, **kwargs: (added_calls.append(list(stream_ids)) or len(stream_ids)),
    )

    manager._discover_and_assign_streams_impl(force=True, skip_check_trigger=True)

    assert len(added_calls) == 1
    assert len(added_calls[0]) == 5  # all matching streams pass through uncapped
