#!/usr/bin/env python3
"""
Unit test for CircuitBreaker - verifies state machine transitions.
"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from safety_monitor import CircuitBreaker, CircuitState

TEST_STATE_FILE = "/tmp/test_circuit_breaker.json"

def test_initial_state():
    """Objective: Verify CircuitBreaker starts in ACTIVE state."""
    os.environ['TEST_MODE'] = '1'
    cb = CircuitBreaker(state_file=TEST_STATE_FILE)
    
    actual = cb.state
    expected = CircuitState.ACTIVE
    
    print(f"Initial state: {actual.value} (expected: {expected.value})")
    assert actual == expected, f"FAIL: Expected {expected}, got {actual}"
    assert cb.can_trade() == True, "FAIL: Should be able to trade initially"
    print("✅ PASS: Initial state is ACTIVE")

def test_pause_for_drawdown():
    """Objective: Verify drawdown pause sets correct state."""
    cb = CircuitBreaker(state_file=TEST_STATE_FILE)
    
    cb.pause_for_drawdown("Test drawdown 12%")
    
    actual = cb.state
    assert actual == CircuitState.PAUSED_DRAWDOWN
    assert cb.can_trade() == False
    assert "drawdown" in cb.pause_reason.lower()
    print("✅ PASS: pause_for_drawdown sets PAUSED_DRAWDOWN")

def test_manual_resume_blocked():
    """Objective: Verify PAUSED_DRAWDOWN cannot auto-resume."""
    cb = CircuitBreaker(state_file=TEST_STATE_FILE)
    cb.state = CircuitState.PAUSED_DRAWDOWN
    
    result = cb.resume()
    
    assert result == False, "FAIL: Should not auto-resume from drawdown"
    assert cb.state == CircuitState.PAUSED_DRAWDOWN
    print("✅ PASS: PAUSED_DRAWDDOWN blocks auto-resume")

def test_pause_for_error():
    """Objective: Verify error pause sets correct state."""
    cb = CircuitBreaker(state_file=TEST_STATE_FILE)
    cb._reset_to_active()  # Reset first
    
    cb.pause_for_error("API error rate 25%")
    
    assert cb.state == CircuitState.PAUSED_ERROR
    assert "error" in cb.pause_reason.lower()
    print("✅ PASS: pause_for_error sets PAUSED_ERROR")

def test_auto_reset_conditions():
    """Objective: Verify auto-reset only happens when conditions met."""
    cb = CircuitBreaker(state_file=TEST_STATE_FILE)
    cb.state = CircuitState.PAUSED_ERROR
    cb.state_since = datetime.now()  # Reset timestamp
    
    # Should NOT reset - too soon
    result = cb.check_auto_reset(api_error_rate=0.01)
    assert result == False, "FAIL: Should not reset when < 5 min elapsed"
    print("✅ PASS: No auto-reset when < 5 min elapsed")
    
    # Manually backdate state
    from datetime import timedelta
    cb.state_since = datetime.now() - timedelta(minutes=6)
    
    # Should NOT reset - error rate still too high
    result = cb.check_auto_reset(api_error_rate=0.10)
    assert result == False, "FAIL: Should not reset when error rate > 5%"
    print("✅ PASS: No auto-reset when error rate > 5%")
    
    # Should reset - time passed AND error rate low
    result = cb.check_auto_reset(api_error_rate=0.01)
    assert result == True, "FAIL: Should auto-reset"
    assert cb.state == CircuitState.ACTIVE
    print("✅ PASS: Auto-reset when conditions met")

def cleanup():
    """Remove test state file."""
    if os.path.exists(TEST_STATE_FILE):
        os.remove(TEST_STATE_FILE)

if __name__ == "__main__":
    from datetime import datetime
    
    print("=" * 50)
    print("Running CircuitBreaker Unit Tests")
    print("=" * 50)
    
    test_initial_state()
    test_pause_for_drawdown()
    test_manual_resume_blocked()
    test_pause_for_error()
    test_auto_reset_conditions()
    
    cleanup()
    
    print("=" * 50)
    print("✅ ALL TESTS PASSED")
    print("=" * 50)
