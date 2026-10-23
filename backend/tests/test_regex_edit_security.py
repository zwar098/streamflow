"""Real worker deadline, Python-re parity and no writes on rejected batches."""

import re
import time
from unittest.mock import Mock

from flask import Flask
import pytest

from apps.api.regex_handlers import mass_edit_preview_response, mass_edit_regex_patterns_response
from apps.automation.regex_edit import prepare_replacements
from apps.automation.regex_validation import is_dangerous_regex
from apps.core.exceptions import ValidationError


@pytest.mark.parametrize(('find', 'replacement', 'texts'), [
    (r'(HD|FHD)', r'Quality-\1', ['BBC HD', 'BBC FHD', 'CNN']),
    (r'(?P<name>BBC)', r'\g<name> One', ['BBC', 'BBC BBC']),
    (r'x*', '-', ['', 'abxd', 'xx']),
    (r'.*?', r'[\g<0>]', ['ab', '']),
    (r'(?<=BBC)\s+', '-', ['BBC HD', 'abc']),
    (r'(?i)bbc', 'ARD', ['bbc', 'BBC HD', 'Bbc']),
    (r'(a)?b', r'\1', ['b', 'ab', 'b ab']),
    ('', '.', ['abc', '']),
])
def test_regex_replacement_preserves_stdlib_substitution(find, replacement, texts):
    expected = {text: re.sub(find, replacement, text) for text in texts}
    assert prepare_replacements(find, replacement, texts, use_regex=True) == expected


@pytest.mark.parametrize(('find', 'replacement'), [('HD', r'\1'), ('', r'\path'), ('.*', 'x')])
def test_literal_replacement_preserves_backslashes_and_empty_find(find, replacement):
    texts = ['BBC HD', 'HD HD', '']
    assert prepare_replacements(find, replacement, texts, use_regex=False) == {
        text: text.replace(find, replacement) for text in texts
    }


@pytest.mark.parametrize(('find', 'replacement'), [('(', 'x'), ('(x)', r'\2'), ('x', r'\g<missing>')])
def test_invalid_templates_fail_even_without_matches(find, replacement):
    with pytest.raises(ValidationError) as error:
        prepare_replacements(find, replacement, ['BBC'], use_regex=True)
    assert error.value.error_code == 'invalid_regex_edit'


def test_replacement_amplification_has_a_size_limit():
    with pytest.raises(ValidationError) as error:
        prepare_replacements('.', 'x' * 16384, ['a' * 128], use_regex=True)
    assert error.value.error_code == 'regex_edit_too_large'


@pytest.mark.parametrize('handler', [mass_edit_preview_response, mass_edit_regex_patterns_response])
def test_ambiguous_regex_is_killed_before_any_channel_write(handler, monkeypatch):
    from apps.automation import regex_edit
    # This bypasses the old nested-quantifier heuristic and hangs stdlib re.
    find = '(a|aa)+$'
    assert not is_dangerous_regex(find)
    matcher, udi = Mock(), Mock()
    matcher.get_patterns.return_value = {'patterns': {
        '1': {'regex_patterns': [{'pattern': 'a'}]},
        '2': {'regex_patterns': [{'pattern': 'a' * 100 + '!'}]},
    }}
    udi.get_channel_by_id.side_effect = lambda channel_id: {'id': channel_id, 'name': 'Test'}
    children = []
    popen = regex_edit.subprocess.Popen
    def record_child(*args, **kwargs):
        child = popen(*args, **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(regex_edit.subprocess, 'Popen', record_child)
    started = time.monotonic()
    with Flask(__name__).app_context():
        response, status = handler(payload={'channel_ids': [1, 2], 'find_pattern': find,
            'replace_pattern': 'x', 'use_regex': True}, get_regex_matcher=lambda: matcher,
            get_udi_manager=lambda: udi, is_dangerous_regex=is_dangerous_regex)
    assert status == 400 and response.get_json()['code'] == 'regex_edit_timeout'
    assert time.monotonic() - started < 6
    assert children and all(child.poll() is not None for child in children)
    matcher.add_channel_pattern.assert_not_called()


@pytest.mark.parametrize('handler', [mass_edit_preview_response, mass_edit_regex_patterns_response])
@pytest.mark.parametrize('payload', [[], {'channel_ids': [True]}, {'channel_ids': [1], 'find_pattern': {}, 'replace_pattern': ''}])
def test_invalid_payload_is_rejected_before_loading_managers(handler, payload):
    manager = Mock()
    with Flask(__name__).app_context():
        response, status = handler(payload=payload, get_regex_matcher=manager,
            get_udi_manager=manager, is_dangerous_regex=is_dangerous_regex)
    assert status == 400
    manager.assert_not_called()


def test_preview_and_apply_preserve_provider_priority_and_saved_patterns():
    matcher, udi = Mock(), Mock()
    matcher.get_patterns.return_value = {'patterns': {'1': {'enabled': True, 'regex_patterns': [
        {'pattern': '^BBC HD$', 'm3u_accounts': [2], 'priority': 7},
        {'pattern': '^BBC SD$', 'm3u_accounts': [3], 'priority': 8},
    ]}}}
    matcher.validate_regex_patterns.return_value = (True, None)
    udi.get_channel_by_id.return_value = {'id': 1, 'name': 'BBC'}
    arguments = dict(payload={'channel_ids': [1], 'find_pattern': '(BBC)', 'replace_pattern': r'\1 One', 'use_regex': True},
        get_regex_matcher=lambda: matcher, get_udi_manager=lambda: udi, is_dangerous_regex=is_dangerous_regex)
    with Flask(__name__).app_context():
        preview = mass_edit_preview_response(**arguments).get_json()
        assert preview['total_patterns_affected'] == 2
        matcher.add_channel_pattern.assert_not_called()
        applied = mass_edit_regex_patterns_response(**arguments).get_json()
    assert applied['success_count'] == 1
    updated = matcher.add_channel_pattern.call_args.args[2]
    assert updated == [
        {'pattern': '^BBC One HD$', 'm3u_accounts': [2], 'priority': 7},
        {'pattern': '^BBC One SD$', 'm3u_accounts': [3], 'priority': 8},
    ]
