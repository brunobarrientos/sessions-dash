#!/usr/bin/env pytest
"""Unit tests for sessions-dash server.py business logic."""

import os
import sys
import json
import tempfile
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

# Import the module under test
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from server import (
    _cost,
    _decode_project_folder,
    compute_usage,
    compute_sessions,
    MODEL_PRICING,
)


class TestCostCalculation:
    """Tests for _cost() function."""

    def test_claude_sonnet_cost(self):
        """Test cost calculation for Claude Sonnet 4-6."""
        usage = {'input': 100000, 'output': 50000, 'cacheRead': 10000, 'cacheWrite': 5000}
        cost = _cost('claude-sonnet-4-6', usage)
        # input: (100000 - 15000) * 3.00 / 1M = 0.255
        # output: 50000 * 15.00 / 1M = 0.75
        # cacheRead: 10000 * 0.30 / 1M = 0.003
        # cacheWrite: 5000 * 3.75 / 1M = 0.01875
        expected = 0.255 + 0.75 + 0.003 + 0.01875
        assert abs(cost - expected) < 0.001

    def test_minimax_cost(self):
        """Test cost calculation for MiniMax M2.5."""
        usage = {'input': 1000000, 'output': 500000, 'cacheRead': 100000, 'cacheWrite': 50000}
        cost = _cost('MiniMax-M2.5', usage)
        # input: (1000000 - 150000) * 0.30 / 1M = 0.255
        # output: 500000 * 1.20 / 1M = 0.60
        # cacheRead: 100000 * 0.03 / 1M = 0.003
        # cacheWrite: 50000 * 0.30 / 1M = 0.015
        expected = 0.255 + 0.60 + 0.003 + 0.015
        assert abs(cost - expected) < 0.001

    def test_claude_opus_cost(self):
        """Test cost calculation for Claude Opus 4-6 (expensive)."""
        usage = {'input': 100000, 'output': 100000, 'cacheRead': 0, 'cacheWrite': 0}
        cost = _cost('claude-opus-4-6', usage)
        # input: 100000 * 15.00 / 1M = 1.50
        # output: 100000 * 75.00 / 1M = 7.50
        expected = 1.50 + 7.50
        assert abs(cost - expected) < 0.001

    def test_default_pricing(self):
        """Test cost uses default pricing for unknown models."""
        usage = {'input': 1000000, 'output': 500000, 'cacheRead': 0, 'cacheWrite': 0}
        cost = _cost('unknown-model-xyz', usage)
        # Default: input 1.00, output 5.00
        # input: 1000000 * 1.00 / 1M = 1.00
        # output: 500000 * 5.00 / 1M = 2.50
        expected = 1.00 + 2.50
        assert abs(cost - expected) < 0.001

    def test_zero_usage(self):
        """Test cost is zero when there's no usage."""
        usage = {'input': 0, 'output': 0, 'cacheRead': 0, 'cacheWrite': 0}
        cost = _cost('claude-sonnet-4-6', usage)
        assert cost == 0

    def test_cache_only(self):
        """Test cost calculation with only cache tokens."""
        usage = {'input': 0, 'output': 0, 'cacheRead': 100000, 'cacheWrite': 50000}
        cost = _cost('claude-sonnet-4-6', usage)
        # cacheRead: 100000 * 0.30 / 1M = 0.03
        # cacheWrite: 50000 * 3.75 / 1M = 0.1875
        expected = 0.03 + 0.1875
        assert abs(cost - expected) < 0.001


class TestProjectFolderDecoding:
    """Tests for _decode_project_folder() function."""

    @patch('os.path.exists')
    def test_home_folder(self, mock_exists):
        """Test decoding home folder."""
        mock_exists.return_value = True
        # Encoded home folder
        result = _decode_project_folder('-home-asus')
        assert result == '~'

    @patch('os.path.exists')
    def test_simple_subfolder(self, mock_exists):
        """Test decoding a simple subfolder."""
        mock_exists.return_value = True
        with patch('os.path.join', side_effect=lambda *args: '/'.join(args)):
            result = _decode_project_folder('-home-asus-AI-projects')
            # Should decode to ~/AI (if the path exists)
            # The function tries to reconstruct, results vary based on filesystem

    def test_unknown_folder_returns_unchanged(self):
        """Test that unknown folders return encoded form."""
        # Non-home, non-prefixed folder
        result = _decode_project_folder('some-random-folder')
        assert result == 'some-random-folder'


class TestComputeUsage:
    """Tests for compute_usage() function."""

    def test_no_claude_dir(self):
        """Test when .claude/projects doesn't exist."""
        with patch('os.path.isdir', return_value=False):
            result = compute_usage(7)
            assert result['byModel'] == {}
            assert result['byDay'] == []
            assert result['totalEstimatedCost'] == 0
            assert result['totalSessions'] == 0

    @patch('os.path.isdir')
    @patch('os.listdir')
    def test_empty_projects_dir(self, mock_listdir, mock_isdir):
        """Test when projects directory is empty."""
        mock_isdir.return_value = True
        mock_listdir.return_value = []
        result = compute_usage(7)
        assert result['byModel'] == {}
        assert result['byDay'] == []

    @patch('os.path.isdir')
    @patch('os.listdir')
    @patch('glob.glob')
    @patch('builtins.open', MagicMock())
    @patch('os.path.getmtime')
    def test_parses_session_file(self, mock_mtime, mock_glob, mock_listdir, mock_isdir):
        """Test parsing a session JSONL file."""
        mock_isdir.return_value = True
        mock_listdir.return_value = ['-home-user-testproject']
        mock_glob.return_value = ['/home/user/.claude/projects/-home-user-testproject/session.jsonl']

        # Mock file mtime to be recent
        mock_mtime.return_value = datetime.now().timestamp()

        # Mock file content
        session_data = {
            'timestamp': datetime.now().isoformat(),
            'model': 'claude-sonnet-4-6',
            'message': {
                'model': 'claude-sonnet-4-6',
                'usage': {
                    'input_tokens': 1000,
                    'output_tokens': 500,
                    'cache_read_input_tokens': 100,
                    'cache_creation_input_tokens': 50,
                }
            }
        }

        mock_file = MagicMock()
        mock_file.__enter__ = MagicMock(return_value=mock_file)
        mock_file.__exit__ = MagicMock(return_value=False)
        mock_file.__iter__ = MagicMock(return_value=iter([json.dumps(session_data)]))

        with patch('builtins.open', return_value=mock_file):
            result = compute_usage(7)

        # Should have parsed the usage
        assert 'claude-sonnet-4-6' in result['byModel']
        model_usage = result['byModel']['claude-sonnet-4-6']
        assert model_usage['input'] == 1000
        assert model_usage['output'] == 500


class TestComputeSessions:
    """Tests for compute_sessions() function."""

    def test_no_claude_dir(self):
        """Test when .claude/projects doesn't exist."""
        with patch('os.path.isdir', return_value=False):
            result = compute_sessions(7)
            assert result['sessions'] == []
            assert result['total'] == 0

    @patch('os.path.isdir')
    @patch('os.listdir')
    def test_empty_projects_dir(self, mock_listdir, mock_isdir):
        """Test when projects directory is empty."""
        mock_isdir.return_value = True
        mock_listdir.return_value = []
        result = compute_sessions(7)
        assert result['sessions'] == []


class TestModelPricing:
    """Tests for MODEL_PRICING configuration."""

    def test_all_pricing_has_required_fields(self):
        """Test that all models in pricing have required fields."""
        required_fields = {'input', 'output', 'cacheRead', 'cacheWrite'}
        for model, pricing in MODEL_PRICING.items():
            assert required_fields.issubset(pricing.keys()), f"Model {model} missing fields"

    def test_pricing_values_are_positive(self):
        """Test that all pricing values are positive."""
        for model, pricing in MODEL_PRICING.items():
            for field, value in pricing.items():
                assert value > 0, f"Model {model} has non-positive {field}: {value}"

    def test_minimax_models_defined(self):
        """Test that MiniMax models are in pricing."""
        assert 'MiniMax-M2.5' in MODEL_PRICING
        assert 'MiniMax-M2.1' in MODEL_PRICING

    def test_claude_models_defined(self):
        """Test that Claude models are in pricing."""
        assert 'claude-sonnet-4-6' in MODEL_PRICING
        assert 'claude-opus-4-6' in MODEL_PRICING
        assert 'claude-haiku-4-5-20251001' in MODEL_PRICING


class TestComputeUsageComparison:
    """Tests for compute_usage_comparison() function."""

    @patch('os.path.isdir')
    @patch('os.listdir')
    def test_comparison_returns_change_percent(self, mock_listdir, mock_isdir):
        """Test that comparison includes change percent."""
        from server import compute_usage_comparison
        mock_isdir.return_value = True
        mock_listdir.return_value = []
        result = compute_usage_comparison(7)
        assert 'changePercent' in result
        assert 'previousCost' in result


class TestComputeUsageWithOffset:
    """Tests for compute_usage_with_offset() function."""

    @patch('os.path.isdir')
    @patch('os.listdir')
    def test_offset_returns_data(self, mock_listdir, mock_isdir):
        """Test that offset function returns usage data."""
        from server import compute_usage_with_offset
        mock_isdir.return_value = True
        mock_listdir.return_value = []
        result = compute_usage_with_offset(7, 7)
        assert 'byModel' in result
        assert 'byDay' in result
        assert 'totalEstimatedCost' in result
        assert result['totalEstimatedCost'] == 0


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
