#!/usr/bin/env python3
"""
Agent Decision Logger for K-ATA Adaptive Agent.

Per AGENT_LOGIC.md Section 5.2:
- Logs all agent decisions to SQLite
- Provides query interface for audit trail
- Used by agent loop for decision tracking
"""

import sqlite3
import json
import os
from datetime import datetime
from typing import Optional, List, Dict, Any
from dataclasses import dataclass


# Decision types per spec
DECISION_TYPES = [
    'parameter_tuning',
    'strategy_enable', 
    'strategy_disable',
    'circuit_breaker',
    'rollback',
    'hypothesis_generated'
]


class AgentDecisionLogger:
    """
    Manages agent decision logging to SQLite.
    Stateless - reads/writes from DB on each operation.
    """
    
    def __init__(self, db_path: str = "data/kalshi.db"):
        self.db_path = db_path
        self._ensure_table()
    
    def _ensure_table(self) -> None:
        """Create agent_decisions table if not exists."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS agent_decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    decided_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    decision_type TEXT NOT NULL CHECK(decision_type IN (
                        'parameter_tuning',
                        'strategy_enable',
                        'strategy_disable', 
                        'circuit_breaker',
                        'rollback',
                        'hypothesis_generated'
                    )),
                    parameters_modified JSON,
                    rationale TEXT,
                    hypothesis_tested TEXT,
                    p_value REAL,
                    effect_size REAL,
                    metrics_before JSON,
                    metrics_after JSON,
                    applied BOOLEAN DEFAULT 0,
                    source TEXT DEFAULT 'agent'
                )
            ''')
            conn.commit()
    
    def log_decision(
        self,
        decision_type: str,
        rationale: str,
        parameters_modified: Optional[Dict[str, Any]] = None,
        hypothesis_tested: Optional[str] = None,
        p_value: Optional[float] = None,
        effect_size: Optional[float] = None,
        metrics_before: Optional[Dict[str, Any]] = None,
        metrics_after: Optional[Dict[str, Any]] = None,
        applied: bool = False,
        source: str = "agent"
    ) -> int:
        """
        Log a decision to the database.
        
        Returns:
            Row ID of inserted decision
        """
        if decision_type not in DECISION_TYPES:
            raise ValueError(f"Invalid decision_type: {decision_type}")
        
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute('''
                INSERT INTO agent_decisions (
                    decision_type,
                    parameters_modified,
                    rationale,
                    hypothesis_tested,
                    p_value,
                    effect_size,
                    metrics_before,
                    metrics_after,
                    applied,
                    source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                decision_type,
                json.dumps(parameters_modified) if parameters_modified else None,
                rationale,
                hypothesis_tested,
                p_value,
                effect_size,
                json.dumps(metrics_before) if metrics_before else None,
                json.dumps(metrics_after) if metrics_after else None,
                applied,
                source
            ))
            conn.commit()
            return cur.lastrowid
    
    def get_recent_decisions(
        self, 
        limit: int = 10, 
        decision_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get recent decisions, optionally filtered by type."""
        with sqlite3.connect(self.db_path) as conn:
            if decision_type:
                cur = conn.execute('''
                    SELECT * FROM agent_decisions 
                    WHERE decision_type = ?
                    ORDER BY decided_at DESC 
                    LIMIT ?
                ''', (decision_type, limit))
            else:
                cur = conn.execute('''
                    SELECT * FROM agent_decisions 
                    ORDER BY decided_at DESC 
                    LIMIT ?
                ''', (limit,))
            
            rows = cur.fetchall()
            cols = [desc[0] for desc in cur.description]
            return [dict(zip(cols, row)) for row in rows]
    
    def get_decisions_count_last_24h(self) -> int:
        """Count decisions in last 24 hours (for rate limiting)."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute('''
                SELECT COUNT(*) FROM agent_decisions
                WHERE decided_at > datetime('now', '-1 day')
            ''')
            return cur.fetchone()[0]
    
    def get_last_applied_decision(self) -> Optional[Dict[str, Any]]:
        """Get most recent applied decision for rollback safety."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute('''
                SELECT * FROM agent_decisions 
                WHERE applied = 1 
                ORDER BY decided_at DESC 
                LIMIT 1
            ''')
            row = cur.fetchone()
            if row:
                cols = [desc[0] for desc in cur.description]
                return dict(zip(cols, row))
            return None


if __name__ == "__main__":
    # Quick test
    import tempfile
    
    # Use temp DB for testing
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        test_db = f.name
    
    logger = AgentDecisionLogger(db_path=test_db)
    
    # Test logging
    decision_id = logger.log_decision(
        decision_type='parameter_tuning',
        rationale='Test: Kelly adjusted due to Sharpe > 2.0',
        parameters_modified={'kellyFraction': 0.65},
        hypothesis_tested='kelly_adjust_high_sharpe',
        p_value=0.03,
        effect_size=0.15,
        applied=True
    )
    print(f"Logged decision ID: {decision_id}")
    
    # Test querying
    recent = logger.get_recent_decisions(limit=5)
    print(f"Recent decisions: {len(recent)}")
    print(f"  First: {recent[0]['decision_type']} - {recent[0]['rationale'][:40]}...")
    
    # Test 24h count
    count = logger.get_decisions_count_last_24h()
    print(f"Decisions last 24h: {count}")
    
    # Cleanup
    os.remove(test_db)
    
    print("✅ AgentDecisionLogger test passed")
