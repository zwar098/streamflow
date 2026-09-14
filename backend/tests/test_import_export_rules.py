"""
Tests for Auto-Create Rules Import/Export functionality.

This test suite validates:
1. Export functionality - rules can be exported to JSON format
2. Import functionality - rules can be imported from JSON format
3. Import validation - invalid rules are rejected properly
4. Round-trip integrity - exported rules can be imported back
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

# Set up test environment before imports
os.environ['CONFIG_DIR'] = tempfile.mkdtemp()

import apps.automation.scheduling_service
from apps.udi import get_udi_manager


class TestAutoCreateRulesImportExport(unittest.TestCase):
    """Test auto-create rules import/export functionality."""
    
    @classmethod
    def setUpClass(cls):
        """Set up test fixtures."""
        cls.temp_dir = tempfile.mkdtemp()
        os.environ['CONFIG_DIR'] = cls.temp_dir
        
        # Set paths
        scheduling_service.CONFIG_DIR = Path(cls.temp_dir)
        scheduling_service.SCHEDULED_EVENTS_FILE = scheduling_service.CONFIG_DIR / 'scheduled_events.json'
        scheduling_service.AUTO_CREATE_RULES_FILE = scheduling_service.CONFIG_DIR / 'auto_create_rules.json'
        scheduling_service.SCHEDULING_CONFIG_FILE = scheduling_service.CONFIG_DIR / 'scheduling_config.json'
        scheduling_service.EXECUTED_EVENTS_FILE = scheduling_service.CONFIG_DIR / 'executed_events.json'
    
    @classmethod
    def tearDownClass(cls):
        """Clean up test fixtures."""
        if os.path.exists(cls.temp_dir):
            shutil.rmtree(cls.temp_dir)
    
    def setUp(self):
        """Set up each test."""
        # Clear any existing rules
        self.service = scheduling_service.SchedulingService()
        self.service._auto_create_rules = []
        self.service._save_auto_create_rules()
    
    def tearDown(self):
        """Clean up after each test."""
        pass
    
    def test_export_empty_rules(self):
        """Test exporting when there are no rules."""
        exported = self.service.export_auto_create_rules()
        self.assertEqual(exported, [])
        self.assertIsInstance(exported, list)
    
    def test_export_single_rule(self):
        """Test exporting a single rule."""
        # Create a rule with mock channel
        rule_data = {
            'name': 'Test Rule',
            'channel_ids': [1],
            'regex_pattern': '^Test',
            'minutes_before': 5
        }
        
        # Mock the UDI manager
        udi = get_udi_manager()
        original_get_channel = udi.get_channel_by_id
        
        def mock_get_channel(channel_id):
            if channel_id == 1:
                return {
                    'id': 1,
                    'name': 'Test Channel',
                    'tvg_id': 'test.channel'
                }
            return None
        
        try:
            udi.get_channel_by_id = mock_get_channel
            created_rule = self.service.create_auto_create_rule(rule_data)
            
            # Export rules
            exported = self.service.export_auto_create_rules()
            
            # Validate export
            self.assertEqual(len(exported), 1)
            self.assertEqual(exported[0]['name'], 'Test Rule')
            self.assertEqual(exported[0]['channel_ids'], [1])
            self.assertEqual(exported[0]['channel_id'], 1)  # Backward compatibility
            self.assertEqual(exported[0]['regex_pattern'], '^Test')
            self.assertEqual(exported[0]['minutes_before'], 5)
            
            # Should not include internal fields
            self.assertNotIn('id', exported[0])
            self.assertNotIn('created_at', exported[0])
            self.assertNotIn('channels_info', exported[0])
            
        finally:
            udi.get_channel_by_id = original_get_channel
    
    def test_export_multiple_channels_rule(self):
        """Test exporting a rule with multiple channels."""
        rule_data = {
            'name': 'Multi-Channel Rule',
            'channel_ids': [1, 2, 3],
            'regex_pattern': '^News',
            'minutes_before': 10
        }
        
        # Mock the UDI manager
        udi = get_udi_manager()
        original_get_channel = udi.get_channel_by_id
        
        def mock_get_channel(channel_id):
            if channel_id in [1, 2, 3]:
                return {
                    'id': channel_id,
                    'name': f'Channel {channel_id}',
                    'tvg_id': f'channel.{channel_id}'
                }
            return None
        
        try:
            udi.get_channel_by_id = mock_get_channel
            created_rule = self.service.create_auto_create_rule(rule_data)
            
            # Export rules
            exported = self.service.export_auto_create_rules()
            
            # Validate export
            self.assertEqual(len(exported), 1)
            self.assertEqual(exported[0]['channel_ids'], [1, 2, 3])
            # Should not have channel_id for multi-channel rules
            self.assertNotIn('channel_id', exported[0])
            
        finally:
            udi.get_channel_by_id = original_get_channel
    
    def test_import_valid_rules(self):
        """Test importing valid rules."""
        rules_data = [
            {
                'name': 'Rule 1',
                'channel_ids': [1],
                'regex_pattern': '^Test1',
                'minutes_before': 5
            },
            {
                'name': 'Rule 2',
                'channel_id': 2,  # Backward compatibility format
                'regex_pattern': '^Test2',
                'minutes_before': 10
            }
        ]
        
        # Mock the UDI manager
        udi = get_udi_manager()
        original_get_channel = udi.get_channel_by_id
        
        def mock_get_channel(channel_id):
            if channel_id in [1, 2]:
                return {
                    'id': channel_id,
                    'name': f'Channel {channel_id}',
                    'tvg_id': f'channel.{channel_id}'
                }
            return None
        
        try:
            udi.get_channel_by_id = mock_get_channel
            
            # Import rules
            result = self.service.import_auto_create_rules(rules_data)
            
            # Validate result
            self.assertEqual(result['imported'], 2)
            self.assertEqual(result['failed'], 0)
            self.assertEqual(result['total'], 2)
            self.assertEqual(len(result['errors']), 0)
            
            # Check rules were created
            rules = self.service.get_auto_create_rules()
            self.assertEqual(len(rules), 2)
            
        finally:
            udi.get_channel_by_id = original_get_channel
    
    def test_import_invalid_rules(self):
        """Test importing invalid rules."""
        rules_data = [
            {
                'name': 'Valid Rule',
                'channel_ids': [1],
                'regex_pattern': '^Test',
                'minutes_before': 5
            },
            {
                # Missing name
                'channel_ids': [1],
                'regex_pattern': '^Test',
                'minutes_before': 5
            },
            {
                'name': 'Missing Pattern',
                'channel_ids': [1],
                # Missing regex_pattern
                'minutes_before': 5
            },
            {
                'name': 'Invalid Channel',
                'channel_ids': [999],  # Non-existent channel
                'regex_pattern': '^Test',
                'minutes_before': 5
            }
        ]
        
        # Mock the UDI manager
        udi = get_udi_manager()
        original_get_channel = udi.get_channel_by_id
        
        def mock_get_channel(channel_id):
            if channel_id == 1:
                return {
                    'id': 1,
                    'name': 'Channel 1',
                    'tvg_id': 'channel.1'
                }
            return None
        
        try:
            udi.get_channel_by_id = mock_get_channel
            
            # Import rules
            result = self.service.import_auto_create_rules(rules_data)
            
            # Validate result
            self.assertEqual(result['imported'], 1)  # Only first rule should succeed
            self.assertEqual(result['failed'], 3)
            self.assertEqual(result['total'], 4)
            self.assertEqual(len(result['errors']), 3)
            
            # Check only valid rule was created
            rules = self.service.get_auto_create_rules()
            self.assertEqual(len(rules), 1)
            self.assertEqual(rules[0]['name'], 'Valid Rule')
            
        finally:
            udi.get_channel_by_id = original_get_channel
    
    def test_import_not_list(self):
        """Test importing when data is not a list."""
        with self.assertRaises(ValueError):
            self.service.import_auto_create_rules({'not': 'a list'})
    
    def test_round_trip_export_import(self):
        """Test that exported rules can be imported back successfully."""
        # Create some rules
        initial_rules = [
            {
                'name': 'Rule 1',
                'channel_ids': [1],
                'regex_pattern': '^Test1',
                'minutes_before': 5
            },
            {
                'name': 'Rule 2',
                'channel_ids': [2, 3],
                'regex_pattern': '^Test2',
                'minutes_before': 10
            }
        ]
        
        # Mock the UDI manager
        udi = get_udi_manager()
        original_get_channel = udi.get_channel_by_id
        
        def mock_get_channel(channel_id):
            if channel_id in [1, 2, 3]:
                return {
                    'id': channel_id,
                    'name': f'Channel {channel_id}',
                    'tvg_id': f'channel.{channel_id}'
                }
            return None
        
        try:
            udi.get_channel_by_id = mock_get_channel
            
            # Create initial rules
            for rule_data in initial_rules:
                self.service.create_auto_create_rule(rule_data)
            
            # Export
            exported = self.service.export_auto_create_rules()
            self.assertEqual(len(exported), 2)
            
            # Clear rules
            self.service._auto_create_rules = []
            self.service._save_auto_create_rules()
            self.assertEqual(len(self.service.get_auto_create_rules()), 0)
            
            # Import back
            result = self.service.import_auto_create_rules(exported)
            
            # Validate
            self.assertEqual(result['imported'], 2)
            self.assertEqual(result['failed'], 0)
            
            # Check rules match
            final_rules = self.service.get_auto_create_rules()
            self.assertEqual(len(final_rules), 2)
            
            # Verify key fields match
            for i, rule in enumerate(final_rules):
                self.assertEqual(rule['name'], initial_rules[i]['name'])
                self.assertEqual(rule['regex_pattern'], initial_rules[i]['regex_pattern'])
                self.assertEqual(rule['minutes_before'], initial_rules[i]['minutes_before'])
            
        finally:
            udi.get_channel_by_id = original_get_channel

    def test_round_trip_preserves_timing_direction(self):
        """A rule scheduled after program start must still say 'after' once
        exported and re-imported — export/import previously only carried
        minutes_before, which would have silently reverted every 'after'
        rule back to 'before' on the very next backup/restore."""
        rule_data = {
            'name': 'After Start Rule',
            'channel_ids': [1],
            'regex_pattern': '^Test',
            'minutes_before': 20,
            'timing_direction': 'after',
        }

        udi = get_udi_manager()
        original_get_channel = udi.get_channel_by_id

        def mock_get_channel(channel_id):
            if channel_id == 1:
                return {'id': 1, 'name': 'Test Channel', 'tvg_id': 'test.channel'}
            return None

        try:
            udi.get_channel_by_id = mock_get_channel

            self.service.create_auto_create_rule(rule_data)
            exported = self.service.export_auto_create_rules()
            self.assertEqual(exported[0]['timing_direction'], 'after')

            self.service._auto_create_rules = []
            self.service._save_auto_create_rules()

            result = self.service.import_auto_create_rules(exported)
            self.assertEqual(result['imported'], 1)

            final_rules = self.service.get_auto_create_rules()
            self.assertEqual(final_rules[0]['timing_direction'], 'after')
        finally:
            udi.get_channel_by_id = original_get_channel

    def test_export_defaults_missing_timing_direction_to_before(self):
        """Rules created before timing_direction existed must still export
        as 'before' rather than an absent/null field."""
        rule_data = {
            'name': 'Legacy Rule',
            'channel_ids': [1],
            'regex_pattern': '^Test',
            'minutes_before': 5,
        }

        udi = get_udi_manager()
        original_get_channel = udi.get_channel_by_id
        udi.get_channel_by_id = lambda channel_id: (
            {'id': 1, 'name': 'Test Channel', 'tvg_id': 'test.channel'} if channel_id == 1 else None
        )

        try:
            self.service.create_auto_create_rule(rule_data)
            exported = self.service.export_auto_create_rules()
            self.assertEqual(exported[0]['timing_direction'], 'before')
        finally:
            udi.get_channel_by_id = original_get_channel


if __name__ == '__main__':
    unittest.main()
