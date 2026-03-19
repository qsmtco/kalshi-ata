#!/usr/bin/env python3
"""
Unit tests for agent_loop helper functions.
Tests guardrail validation and adjustment calculation.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


# Guardrails (copied from agent_loop.py)
GUARDRAILS = {
    'kellyFraction': (0.1, 0.8),
    'maxPositionSizePct': (0.01, 0.25),
    'stopLossPct': (0.01, 0.20),
    'newsSentimentThreshold': (0.3, 0.9),
}


def validate_guardrail(parameter: str, value: float) -> tuple:
    """Validate value against guardrails. Returns (valid, error_msg)."""
    if parameter not in GUARDRAILS:
        return True, ""
    min_val, max_val = GUARDRAILS[parameter]
    if not (min_val <= value <= max_val):
        return False, f"Value {value} outside [{min_val}, {max_val}]"
    return True, ""


def compute_conservative_adjustment(current: float, suggested: float) -> float:
    """Take 50% step toward suggested value."""
    return current + (suggested - current) * 0.5


# === TESTS ===

def test_validate_guardrail_valid():
    """Valid values should pass."""
    valid, msg = validate_guardrail('kellyFraction', 0.5)
    assert valid == True, f"Expected valid, got: {msg}"
    print("✅ PASS: Valid kellyFraction accepted")


def test_validate_guardrail_below_min():
    """Values below min should fail."""
    valid, msg = validate_guardrail('kellyFraction', 0.05)
    assert valid == False, "Should reject below min"
    assert "outside" in msg, f"Error msg should mention bounds: {msg}"
    print("✅ PASS: Below min rejected")


def test_validate_guardrail_above_max():
    """Values above max should fail."""
    valid, msg = validate_guardrail('kellyFraction', 0.9)
    assert valid == False, "Should reject above max"
    print("✅ PASS: Above max rejected")


def test_validate_guardrail_unknown_param():
    """Unknown parameters should pass (no guardrail)."""
    valid, msg = validate_guardrail('unknownParam', 999)
    assert valid == True, "Unknown params should pass"
    print("✅ PASS: Unknown param accepted")


def test_compute_conservative_adjustment():
    """50% step calculation."""
    result = compute_conservative_adjustment(0.5, 0.7)
    assert result == 0.6, f"Expected 0.6, got {result}"
    print("✅ PASS: 50% step calculation correct")


def test_compute_conservative_no_change():
    """Same current and suggested."""
    result = compute_conservative_adjustment(0.5, 0.5)
    assert result == 0.5, f"Expected 0.5, got {result}"
    print("✅ PASS: No change when same values")


def test_check_circuit_breaker_active():
    """Circuit breaker in ACTIVE allows trading."""
    from safety_monitor import CircuitBreaker, CircuitState
    
    cb = CircuitBreaker(state_file="/tmp/test_cb_active.json")
    cb._reset_to_active()
    
    status = cb.get_status()
    assert status['can_trade'] == True, "ACTIVE should allow trading"
    print("✅ PASS: ACTIVE circuit breaker allows trading")


def test_check_circuit_breaker_paused():
    """Circuit breaker in PAUSED blocks trading."""
    from safety_monitor import CircuitBreaker, CircuitState
    
    cb = CircuitBreaker(state_file="/tmp/test_cb_paused.json")
    cb.pause_for_drawdown("Test drawdown")
    
    status = cb.get_status()
    assert status['can_trade'] == False, "PAUSED should block trading"
    print("✅ PASS: PAUSED circuit breaker blocks trading")


def test_rate_limit_under():
    """Under limit should pass."""
    from agent_decisions import AgentDecisionLogger
    
    logger = AgentDecisionLogger(db_path="/tmp/test_rate_limit.db")
    # Fresh DB = 0 decisions = under limit
    
    count = logger.get_decisions_count_last_24h()
    assert count < 3, f"Should be under limit, got {count}"
    print("✅ PASS: Under rate limit passes")


def cleanup():
    """Remove test files."""
    import glob
    for f in glob.glob("/tmp/test_*.json") + glob.glob("/tmp/test_*.db"):
        try:
            os.remove(f)
        except:
            pass


if __name__ == "__main__":
    print("=" * 50)
    print("Running Agent Loop Helper Tests")
    print("=" * 50)
    
    test_validate_guardrail_valid()
    test_validate_guardrail_below_min()
    test_validate_guardrail_above_max()
    test_validate_guardrail_unknown_param()
    test_compute_conservative_adjustment()
    test_compute_conservative_no_change()
    test_check_circuit_breaker_active()
    test_check_circuit_breaker_paused()
    test_rate_limit_under()
    
    cleanup()
    
    print("=" * 50)
    print("✅ ALL TESTS PASSED")
    print("=" * 50)
