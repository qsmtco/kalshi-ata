#!/usr/bin/env python3
"""
Circuit Breaker State Module for K-ATA Safety Systems.

Tracks trading state machine: ACTIVE <-> PAUSED_ERROR <-> PAUSED_DRAWDOWN
Enforces safety-first trading rules per SAFETY_GUARDRAILS.md Section 3.

State transitions:
- ACTIVE -> PAUSED_DRAWDOWN: drawdown > 10% OR 24h loss > 5%
- ACTIVE -> PAUSED_ERROR: API error rate > 20% for > 1 minute
- PAUSED_ERROR -> ACTIVE: after 5 min if error rate < 5% for 2 min
- PAUSED_DRAWDOWN -> ACTIVE: MANUAL RESUME ONLY
"""

from enum import Enum
from datetime import datetime, timedelta
from typing import Optional
import json
import os

class CircuitState(Enum):
    """Valid circuit breaker states per spec."""
    ACTIVE = "ACTIVE"
    PAUSED_ERROR = "PAUSED_ERROR"
    PAUSED_DRAWDOWN = "PAUSED_DRAWDOWN"
    HALTED = "HALTED"

class CircuitBreaker:
    """
    Manages circuit breaker state machine.
    Persists state to JSON file for crash recovery.
    """
    
    # Thresholds from SAFETY_GUARDRAILS.md Section 3.1
    DRAWDOWN_THRESHOLD = 0.10  # 10%
    DAILY_LOSS_THRESHOLD = 0.05  # 5%
    API_ERROR_THRESHOLD = 0.20  # 20%
    ERROR_RESET_THRESHOLD = 0.05  # 5%
    
    def __init__(self, state_file: str = "data/circuit_breaker.json"):
        self.state_file = state_file
        self.state = CircuitState.ACTIVE
        self.state_since = datetime.now()
        self.pause_reason: Optional[str] = None
        self._load_state()
    
    def _load_state(self) -> None:
        """Load persisted state from file if exists."""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r') as f:
                    data = json.load(f)
                    self.state = CircuitState(data.get('state', 'ACTIVE'))
                    self.state_since = datetime.fromisoformat(data.get('state_since', datetime.now().isoformat()))
                    self.pause_reason = data.get('reason')
            except (json.JSONDecodeError, ValueError) as e:
                # Corrupted state file - reset to ACTIVE
                print(f"WARN: Corrupted circuit breaker state file: {e}")
                self._reset_to_active()
    
    def _save_state(self) -> None:
        """Persist state to file for crash recovery."""
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
        with open(self.state_file, 'w') as f:
            json.dump({
                'state': self.state.value,
                'state_since': self.state_since.isoformat(),
                'reason': self.pause_reason
            }, f)
    
    def _reset_to_active(self) -> None:
        """Reset to ACTIVE state."""
        self.state = CircuitState.ACTIVE
        self.state_since = datetime.now()
        self.pause_reason = None
        self._save_state()
    
    def can_trade(self) -> bool:
        """Check if trading is currently allowed."""
        return self.state == CircuitState.ACTIVE
    
    def get_status(self) -> dict:
        """Return current circuit breaker status."""
        return {
            'state': self.state.value,
            'state_since': self.state_since.isoformat(),
            'reason': self.pause_reason,
            'can_trade': self.can_trade()
        }
    
    def pause_for_drawdown(self, reason: str) -> None:
        """Trigger PAUSED_DRAWDOWN state (manual resume required)."""
        if self.state != CircuitState.PAUSED_DRAWDOWN:
            self.state = CircuitState.PAUSED_DRAWDOWN
            self.state_since = datetime.now()
            self.pause_reason = reason
            self._save_state()
            print(f"CIRCUIT BREAKER: Paused for drawdown - {reason}")
    
    def pause_for_error(self, reason: str) -> None:
        """Trigger PAUSED_ERROR state (auto-reset possible)."""
        if self.state == CircuitState.ACTIVE:
            self.state = CircuitState.PAUSED_ERROR
            self.state_since = datetime.now()
            self.pause_reason = reason
            self._save_state()
            print(f"CIRCUIT BREAKER: Paused for error - {reason}")
    
    def resume(self) -> bool:
        """
        Attempt to resume trading.
        Only allowed if currently PAUSED_ERROR (auto-resume).
        PAUSED_DRAWDOWN requires manual intervention.
        """
        if self.state == CircuitState.PAUSED_DRAWDOWN:
            print("CIRCUIT BREAKER: Cannot auto-resume from PAUSED_DRAWDOWN - manual intervention required")
            return False
        
        if self.state == CircuitState.PAUSED_ERROR:
            self._reset_to_active()
            print("CIRCUIT BREAKER: Resumed to ACTIVE")
            return True
        
        return False  # Already ACTIVE
    
    def check_auto_reset(self, api_error_rate: float) -> bool:
        """
        Check if PAUSED_ERROR should auto-reset.
        Returns True if reset occurred.
        """
        if self.state != CircuitState.PAUSED_ERROR:
            return False
        
        # Must be paused for at least 5 minutes
        elapsed = (datetime.now() - self.state_since).total_seconds()
        if elapsed < 300:  # 5 minutes
            return False
        
        # Error rate must be below threshold
        if api_error_rate < self.ERROR_RESET_THRESHOLD:
            self._reset_to_active()
            print(f"CIRCUIT BREAKER: Auto-resumed after error rate dropped to {api_error_rate:.1%}")
            return True
        
        return False


if __name__ == "__main__":
    # Quick test of state machine
    cb = CircuitBreaker()
    print("Initial:", cb.get_status())
    
    # Test transition
    cb.pause_for_drawdown("Test drawdown > 10%")
    print("After pause:", cb.get_status())
    print("Can trade?", cb.can_trade())
    
    # Test resume (should fail for drawdown)
    result = cb.resume()
    print("Resume result:", result)
    
    # Test error pause + auto-resume
    cb2 = CircuitBreaker()
    cb2.pause_for_error("Test API error > 20%")
    print("\nError pause:", cb2.get_status())
