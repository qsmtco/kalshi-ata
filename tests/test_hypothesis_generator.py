#!/usr/bin/env python3
"""
Unit tests for HypothesisGenerator.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from hypothesis_generator import HypothesisGenerator, Hypothesis


def test_kelly_adjust_high_sharpe():
    """Objective: Verify Kelly increases when Sharpe > 2.0."""
    settings = {'kellyFraction': 0.5, 'maxPositionSizePct': 0.1}
    perf = {
        'overall_statistics': {'sharpe_ratio': 2.5},
        'strategy_breakdown': {}
    }
    
    gen = HypothesisGenerator(settings)
    hyps = gen.generate(perf)
    
    kelly_hyps = [h for h in hyps if h.parameter == 'kellyFraction']
    assert len(kelly_hyps) == 1, "Should generate Kelly hypothesis"
    assert kelly_hyps[0].suggested_value > kelly_hyps[0].current_value
    print("✅ PASS: Kelly increases with high Sharpe")


def test_kelly_adjust_low_sharpe():
    """Objective: Verify Kelly decreases when Sharpe < 1.0."""
    settings = {'kellyFraction': 0.5, 'maxPositionSizePct': 0.1}
    perf = {
        'overall_statistics': {'sharpe_ratio': 0.5},
        'strategy_breakdown': {}
    }
    
    gen = HypothesisGenerator(settings)
    hyps = gen.generate(perf)
    
    kelly_hyps = [h for h in hyps if h.parameter == 'kellyFraction']
    assert len(kelly_hyps) == 1, "Should generate Kelly hypothesis"
    assert kelly_hyps[0].suggested_value < kelly_hyps[0].current_value
    print("✅ PASS: Kelly decreases with low Sharpe")


def test_threshold_adjust_low_winrate():
    """Objective: Verify threshold increases when win rate < 50%."""
    settings = {'newsSentimentThreshold': 0.6, 'kellyFraction': 0.5}
    perf = {
        'overall_statistics': {'sharpe_ratio': 1.0},
        'strategy_breakdown': {
            'news_sentiment': {'win_rate': 0.35, 'total_trades': 20}
        }
    }
    
    gen = HypothesisGenerator(settings)
    hyps = gen.generate(perf)
    
    thresh_hyps = [h for h in hyps if 'Threshold' in h.parameter]
    assert len(thresh_hyps) == 1, "Should generate threshold hypothesis"
    assert thresh_hyps[0].suggested_value > thresh_hyps[0].current_value
    print("✅ PASS: Threshold increases with low win rate")


def test_strategy_disable_underperforming():
    """Objective: Verify strategy disables with few trades + low Sharpe."""
    settings = {'news_sentiment_enabled': True, 'kellyFraction': 0.5}
    perf = {
        'overall_statistics': {'sharpe_ratio': 1.0},
        'strategy_breakdown': {
            'news_sentiment': {'total_trades': 5, 'sharpe_ratio': 0.3}
        }
    }
    
    gen = HypothesisGenerator(settings)
    hyps = gen.generate(perf)
    
    disable_hyps = [h for h in hyps if h.hypothesis_type == 'strategy_disable']
    assert len(disable_hyps) == 1, "Should generate disable hypothesis"
    assert disable_hyps[0].suggested_value == False
    print("✅ PASS: Underperforming strategy disabled")


def test_no_hypothesis_when_healthy():
    """Objective: Verify no hypotheses when performance is good."""
    settings = {'kellyFraction': 0.5, 'maxPositionSizePct': 0.1, 'newsSentimentThreshold': 0.6}
    perf = {
        'overall_statistics': {'sharpe_ratio': 1.5, 'total_trades': 100},
        'strategy_breakdown': {
            'news_sentiment': {'win_rate': 0.55, 'total_trades': 30, 'sharpe_ratio': 1.2}
        }
    }
    
    gen = HypothesisGenerator(settings)
    hyps = gen.generate(perf)
    
    # Should only have correlation adjustment (3 strategies)
    assert len(hyps) <= 1, "Should have minimal hypotheses"
    print("✅ PASS: Few hypotheses when healthy")


if __name__ == "__main__":
    print("=" * 50)
    print("Running HypothesisGenerator Tests")
    print("=" * 50)
    
    test_kelly_adjust_high_sharpe()
    test_kelly_adjust_low_sharpe()
    test_threshold_adjust_low_winrate()
    test_strategy_disable_underperforming()
    test_no_hypothesis_when_healthy()
    
    print("=" * 50)
    print("✅ ALL TESTS PASSED")
    print("=" * 50)
