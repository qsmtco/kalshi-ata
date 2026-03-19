#!/usr/bin/env python3
"""
Position Manager for K-ATA - Tracks position lifecycle including max hold time.

Phase 2 of Improvement Plan:
- Track position entry time
- Detect when positions exceed max hold time (10 days)
- Persist positions to SQLite for crash recovery
"""

import os
import sqlite3
import json
from datetime import datetime, timedelta
from typing import Dict, Optional, List, Any
from dataclasses import dataclass, asdict


@dataclass
class Position:
    """Single position record."""
    event_id: str
    entry_time: datetime
    quantity: int
    entry_price: float
    strategy: str
    side: str  # 'buy' or 'sell'


class PositionManager:
    """
    Manages position lifecycle for max hold time enforcement.
    """
    
    MAX_HOLD_DAYS = 10
    
    def __init__(self, db_path: str = "data/kalshi.db"):
        self.db_path = db_path
        self.positions: Dict[str, Position] = {}
        self._ensure_table()
        self._load_positions()
    
    def _ensure_table(self):
        """Create positions table if not exists."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS positions (
                    event_id TEXT PRIMARY KEY,
                    entry_time TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    entry_price REAL NOT NULL,
                    strategy TEXT,
                    side TEXT
                )
            ''')
            conn.commit()
    
    def _load_positions(self):
        """Load positions from database on startup."""
        if not os.path.exists(self.db_path):
            return
        
        try:
            with sqlite3.connect(self.db_path) as conn:
                cur = conn.execute('SELECT * FROM positions')
                rows = cur.fetchall()
                
                for row in rows:
                    event_id, entry_time_str, quantity, entry_price, strategy, side = row
                    self.positions[event_id] = Position(
                        event_id=event_id,
                        entry_time=datetime.fromisoformat(entry_time_str),
                        quantity=quantity,
                        entry_price=entry_price,
                        strategy=strategy or 'unknown',
                        side=side or 'buy'
                    )
        except sqlite3.OperationalError:
            pass  # Table doesn't exist yet
    
    def open_position(self, event_id: str, quantity: int, entry_price: float, 
                     strategy: str, side: str = 'buy') -> None:
        """Record a new position with current timestamp."""
        position = Position(
            event_id=event_id,
            entry_time=datetime.now(),
            quantity=quantity,
            entry_price=entry_price,
            strategy=strategy,
            side=side
        )
        
        self.positions[event_id] = position
        self._save_position(position)
    
    def close_position(self, event_id: str) -> bool:
        """Remove position from tracking. Returns True if position existed."""
        if event_id in self.positions:
            del self.positions[event_id]
            self._delete_position(event_id)
            return True
        return False
    
    def get_position(self, event_id: str) -> Optional[Position]:
        """Get position by event ID."""
        return self.positions.get(event_id)
    
    def get_all_positions(self) -> Dict[str, Position]:
        """Get all open positions."""
        return self.positions.copy()
    
    def should_close(self, event_id: str) -> bool:
        """
        Check if position exceeds max hold time.
        
        Returns True if position should be closed.
        """
        if event_id not in self.positions:
            return False
        
        position = self.positions[event_id]
        days_held = (datetime.now() - position.entry_time).days
        
        return days_held >= self.MAX_HOLD_DAYS
    
    def get_days_held(self, event_id: str) -> Optional[int]:
        """Get days held for a position. Returns None if position not found."""
        if event_id not in self.positions:
            return None
        
        position = self.positions[event_id]
        return (datetime.now() - position.entry_time).days
    
    def get_positions_to_close(self) -> List[str]:
        """Get list of all positions that should be closed."""
        return [
            event_id for event_id in self.positions.keys()
            if self.should_close(event_id)
        ]
    
    def _save_position(self, position: Position):
        """Persist position to database."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                INSERT OR REPLACE INTO positions 
                (event_id, entry_time, quantity, entry_price, strategy, side)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                position.event_id,
                position.entry_time.isoformat(),
                position.quantity,
                position.entry_price,
                position.strategy,
                position.side
            ))
            conn.commit()
    
    def _delete_position(self, event_id: str):
        """Remove position from database."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('DELETE FROM positions WHERE event_id = ?', (event_id,))
            conn.commit()
    
    def get_status(self) -> Dict[str, Any]:
        """Get status of all positions."""
        positions_list = []
        
        for event_id, pos in self.positions.items():
            days = self.get_days_held(event_id)
            positions_list.append({
                'event_id': event_id,
                'days_held': days,
                'should_close': self.should_close(event_id),
                'quantity': pos.quantity,
                'entry_price': pos.entry_price,
                'strategy': pos.strategy
            })
        
        return {
            'total_positions': len(self.positions),
            'positions': positions_list,
            'to_close': len(self.get_positions_to_close())
        }


if __name__ == "__main__":
    import tempfile
    
    # Test with temp DB
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        test_db = f.name
    
    pm = PositionManager(db_path=test_db)
    
    # Test opening position
    pm.open_position('EVENT1', 10, 0.55, 'news_sentiment', 'buy')
    print(f"Opened position: {pm.get_status()}")
    
    # Test should_close (new position shouldn't close)
    result = pm.should_close('EVENT1')
    print(f"Should close new position: {result}")
    assert result == False, "New position should not close"
    
    # Test get_days_held
    days = pm.get_days_held('EVENT1')
    print(f"Days held: {days}")
    assert days == 0, "New position should be 0 days"
    
    # Test closing
    pm.close_position('EVENT1')
    assert 'EVENT1' not in pm.positions, "Position should be removed"
    print("✅ Basic position tracking works")
    
    # Test with old position (simulate)
    from datetime import timedelta
    old_pos = Position(
        event_id='OLD_EVENT',
        entry_time=datetime.now() - timedelta(days=11),
        quantity=5,
        entry_price=0.60,
        strategy='volatility',
        side='buy'
    )
    pm.positions['OLD_EVENT'] = old_pos
    
    should_close = pm.should_close('OLD_EVENT')
    print(f"Should close 11-day old position: {should_close}")
    assert should_close == True, "11-day position should close"
    
    print("✅ Max hold time detection works")
    
    # Cleanup
    os.remove(test_db)
    
    print("\n✅ ALL PositionManager tests passed")
