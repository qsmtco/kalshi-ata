#!/usr/bin/env python3
"""
Unit tests for AgentDecisionLogger.
"""

import sys
import os
import tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from agent_decisions import AgentDecisionLogger, DECISION_TYPES


def test_log_decision():
    """Objective: Verify decision logging works."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        test_db = f.name
    
    logger = AgentDecisionLogger(db_path=test_db)
    
    decision_id = logger.log_decision(
        decision_type='parameter_tuning',
        rationale='Test Kelly increase',
        parameters_modified={'kellyFraction': 0.7},
        applied=True
    )
    
    assert decision_id > 0, "Should return valid ID"
    print("✅ PASS: log_decision returns valid ID")


def test_get_recent_decisions():
    """Objective: Verify querying recent decisions works."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        test_db = f.name
    
    logger = AgentDecisionLogger(db_path=test_db)
    
    # Log multiple decisions
    logger.log_decision('parameter_tuning', 'Test 1', applied=True)
    logger.log_decision('strategy_enable', 'Test 2', applied=False)
    logger.log_decision('hypothesis_generated', 'Test 3', applied=False)
    
    # Query all
    recent = logger.get_recent_decisions(limit=10)
    assert len(recent) == 3, f"Should have 3, got {len(recent)}"
    
    # Query by type
    param_tuning = logger.get_recent_decisions(limit=10, decision_type='parameter_tuning')
    assert len(param_tuning) == 1, "Should have 1 parameter_tuning"
    
    print("✅ PASS: get_recent_decisions filters correctly")


def test_decisions_count_24h():
    """Objective: Verify 24h count works."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        test_db = f.name
    
    logger = AgentDecisionLogger(db_path=test_db)
    
    logger.log_decision('parameter_tuning', 'Test', applied=True)
    
    count = logger.get_decisions_count_last_24h()
    assert count == 1, f"Should be 1, got {count}"
    
    print("✅ PASS: get_decisions_count_last_24h works")


def test_get_last_applied():
    """Objective: Verify getting last applied decision works."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        test_db = f.name
    
    logger = AgentDecisionLogger(db_path=test_db)
    
    # Log some decisions
    logger.log_decision('parameter_tuning', 'Not applied', applied=False)
    logger.log_decision('parameter_tuning', 'Applied', applied=True)
    
    last = logger.get_last_applied_decision()
    assert last is not None, "Should find applied decision"
    assert last['applied'] == 1, "Should be marked applied"
    assert 'Applied' in last['rationale'], "Should be the applied one"
    
    print("✅ PASS: get_last_applied_decision works")


def test_invalid_decision_type():
    """Objective: Verify invalid types are rejected."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        test_db = f.name
    
    logger = AgentDecisionLogger(db_path=test_db)
    
    try:
        logger.log_decision('invalid_type', 'Test')
        assert False, "Should have raised ValueError"
    except ValueError:
        pass  # Expected
    
    print("✅ PASS: Invalid decision_type rejected")


def cleanup(test_db):
    """Remove test DB."""
    if os.path.exists(test_db):
        os.remove(test_db)


if __name__ == "__main__":
    print("=" * 50)
    print("Running AgentDecisionLogger Tests")
    print("=" * 50)
    
    test_log_decision()
    test_get_recent_decisions()
    test_decisions_count_24h()
    test_get_last_applied()
    test_invalid_decision_type()
    
    print("=" * 50)
    print("✅ ALL TESTS PASSED")
    print("=" * 50)
