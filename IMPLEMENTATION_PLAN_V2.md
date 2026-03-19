# K-ATA Improvement Implementation Plan

**Project:** Kalshi Adaptive Trading Agent (K-ATA)  
**Version:** 1.0  
**Date:** 2026-03-19

---

## Overview

This document outlines the technical implementation plan for improving the K-ATA trading bot based on research into successful Kalshi trading bots (ryanfrigo, yllvar) and official documentation.

**Five Improvements Identified:**

1. Daily Loss Limit (15%) — Stop trading when daily loss exceeds threshold
2. Max Hold Time (10 days) — Exit positions after max duration
3. Paper Trading Mode — Test strategies without real money
4. Official SDK Integration — Replace custom API with `kalshi-python`
5. Market Making Strategy — Capture bid-ask spread

---

## Phase 1: Daily Loss Limit

**Priority:** HIGH  
**Estimated Effort:** 2-4 hours  
**Status:** NOT STARTED

### Objective

Stop all trading when daily loss exceeds 15% of bankroll. This prevents catastrophic losses during adverse market conditions.

### Background (from ryanfrigo)

> "Hard daily loss limit — stops trading at 15% drawdown"

### Implementation Steps

#### Step 1.1: Add daily loss tracking to performance_analytics.py

```python
# In performance_analytics.py, add:

def get_daily_pnl(self) -> float:
    """Calculate P&L for current trading day (UTC)."""
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    
    today_trades = [
        t for t in self.trades 
        if datetime.fromisoformat(t['created_at']) >= today_start
    ]
    
    return sum(t.get('pnl', 0) for t in today_trades)

def get_daily_loss_percentage(self, bankroll: float) -> float:
    """Calculate daily loss as percentage of bankroll."""
    daily_pnl = self.get_daily_pnl()
    return abs(daily_pnl) / bankroll if daily_pnl < 0 else 0
```

**Verification:** Run unit test with mock trades, verify loss calculation.

#### Step 1.2: Add check in trader.py before each trade

```python
# In trader.py, before execute_trade():

def check_daily_loss_limit(self) -> bool:
    """Check if daily loss exceeds limit. Returns True if should stop trading."""
    daily_loss_pct = self.performance_analytics.get_daily_loss_percentage(self.bankroll)
    
    DAILY_LOSS_LIMIT = 0.15  # 15%
    
    if daily_loss_pct > DAILY_LOSS_LIMIT:
        self.logger.warning(f"Daily loss {daily_loss_pct:.1%} exceeds {DAILY_LOSS_LIMIT:.0%} limit - halting trading")
        self.notifier.send_error_notification(
            f"🚨 DAILY LOSS LIMIT: {daily_loss_pct:.1%} exceeded. Trading halted."
        )
        return False
    return True
```

**Verification:** Test with simulated loss trades, verify trading stops at 15%.

#### Step 1.3: Integrate into trading loop (main.py)

```python
# In main.py trading loop:

while True:
    # Check daily loss limit BEFORE strategy execution
    if not trader.check_daily_loss_limit():
        logger.warning("Trading halted due to daily loss limit")
        break
    
    trader.run_trading_strategy()
    time.sleep(TRADE_INTERVAL_SECONDS)
```

**Verification:** Run full loop with simulated losses, verify halt at threshold.

#### Step 1.4: Add reset at market open

Daily loss should reset at UTC midnight. The `get_daily_pnl()` already filters by today's trades, so this is automatic.

**Verification:** Verify new day resets the calculation.

---

## Phase 2: Max Hold Time

**Priority:** HIGH  
**Estimated Effort:** 2-3 hours  
**Status:** NOT STARTED

### Objective

Exit positions after 10 calendar days maximum to avoid holding through event resolution uncertainty.

### Background (from ryanfrigo)

> "Time-based exits (10-day max hold)"

### Implementation Steps

#### Step 2.1: Add position tracking with timestamp

```python
# In trader.py or a new position_manager.py:

class PositionManager:
    """Manages position lifecycle including max hold time."""
    
    MAX_HOLD_DAYS = 10
    
    def __init__(self):
        self.positions = {}  # event_id -> {entry_time, quantity, entry_price}
    
    def open_position(self, event_id: str, quantity: int, price: float):
        """Record new position with entry time."""
        self.positions[event_id] = {
            'entry_time': datetime.now(),
            'quantity': quantity,
            'entry_price': price
        }
    
    def should_close(self, event_id: str) -> bool:
        """Check if position exceeds max hold time."""
        if event_id not in self.positions:
            return False
        
        position = self.positions[event_id]
        days_held = (datetime.now() - position['entry_time']).days
        
        return days_held >= self.MAX_HOLD_DAYS
    
    def close_position(self, event_id: str):
        """Remove position from tracking."""
        if event_id in self.positions:
            del self.positions[event_id]
```

**Verification:** Test with mock positions, verify 10-day detection.

#### Step 2.2: Add check in trading loop

```python
# In trader.py, add to run_trading_strategy() or a new check_positions() method:

def check_position_exits(self):
    """Check for positions that should be exited due to max hold time."""
    for event_id in list(self.current_positions.keys()):
        if self.position_manager.should_close(event_id):
            self.logger.info(f"Closing position {event_id} - max hold time reached")
            # Execute close logic
            self.close_position(event_id)
```

**Verification:** Test with aged positions, verify exits trigger.

#### Step 2.3: Persist positions to DB

```python
# In settings_manager.py or new position_manager.py, add table:

CREATE TABLE IF NOT EXISTS positions (
    event_id TEXT PRIMARY KEY,
    entry_time TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    entry_price REAL NOT NULL,
    strategy TEXT
);
```

**Verification:** Verify positions persist across restarts.

---

## Phase 3: Paper Trading Mode

**Priority:** HIGH  
**Estimated Effort:** 3-5 hours  
**Status:** NOT STARTED

### Objective

Allow the bot to simulate trades without placing real orders, logging outcomes for analysis.

### Background (from ryanfrigo)

> "Paper trading mode — simulate trades without real orders; track outcomes on settled markets"

### Implementation Steps

#### Step 3.1: Add paper trading flag to config

```python
# In config.py:

PAPER_TRADING = os.environ.get('PAPER_TRADING', 'true').lower() == 'true'
# Default to True for safety - must explicitly enable live trading
```

**Verification:** Verify default is paper trading.

#### Step 3.2: Create paper_trader.py module

```python
# In src/paper_trader.py:

class PaperTrader:
    """
    Simulates trade execution without placing real orders.
    Logs trades to database for later analysis.
    """
    
    def __init__(self, db_path: str = "data/kalshi.db"):
        self.db_path = db_path
        self._ensure_table()
    
    def _ensure_table(self):
        """Create paper_trades table."""
        import sqlite3
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS paper_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    entry_price REAL NOT NULL,
                    simulated_at TEXT NOT NULL,
                    strategy TEXT,
                    status TEXT DEFAULT 'open',
                    exit_price REAL,
                    exit_at TEXT,
                    pnl REAL
                )
            ''')
            conn.commit()
    
    def simulate_trade(self, event_id: str, action: str, quantity: int, 
                       price: float, strategy: str) -> int:
        """Log a simulated trade. Returns trade ID."""
        import sqlite3
        from datetime import datetime
        
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute('''
                INSERT INTO paper_trades 
                (event_id, action, quantity, entry_price, simulated_at, strategy)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (event_id, action, quantity, price, datetime.now().isoformat(), strategy))
            conn.commit()
            return cur.lastrowid
    
    def settle_trade(self, trade_id: int, exit_price: float):
        """Mark trade as closed with exit price and calculate P&L."""
        import sqlite3
        from datetime import datetime
        
        with sqlite3.connect(self.db_path) as conn:
            # Get entry details
            cur = conn.execute('SELECT * FROM paper_trades WHERE id = ?', (trade_id,))
            trade = cur.fetchone()
            
            if not trade:
                return
            
            # Calculate P&L
            action = trade[2]  # action column
            entry_price = trade[4]
            
            if action == 'buy':
                pnl = (exit_price - entry_price) * trade[3]  # (exit - entry) * quantity
            else:  # sell
                pnl = (entry_price - exit_price) * trade[3]
            
            # Update record
            conn.execute('''
                UPDATE paper_trades 
                SET status = 'closed', exit_price = ?, exit_at = ?, pnl = ?
                WHERE id = ?
            ''', (exit_price, datetime.now().isoformat(), pnl, trade_id))
            conn.commit()
```

**Verification:** Run with mock trades, verify logging and settlement.

#### Step 3.3: Modify trader.py to use PaperTrader

```python
# In trader.py:

def __init__(self, ...):
    ...
    # Add paper trading support
    from paper_trader import PaperTrader
    self.paper_trader = PaperTrader() if config.PAPER_TRADING else None

def execute_trade(self, trade_decision):
    # ... existing checks ...
    
    if self.paper_trading:
        # Log to paper trades instead of real API
        self.paper_trader.simulate_trade(
            event_id=event_id,
            action=action,
            quantity=quantity,
            price=price,
            strategy=strategy
        )
        self.logger.info(f"PAPER TRADE: {action} {quantity} {event_id} at ${price}")
        return
    
    # Real trading logic
    ...
```

**Verification:** Run in paper mode, verify trades logged but no API calls.

#### Step 3.4: Add paper trading commands to CLI

```python
# In bot_state.py, add:

elif command == 'paper':
    if config.PAPER_TRADING:
        response = "📄 Paper Trading Mode: ACTIVE\nTrades are simulated only."
    else:
        response = "💰 Paper Trading Mode: DISABLED\nReal trades enabled."

elif command == 'paper-toggle':
    config.PAPER_TRADING = not config.PAPER_TRADING
    response = f"Paper trading: {config.PAPER_TRADING}"
```

**Verification:** Test toggle via CLI.

---

## Phase 4: Official SDK Integration

**Priority:** MEDIUM  
**Estimated Effort:** 4-6 hours  
**Status:** NOT STARTED

### Objective

Replace custom kalshi_api.py with official `kalshi-python` SDK for better reliability and features.

### Background

- Official SDK: https://pypi.org/project/kalshi-python/
- Auto-generated from OpenAPI spec
- Actively maintained
- Supports all API endpoints

### Implementation Steps

#### Step 4.1: Install official SDK

```bash
pip install kalshi-python
```

**Verification:** Verify package installs and imports.

#### Step 4.2: Create new SDK wrapper

```python
# In src/kalshi_sdk.py:

import os
from kalshi_python import Configuration, KalshiClient

class KalshiSDK:
    """Official SDK wrapper for K-ATA."""
    
    def __init__(self):
        api_key_id = os.environ.get('KALSHI_API_KEY_ID')
        private_key_path = os.environ.get('KALSHI_PRIVATE_KEY_PATH', 'kalshi_private_key.pem')
        
        # Read private key
        with open(private_key_path, 'r') as f:
            private_key = f.read()
        
        config = Configuration(
            host="https://api.elections.kalshi.com/trade-api/v2"
        )
        config.api_key_id = api_key_id
        config.private_key_pem = private_key
        
        self.client = KalshiClient(config)
    
    def get_balance(self) -> dict:
        """Get account balance."""
        return self.client.get_balance()
    
    def get_markets(self, **kwargs) -> list:
        """Get market list."""
        return self.client.get_markets(**kwargs).markets
    
    def get_market(self, market_id: str) -> dict:
        """Get specific market."""
        return self.client.get_market(market_id=market_id)
    
    def create_order(self, market_id: str, side: str, order_type: str,
                    price: float, quantity: int) -> dict:
        """Place an order."""
        return self.client.create_order(
            market_id=market_id,
            side=side,
            order_type=order_type,
            price=int(price * 100),  # Convert to cents
            count=quantity
        )
    
    def cancel_order(self, order_id: str) -> dict:
        """Cancel an order."""
        return self.client.cancel_order(order_id=order_id)
    
    def get_positions(self) -> list:
        """Get open positions."""
        return self.client.get_positions().positions
    
    def get_orders(self) -> list:
        """Get open orders."""
        return self.client.get_orders().orders
```

**Verification:** Test with real API (or demo environment).

#### Step 4.3: Update config to require key files

```python
# In .env template:

# Kalshi API Configuration
KALSHI_API_KEY_ID=your_api_key_id_here
KALSHI_PRIVATE_KEY_PATH=./kalshi_private_key.pem
```

**Verification:** Verify config validation.

#### Step 4.4: Migrate existing functionality

Replace calls in:
- `kalshi_api.py` → use `KalshiSDK`
- `market_data_streamer.py` → use SDK methods
- `trader.py` → use SDK for orders

**Verification:** Run full trading cycle in paper mode.

---

## Phase 5: Market Making Strategy

**Priority:** MEDIUM  
**Estimated Effort:** 6-8 hours  
**Status:** NOT STARTED

### Objective

Add market making strategy that places limit orders on both sides of the spread to capture the bid-ask difference.

### Background (from ryanfrigo)

> "Market making (40%) — automated limit orders capturing bid-ask spread"

### Implementation Steps

#### Step 5.1: Create market_maker.py

```python
# In src/market_maker.py:

class MarketMaker:
    """
    Market making strategy for Kalshi prediction markets.
    Places symmetrical limit orders around mid-price to capture spread.
    """
    
    def __init__(self, api, config):
        self.api = api
        self.config = config
        self.max_position_pct = 0.40  # 40% of capital
    
    def analyze_market(self, market: dict) -> dict:
        """
        Analyze a market for market making opportunity.
        
        Returns:
            dict with spread_info, midpoint, bid, ask
        """
        yes_price = market.get('yes_price', 0.5)
        no_price = market.get('no_price', 0.5)
        
        # Midpoint
        midpoint = (yes_price + no_price) / 2
        
        # Spread
        spread = abs(yes_price - no_price)
        
        # Theoretical bid/ask around midpoint
        spread_width = spread / 2
        
        return {
            'market_id': market['ticker'],
            'midpoint': midpoint,
            'bid': midpoint - spread_width,
            'ask': midpoint + spread_width,
            'spread': spread,
            'liquidity': market.get('volume', 0)
        }
    
    def should_make_market(self, market: dict) -> bool:
        """Check if market has sufficient liquidity for market making."""
        volume = market.get('volume', 0)
        MIN_VOLUME = 1000  # Minimum volume threshold
        
        return volume >= MIN_VOLUME
    
    def calculate_position_size(self, bankroll: float) -> int:
        """Calculate position size for market making."""
        max_position = bankroll * self.max_position_pct
        
        # Assume average price of 0.50
        quantity = int(max_position / 0.50)
        
        return max(1, quantity)  # Minimum 1 contract
    
    def place_market_making_orders(self, market: dict, bankroll: float) -> list:
        """
        Place symmetrical buy/sell orders.
        
        Returns list of placed orders.
        """
        if not self.should_make_market(market):
            return []
        
        analysis = self.analyze_market(market)
        quantity = self.calculate_position_size(bankroll)
        
        orders = []
        
        # Place buy order at bid
        buy_order = self.api.create_order(
            market_id=analysis['market_id'],
            side='yes',  # Buy Yes
            order_type='limit',
            price=analysis['bid'],
            quantity=quantity
        )
        orders.append(buy_order)
        
        # Place sell order at ask
        sell_order = self.api.create_order(
            market_id=analysis['market_id'],
            side='no',  # Buy No = sell Yes
            order_type='limit',
            price=analysis['ask'],
            quantity=quantity
        )
        orders.append(sell_order)
        
        return orders
    
    def manage_existing_orders(self, positions: list, orders: list) -> None:
        """
        Monitor and adjust existing market making orders.
        - Cancel if spread moves
        - Roll positions if too long
        """
        for order in orders:
            # Check if order is stale (> 1 hour)
            order_time = order.get('created_at')
            # ... cancellation logic
            pass
```

**Verification:** Test with market data, verify order calculation.

#### Step 5.2: Add to trader.py integration

```python
# In trader.py:

def __init__(self, ...):
    ...
    # Add market making
    from market_maker import MarketMaker
    self.market_maker = MarketMaker(api, config)

def run_market_making_strategy(self):
    """Run market making on suitable markets."""
    if not self.settings.market_making_enabled:
        return
    
    markets = self.api.get_markets(status='open', limit=20)
    
    for market in markets:
        if self.market_maker.should_make_market(market):
            self.market_maker.place_market_making_orders(market, self.bankroll)
```

**Verification:** Run in paper mode, verify orders placed.

#### Step 5.3: Add config parameters

```python
# In config.py:

# Market Making Configuration
MARKET_MAKING_ENABLED = os.environ.get('MARKET_MAKING_ENABLED', 'false').lower() == 'true'
MARKET_MAKING_MAX_POSITION_PCT = float(os.environ.get('MARKET_MAKING_MAX_PCT', '0.40'))
MARKET_MAKING_MIN_VOLUME = int(os.environ.get('MARKET_MAKING_MIN_VOLUME', '1000'))
```

**Verification:** Verify config loads correctly.

---

## Implementation Order

Given dependencies, implement in this order:

1. **Phase 1** (Daily Loss) — Quick win, high impact
2. **Phase 2** (Max Hold Time) — Quick, depends on Phase 1
3. **Phase 3** (Paper Trading) — Essential for testing others safely
4. **Phase 4** (SDK) — Infrastructure improvement, enables better testing
5. **Phase 5** (Market Making) — Most complex, do last

---

## Testing Requirements

Each phase should include:

1. **Unit tests** for new functions
2. **Integration test** in paper trading mode
3. **Documentation update** in code comments

---

## Success Criteria

| Phase | Criteria |
|-------|----------|
| 1 | Trading stops within 1% of 15% daily loss |
| 2 | Positions auto-close after 10 days |
| 3 | Paper trades logged, no real API calls in paper mode |
| 4 | All existing functionality works with SDK |
| 5 | Market making orders placed in paper mode |

---

## Notes

- All phases can be tested in paper trading mode
- SDK integration (Phase 4) should use demo environment first
- Market making requires careful risk management - start small

---

*Document Version: 1.0*  
*Last Updated: 2026-03-19*
