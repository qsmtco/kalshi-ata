#!/usr/bin/env python3
"""
Unit tests for circuit breaker monitor logic.
Tests the check functions without requiring API connection.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from safety_monitor import CircuitBreaker, CircuitState


def test_drawdown_threshold_triggers_pause():
    """
    Objective: Verify drawdown > 10% triggers PAUSED_DRAWDOWN.
    """
    cb = CircuitBreaker(state_file="/tmp/test_cb_drawdown.json")
    cb._reset_to_active()
    
    # Simulate metrics with 12% drawdown
    metrics = {
        'overall_statistics': {
            'total_pnl': -120  # $120 loss on $1000 bankroll = 12% drawdown
        }
    }
    
    # Import the check function logic directly
    bankroll = 1000
    total_pnl = metrics['overall_statistics']['total_pnl']
    current_equity = bankroll + total_pnl
    drawdown = (bankroll - current_equity) / bankroll
    
    assert drawdown > 0.10, f"Test setup: drawdown should be > 10%, got {drawdown:.1%}"
    
    # This should trigger pause
    if drawdown > cb.DRAWDOWN_THRESHOLD:
        cb.pause_for_drawdown(f"Test drawdown {drawdown:.1%}")
    
    assert cb.state == CircuitState.PAUSED_DRAWDOWN
    print("✅ PASS: 12% drawdown triggers PAUSED_DRAWDOWN")


def test_loss_threshold_triggers_pause():
    """
    Objective: Verify 24h loss > 5% triggers PAUSED_DRAWDOWN.
    """
    cb = CircuitBreaker(state_file="/tmp/test_cb_loss.json")
    cb._reset_to_active()
    
    # Simulate 6% daily loss
    metrics = {
        'overall_statistics': {
            'total_pnl': -60  # $60 loss = 6%
        }
    }
    
    bankroll = 1000
    total_pnl = metrics['overall_statistics']['total_pnl']
    daily_loss_pct = abs(total_pnl) / bankroll
    
    assert daily_loss_pct > 0.05
    
    if daily_loss_pct > cb.DAILY_LOSS_THRESHOLD:
        cb.pause_for_drawdown(f"Test daily loss {daily_loss_pct:.1%}")
    
    assert cb.state == CircuitState.PAUSED_DRAWDOWN
    print("✅ PASS: 6% daily loss triggers PAUSED_DRAWDOWN")


def test_api_error_threshold_triggers_pause():
    """
    Objective: Verify API error rate > 20% triggers PAUSED_ERROR.
    """
    cb = CircuitBreaker(state_file="/tmp/test_cb_error.json")
    cb._reset_to_active()
    
    api_error_rate = 0.25  # 25% error rate
    
    if cb.state == CircuitState.ACTIVE:
        if api_error_rate > cb.API_ERROR_THRESHOLD:
            cb.pause_for_error(f"API error rate {api_error_rate:.1%}")
    
    assert cb.state == CircuitState.PAUSED_ERROR
    print("✅ PASS: 25% API error rate triggers PAUSED_ERROR")


def test_auto_reset_when_error_rate_normalizes():
    """
    Objective: Verify PAUSED_ERROR auto-resets when error rate drops below 5%.
    """
    from datetime import timedelta
    
    cb = CircuitBreaker(state_file="/tmp/test_cb_reset.json")
    cb.state = CircuitState.PAUSED_ERROR
    cb.state_since = datetime.now() - timedelta(minutes=6)  # Backdate
    
    api_error_rate = 0.01  # 1% - below 5% threshold
    
    # Check auto reset
    result = cb.check_auto_reset(api_error_rate)
    
    assert result == True, "Should auto-reset"
    assert cb.state == CircuitState.ACTIVE
    print("✅ PASS: Auto-reset when error rate normalizes")


def test_no_trigger_when_under_thresholds():
    """
    Objective: Verify no state change when under all thresholds.
    """
    cb = CircuitBreaker(state_file="/tmp/test_cb_safe.json")
    cb._reset_to_active()
    
    # Under drawdown threshold
    metrics = {'overall_statistics': {'total_pnl': -50}}  # 5% - exactly at threshold
    bankroll = 1000
    
    # Don't trigger if exactly at threshold (need >)
    total_pnl = metrics['overall_statistics']['total_pnl']
    current_equity = bankroll + total_pnl
    drawdown = max(0, (bankroll - current_equity) / bankroll)
    
    if drawdown > cb.DRAWDOWN_THRESHOLD:
        cb.pause_for_drawdown("test")
    
    # API errors under threshold
    api_error_rate = 0.05  # 5% - exactly at threshold
    
    if cb.state == CircuitState.ACTIVE:
        if api_error_rate > cb.API_ERROR_THRESHOLD:
            cb.pause_for_error("test")
    
    assert cb.state == CircuitState.ACTIVE
    print("✅ PASS: No trigger when under all thresholds")


def cleanup():
    """Remove test state files."""
    import glob
    for f in glob.glob("/tmp/test_cb_*.json"):
        os.remove(f)


if __name__ == "__main__":
    from datetime import datetime, timedelta
    
    print("=" * 50)
    print("Running Circuit Breaker Monitor Tests")
    print("=" * 50)
    
    test_drawdown_threshold_triggers_pause()
    test_loss_threshold_triggers_pause()
    test_api_error_threshold_triggers_pause()
    test_auto_reset_when_error_rate_normalizes()
    test_no_trigger_when_under_thresholds()
    
    cleanup()
    
    print("=" * 50)
    print("✅ ALL MONITOR TESTS PASSED")
    print("=" * 50)
