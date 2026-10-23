"""Prepare mass-edit replacements before allowing any channel writes."""

import json
from pathlib import Path
import subprocess
import sys

from apps.automation.regex_edit_worker import MAX_INPUT_BYTES
from apps.core.exceptions import ValidationError

REGEX_EDIT_TIMEOUT_SECONDS = 2
ERROR_MESSAGES = {
    'regex_edit_timeout': 'Regex edit exceeded the execution time limit. Simplify the find pattern or select fewer channels.',
    'invalid_regex_edit': 'Invalid find regex or replacement pattern. Check capture groups and backreferences.',
    'regex_edit_too_large': 'Regex edit exceeds the size limit. Use a smaller replacement or select fewer channels.',
}


def prepare_replacements(find, replacement, patterns, *, use_regex):
    texts = list(dict.fromkeys(patterns))
    payload = json.dumps({'find': find, 'replacement': replacement, 'texts': texts,
                          'use_regex': use_regex}, ensure_ascii=True).encode('ascii')
    if len(payload) > MAX_INPUT_BYTES:
        raise ValidationError(ERROR_MESSAGES['regex_edit_too_large'], error_code='regex_edit_too_large')
    try:
        worker = subprocess.run(
            [sys.executable, '-I', str(Path(__file__).with_name('regex_edit_worker.py'))],
            input=payload, capture_output=True, timeout=REGEX_EDIT_TIMEOUT_SECONDS,
            check=True,
        )
    except subprocess.TimeoutExpired:
        # subprocess.run kills and reaps the child, including a re call that
        # holds the GIL; a thread or future timeout would leave it running.
        raise ValidationError(ERROR_MESSAGES['regex_edit_timeout'], error_code='regex_edit_timeout') from None
    result = json.loads(worker.stdout)
    if 'error' in result:
        code = result['error']
        raise ValidationError(ERROR_MESSAGES[code], error_code=code)
    return dict(zip(texts, result['results']))
