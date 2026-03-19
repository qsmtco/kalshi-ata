#!/usr/bin/env python3
"""
Market Maker for K-ATA - Captures bid-ask spread.

Phase 5 of Improvement Plan:
- Places limit orders on both sides of spread
- Captures spread without directional exposure
- Risk-managed position limits
"""

import os
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass


@dataclass
class MarketMakingPosition:
    """Active market making position."""
    market_id: str
    buy_order_id: Optional[str]
    sell_order_id: Optional[str]
    quantity: int
    entry_bid: float  # Our buy price
    entry_ask: float  # Our sell price
    entry_time: datetime


class MarketMaker:
    """
    Market making strategy for Kalshi prediction markets.
    Places symmetrical limit orders around mid-price to capture spread.
    """
    
    # Configurable parameters
    MAX_POSITION_PCT = 0.40  # 40% of capital max
    MIN_SPREAD = 0.02  # 2% minimum spread to attempt
    MIN_VOLUME = 1000  # Minimum market volume
    MAX_ORDERS_PER_MARKET = 3  # Limit orders per market
    
    def __init__(self, api, db_path: str = "data/kalshi.db"):
        self.api = api
        self.db_path = db_path
        self.positions: Dict[str, MarketMakingPosition] = {}
        self._ensure_table()
    
    def _ensure_table(self):
        """Create market_making table."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS market_making (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    market_id TEXT NOT NULL,
                    buy_order_id TEXT,
                    sell_order_id TEXT,
                    quantity INTEGER NOT NULL,
                    entry_bid REAL NOT NULL,
                    entry_ask REAL NOT NULL,
                    entry_time TEXT NOT NULL,
                    status TEXT DEFAULT 'open'
                )
            ''')
            conn.commit()
    
    def analyze_market(self, market: Dict[str, Any]) -> Optional[Dict[str, float]]:
        """
        Analyze a market for market making opportunity.
        
        Returns dict with spread info or None if not suitable.
        """
        # Check volume
        volume = market.get('volume', 0)
        if volume < self.MIN_VOLUME:
            return None
        
        yes_price = market.get('yes_price', 0.5)
        no_price = market.get('no_price', 0.5)
        
        # Calculate spread
        spread = abs(yes_price - no_price)
        
        # Skip if spread too narrow
        if spread < self.MIN_SPREAD:
            return None
        
        midpoint = (yes_price + no_price) / 2
        
        # Our prices (slightly inside the spread)
        our_bid = yes_price - (spread * 0.1)  # Buy at 90% of yes
        our_ask = no_price + (spread * 0.1)   # Sell at 110% of no
        
        return {
            'market_id': market['ticker'],
            'midpoint': midpoint,
            'spread': spread,
            'our_bid': our_bid,
            'our_ask': our_ask,
            'volume': volume
        }
    
    def should_market_make(self, market: Dict[str, Any]) -> bool:
        """Check if market is suitable for market making."""
        # Check volume
        if market.get('volume', 0) < self.MIN_VOLUME:
            return False
        
        # Check spread
        spread = abs(market.get('yes_price', 0) - market.get('no_price', 0))
        if spread < self.MIN_SPREAD:
            return False
        
        # Check if we already have position
        if market['ticker'] in self.positions:
            return False
        
        return True
    
    def calculate_quantity(self, bankroll: float) -> int:
        """Calculate position size for market making."""
        max_position = bankroll * self.MAX_POSITION_PCT
        
        # Assume average price of 0.50
        quantity = int(max_position / 0.50)
        
        return max(1, quantity)
    
    def open_market_making_position(
        self,
        market_id: str,
        quantity: int,
        bid_price: float,
        ask_price: float
    ) -> bool:
        """
        Open market making position.
        
        Places both buy and sell orders.
        Returns True if both orders placed successfully.
        """
        try:
            # Place buy order (yes side)
            buy_result = self.api.create_order(
                market_id=market_id,
                side='yes',
                order_type='limit',
                price=bid_price,
                quantity=quantity
            )
            
            # Place sell order (no side)
            sell_result = self.api.create_order(
                market_id=market_id,
                side='no',
                order_type='limit',
                price=ask_price,
                quantity=quantity
            )
            
            # Record position
            position = MarketMakingPosition(
                market_id=market_id,
                buy_order_id=buy_result.get('order_id'),
                sell_order_id=sell_result.get('order_id'),
                quantity=quantity,
                entry_bid=bid_price,
                entry_ask=ask_price,
                entry_time=datetime.now()
            )
            
            self.positions[market_id] = position
            self._save_position(position)
            
            return True
            
        except Exception as e:
            print(f"Failed to open market making position: {e}")
            return False
    
    def close_market_making_position(self, market_id: str) -> bool:
        """
        Close market making position.
        
        Cancels open orders and records closure.
        """
        if market_id not in self.positions:
            return False
        
        position = self.positions[market_id]
        
        try:
            # Cancel buy order if exists
            if position.buy_order_id:
                self.api.cancel_order(position.buy_order_id)
            
            # Cancel sell order if exists
            if position.sell_order_id:
                self.api.cancel_order(position.sell_order_id)
            
            # Remove from tracking
            del self.positions[market_id]
            self._delete_position(market_id)
            
            return True
            
        except Exception as e:
            print(f"Failed to close market making position: {e}")
            return False
    
    def get_spread_capture(self, market_id: str, current_prices: Dict[str, float]) -> float:
        """
        Calculate potential spread capture.
        
        Returns estimated profit from spread.
        """
        if market_id not in self.positions:
            return 0.0
        
        position = self.positions[market_id]
        
        # Get current market prices
        yes_price = current_prices.get(f"{market_id}_yes", position.entry_bid)
        no_price = current_prices.get(f"{market_id}_no", position.entry_ask)
        
        # Calculate spread capture
        # If our orders filled, we bought at bid, sold at ask
        # Profit = (ask - bid) * quantity
        spread = position.entry_ask - position.entry_bid
        spread_capture = spread * position.quantity
        
        return spread_capture
    
    def _save_position(self, position: MarketMakingPosition):
        """Persist position to database."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                INSERT INTO market_making 
                (market_id, buy_order_id, sell_order_id, quantity, 
                 entry_bid, entry_ask, entry_time, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'open')
            ''', (
                position.market_id,
                position.buy_order_id,
                position.sell_order_id,
                position.quantity,
                position.entry_bid,
                position.entry_ask,
                position.entry_time.isoformat()
            ))
            conn.commit()
    
    def _delete_position(self, market_id: str):
        """Remove position from database."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                'DELETE FROM market_making WHERE market_id = ?',
                (market_id,)
            )
            conn.commit()
    
    def get_status(self) -> Dict[str, Any]:
        """Get status of all market making positions."""
        return {
            'total_positions': len(self.positions),
            'positions': [
                {
                    'market_id': p.market_id,
                    'quantity': p.quantity,
                    'spread': p.entry_ask - p.entry_bid,
                    'entry_time': p.entry_time.isoformat()
                }
                for p in self.positions.values()
            ]
        }


if __name__ == "__main__":
    print("MarketMaker class loaded")
    print("Requires API integration for live trading")
