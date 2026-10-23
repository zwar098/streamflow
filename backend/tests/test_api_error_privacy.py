"""API responses keep user guidance but never echo internal exceptions."""

import threading
from unittest.mock import Mock, MagicMock

from flask import Flask
import pytest

from apps.api import regex_handlers, scheduling_handlers
from apps.api.dispatcharr_handlers import initialize_udi_response
from apps.api.playback_stability_handlers import playback_stability_config_response
from apps.api.stream_checker_handlers import check_single_channel_now_response
from apps.api.stream_sessions_handlers import create_group_stream_sessions_response
from apps.automation.regex_validation import is_dangerous_regex
from apps.automation.scheduling_service import NoTvgIdError, SchedulingService

SECRET = 'postgres://admin:private-password@internal.invalid/app/secret.db'


@pytest.fixture
def context():
    with Flask(__name__).app_context():
        yield


def body(response):
    return (response[0] if isinstance(response, tuple) else response).get_data(as_text=True)


def test_initialization_failure_is_logged_without_exposing_credentials(context, caplog):
    config, udi = Mock(), Mock()
    config.is_configured.return_value = True
    udi.initialize.side_effect = RuntimeError(SECRET)
    response = initialize_udi_response(get_dispatcharr_config=lambda: config, get_udi_manager=lambda: udi)
    assert response[1] == 500 and SECRET not in body(response)
    assert SECRET in caplog.text


def test_playback_config_error_keeps_public_validation_code(context, caplog):
    service = Mock()
    service.update_config.side_effect = ValueError(SECRET)
    response, status = playback_stability_config_response(method='PUT', payload={}, get_service=lambda: service)
    assert status == 400
    assert response.get_json()['code'] == 'invalid_playback_stability_config'
    assert SECRET not in body(response) and SECRET in caplog.text


@pytest.mark.parametrize('operation', ['add', 'delete', 'edit', 'settings', 'mass'])
def test_bulk_failures_keep_channel_ids_without_echoing_exception(context, operation):
    matcher, udi = MagicMock(), Mock()
    matcher.get_patterns.return_value = {'patterns': {'1': {'regex_patterns': [{'pattern': 'BBC'}]}}}
    matcher.validate_regex_patterns.return_value = (True, None)
    udi.get_channel_by_id.return_value = {'id': 1, 'name': 'BBC'}
    for method in ('add_channel_pattern', 'delete_channel_pattern', 'set_match_by_tvg_id'):
        getattr(matcher, method).side_effect = RuntimeError(SECRET)
    payload = {'channel_ids': [1]}
    args = {'get_regex_matcher': lambda: matcher}
    if operation == 'delete':
        handler = regex_handlers.bulk_delete_regex_patterns_response
    elif operation == 'settings':
        handler = regex_handlers.bulk_update_match_settings_response
        payload['settings'] = {'match_by_tvg_id': True}
    else:
        args['get_udi_manager'] = lambda: udi
        if operation == 'add':
            handler = regex_handlers.add_bulk_regex_patterns_response
            payload['regex_patterns'] = [{'pattern': 'News'}]
        elif operation == 'edit':
            handler = regex_handlers.bulk_edit_regex_pattern_response
            payload.update(old_pattern='BBC', new_pattern='ARD')
        else:
            handler = regex_handlers.mass_edit_regex_patterns_response
            payload.update(find_pattern='BBC', replace_pattern='ARD')
            args['is_dangerous_regex'] = is_dangerous_regex
    response = handler(payload=payload, **args)
    data = (response[0] if isinstance(response, tuple) else response).get_json()
    assert data['failed_channels'][0]['channel_id'] == 1
    assert SECRET not in body(response)


@pytest.mark.parametrize('preview', [False, True])
def test_missing_tvg_guidance_uses_channel_id_instead_of_exception_text(context, preview):
    service = Mock()
    error = NoTvgIdError(42)
    error.args = (SECRET,)
    service.get_programs_by_channel.side_effect = error
    service.test_regex_against_epg_for_rule.side_effect = error
    if preview:
        response = scheduling_handlers.test_auto_create_rule_response(
            payload={'channel_ids': [42], 'regex_pattern': 'News'}, get_scheduling_service=lambda: service)
    else:
        response = scheduling_handlers.get_channel_programs_response(channel_id=42, get_scheduling_service=lambda: service)
    assert response[1] == 200 and response[0].get_json()['no_tvg_id']
    assert 'Channel 42 has no TVG-ID' in body(response) and SECRET not in body(response)


def test_rule_import_failure_does_not_expose_service_exception(monkeypatch):
    from apps.automation import scheduling_service
    service = SchedulingService.__new__(SchedulingService)
    service._lock, service._auto_create_rules = threading.Lock(), []
    service.create_auto_create_rule = Mock(side_effect=OSError(SECRET))
    udi = Mock()
    udi.get_channel_by_id.return_value = {'id': 1}
    monkeypatch.setattr(scheduling_service, 'get_udi_manager', lambda: udi)
    result = service.import_auto_create_rules([{'name': 'News', 'channel_ids': [1], 'regex_pattern': 'News'}])
    assert result['failed'] == 1 and SECRET not in str(result)


def test_partial_group_session_failure_has_no_exception_details(context):
    udi, manager = Mock(), Mock()
    udi.get_channels_by_group.return_value = [{'id': 1, 'name': 'News'}]
    manager.create_session.side_effect = RuntimeError(SECRET)
    response = create_group_stream_sessions_response(payload={'group_id': 7, 'regex_filter': 'News'},
        get_udi_manager=lambda: udi, get_session_manager=lambda: manager,
        get_monitoring_service=Mock(), get_regex_matcher=Mock())
    assert response[1] == 400 and response[0].get_json()['errors']
    assert SECRET not in body(response)


def test_single_channel_success_preserves_stats_without_private_diagnostics(context):
    checker = Mock()
    checker.get_status.return_value = {'checking': False, 'queue': {}, 'progress': {}}
    checker.check_single_channel.return_value = {'success': True, 'channel_id': 1,
        'error': None, 'exception': SECRET, 'stats': {'total_streams': 2,
            'error': SECRET, 'checked_streams': [{'stream_id': 4, 'score': 3.5, 'stream_url': SECRET, 'stderr': SECRET}]}}
    response, status = check_single_channel_now_response(payload={'channel_id': 1}, get_stream_checker_service=lambda: checker)
    assert status == 200 and SECRET not in body(response)
    assert response.get_json()['stats']['total_streams'] == 2
    assert response.get_json()['stats']['checked_streams'] == [{'stream_id': 4, 'score': 3.5}]
