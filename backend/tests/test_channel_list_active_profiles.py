"""Whole-list profile filtering uses the same resolver as the single-channel view."""
from types import SimpleNamespace

import pytest
from flask import Flask
from sqlalchemy import event

from apps.api.active_profile_handlers import get_channel_active_profile_response
from apps.api.channel_handlers import get_channels_response
from apps.automation.automation_config_manager import AutomationConfigManager
from apps.channels.service import ChannelQuery, ChannelService


@pytest.fixture
def configured_manager():
    manager = AutomationConfigManager()
    first = manager.create_profile({'name': 'Group profile'})
    second = manager.create_profile({'name': 'Channel profile'})
    schedule = {'type': 'cron', 'value': '0 * * * *'}
    low = manager.create_period({'name': 'Low', 'profile_id': first, 'priority': 1, 'schedule': schedule})
    high = manager.create_period({'name': 'High', 'profile_id': first, 'priority': 10, 'schedule': schedule})
    disabled = manager.create_period({'name': 'Disabled', 'profile_id': second, 'priority': 100, 'enabled': False, 'schedule': schedule})
    assert low and high and disabled
    assert manager.assign_period_to_groups(high, [10], first)
    assert manager.assign_period_to_channels(high, [2], second)
    assert manager.assign_period_to_channels(low, [3], second)
    assert manager.assign_period_to_channels(disabled, [4], second)
    assert manager.assign_epg_scheduled_profile_to_channel(5, second)
    channels = [{'id': cid, 'name': f'Channel {cid}', 'channel_group_id': 10 if cid <= 3 else None} for cid in range(1, 6)]
    return manager, channels, first, second


def service(manager, channels):
    return ChannelService(repository=SimpleNamespace(get_channels=lambda: channels),
                          automation_config_manager=manager,
                          channel_order_manager=SimpleNamespace(apply_order=lambda items: items),
                          stream_checker_service=None)


def test_list_profiles_match_single_channel_resolution_including_inheritance_and_priority(configured_manager):
    manager, channels, first, second = configured_manager
    enriched = service(manager, channels).list_channels(ChannelQuery(include_active_profile=True))['items']
    profiles = {channel['id']: channel['active_profile'] for channel in enriched}
    assert profiles[1]['automation']['profile_id'] == first
    assert profiles[1]['automation']['source'] == 'group'
    assert profiles[2]['automation']['profile_id'] == second
    assert profiles[2]['automation']['source'] == 'channel'
    # The inherited high-priority period wins over a channel's different low-priority period.
    assert profiles[3]['automation']['profile_id'] == first
    assert profiles[3]['automation']['source'] == 'group'
    assert profiles[4]['automation']['profile_id'] is None
    assert profiles[5]['automation']['profile_id'] is None
    assert profiles[5]['epg_override']['profile_name'] == 'Channel profile'
    app = Flask(__name__)
    for channel in channels:
        with app.app_context():
            response, status = get_channel_active_profile_response(
                channel_id=channel['id'], get_automation_config_manager=lambda: manager,
                get_udi_manager=lambda: SimpleNamespace(get_channel_by_id=lambda cid: channel))
        assert status == 200
        assert response.get_json() == profiles[channel['id']]
    assert all('active_profile' not in channel for channel in channels)


def test_profiles_are_opt_in_and_only_enrich_requested_page(configured_manager):
    manager, channels, _, _ = configured_manager
    channel_service = service(manager, channels)
    ordinary = channel_service.list_channels(ChannelQuery())
    assert all('active_profile' not in channel for channel in ordinary['items'])
    page = channel_service.list_channels(ChannelQuery(page=2, per_page=2, include_active_profile=True))
    assert [channel['id'] for channel in page['items']] == [3, 4]
    assert page['total'] == 5
    assert all('active_profile' in channel for channel in page['items'])


def test_profile_enrichment_uses_constant_query_count(configured_manager, clean_test_db):
    manager, channels, _, _ = configured_manager
    queries = []
    def record(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith('SELECT'):
            queries.append(statement)
    event.listen(clean_test_db, 'before_cursor_execute', record)
    try:
        service(manager, channels).list_channels(ChannelQuery(include_active_profile=True))
        small_count = len(queries)
        queries.clear()
        large = channels * 50
        service(manager, large).list_channels(ChannelQuery(include_active_profile=True))
        assert len(queries) == small_count == 4
    finally:
        event.remove(clean_test_db, 'before_cursor_execute', record)


def test_snapshot_is_restored_if_profile_resolution_fails(configured_manager, monkeypatch):
    manager, channels, _, _ = configured_manager
    previous = manager.begin_run_snapshot()
    pinned = manager._active_run_snapshot()
    def fail(*args):
        raise RuntimeError('resolution failed')
    monkeypatch.setattr(manager, 'get_effective_configuration', fail)
    try:
        with pytest.raises(RuntimeError, match='resolution failed'):
            service(manager, channels).list_channels(ChannelQuery(include_active_profile=True))
        assert manager._active_run_snapshot() is pinned
    finally:
        manager.end_run_snapshot(previous)


def test_channel_handler_passes_opt_in_profile_summary_flag(configured_manager):
    manager, channels, _, _ = configured_manager
    app = Flask(__name__)
    with app.app_context():
        response = get_channels_response(request_args={'include_active_profile': 'true'},
            parse_pagination_params=lambda page, per_page: (None, 50, None),
            get_channel_service=lambda: service(manager, channels))
    assert all('active_profile' in channel for channel in response.get_json())
