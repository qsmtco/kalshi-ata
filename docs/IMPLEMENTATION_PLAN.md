# K-ATA Implementation Plan: Exit Logic, Liquidity & Market Selection
**Version:** 1.0  
**Created:** 2026-03-21  
**Based on:** `EXIT_LIQUIDITY_RESEARCH.md`

---

## Overview

This plan implements three major enhancements to K-ATA based on research into open-source trading bots (Jesse, Polymarket bots, NautilusTrader, and market-making frameworks):

1. **Liquidity Filtering** — reject markets that can't be traded before wasting API calls
2. **Sell/Exit Logic** — close positions based on profit, loss, time, or probability shift
3. **Market Selection** — only enter markets with a genuine edge (sweet spot, time, alignment)

---

## PHASE 1: Infrastructure & Safety Nets
### Goal: Stop wasting orders on untradeable markets. Add position awareness.

**Files touched:** `src/market_data_streamer.py`, `src/position_tracker.py` (new)

---

### Step 1.1 — Add Liquidity Check to Market Data Streamer
**File:** `src/market_data_streamer.py`

Add `is_market_liquid()` method that returns `(bool, dict)`:

```python
def is_market_liquid(self, market: dict, min_spread_pct: float = 0.15) -> tuple[bool, dict]:
    """
    Returns (is_liquid, details_dict)
    Rejects markets with zero bids, zero asks, or spreads > min_spread_pct.
    """
    yes_bid = market.get('yes_bid_dollars') or market.get('yes_bid')
    yes_ask = market.get('yes_ask_dollars') or market.get('yes_ask')
    last = market.get('last_price_dollars') or market.get('last_price')

    if not yes_bid:
        return False, {'reason': 'no_bid'}

    try:
        bid_f = float(yes_bid) if isinstance(yes_bid, str) else yes_bid
        ask_f = float(yes_ask) if isinstance(yes_ask, str) else yes_ask
    except (ValueError, TypeError):
        return False, {'reason': 'invalid_price'}

    if bid_f <= 0:
        return False, {'reason': 'bid_zero_or_negative'}

    if ask_f and ask_f > 0:
        spread_pct = (ask_f - bid_f) / ask_f
    else:
        spread_pct = 0.0  # Conservative — can try to buy at bid

    mid = (bid_f + (ask_f or bid_f)) / 2
    details = {'bid': bid_f, 'ask': ask_f, 'spread_pct': spread_pct, 'mid': mid}

    if spread_pct > min_spread_pct:
        return False, {'reason': f'spread_too_wide_{spread_pct:.1%}', **details}

    return True, details
```

Add `get_current_price()` helper:

```python
def get_current_price(self, market: dict) -> float:
    """Extract usable current price from market dict."""
    bid = market.get('yes_bid_dollars') or market.get('yes_bid')
    ask = market.get('yes_ask_dollars') or market.get('yes_ask')
    last = market.get('last_price_dollars') or market.get('last_price')

    if bid:
        try:
            bid_f = float(bid) if isinstance(bid, str) else bid
            ask_f = float(ask) if isinstance(ask, str) and ask else None
            if ask_f and ask_f > 0:
                return (bid_f + ask_f) / 2.0
            return bid_f
        except (ValueError, TypeError):
            pass

    if last:
        try:
            return float(last) if isinstance(last, str) else last
        except (ValueError, TypeError):
            pass

    return 0.0
```

---

### Step 1.2 — Add Market Quality Score
**File:** `src/market_data_streamer.py`

```python
def get_market_quality_score(self, market: dict) -> dict:
    """
    Scores a market 0-100 on tradeability.
    Returns dict with score, breakdown, and rejection reasons.
    """
    score = 0.0
    details = {}
    reasons = []

    # Factor 1: Bid presence (0-30 pts)
    bid = market.get('yes_bid_dollars') or market.get('yes_bid')
    if bid:
        try:
            bid_f = float(bid)
            score += min(30, bid_f * 300)  # 1¢ bid = 30pts
            details['bid'] = bid_f
        except:
            reasons.append('invalid_bid')
    else:
        reasons.append('no_bid')
        details['bid'] = 0

    # Factor 2: Spread tightness (0-30 pts)
    ask = market.get('yes_ask_dollars') or market.get('yes_ask')
    if bid and ask:
        try:
            bid_f = float(bid); ask_f = float(ask)
            if ask_f > 0:
                spread_pct = (ask_f - bid_f) / ask_f
                spread_score = max(0, 30 * (1 - spread_pct / 0.20))
                score += spread_score
                details['spread_pct'] = spread_pct
        except:
            pass

    # Factor 3: Volume (0-20 pts)
    vol = market.get('volume_24h', 0)
    try:
        vol_f = float(vol) if not isinstance(vol, str) else 0
    except:
        vol_f = 0
    score += min(20, vol_f / 100)
    details['volume'] = vol_f

    # Factor 4: Time remaining (0-20 pts, penalty)
    close = market.get('close_date') or market.get('market_close')
    if close:
        try:
            close_dt = datetime.fromisoformat(close.replace('Z', '+00:00'))
            hours_left = (close_dt - datetime.now(timezone.utc)).total_seconds() / 3600
            if hours_left < 0:
                score = 0
                reasons.append('market_closed')
            elif hours_left < 2:
                score *= 0.1
                reasons.append(f'under_2h_{hours_left:.1f}h')
            elif hours_left < 24:
                score *= 0.7
            details['hours_left'] = hours_left
        except:
            details['hours_left'] = None

    details['score'] = round(score, 1)
    details['reasons'] = reasons
    return details
```

---

### Step 1.3 — PositionTracker Class
**File:** `src/position_tracker.py` (new)

```python
"""
position_tracker.py — Real-time position state management.

Maintains a local mirror of all open positions, updated after every trade cycle.
Replaces reliance on repeated API calls for position state.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

@dataclass
class Position:
    ticker: str
    event_id: str
    strategy: str
    side: str            # 'yes' or 'no'
    count: int
    avg_fill_price: float
    open_time: datetime
    last_updated: datetime
    current_market_price: float = 0.0
    signal_at_entry: float = 0.0
    stop_loss_price: float = 0.0
    take_profit_price: float = 0.0

    @property
    def cost_basis(self) -> float:
        return self.count * self.avg_fill_price

    @property
    def market_value(self) -> float:
        return self.count * self.current_market_price

    @property
    def unrealized_pnl(self) -> float:
        return (self.current_market_price - self.avg_fill_price) * self.count

    @property
    def age_hours(self) -> float:
        return (datetime.now(timezone.utc) - self.open_time).total_seconds() / 3600


class PositionTracker:
    def __init__(self):
        self.positions: dict[str, Position] = {}  # ticker -> Position
        self.logger = logging.getLogger(__name__)

    def add_position(self, ticker: str, event_id: str, strategy: str,
                     side: str, count: int, avg_fill_price: float,
                     signal_score: float = 0.0):
        """Record a newly opened position. Updates existing if already present."""
        if ticker in self.positions:
            pos = self.positions[ticker]
            total_cost = pos.avg_fill_price * pos.count + avg_fill_price * count
            new_count = pos.count + count
            pos.avg_fill_price = total_cost / new_count
            pos.count = new_count
            pos.last_updated = datetime.now(timezone.utc)
            self.logger.info(f"Position {ticker}: increased to {new_count} @ ${pos.avg_fill_price:.4f}")
            return

        self.positions[ticker] = Position(
            ticker=ticker,
            event_id=event_id,
            strategy=strategy,
            side=side,
            count=count,
            avg_fill_price=avg_fill_price,
            open_time=datetime.now(timezone.utc),
            last_updated=datetime.now(timezone.utc),
            current_market_price=avg_fill_price,
            signal_at_entry=signal_score,
            stop_loss_price=avg_fill_price * 0.60,    # -40% default stop
            take_profit_price=avg_fill_price * 1.50,   # +50% default take profit
        )
        self.logger.info(f"Position opened: {ticker} — {count} @ ${avg_fill_price:.4f}")

    def update_price(self, ticker: str, current_price: float):
        if ticker in self.positions:
            self.positions[ticker].current_market_price = current_price
            self.positions[ticker].last_updated = datetime.now(timezone.utc)

    def close_position(self, ticker: str, reason: str = ""):
        if ticker in self.positions:
            pos = self.positions.pop(ticker)
            self.logger.info(f"Position closed: {ticker} — {pos.count} @ ${pos.avg_fill_price:.4f}. Reason: {reason}")

    def get_open_tickers(self) -> list[str]:
        return list(self.positions.keys())

    def has_position(self, ticker: str) -> bool:
        return ticker in self.positions

    def get_position(self, ticker: str) -> Optional[Position]:
        return self.positions.get(ticker)

    def get_all_positions(self) -> list[Position]:
        return list(self.positions.values())

    def total_exposure(self) -> float:
        return sum(p.cost_basis for p in self.positions.values())

    def sync_from_api(self, api_positions: list):
        """Rebuild local state from API response on bot startup."""
        self.positions.clear()
        for p in api_positions:
            ticker = p.get('ticker') or p.get('market_ticker')
            if not ticker:
                continue
            self.add_position(
                ticker=ticker,
                event_id=p.get('event_id', ticker),
                strategy='unknown',
                side=p.get('side', 'yes'),
                count=int(p.get('count', 0)),
                avg_fill_price=float(p.get('avg_fill_price', 0)),
            )
```

---

### Step 1.4 — Integrate PositionTracker Into Trader
**File:** `src/trader.py`

Add to imports:
```python
from position_tracker import PositionTracker
```

In `__init__`:
```python
self.position_tracker = PositionTracker()
```

After every successful buy execution:
```python
self.position_tracker.add_position(
    ticker=event_id,
    event_id=event_id,
    strategy='news_sentiment',  # or the actual strategy name
    side='yes',
    count=quantity,
    avg_fill_price=price,
    signal_score=sentiment_decision.get('sentiment_score', 0.0),
)
```

On each trading cycle, update position prices:
```python
for pos in self.position_tracker.get_all_positions():
    market_info = self.market_data_streamer.get_market(pos.ticker)
    if market_info:
        current_price = self.market_data_streamer.get_current_price(market_info)
        self.position_tracker.update_price(pos.ticker, current_price)
```

On startup, sync from API:
```python
api_positions = self.api.get_positions()
if api_positions and api_positions.get('positions'):
    self.position_tracker.sync_from_api(api_positions['positions'])
```

---

## PHASE 2: Sell / Exit Logic
### Goal: Actually close positions when conditions are met.

**Files touched:** `src/exit_rules.py` (new), `src/trader.py`

---

### Step 2.1 — Exit Rules
**File:** `src/exit_rules.py` (new)

```python
"""
exit_rules.py — Exit trigger logic for K-ATA positions.

Each rule evaluates a single exit condition.
evaluate_all() runs all rules and returns the first triggered exit.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

@dataclass
class ExitResult:
    should_exit: bool
    exit_type: str   # 'take_profit' | 'stop_loss' | 'time_exit' | 'prob_shift' | 'none'
    reason: str
    urgency: str = 'normal'  # 'high' | 'normal' | 'low'


def check_take_profit(position, current_price: float, threshold: float = 1.50) -> ExitResult:
    """
    Exit if price has risen enough to lock in profit.
    threshold=1.50 means exit if current >= entry * 1.50 (+50%).
    """
    if current_price >= position.avg_fill_price * threshold:
        pnl_pct = (current_price - position.avg_fill_price) / position.avg_fill_price * 100
        return ExitResult(
            should_exit=True,
            exit_type='take_profit',
            reason=f"Take profit: ${current_price:.4f} >= ${position.avg_fill_price * threshold:.4f} (+{pnl_pct:.0f}%)",
            urgency='high'
        )
    return ExitResult(should_exit=False, exit_type='none', reason='')


def check_stop_loss(position, current_price: float, threshold: float = 0.60) -> ExitResult:
    """
    Exit if price dropped enough that the trade is going wrong.
    threshold=0.60 means exit if current <= entry * 0.60 (-40%).
    For prediction markets: this = implied probability moved against your thesis.
    """
    if current_price <= position.avg_fill_price * threshold:
        loss_pct = (position.avg_fill_price - current_price) / position.avg_fill_price * 100
        return ExitResult(
            should_exit=True,
            exit_type='stop_loss',
            reason=f"Stop loss: ${current_price:.4f} <= ${position.avg_fill_price * threshold:.4f} (-{loss_pct:.0f}%)",
            urgency='high'
        )
    return ExitResult(should_exit=False, exit_type='none', reason='')


def check_market_close(position, hours_remaining: float, limit: float = 0.5) -> ExitResult:
    """
    Exit if market is about to close (settle).
    limit=0.5: exit when < 30 minutes remain.
    """
    if 0 < hours_remaining < limit:
        return ExitResult(
            should_exit=True,
            exit_type='time_exit',
            reason=f"Market closing in {hours_remaining:.1f}h — exiting before settlement",
            urgency='high'
        )
    if hours_remaining <= 0:
        return ExitResult(
            should_exit=True,
            exit_type='time_exit',
            reason="Market has closed — should auto-settle",
            urgency='high'
        )
    return ExitResult(should_exit=False, exit_type='none', reason='')


def check_probability_shift(position, current_price: float) -> ExitResult:
    """
    Exit if the market's implied probability has shifted against our thesis.
    We bought on a bullish signal. If price drops significantly, the market rejected the signal.
    """
    if position.signal_at_entry > 0 and current_price < position.avg_fill_price * 0.75:
        return ExitResult(
            should_exit=True,
            exit_type='prob_shift',
            reason=f"Probability shift: market moved from ${position.avg_fill_price:.4f} to ${current_price:.4f} — signal rejected",
            urgency='high'
        )
    return ExitResult(should_exit=False, exit_type='none', reason='')


def check_time_exit(position, hours_limit: float = 24) -> ExitResult:
    """
    Exit if position has been open too long without meaningful profit.
    hours_limit=24: cut after 24 hours regardless.
    """
    if position.age_hours >= hours_limit:
        in_profit = position.unrealized_pnl > 0
        return ExitResult(
            should_exit=True,
            exit_type='time_exit',
            reason=f"Time exit: position {position.age_hours:.1f}h old {'in profit' if in_profit else 'not in profit'}",
            urgency='normal' if in_profit else 'high'
        )
    return ExitResult(should_exit=False, exit_type='none', reason='')


def evaluate_all(position, current_price: float, hours_remaining: float) -> ExitResult:
    """
    Run all exit rules. Returns first triggered exit in priority order:
    stop_loss → market_close → take_profit → probability_shift → time_exit
    """
    checks = [
        lambda p, c: check_stop_loss(p, c),
        lambda p, c: check_market_close(p, hours_remaining, limit=0.5),
        lambda p, c: check_take_profit(p, c),
        lambda p, c: check_probability_shift(p, c),
        lambda p, c: check_time_exit(p),
    ]

    for check in checks:
        result = check(position, current_price)
        if result.should_exit:
            return result

    return ExitResult(should_exit=False, exit_type='none', reason='')
```

---

### Step 2.2 — Implement `_execute_sell()` and `_execute_sell_fak()`
**File:** `src/trader.py`

```python
def _execute_sell(self, ticker: str, count: int, price: float,
                  exit_reason: str, strategy: str) -> dict:
    """
    Place a sell order to close a position.
    Tries FAK first (Fill-And-Kill), falls back to limit order.
    """
    trade_id = f"exit_{strategy}_{ticker[:20]}_{int(time.time())}"
    price_cents = int(price * 100) if price <= 1 else int(price)

    # Try FAK (Fill-And-Kill) for immediate exit
    order_payload = {
        'ticker': ticker,
        'side': 'yes',
        'action': 'sell',
        'client_order_id': trade_id,
        'count': count,
        'yes_price': price_cents,
    }

    try:
        api_response = self.api.create_order(order_payload)
        if api_response and api_response.get('order'):
            status = api_response['order'].get('status')
            self.logger.info(
                f"SELL EXIT: {count} {ticker[:30]} @ ${price:.4f} "
                f"reason={exit_reason} status={status}"
            )
            return {'success': True, 'order': api_response['order'], 'method': 'FAK'}
    except Exception as e:
        self.logger.warning(f"Sell FAK failed for {ticker}: {e}")

    # Fallback: limit sell order (sits on book waiting for buyer)
    order_payload['client_order_id'] = f"exit_limit_{trade_id}"
    try:
        api_response = self.api.create_order(order_payload)
        if api_response and api_response.get('order'):
            status = api_response['order'].get('status')
            self.logger.info(
                f"SELL LIMIT PLACED: {count} {ticker[:30]} @ ${price:.4f} "
                f"reason={exit_reason} status={status} — resting on book"
            )
            return {'success': True, 'order': api_response['order'], 'method': 'limit'}
    except Exception as e:
        self.logger.error(f"Sell limit also failed for {ticker}: {e}")
        return {'success': False, 'error': str(e)}

    return {'success': False, 'error': 'Both FAK and limit sell failed'}
```

---

### Step 2.3 — `check_and_execute_exits()` Method
**File:** `src/trader.py`

```python
def check_and_execute_exits(self) -> list[dict]:
    """
    Check all open positions against exit rules.
    Execute sells for any position that triggers.
    Returns list of exit events for logging/reporting.
    """
    exits = []

    for position in self.position_tracker.get_all_positions():
        # Get current price from market data
        market_info = self.market_data_streamer.get_market(position.ticker)
        if not market_info:
            continue

        current_price = self.market_data_streamer.get_current_price(market_info)
        if not current_price or current_price == 0:
            continue

        # Get hours remaining
        close_date = market_info.get('close_date') or market_info.get('market_close')
        hours_remaining = None
        if close_date:
            try:
                close_dt = datetime.fromisoformat(close_date.replace('Z', '+00:00'))
                hours_remaining = (close_dt - datetime.now(timezone.utc)).total_seconds() / 3600
            except:
                pass

        # Evaluate all exit rules
        result = evaluate_all(position, current_price, hours_remaining or 999)

        if not result.should_exit:
            continue

        self.logger.info(f"EXIT TRIGGERED [{result.exit_type.upper()}]: {position.ticker[:30]} — {result.reason}")

        exit_result = self._execute_sell(
            ticker=position.ticker,
            count=position.count,
            price=current_price,
            exit_reason=result.reason,
            strategy=position.strategy,
        )

        if exit_result['success']:
            self.position_tracker.close_position(position.ticker, reason=f"{result.exit_type}: {result.reason}")
            exits.append({
                'ticker': position.ticker,
                'exit_type': result.exit_type,
                'reason': result.reason,
                'exit_price': current_price,
                'exit_qty': position.count,
                'entry_price': position.avg_fill_price,
                'pnl_estimate': (current_price - position.avg_fill_price) * position.count,
                'method': exit_result.get('method'),
            })
            self._send_exit_alert(exits[-1])
        else:
            self.logger.error(f"Exit failed for {position.ticker}: {exit_result.get('error')}")

    return exits
```

---

### Step 2.4 — Wire Exit Check Into Main Trading Loop
**File:** `src/trader.py`

At the start of `run_trading_strategy()`, BEFORE signal generation:

```python
def run_trading_strategy(self, market_data: dict) -> None:
    # STEP 0: Update position prices
    for pos in self.position_tracker.get_all_positions():
        market_info = self.market_data_streamer.get_market(pos.ticker)
        if market_info:
            current_price = self.market_data_streamer.get_current_price(market_info)
            self.position_tracker.update_price(pos.ticker, current_price)

    # STEP 1: Check exits FIRST — close positions that have hit exit criteria
    exit_events = self.check_and_execute_exits()
    if exit_events:
        self.logger.info(f"Exit events this cycle: {len(exit_events)}")

    # STEP 2: Then do signal generation and new trades (rest of existing method)
    # ... existing signal generation code continues here ...
```

---

## PHASE 3: Market Selection
### Goal: Only enter markets that have a genuine edge.

**Files touched:** `src/market_selector.py` (new), `src/trader.py`

---

### Step 3.1 — Market Selector
**File:** `src/market_selector.py` (new)

```python
"""
market_selector.py — Market quality filtering and selection.

Decides which markets are worth trading based on:
- Liquidity (Phase 1)
- Probability sweet spot (25%-75%)
- Time remaining
- Signal-market alignment
"""

from datetime import datetime, timezone


def probability_sweet_spot(price: float, min_pct: float = 0.25, max_pct: float = 0.75) -> tuple[bool, str]:
    """
    Only trade markets where implied probability is 25%-75%.
    Outside this range the market has mostly resolved — no edge left.
    """
    try:
        price_f = float(price)
    except (ValueError, TypeError):
        return False, f"Cannot parse price: {price}"

    if price_f < min_pct:
        return False, f"Probability {price_f:.0%} too low — fighting consensus"
    if price_f > max_pct:
        return False, f"Probability {price_f:.0%} too high — no edge left"
    return True, f"In sweet spot ({price_f:.0%})"


def time_remaining_ok(close_date: str, min_hours: float = 2.0) -> tuple[bool, str, float]:
    """
    Skip markets that are about to close.
    Returns (ok, reason, hours_remaining)
    """
    try:
        close_dt = datetime.fromisoformat(close_date.replace('Z', '+00:00'))
        hours_left = (close_dt - datetime.now(timezone.utc)).total_seconds() / 3600
    except:
        return False, "Cannot parse close_date", 0.0

    if hours_left <= 0:
        return False, "Market already closed", hours_left
    if hours_left < min_hours:
        return False, f"Only {hours_left:.1f}h remaining — too close to enter", hours_left
    return True, f"{hours_left:.1f}h remaining", hours_left


def signal_market_alignment(signal_score: float, market_price: float,
                            min_edge: float = 0.05) -> tuple[bool, str, float]:
    """
    Check if our signal has genuine edge vs. what the market already prices.

    Our signal of 0.15 means we expect probability to move roughly 0.15*2 toward 1.0.
    If market is already at 85%, there's nowhere left for it to go — no edge.

    Returns (has_edge, reason, expected_edge_pct)
    """
    expected_prob = min(0.95, market_price + signal_score * 2)
    edge = expected_prob - market_price

    if edge < min_edge:
        return False, (
            f"No edge: market at {market_price:.0%}, signal expects {expected_prob:.0%} "
            f"(+{edge:.0%} move vs +{min_edge:.0%} minimum)"
        ), edge

    return True, f"Edge of {edge:.0%} (market {market_price:.0%} → expected {expected_prob:.0%})", edge


def is_tradeable(market: dict, market_data_streamer,
                 signal_score: float = 0.0,
                 min_quality: float = 30.0) -> tuple[bool, str]:
    """
    Master filter: combines all checks into one go/no-go decision.

    Returns (should_trade, reason)
    """
    ticker = market.get('ticker', 'unknown')
    price = market.get('last_price_dollars') or market.get('last_price', 0.5)
    close_date = market.get('close_date') or market.get('market_close')

    # 1. Quality score (from Phase 1)
    quality = market_data_streamer.get_market_quality_score(market)
    if quality.get('score', 0) < min_quality:
        return False, f"Quality score {quality['score']:.1f} < {min_quality:.1f}"

    # 2. Liquidity check
    is_liq, liq_details = market_data_streamer.is_market_liquid(market)
    if not is_liq:
        return False, f"Not liquid: {liq_details.get('reason', 'unknown')}"

    # 3. Probability sweet spot
    try:
        price_f = float(price)
    except:
        return False, f"Cannot parse price: {price}"

    in_spot, spot_reason = probability_sweet_spot(price_f)
    if not in_spot:
        return False, f"Probability: {spot_reason}"

    # 4. Time remaining
    if close_date:
        time_ok, time_reason, hours_left = time_remaining_ok(close_date)
        if not time_ok:
            return False, f"Time: {time_reason}"

    # 5. Signal-market alignment (only if signal provided)
    if signal_score != 0.0:
        aligned, align_reason, edge = signal_market_alignment(signal_score, price_f)
        if not aligned:
            return False, f"Alignment: {align_reason}"

    return True, "All checks passed"
```

---

### Step 3.2 — Wire Market Selection Into Trader
**File:** `src/trader.py`

In signal generation, before creating any trade decision:

```python
# Get signal score for this signal type
signal_score = sentiment_decision.get('sentiment_score', 0.0) if signal_name == 'news_sentiment' else 0.0

# Market selection check — skip if market not tradeable
market_info_for_select = market_data  # use the market dict from earlier in the loop

should_trade, trade_reason = is_tradeable(
    market=market_info_for_select,
    market_data_streamer=self.market_data_streamer,
    signal_score=signal_score,
    min_quality=30.0,
)

if not should_trade:
    self.logger.info(f"SKIPPING {event_id}: market selection — {trade_reason}")
    continue  # Skip this signal, move to next
```

---

## PHASE 4: Enhanced Monitoring & Reporting
### Goal: Full visibility into what's happening and why.

**Files touched:** `src/trader.py`

---

### Step 4.1 — Compact Position Status Log
Add to `run_trading_strategy()`, called every cycle:

```python
def log_position_status(self):
    """Log compact status line for all open positions."""
    positions = self.position_tracker.get_all_positions()
    if not positions:
        return

    for pos in positions:
        pnl = pos.unrealized_pnl
        pnl_pct = (pos.current_market_price / pos.avg_fill_price - 1) * 100 if pos.avg_fill_price > 0 else 0
        self.logger.info(
            f"POS: {pos.ticker[:25]:<25} "
            f"qty:{pos.count:<4} "
            f"entry:${pos.avg_fill_price:.3f} "
            f"curr:${pos.current_market_price:.3f} "
            f"{'+' if pnl >= 0 else ''}{pnl:.2f}({pnl_pct:+.1f}%) "
            f"age:{pos.age_hours:.0f}h "
            f"TP:${pos.take_profit_price:.3f} SL:${pos.stop_loss_price:.3f}"
        )
```

Call at end of each `run_trading_strategy()` cycle.

---

### Step 4.2 — Telegram Alert Methods
Add to `__init__` or as standalone methods:

```python
def _send_telegram(self, message: str):
    import requests
    if not self.telegram_bot_token or not self.telegram_chat_id:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage",
            json={'chat_id': self.telegram_chat_id, 'text': message, 'parse_mode': 'Markdown'},
            timeout=5,
        )
    except Exception as e:
        self.logger.warning(f"Telegram send failed: {e}")

def _send_new_trade_alert(self, trade: dict):
    self._send_telegram(
        f"🟢 *K-ATA NEW TRADE*\n"
        f"`{trade['event_id'][:30]}`\n"
        f"Action: *{trade['action'].upper()}* {trade['quantity']} @ ${trade['price']:.4f}\n"
        f"Strategy: {trade['strategy']}\n"
        f"Sentiment: {trade.get('sentiment_score', 'N/A')}"
    )

def _send_exit_alert(self, exit_event: dict):
    pnl = exit_event.get('pnl_estimate', 0)
    pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
    emoji = "✅" if pnl >= 0 else "❌"
    self._send_telegram(
        f"{emoji} *K-ATA EXIT*\n"
        f"`{exit_event['ticker'][:30]}`\n"
        f"Type: *{exit_event['exit_type'].upper()}*\n"
        f"Qty: {exit_event['exit_qty']} @ ${exit_event['exit_price']:.4f}\n"
        f"Entry: ${exit_event['entry_price']:.4f}\n"
        f"P&L: {pnl_str}\n"
        f"{exit_event['reason']}"
    )

def _send_position_summary_alert(self):
    positions = self.position_tracker.get_all_positions()
    if not positions:
        return
    lines = ["📊 *K-ATA POSITIONS*"]
    total_pnl = 0
    for pos in positions:
        pnl = pos.unrealized_pnl
        total_pnl += pnl
        lines.append(
            f"• {pos.ticker[-20:]}: {pos.count} @ ${pos.avg_fill_price:.2f} "
            f"→ ${pos.current_market_price:.2f} {'+' if pnl >= 0 else ''}{pnl:.2f}"
        )
    lines.append(f"*Total P&L: {'+' if total_pnl >= 0 else ''}${total_pnl:.2f}*")
    self._send_telegram("\n".join(lines))
```

---

### Step 4.3 — Daily Summary Check
Add to the main loop (check via timestamp comparison):

```python
def maybe_send_daily_summary(self):
    now = datetime.now(timezone.utc)
    if now.hour == 9 and now.minute < 5:  # ~9:00 AM each day
        if not hasattr(self, '_last_daily_summary') or (now - self._last_daily_summary).days >= 1:
            self._send_position_summary_alert()
            self._last_daily_summary = now
```

Call `maybe_send_daily_summary()` once per cycle (it gates itself).

---

## PHASE 5: Integration, Testing & Polish
### Goal: Tie everything together, verify it works, push to GitHub.

---

### Step 5.1 — Update `main.py` to Wire Everything
Ensure `main.py` initializes everything in the right order:

```python
# 1. Initialize API
api = KalshiAPI()

# 2. Initialize market data streamer (starts fetching immediately)
market_data_streamer = MarketDataStreamer(api)
market_data_streamer.start_streaming()

# 3. Initialize position tracker and sync from API
position_tracker = PositionTracker()
api_positions = api.get_positions()
if api_positions and api_positions.get('positions'):
    position_tracker.sync_from_api(api_positions['positions'])

# 4. Initialize trader with all dependencies
trader = TradingStrategy(api, market_data_streamer, position_tracker, ...)
```

---

### Step 5.2 — Add to `.gitignore` Before Committing
Ensure no secrets are pushed:
```
kalshi_private_key.pem
.env
*.log
__pycache__/
*.pyc
```

---

### Step 5.3 — Push to GitHub
```bash
git add src/exit_rules.py src/position_tracker.py src/market_selector.py
git add src/market_data_streamer.py src/trader.py
git commit -m "feat: exit logic, liquidity filtering, market selection

Phase 1: PositionTracker for real-time position state
Phase 2: Exit rules (take profit, stop loss, time exit, probability shift)
Phase 3: Market selector (liquidity, sweet spot, signal alignment)
Phase 4: Enhanced Telegram alerts and position logging

New files: exit_rules.py, position_tracker.py, market_selector.py"
git push origin main
```

---

## Implementation Order Summary

| Phase | Steps | New Files | Estimated Complexity |
|-------|-------|-----------|---------------------|
| **Phase 1** Infrastructure | 1.1, 1.2, 1.3, 1.4 | `position_tracker.py` | Medium |
| **Phase 2** Exit Logic | 2
| Phase | Steps | New Files | Estimated Complexity |
|-------|-------|-----------|---------------------|
| **Phase 1** Infrastructure | 1.1, 1.2, 1.3, 1.4 | `position_tracker.py` | Medium |
| **Phase 2** Exit Logic | 2.1, 2.2, 2.3, 2.4 | `exit_rules.py` | Medium |
| **Phase 3** Market Selection | 3.1, 3.2 | `market_selector.py` | Medium |
| **Phase 4** Monitoring | 4.1, 4.2, 4.3 | — | Low |
| **Phase 5** Integration & Push | 5.1, 5.2, 5.3 | — | Low |

---

## Files Summary

| File | Action |
|------|--------|
| `src/position_tracker.py` | **NEW** — Position state management |
| `src/exit_rules.py` | **NEW** — Exit trigger evaluation |
| `src/market_selector.py` | **NEW** — Market quality filtering |
| `src/market_data_streamer.py` | **MODIFIED** — Add `is_market_liquid()`, `get_market_quality_score()`, `get_current_price()` |
| `src/trader.py` | **MODIFIED** — Wire exit checks, sell execution, market selection, Telegram alerts |
| `src/main.py` | **MODIFIED** — Wire Phase 1-3 components together on startup |

---

## Settings to Add/Update in `.env`

```bash
# Minimum market quality score (0-100) — reject markets below this
MIN_MARKET_QUALITY=30.0

# Take profit threshold (1.5 = +50% from entry)
TAKE_PROFIT_MULTIPLIER=1.50

# Stop loss threshold (0.6 = -40% from entry)
STOP_LOSS_MULTIPLIER=0.60

# Maximum position age before time exit (hours)
MAX_POSITION_AGE_HOURS=24

# Minimum hours remaining to open new position
MIN_HOURS_TO_OPEN=2.0

# Market probability sweet spot range
MIN_PROBABILITY=0.25
MAX_PROBABILITY=0.75

# Enable/disable Telegram alerts
ENABLE_TELEGRAM_ALERTS=true
```

---

## Quick-Reference: What Each Phase Does

**Phase 1 — Infrastructure**
- Before: Bot has no idea what positions it holds between cycles
- After: `PositionTracker` maintains live state, updated every cycle

**Phase 2 — Exit Logic**
- Before: Bot only buys, never sells
- After: Bot checks 5 exit triggers every cycle and places sell orders automatically

**Phase 3 — Market Selection**
- Before: Bot tries to trade any market that passes signal threshold
- After: Bot only trades markets that pass liquidity + probability + time + alignment filters

**Phase 4 — Monitoring**
- Before: Telegram only sends errors
- After: Telegram sends new trade alerts, exit alerts, and position summaries

**Phase 5 — Integration**
- Before: Pieces exist in isolation
- After: Fully wired pipeline, tested, pushed to GitHub
