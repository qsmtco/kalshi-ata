#!/usr/bin/env python3
"""
Paper Trader for K-ATA - Simulates trades without real orders.

Phase 3 of Improvement Plan:
- Logs simulated trades to database
- Tracks settlement and P&L
- No real API calls
"""

import os
import sqlite3
from datetime import datetime
from typing import Dict, Optional, List, Any
from dataclasses import dataclass


@dataclass
class PaperTrade:
    """Simulated trade record."""
    id: Optional[int] = None
    event_id: str = ""
    action: str = ""  # 'buy' or 'sell'
    quantity: int = 0
    entry_price: float = 0.0
    entry_time: datetime = None
    strategy: str = ""
    status: str = "open"  # 'open' or 'closed'
    exit_price: Optional[float] = None
    exit_time: Optional[datetime] = None
    pnl: Optional[float] = None


class PaperTrader:
    """
    Simulates trade execution without placing real orders.
    Logs trades to database for later analysis.
    """
    
    def __init__(self, db_path: str = "data/kalshi.db"):
        # Convert relative path to absolute based on script location
        if not os.path.isabs(db_path):
            script_dir = os.path.dirname(os.path.abspath(__file__))
            project_root = os.path.dirname(script_dir)
            db_path = os.path.join(project_root, db_path)
        self.db_path = db_path
        self._ensure_table()
    
    def _ensure_table(self):
        """Create paper_trades table if not exists."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS paper_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    entry_price REAL NOT NULL,
                    entry_time TEXT NOT NULL,
                    strategy TEXT,
                    status TEXT DEFAULT 'open',
                    exit_price REAL,
                    exit_time TEXT,
                    pnl REAL
                )
            ''')
            conn.commit()
    
    def simulate_trade(self, event_id: str, action: str, quantity: int,
                      entry_price: float, strategy: str) -> int:
        """
        Log a simulated trade.
        
        Returns:
            int: Trade ID
        """
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute('''
                INSERT INTO paper_trades 
                (event_id, action, quantity, entry_price, entry_time, strategy, status)
                VALUES (?, ?, ?, ?, ?, ?, 'open')
            ''', (
                event_id, action, quantity, entry_price,
                datetime.now().isoformat(), strategy
            ))
            conn.commit()
            return cur.lastrowid
    
    def settle_trade(self, trade_id: int, exit_price: float) -> float:
        """
        Mark trade as closed with exit price.
        
        Returns:
            float: P&L
        """
        with sqlite3.connect(self.db_path) as conn:
            # Get entry details
            cur = conn.execute(
                'SELECT action, quantity, entry_price FROM paper_trades WHERE id = ?',
                (trade_id,)
            )
            row = cur.fetchone()
            
            if not row:
                return 0.0
            
            action, quantity, entry_price = row
            
            # Calculate P&L
            if action == 'buy':
                pnl = (exit_price - entry_price) * quantity
            else:  # sell
                pnl = (entry_price - exit_price) * quantity
            
            # Update record
            conn.execute('''
                UPDATE paper_trades 
                SET status = 'closed', exit_price = ?, exit_time = ?, pnl = ?
                WHERE id = ?
            ''', (exit_price, datetime.now().isoformat(), pnl, trade_id))
            conn.commit()
            
            return pnl
    
    def get_open_trades(self) -> List[Dict[str, Any]]:
        """Get all open paper trades."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "SELECT * FROM paper_trades WHERE status = 'open'"
            )
            rows = cur.fetchall()
            cols = [desc[0] for desc in cur.description]
            return [dict(zip(cols, row)) for row in rows]
    
    def get_all_trades(self) -> List[Dict[str, Any]]:
        """Get all paper trades."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute("SELECT * FROM paper_trades ORDER BY entry_time DESC")
            rows = cur.fetchall()
            cols = [desc[0] for desc in cur.description]
            return [dict(zip(cols, row)) for row in rows]
    
    def get_performance_summary(self) -> Dict[str, Any]:
        """Get performance summary for paper trades."""
        with sqlite3.connect(self.db_path) as conn:
            # Total trades
            cur = conn.execute("SELECT COUNT(*) FROM paper_trades")
            total = cur.fetchone()[0]
            
            # Closed trades
            cur = conn.execute(
                "SELECT COUNT(*) FROM paper_trades WHERE status = 'closed'"
            )
            closed = cur.fetchone()[0]
            
            # Open trades
            cur = conn.execute(
                "SELECT COUNT(*) FROM paper_trades WHERE status = 'open'"
            )
            open_count = cur.fetchone()[0]
            
            # Total P&L
            cur = conn.execute("SELECT SUM(pnl) FROM paper_trades WHERE pnl IS NOT NULL")
            total_pnl = cur.fetchone()[0] or 0.0
            
            # Win rate
            cur = conn.execute(
                "SELECT COUNT(*) FROM paper_trades WHERE status = 'closed' AND pnl > 0"
            )
            wins = cur.fetchone()[0]
            
            win_rate = wins / closed if closed > 0 else 0.0
            
            return {
                'total_trades': total,
                'closed_trades': closed,
                'open_trades': open_count,
                'total_pnl': total_pnl,
                'win_rate': win_rate
            }


if __name__ == "__main__":
    import tempfile
    
    # Test with temp DB
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        test_db = f.name
    
    pt = PaperTrader(db_path=test_db)
    
    # Test simulate trade
    trade_id = pt.simulate_trade('EVENT1', 'buy', 10, 0.55, 'news_sentiment')
    print(f"Simulated trade ID: {trade_id}")
    
    # Test get open trades
    open_trades = pt.get_open_trades()
    print(f"Open trades: {len(open_trades)}")
    assert len(open_trades) == 1
    
    # Test settle trade
    pnl = pt.settle_trade(trade_id, 0.60)
    print(f"Settled trade P&L: ${pnl}")
    assert abs(pnl - 0.50) < 0.01  # (0.60 - 0.55) * 10 = 0.50
    
    # Test performance summary
    summary = pt.get_performance_summary()
    print(f"Summary: {summary}")
    assert abs(summary['total_pnl'] - 0.50) < 0.01
    assert summary['closed_trades'] == 1
    assert summary['win_rate'] == 1.0
    
    print("✅ PaperTrader basic tests passed")
    
    # Cleanup
    os.remove(test_db)
    
    print("✅ ALL PaperTrader tests passed")
