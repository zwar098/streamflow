#!/usr/bin/env python3
"""Tests for per-channel/per-group Stream Limit overrides and their
channel -> group -> profile fallback resolution."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('DISPATCHARR_BASE_URL', 'http://test.local')
os.environ.setdefault('DISPATCHARR_TOKEN', 'test_token')

from apps.automation import stream_limit_config
from apps.automation.stream_limit_config import (
    StreamLimitConfig,
    get_stream_limit_config,
)


class TestStreamLimitConfig(unittest.TestCase):
    def setUp(self):
        stream_limit_config._stream_limit_config = None

    def tearDown(self):
        stream_limit_config._stream_limit_config = None

    def test_channel_override_round_trip(self):
        config = StreamLimitConfig()
        self.assertIsNone(config.get_channel_stream_limit(123))

        config.set_channel_stream_limit(123, 5)
        self.assertEqual(config.get_channel_stream_limit(123), 5)

        # Reload from persisted storage as a fresh instance would after a restart.
        reloaded = StreamLimitConfig()
        self.assertEqual(reloaded.get_channel_stream_limit(123), 5)

        config.delete_channel_stream_limit(123)
        self.assertIsNone(config.get_channel_stream_limit(123))
        self.assertIsNone(StreamLimitConfig().get_channel_stream_limit(123))

    def test_group_override_round_trip(self):
        config = StreamLimitConfig()
        config.set_group_stream_limit('sports', 10)
        self.assertEqual(config.get_group_stream_limit('sports'), 10)

        reloaded = StreamLimitConfig()
        self.assertEqual(reloaded.get_group_stream_limit('sports'), 10)

        config.delete_group_stream_limit('sports')
        self.assertIsNone(config.get_group_stream_limit('sports'))

    def test_bulk_set_channel_stream_limits(self):
        config = StreamLimitConfig()
        config.bulk_set_channel_stream_limits([1, 2, 3], 8)
        self.assertEqual(config.get_channel_stream_limit(1), 8)
        self.assertEqual(config.get_channel_stream_limit(2), 8)
        self.assertEqual(config.get_channel_stream_limit(3), 8)

    def test_zero_is_a_valid_explicit_unlimited_override(self):
        config = StreamLimitConfig()
        config.set_channel_stream_limit(1, 0)
        # An explicit 0 override is stored and returned (not treated as "unset").
        self.assertEqual(config.get_channel_stream_limit(1), 0)

    def test_negative_limit_rejected(self):
        config = StreamLimitConfig()
        with self.assertRaises(ValueError):
            config.set_channel_stream_limit(1, -1)
        with self.assertRaises(ValueError):
            config.set_group_stream_limit('g1', -5)

    def test_effective_limit_precedence_channel_over_group_over_profile(self):
        config = StreamLimitConfig()
        config.set_channel_stream_limit(1, 3)
        config.set_group_stream_limit('g1', 7)

        # Channel override wins even though a group override and profile value exist.
        self.assertEqual(config.get_effective_stream_limit(1, 'g1', profile_stream_limit=20), 3)

    def test_effective_limit_falls_back_to_group_when_no_channel_override(self):
        config = StreamLimitConfig()
        config.set_group_stream_limit('g1', 7)

        self.assertEqual(config.get_effective_stream_limit(2, 'g1', profile_stream_limit=20), 7)

    def test_effective_limit_falls_back_to_profile_when_no_overrides(self):
        config = StreamLimitConfig()
        self.assertEqual(config.get_effective_stream_limit(99, 'no-such-group', profile_stream_limit=6), 6)

    def test_effective_limit_defaults_to_zero_unlimited(self):
        config = StreamLimitConfig()
        self.assertEqual(config.get_effective_stream_limit(99, None, profile_stream_limit=0), 0)
        self.assertEqual(config.get_effective_stream_limit(None, None), 0)

    def test_singleton_getter_returns_same_instance(self):
        first = get_stream_limit_config()
        second = get_stream_limit_config()
        self.assertIs(first, second)


if __name__ == '__main__':
    unittest.main()
