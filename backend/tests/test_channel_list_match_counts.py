"""Channel view changes must not revalidate every pattern for every stream."""
from unittest.mock import Mock

import pytest
from flask import Flask

from apps.api.match_preview_handlers import bulk_match_count_response, test_match_live_response as match_preview
from apps.automation.regex_validation import is_dangerous_regex


@pytest.mark.parametrize('case_sensitive', [True, False])
@pytest.mark.parametrize('priority', [['tvg', 'regex'], ['regex', 'tvg'], ['tvg'], ['regex'], []])
def test_bulk_count_preserves_preview_matches_providers_flags_and_substitution(case_sensitive, priority):
    streams = [
        {'id': 1, 'name': 'A+B Sports HD', 'm3u_account': 1},
        {'id': 2, 'name': 'a+b sports HD', 'm3u_account': 1},
        {'id': 3, 'name': 'A+B Sports HD', 'm3u_account': 2},
        {'id': 4, 'name': 'Other', 'm3u_account': 2, 'tvg_id': 'target'},
        {'id': 5, 'name': '', 'tvg_id': 'target'},
        {'id': 6, 'name': 'News\tHD', 'm3u_account': 2},
        {'id': 7, 'name': 'Danger aaaa'},
        None,
    ]
    config = {'channel_id': 20, 'channel_name': 'A+B Sports', 'match_by_tvg_id': True,
              'tvg_id': 'target', 'case_sensitive': case_sensitive, 'match_priority_order': priority,
              'regex_patterns': [{'pattern': r'^CHANNEL_NAME HD$', 'm3u_accounts': [1]},
                                 {'pattern': r'(?i)^news HD$', 'm3u_accounts': [2]},
                                 {'pattern': r'(a+)+$'}, '[', '', r'^Never$']}
    app = Flask(__name__)
    with app.app_context():
        preview = match_preview(payload={**config, 'max_matches': 1000}, get_streams=lambda: streams,
            get_m3u_accounts=lambda: [], is_dangerous_regex=is_dangerous_regex)
        result = bulk_match_count_response(payload={'channels': [config]}, get_streams=lambda **kwargs: streams,
            get_m3u_accounts=lambda: [], is_dangerous_regex=is_dangerous_regex)
    assert result.get_json()['counts']['20'] == preview.get_json()['total_matches']
    assert result.get_json()['total_streams'] == len(streams)


def test_safety_validation_runs_once_per_nonempty_pattern_even_with_large_stream_pool():
    validator = Mock(side_effect=is_dangerous_regex)
    streams = [{'id': index, 'name': 'Sports HD', 'm3u_account': 1} for index in range(1000)]
    app = Flask(__name__)
    with app.app_context():
        result = bulk_match_count_response(payload={'channels': [
            {'channel_id': 10, 'regex_patterns': ['^Sports HD$', '(a+)+$', '[', '']},
            {'channel_id': 11, 'regex_patterns': []},
            {'channel_id': 12, 'regex_patterns': ['[', '(a+)+$']},
        ]}, get_streams=lambda **kwargs: streams, get_m3u_accounts=lambda: [], is_dangerous_regex=validator)
    assert result.get_json()['counts'] == {'10': 1000, '12': 0}
    assert validator.call_count == 5
