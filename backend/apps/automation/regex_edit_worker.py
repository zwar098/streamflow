"""Bounded, disposable stdlib-re worker for an entire mass-edit request.

Run by regex_edit.py with isolated Python, a wall-clock deadline, bounded I/O
and (on Unraid/Linux) an address-space limit. No application state is imported.
"""

import json
import re
import sys

MAX_INPUT_BYTES = 2 * 1024 * 1024
MAX_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_RESULT_CHARS = 1024 * 1024
MAX_EXPRESSION_CHARS = 16384


def replace_batch(data):
    find, replacement, texts = data['find'], data['replacement'], data['texts']
    expression = re.compile(find if data['use_regex'] else re.escape(find))
    # Validate backreferences even when no input matches, as re.sub does.
    template = replacement if data['use_regex'] else (lambda match: replacement)
    expression.sub(template, '', count=1)
    results = []
    remaining = MAX_RESULT_CHARS
    for text in texts:
        parts, position = [], 0
        for match in expression.finditer(text):
            prefix = text[position:match.start()]
            value = match.expand(replacement) if data['use_regex'] else replacement
            remaining -= len(prefix) + len(value)
            if remaining < 0:
                return {'error': 'regex_edit_too_large'}
            parts.extend((prefix, value))
            position = match.end()
        suffix = text[position:]
        remaining -= len(suffix)
        if remaining < 0:
            return {'error': 'regex_edit_too_large'}
        parts.append(suffix)
        results.append(''.join(parts))
    return {'results': results}


def main():
    # CPU/wall time is also enforced by the parent. This bounds native re
    # allocation and replacement expansion before any result reaches Flask.
    if sys.platform == 'linux':
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024,) * 2)
    try:
        payload = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(payload) > MAX_INPUT_BYTES:
            result = {'error': 'regex_edit_too_large'}
        else:
            result = replace_batch(json.loads(payload))
    except (re.error, IndexError):
        result = {'error': 'invalid_regex_edit'}
    except (MemoryError, OverflowError, RecursionError):
        result = {'error': 'regex_edit_too_large'}
    encoded = json.dumps(result, ensure_ascii=True).encode('ascii')
    if len(encoded) > MAX_OUTPUT_BYTES:
        encoded = b'{"error":"regex_edit_too_large"}'
    sys.stdout.buffer.write(encoded)


if __name__ == '__main__':
    main()
