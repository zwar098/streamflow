#!/usr/bin/env python3
"""
Unit tests for the Scheduling Service.

Tests cover:
1. Auto-create rules CRUD operations
2. Regex matching against EPG programs
3. Duplicate detection logic
4. Event creation and updating
"""

import unittest
import tempfile
import json
import os
import sys
import re
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timedelta, timezone

# Set up initial CONFIG_DIR before importing scheduling_service
# This will be overridden in setUp for each test
os.environ['CONFIG_DIR'] = tempfile.mkdtemp()
os.environ['DISPATCHARR_BASE_URL'] = 'http://test.local'
os.environ['DISPATCHARR_TOKEN'] = 'test_token'

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import the module but we'll reset the singleton in each test
import apps.automation.scheduling_service
from apps.automation.scheduling_service import SchedulingService


class TestSchedulingService(unittest.TestCase):
    """Test Scheduling Service."""
    
    def setUp(self):
        """Set up test fixtures."""
        # Create a fresh temp directory for each test
        self.test_config_dir = tempfile.mkdtemp()
        os.environ['CONFIG_DIR'] = self.test_config_dir
        
        # Reset the global singleton and reload CONFIG_DIR
        scheduling_service._scheduling_service = None
        # Force reload of the CONFIG_DIR constant in the module
        from pathlib import Path
        scheduling_service.CONFIG_DIR = Path(self.test_config_dir)
        scheduling_service.SCHEDULING_CONFIG_FILE = scheduling_service.CONFIG_DIR / 'scheduling_config.json'
        scheduling_service.SCHEDULED_EVENTS_FILE = scheduling_service.CONFIG_DIR / 'scheduled_events.json'
        scheduling_service.AUTO_CREATE_RULES_FILE = scheduling_service.CONFIG_DIR / 'auto_create_rules.json'
        
        self.service = SchedulingService()
        
        # Mock UDI manager
        self.mock_udi_patcher = patch('scheduling_service.get_udi_manager')
        self.mock_udi = self.mock_udi_patcher.start()
        
        # Mock channel
        self.mock_channel = {
            'id': 1,
            'name': 'Test Channel',
            'tvg_id': 'test-channel-1',
            'logo_id': None
        }
        
        self.mock_udi.return_value.get_channel_by_id.return_value = self.mock_channel
        self.mock_udi.return_value.get_logo_by_id.return_value = None
    
    def tearDown(self):
        """Clean up test fixtures."""
        self.mock_udi_patcher.stop()
        # Clean up the temp directory
        import shutil
        if os.path.exists(self.test_config_dir):
            shutil.rmtree(self.test_config_dir)
        
        # Reset the global singleton to avoid cross-test contamination
        scheduling_service._scheduling_service = None
    
    def test_create_auto_create_rule(self):
        """Test creating an auto-create rule."""
        rule_data = {
            'name': 'Test Rule',
            'channel_id': 1,
            'regex_pattern': '^Breaking News',
            'minutes_before': 5
        }
        
        # Mock match_programs_to_rules to avoid side effects
        with patch.object(self.service, 'match_programs_to_rules'):
            rule = self.service.create_auto_create_rule(rule_data)
        
        self.assertIsNotNone(rule['id'])
        self.assertEqual(rule['name'], 'Test Rule')
        # Check new multi-channel structure
        self.assertEqual(rule['channel_ids'], [1])
        self.assertIn('channels_info', rule)
        self.assertEqual(len(rule['channels_info']), 1)
        self.assertEqual(rule['channels_info'][0]['id'], 1)
        self.assertEqual(rule['channels_info'][0]['name'], 'Test Channel')
        self.assertEqual(rule['regex_pattern'], '^Breaking News')
        self.assertEqual(rule['minutes_before'], 5)
        self.assertEqual(rule['timing_direction'], 'before')

    def test_create_auto_create_rule_with_after_direction(self):
        """A rule may schedule its check after program start instead of before."""
        rule_data = {
            'name': 'After Start Rule',
            'channel_id': 1,
            'regex_pattern': '^Breaking News',
            'minutes_before': 10,
            'timing_direction': 'after',
        }

        with patch.object(self.service, 'match_programs_to_rules'):
            rule = self.service.create_auto_create_rule(rule_data)

        self.assertEqual(rule['timing_direction'], 'after')

    def test_create_auto_create_rule_rejects_unknown_direction(self):
        """An invalid timing_direction falls back to 'before' rather than erroring,
        matching how every other malformed/missing optional field on this rule is handled."""
        rule_data = {
            'name': 'Bad Direction Rule',
            'channel_id': 1,
            'regex_pattern': '^Breaking News',
            'minutes_before': 5,
            'timing_direction': 'sideways',
        }

        with patch.object(self.service, 'match_programs_to_rules'):
            rule = self.service.create_auto_create_rule(rule_data)

        self.assertEqual(rule['timing_direction'], 'before')

    def test_update_auto_create_rule_timing_direction(self):
        """An existing rule can be switched from before to after start."""
        rule_data = {
            'name': 'Switchable Rule',
            'channel_id': 1,
            'regex_pattern': '^Breaking News',
            'minutes_before': 5,
        }
        with patch.object(self.service, 'match_programs_to_rules'):
            rule = self.service.create_auto_create_rule(rule_data)
        self.assertEqual(rule['timing_direction'], 'before')

        with patch.object(self.service, 'match_programs_to_rules'):
            updated = self.service.update_auto_create_rule(
                rule['id'], {'timing_direction': 'after'}
            )

        self.assertEqual(updated['timing_direction'], 'after')

    def test_program_schedule_timing_before_and_after_directions(self):
        """_program_schedule_timing must compute check_time on the correct
        side of program start depending on timing_direction, since every
        auto-create consumer (matching loop, preview, dedup) shares this
        one function."""
        now = datetime.now(timezone.utc)
        program = {
            'start_time': (now + timedelta(minutes=10)).isoformat(),
            'end_time': (now + timedelta(hours=1)).isoformat(),
        }

        before = self.service._program_schedule_timing(program, 15, now, 'before')
        self.assertEqual(before['state'], 'due_now')
        self.assertAlmostEqual(
            (before['check_time'] - (now - timedelta(minutes=5))).total_seconds(), 0, delta=1
        )

        after = self.service._program_schedule_timing(program, 15, now, 'after')
        self.assertEqual(after['state'], 'future')
        self.assertAlmostEqual(
            (after['check_time'] - (now + timedelta(minutes=25))).total_seconds(), 0, delta=1
        )

    def test_program_schedule_timing_defaults_to_before(self):
        """Callers that omit timing_direction (e.g. rules saved before this
        option existed) must keep computing check_time before program start."""
        now = datetime.now(timezone.utc)
        program = {
            'start_time': (now + timedelta(minutes=10)).isoformat(),
            'end_time': (now + timedelta(hours=1)).isoformat(),
        }

        timing = self.service._program_schedule_timing(program, 15, now)
        self.assertEqual(timing['state'], 'due_now')

    def test_create_rule_with_invalid_regex(self):
        """Test creating a rule with invalid regex raises ValueError."""
        rule_data = {
            'name': 'Invalid Rule',
            'channel_id': 1,
            'regex_pattern': '(unclosed group',
            'minutes_before': 5
        }
        
        with self.assertRaises(ValueError) as context:
            self.service.create_auto_create_rule(rule_data)
        
        self.assertIn('Invalid regex pattern', str(context.exception))
    
    def test_create_rule_with_invalid_channel(self):
        """Test creating a rule with invalid channel raises ValueError."""
        self.mock_udi.return_value.get_channel_by_id.return_value = None
        
        rule_data = {
            'name': 'Test Rule',
            'channel_id': 999,
            'regex_pattern': '^Test',
            'minutes_before': 5
        }
        
        with self.assertRaises(ValueError) as context:
            self.service.create_auto_create_rule(rule_data)
        
        self.assertIn('No valid channels found', str(context.exception))
    
    def test_get_auto_create_rules(self):
        """Test getting all auto-create rules."""
        # Create a rule first
        rule_data = {
            'name': 'Test Rule',
            'channel_id': 1,
            'regex_pattern': '^Breaking News',
            'minutes_before': 5
        }
        
        with patch.object(self.service, 'match_programs_to_rules'):
            self.service.create_auto_create_rule(rule_data)
        
        rules = self.service.get_auto_create_rules()
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]['name'], 'Test Rule')
    
    def test_delete_auto_create_rule(self):
        """Test deleting an auto-create rule."""
        # Create a rule first
        rule_data = {
            'name': 'Test Rule',
            'channel_id': 1,
            'regex_pattern': '^Breaking News',
            'minutes_before': 5
        }
        
        with patch.object(self.service, 'match_programs_to_rules'):
            rule = self.service.create_auto_create_rule(rule_data)
        
        # Delete the rule
        success = self.service.delete_auto_create_rule(rule['id'])
        self.assertTrue(success)
        
        # Verify it's deleted
        rules = self.service.get_auto_create_rules()
        self.assertEqual(len(rules), 0)
    
    def test_delete_nonexistent_rule(self):
        """Test deleting a non-existent rule returns False."""
        success = self.service.delete_auto_create_rule('nonexistent-id')
        self.assertFalse(success)
    
    def test_test_regex_against_epg(self):
        """Test testing regex pattern against EPG programs."""
        # Mock EPG programs
        programs = [
            {'title': 'Breaking News at 5', 'start_time': '2024-01-01T17:00:00Z'},
            {'title': 'Regular Show', 'start_time': '2024-01-01T18:00:00Z'},
            {'title': 'Breaking News Special', 'start_time': '2024-01-01T19:00:00Z'},
        ]
        
        with patch.object(self.service, 'get_programs_by_channel', return_value=programs):
            matches = self.service.test_regex_against_epg(1, '^Breaking News')
        
        self.assertEqual(len(matches), 2)
        self.assertEqual(matches[0]['title'], 'Breaking News at 5')
        self.assertEqual(matches[1]['title'], 'Breaking News Special')
    
    def test_test_regex_case_insensitive(self):
        """Test regex matching is case insensitive."""
        programs = [
            {'title': 'breaking news at 5', 'start_time': '2024-01-01T17:00:00Z'},
            {'title': 'BREAKING NEWS SPECIAL', 'start_time': '2024-01-01T19:00:00Z'},
        ]
        
        with patch.object(self.service, 'get_programs_by_channel', return_value=programs):
            matches = self.service.test_regex_against_epg(1, '^Breaking News')
        
        self.assertEqual(len(matches), 2)
    
    def test_test_regex_with_invalid_pattern(self):
        """Test testing invalid regex pattern raises ValueError."""
        with self.assertRaises(ValueError) as context:
            self.service.test_regex_against_epg(1, '(unclosed')
        
        self.assertIn('Invalid regex pattern', str(context.exception))


if __name__ == '__main__':
    unittest.main()
