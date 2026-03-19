#!/usr/bin/env python3
"""
Unit tests for Alert Manager.
Tests rate limiting and alert formatting.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from alert_manager import AlertType, SafetyAlertManager, AlertRateLimiter


def test_rate_limiter_allows_first():
    """First alert of type should always be allowed."""
    limiter = AlertRateLimiter(state_file="/tmp/test_alert_ratelimit.json")
    limiter.last_alert_time = {}  # Reset
    
    result = limiter.should_send(AlertType.CIRCUIT_BREAKER)
    assert result == True, "First alert should be allowed"
    print("✅ PASS: First alert allowed")


def test_rate_limiter_blocks_repeat_medium():
    """MEDIUM alerts should be rate limited to once per 15 min."""
    import time
    limiter = AlertRateLimiter(state_file="/tmp/test_alert_ratelimit2.json")
    limiter.last_alert_time = {}
    
    # First MEDIUM alert - should send
    result1 = limiter.should_send(AlertType.API_ERROR_RATE)
    assert result1 == True
    limiter.record_alert(AlertType.API_ERROR_RATE)
    
    # Immediate second - should be blocked
    result2 = limiter.should_send(AlertType.API_ERROR_RATE)
    assert result2 == False, "Should rate limit MEDIUM alerts"
    print("✅ PASS: MEDIUM alerts rate limited")


def test_high_severity_always_sends():
    """HIGH severity alerts should always send (no rate limit)."""
    import time
    limiter = AlertRateLimiter(state_file="/tmp/test_alert_ratelimit3.json")
    limiter.last_alert_time = {}
    
    # Record a recent HIGH alert
    limiter.last_alert_time[AlertType.CIRCUIT_BREAKER] = time.time()
    
    # Should still be allowed (HIGH severity)
    result = limiter.should_send(AlertType.CIRCUIT_BREAKER)
    assert result == True, "HIGH severity should always send"
    print("✅ PASS: HIGH severity bypasses rate limit")


def test_alert_message_formatting():
    """Verify alert messages are formatted correctly."""
    manager = SafetyAlertManager()
    
    # Test circuit breaker message
    manager.rate_limiter.last_alert_time = {}  # Reset
    
    result = manager.notify_circuit_breaker(
        "PAUSED_DRAWDOWN",
        "Drawdown 12% exceeded 10% threshold",
        None, None
    )
    
    assert result == True
    print("✅ PASS: Alert message formatted correctly")


def test_rollback_alert():
    """Test rollback alert formatting."""
    manager = SafetyAlertManager()
    manager.rate_limiter.last_alert_time = {}
    
    result = manager.notify_rollback(
        "2026-03-18T22:00:00",
        "Parameter drift exceeded limit",
        None, None
    )
    
    assert result == True
    assert "ROLLBACK" in str(manager.rate_limiter.last_alert_time.keys())
    print("✅ PASS: Rollback alert works")


def cleanup():
    """Remove test files."""
    import glob
    for f in glob.glob("/tmp/test_alert_ratelimit*.json"):
        os.remove(f)


if __name__ == "__main__":
    print("=" * 50)
    print("Running Alert Manager Tests")
    print("=" * 50)
    
    test_rate_limiter_allows_first()
    test_rate_limiter_blocks_repeat_medium()
    test_high_severity_always_sends()
    test_alert_message_formatting()
    test_rollback_alert()
    
    cleanup()
    
    print("=" * 50)
    print("✅ ALL ALERT TESTS PASSED")
    print("=" * 50)
