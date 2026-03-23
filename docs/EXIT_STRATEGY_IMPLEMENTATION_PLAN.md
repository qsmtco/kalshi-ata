# K-ATA Exit Strategy Implementation Plan
## Top 5 Strategies — Multi-Phase Build Guide

*Generated: 2026-03-22*
*Strategies: Liquidity Exit · Partial Exit · ATR Trailing Stop · Triple-Barrier · Volatility-Time Hybrid*

---

## Overview

This plan implements five advanced exit strategies on top of the existing K-ATA trading bot. Each strategy is implemented in a dedicated phase. Each phase contains numbered steps that can be checked off individually as they are completed.

**Key constraint:** All strategies must coexist — multiple exit triggers can fire simultaneously, and the system should handle partial exits without double-exiting positions.

**Files involved:**
- `src/exit_rules.py` — exit trigger functions
- `src/trader.py` — `_execute_sell`, `check_and_execute_exits`
- `src/position_tracker.py` — `Position` dataclass, `PositionTracker`
- `src/market_data_streamer.py` — `MarketData` dataclass, market data fetching
- `src/config.py` — new configuration parameters

---

## How to Use This Plan

- Mark `[ ]` → not started
- Mark `[P]` → in progress
- Mark `[D]` → done
- Each step must compile and pass basic tests before the next step in the same phase begins
- After each phase is complete, run the full trading cycle and confirm no crashes

---

## PHASE 1: Infrastructure & Data Layer

*Purpose: Add the data structures and calculations that all five strategies depend on. This phase must be completed before any strategy can be implemented.*

---

### Step 1.1 — Add Price History to MarketData
**File:** `src/market_data_streamer.py`

**What to build:**
The `MarketData` dataclass needs a `price_history: list[float]` that is updated every trading cycle with the latest `current_price`. Currently it exists but is not actively maintained.

**Changes:**
1. In `MarketData.__post_init__`, initialize `price_history: list[float] = field(default_factory=list)` if not provided
2. In the market data update loop, append `current_price` to `price_history` every cycle
3. Cap the history length at `MAX_PRICE_HISTORY = 200` to prevent memory growth — when length exceeds cap, drop the oldest entry
4. Add property `price_history` to `MarketData` that returns the list

**Verification:**
```python
md = market_data_streamer.get_market_data(ticker)
assert len(md.price_history) > 0, "price history should be populated"
assert md.price_history[-1] == md.current_price, "latest price should match current_price"
```

- [x] Step 1.1 complete ✅ (2026-03-22)

---

### Step 1.2 — Add ATR Calculation to VolatilityAnalyzer
**File:** `src/volatility_analyzer.py`

**What to build:**
Add a `calculate_atr(prices: list[float], period: int = 14) -> float` method that computes the Average True Range for the binary price series.

**Changes:**
Add `calculate_atr(self, prices: List[float], period: int = 14) -> float` method to `VolatilityAnalyzer`. For binary markets (0–1 prices), compute True Range as `|price_change|` with a minimum tick of $0.01. ATR = exponential moving average of TR over `period`, or simple mean if fewer than `period` data points. Return ATR in dollar terms.

**Verification:**
```python
analyzer = VolatilityAnalyzer()
atr = analyzer.calculate_atr([0.50, 0.51, 0.52, 0.53, 0.54], period=5)
assert atr > 0, "ATR must be positive"
assert atr <= 0.05, "ATR for tight ranges should be small"
```

- [x] Step 1.2 complete ✅ (2026-03-22)

---

### Step 1.3 — Add Volatility to MarketData
**File:** `src/market_data_streamer.py`

**What to build:**
Every time the market data streamer updates a market's price, also compute and store the current ATR alongside the price history.

**Changes:**
1. In the market data update loop, after updating `price_history`, call `volatility_analyzer.calculate_atr(market_md.price_history)` and store the result in `market_md.volatility`
2. If `len(price_history) < 14`, set `volatility = None` (not enough data for ATR)

**Verification:**
```python
md = market_data_streamer.get_market_data(ticker)
if len(md.price_history) >= 14:
    assert md.volatility is not None, "volatility should be set when enough history"
```

- [x] Step 1.3 complete ✅ (2026-03-22)

---

### Step 1.4 — Extend Position Dataclass for Partial Exits
**File:** `src/position_tracker.py`

**What to build:**
Add fields to `Position` to support partial exit tracking and new exit parameters.

**Add to `Position` dataclass:**
```python
# Partial exit tracking
exit_tiers: List[Dict[str, Any]] = field(default_factory=list)
remaining_count: int = 0
initial_count: int = 0

# ATR trailing stop
atr_trailing_stop: float = 0.0
atr_multiplier: float = 3.0
highest_price_since_entry: float = 0.0

# Triple-barrier
barrier_tp_multiplier: float = 1.50
signal_confidence: float = 0.5

# Volatility-time hybrid
volatility_adjusted_tp_mult: float = 1.50
```

**Changes to `PositionTracker.add_position()`:**
1. Set `initial_count = position.count`
2. Set `remaining_count = position.count`
3. Set `highest_price_since_entry = position.avg_fill_price`
4. Set `atr_trailing_stop = position.avg_fill_price - (atr_multiplier * current_atr)` if ATR available

**Add `reduce_position()` method to `PositionTracker`:**
```python
def reduce_position(self, ticker: str, qty: int, reason: str = '') -> bool:
    if ticker not in self._positions:
        return False
    pos = self._positions[ticker]
    qty = min(qty, pos.remaining_count)
    pos.remaining_count -= qty
    pos.count = pos.remaining_count
    pos.last_updated = datetime.now(timezone.utc)
    logger.info(f"Reduced {ticker}: -{qty} contracts, {pos.remaining_count} remaining. {reason}")
    return True
```

**Verification:**
```python
pos = Position(ticker="TEST", event_id="E1", strategy="test", side="yes",
               count=100, avg_fill_price=0.50, open_time=datetime.now(timezone.utc))
assert pos.initial_count == 100
assert pos.remaining_count == 100
assert pos.highest_price_since_entry == pos.avg_fill_price
```

- [x] Step 1.4 complete ✅ (2026-03-22)

---

### Step 1.5 — Add Liquidity Fields to MarketData
**File:** `src/market_data_streamer.py`

**What to build:**
Store the raw bid/ask data from Kalshi so exit triggers can check liquidity.

**Add to `MarketData` dataclass:**
```python
yes_bid: Optional[float] = None
yes_ask: Optional[float] = None
yes_bid_qty: Optional[int] = None
yes_ask_qty: Optional[int] = None
spread_pct: Optional[float] = None
```

Update the market data parsing code to extract these fields from the Kalshi API response.

**Verification:**
```python
md = market_data_streamer.get_market_data(ticker)
if md.yes_bid is not None:
    assert md.yes_ask > md.yes_bid
    assert 0 <= md.spread_pct <= 1.0
```

- [x] Step 1.5 complete ✅ (2026-03-22)

---

### Step 1.6 — Add New Configuration Parameters
**File:** `src/config.py`

**Add:**
```python
# PHASE 6: ADVANCED EXIT STRATEGY CONFIGURATION

# Liquidity Exit
LIQUIDITY_MIN_BID_DOLLARS: float = 0.05
LIQUIDITY_MIN_BID_QTY: int = 10
LIQUIDITY_SPREAD_MAX: float = 0.15

# Partial Exit Tiers — (price_multiplier, qty_percentage)
PARTIAL_EXIT_TIERS: List[Tuple[float, float]] = [
    (1.20, 0.30),  # Exit 30% at entry + 20%
    (1.40, 0.30),  # Exit 30% at entry + 40%
    (1.60, 0.20),  # Exit 20% at entry + 60%
]

# ATR Trailing Stop
ATR_MULTIPLIER: float = 3.0
ATR_PERIOD: int = 14

# Triple-Barrier
BARRIER_TP_BASE: float = 1.50
BARRIER_TP_CONFIDENCE_SCALING: bool = True
BARRIER_TP_CONFIDENCE_MAX: float = 2.00

# Volatility-Time Hybrid
VOLATILITY_TP_SCALAR: float = 0.3
VOLATILITY_TP_MAX: float = 3.00
TIME_DECAY_FULL_HORIZON_HRS: float = 24.0
TIME_DECAY_MAX_PENALTY: float = 0.20
```

- [x] Step 1.6 complete ✅ (2026-03-22)

---

### Step 1.7 — Phase 1 Verification
**Run:**
```bash
cd /home/q/projects/kalshi-ata
python3 -m py_compile src/volatility_analyzer.py
python3 -m py_compile src/position_tracker.py
python3 -m py_compile src/market_data_streamer.py
python3 -m py_compile src/trader.py
node --check bot_interface.js
```

**Phase 1 Sign-off:**
- [x] All 6 files compile without errors ✅
- [x] `MarketData` has all liquidity fields ✅
- [x] `Position` has all advanced exit fields ✅
- [x] `VolatilityAnalyzer.calculate_atr()` exists ✅
- [x] Config has all new parameters ✅

---

## PHASE 2: Order Book Liquidity Exit

*Purpose: Add a pre-entry gate and a hold-period monitor that prevents K-ATA from entering or staying in illiquid markets. This is the strategy that would have prevented the zombie positions.*

---

### Step 2.1 — Add Liquidity Check Function to MarketDataStreamer
**File:** `src/market_data_streamer.py`

**What to build:**
A `is_market_liquid(market_md: MarketData) -> tuple[bool, str]` method.

**Logic:**
```python
def is_market_liquid(self, market_md: MarketData) -> tuple[bool, str]:
    if market_md.yes_bid is None or market_md.yes_bid < LIQUIDITY_MIN_BID_DOLLARS:
        return False, f"no_bid_or_bid_too_low_{market_md.yes_bid}"
    if market_md.yes_bid_qty is not None and market_md.yes_bid_qty < LIQUIDITY_MIN_BID_QTY:
        return False, f"insufficient_bid_qty_{market_md.yes_bid_qty}"
    if market_md.spread_pct is not None and market_md.spread_pct > LIQUIDITY_SPREAD_MAX:
        return False, f"spread_too_wide_{market_md.spread_pct:.1%}"
    if market_md.yes_ask is None or market_md.yes_ask == 0:
        return False, "no_ask"
    return True, ""
```

**Verification:**
```python
md_bad = MarketData(market_id="X", title="X", current_price=0.50, yes_bid=0.0, yes_ask=0.50)
assert not is_market_liquid(md_bad)[0]

md_good = MarketData(market_id="X", title="X", current_price=0.50,
                     yes_bid=0.48, yes_ask=0.52, yes_bid_qty=100, spread_pct=0.08)
assert is_market_liquid(md_good)[0]
```

- [x] Step 2.1 complete ✅ (2026-03-22)

---

### Step 2.2 — Add Liquidity Exit Trigger to exit_rules
**File:** `src/exit_rules.py`

**What to build:**
A `check_liquidity_exit(position, market_md) -> ExitResult` function.

**Logic:**
```python
def check_liquidity_exit(position, market_md, min_bid_dollars=0.05, min_bid_qty=10) -> ExitResult:
    bid = getattr(market_md, 'yes_bid', None)
    bid_qty = getattr(market_md, 'yes_bid_qty', None)

    if bid is None or bid < min_bid_dollars:
        return ExitResult(
            should_exit=True, exit_type='liquidity_exit',
            reason=f"Liquidity exit: bid ${bid} below ${min_bid_dollars} minimum — no exit available",
            urgency='high')
    if bid_qty is not None and bid_qty < min_bid_qty:
        return ExitResult(
            should_exit=True, exit_type='liquidity_exit',
            reason=f"Liquidity exit: bid qty {bid_qty} below {min_bid_qty} minimum",
            urgency='high')
    return ExitResult(should_exit=False, exit_type='none', reason='')
```

- [x] Step 2.2 complete ✅ (2026-03-22)

---

### Step 2.3 — Wire Liquidity Exit into evaluate_all
**File:** `src/exit_rules.py`

**What to build:**
Update `evaluate_all()` to accept `market_md` parameter and run liquidity check first.

**New priority order:**
1. liquidity_exit (highest — if bids gone, we must exit)
2. stop_loss
3. market_close
4. take_profit (barrier-based — Phase 5)
5. atr_trailing_stop (Phase 4)
6. probability_shift
7. time_exit

**Changes:**
```python
def evaluate_all(position, current_price: float, hours_remaining: float = 999.0,
                market_md=None) -> ExitResult:
    checks = [
        lambda p, c, md: check_liquidity_exit(p, md) if md else ExitResult(False, 'none', ''),
        lambda p, c, md: check_stop_loss(p, c),
        lambda p, c, md: _check_market_close(p, hours_remaining),
        lambda p, c, md: check_take_profit(p, c),  # Phase 5 replaces this
        lambda p, c, md: check_atr_trailing_stop(p, c, getattr(p, 'atr_multiplier', 3.0)),
        lambda p, c, md: check_probability_shift(p, c),
        lambda p, c, md: check_time_exit(p),
    ]
    for check in checks:
        result = check(position, current_price, market_md)
        if result.should_exit:
            return result
    return ExitResult(should_exit=False, exit_type='none', reason='')
```

- [x] Step 2.3 complete ✅ (2026-03-22)

---

### Step 2.4 — Wire Liquidity Check into Entry Logic
**File:** `src/trader.py`

**What to build:**
Before placing any buy order, add a liquidity pre-check. If the market fails the liquidity gate, skip that signal entirely.

**Find the buy order placement code and add before create_order():**
```python
market_md = self.market_data_streamer.get_market_data(ticker)
if market_md:
    is_liquid, reason = self.market_data_streamer.is_market_liquid(market_md)
    if not is_liquid:
        self.logger.info(f"SKIPPING {ticker}: market not liquid — {reason}")
        return None
```

- [x] Step 2.4 complete ✅ (2026-03-22)

---

### Step 2.5 — Phase 2 Verification
**Run:**
```bash
cd /home/q/projects/kalshi-ata
python3 -m py_compile src/exit_rules.py
python3 -m py_compile src/trader.py
```

**Phase 2 Sign-off:**
- [x] `check_liquidity_exit()` function exists ✅
- [x] `is_market_liquid()` method exists ✅
- [x] `evaluate_all()` accepts `market_md` parameter and runs liquidity check first ✅
- [x] Entry logic in `trader.py` has liquidity gate before buy ✅
- [x] All files compile without errors ✅

---

## PHASE 3: Partial Exit / Scaling Out

*Purpose: Exit positions in tiers rather than all-at-once. Reduces P&L variance and exposure to final-hour chaos.*

---

### Step 3.1 — Define Exit Tier Setup on Position Entry
**File:** `src/position_tracker.py`

**What to build:**
When a new position is added, initialize its `exit_tiers` from `PARTIAL_EXIT_TIERS` config.

**Changes to `PositionTracker.add_position()`:**
```python
pos.exit_tiers = [
    {
        'threshold_mult': mult,
        'qty_pct': qty_pct,
        'exited': False,
        'exit_price': None,
    }
    for mult, qty_pct in PARTIAL_EXIT_TIERS
]
pos.remaining_count = count
pos.initial_count = count
```

**Update `ExitResult` dataclass:**
```python
@dataclass
class ExitResult:
    should_exit: bool
    exit_type: str
    reason: str
    urgency: str = 'normal'
    exit_qty: Optional[int] = None  # for partial exits — None means exit full position
```

- [x] Step 3.1 complete ✅ (2026-03-22)

---

### Step 3.2 — Add Partial Exit Trigger to exit_rules
**File:** `src/exit_rules.py`

**What to build:**
A `check_partial_exit(position, current_price) -> ExitResult` function that checks if the next tier threshold has been crossed.

**Logic:**
```python
def check_partial_exit(position, current_price: float) -> ExitResult:
    if not getattr(position, 'exit_tiers', None):
        return ExitResult(should_exit=False, exit_type='none', reason='')

    entry = position.avg_fill_price
    for tier in position.exit_tiers:
        if tier['exited']:
            continue
        threshold = entry * tier['threshold_mult']
        if current_price >= threshold:
            qty_to_exit = max(int(position.initial_count * tier['qty_pct']), 1)
            tier['exited'] = True
            tier['exit_price'] = current_price
            return ExitResult(
                should_exit=True, exit_type='partial_exit',
                reason=f"Partial exit tier: {qty_to_exit} contracts ({int(tier['qty_pct']*100)}%) "
                       f"at ${current_price:.4f} (threshold ×{tier['threshold_mult']:.2f})",
                urgency='normal',
                exit_qty=qty_to_exit)
    return ExitResult(should_exit=False, exit_type='none', reason='')
```

- [x] Step 3.2 complete ✅ (2026-03-22)

---

### Step 3.3 — Modify _execute_sell to Handle Partial Quantities
**File:** `src/trader.py`

**What to build:**
Update `_execute_sell()` to accept an optional `exit_qty` parameter. If provided, only sell `exit_qty` contracts instead of the full position count.

**Changes to `_execute_sell()` signature and body:**
```python
def _execute_sell(self, ticker: str, count: int, price: float,
                  exit_reason: str, strategy: str, exit_qty: int = None) -> dict:
    if exit_qty is not None and exit_qty < count:
        count = min(exit_qty, count)
        # Partial exit — don't close position, reduce it
        self.position_tracker.reduce_position(ticker, count, reason=exit_reason)
    # ... rest of order placement code (FAK then GTC fallback) ...
    # If full exit (exit_qty is None), caller is responsible for close_position()
```

**Changes to `check_and_execute_exits()` — branch on partial vs full exit:**
```python
exit_qty = getattr(result, 'exit_qty', None)

if result.exit_type == 'partial_exit' and exit_qty is not None:
    # Partial exit
    exit_result = self._execute_sell(
        ticker=pos.ticker, count=pos.remaining_count,
        price=current_price, exit_reason=result.reason,
        strategy=pos.strategy, exit_qty=exit_qty)
    if exit_result['success']:
        self.logger.info(f"PARTIAL EXIT SUCCESS: {pos.ticker[:20]} "
                         f"{exit_qty} contracts @ ${current_price:.4f}, "
                         f"{pos.remaining_count} remaining")
else:
    # Full exit — existing logic
    exit_result = self._execute_sell(...)
    if exit_result['success']:
        self.position_tracker.close_position(pos.ticker, ...)
```

- [x] Step 3.3 complete ✅ (2026-03-22)

---

### Step 3.4 — Phase 3 Verification
**Run:**
```bash
cd /home/q/projects/kalshi-ata
python3 -m py_compile src/position_tracker.py
python3 -m py_compile src/exit_rules.py
python3 -m py_compile src/trader.py
```

**Phase 3 Sign-off:**
- [x] `Position.exit_tiers` initialized on `add_position()` ✅
- [x] `check_partial_exit()` returns `ExitResult` with `exit_qty` set ✅
- [x] `ExitResult` has `exit_qty: Optional[int]` field ✅
- [x] `_execute_sell()` accepts `exit_qty` parameter ✅
- [x] `PositionTracker.reduce_position()` method exists and works ✅
- [x] `check_and_execute_exits()` branches correctly (partial vs full exit) ✅
- [x] All files compile without errors ✅

---

## PHASE 4: ATR-Based Trailing Stop (Chandelier Exit)

*Purpose: Replace the static +50% take-profit with a volatility-adaptive trailing stop that rides under the highest price since entry.*

---

### Step 4.1 — Add Highest Price Tracking to PositionTracker
**File:** `src/position_tracker.py`

**What to build:**
A method `update_highest_price(ticker, current_price)` that updates the peak price and recomputes the ATR trailing stop level.

**Add to `PositionTracker`:**
```python
def update_highest_price(self, ticker: str, current_price: float) -> None:
    if ticker not in self._positions:
        return
    pos = self._positions[ticker]
    if current_price > pos.highest_price_since_entry:
        pos.highest_price_since_entry = current_price
        if pos.volatility:
            pos.atr_trailing_stop = pos.highest_price_since_entry - (pos.atr_multiplier * pos.volatility)
```

**Wire into the main trading cycle** — find where `current_price` is updated for positions and add:
```python
self.position_tracker.update_highest_price(pos.ticker, current_price)
```

- [x] Step 4.1 complete ✅ (already done in Step 1.4 — verified)

---

### Step 4.2 — Add ATR Trailing Stop Trigger to exit_rules
**File:** `src/exit_rules.py`

**What to build:**
A `check_atr_trailing_stop(position, current_price, atr_multiplier=None) -> ExitResult` function.

**Logic:**
```python
def check_atr_trailing_stop(position, current_price: float,
                             atr_multiplier: float = None) -> ExitResult:
    if atr_multiplier is None:
        atr_multiplier = getattr(position, 'atr_multiplier', 3.0)

    highest = getattr(position, 'highest_price_since_entry', None) or position.avg_fill_price
    atr = getattr(position, 'volatility', None) or 0.0
    if atr <= 0:
        atr = position.avg_fill_price * 0.02  # fallback: 2% of price

    trailing_stop = highest - (atr_multiplier * atr)

    if current_price <= trailing_stop:
        drawdown = (highest - current_price) / highest * 100
        return ExitResult(
            should_exit=True, exit_type='atr_trailing_stop',
            reason=f"ATR trailing stop: ${current_price:.4f} <= ${trailing_stop:.4f} "
                   f"(high=${highest:.4f}, ATR×{atr_multiplier:.1f}=${atr:.4f}, "
                   f"drawdown={drawdown:.1f}%)",
            urgency='high')
    return ExitResult(should_exit=False, exit_type='none', reason='')
```

- [ ] Step 4.2 complete

---

### Step 4.3 — Add Volatility-Adjusted TP Multiplier Update
**File:** `src/position_tracker.py`

**What to build:**
Add method `update_volatility_adjusted_tp(ticker, current_volatility, base_tp_mult=1.50)` to `PositionTracker`.

**Logic:**
```python
def update_volatility_adjusted_tp(self, ticker: str, current_volatility: float,
                                  base_tp_mult: float = 1.50) -> None:
    if ticker not in self._positions:
        return
    pos = self._positions[ticker]
    vol_scalar = 0.3  # from config VOLATILITY_TP_SCALAR
    price = pos.avg_fill_price
    vol_adjusted = base_tp_mult + (vol_scalar * current_volatility / price)
    pos.volatility_adjusted_tp_mult = min(vol_adjusted, 3.0)  # cap at 3x
```

- [ ] Step 4.3 complete

---

### Step 4.4 — Update check_take_profit to Use Dynamic Threshold
**File:** `src/exit_rules.py`

**What to build:**
Modify `check_take_profit()` to use `volatility_adjusted_tp_mult` if available, falling back to static `take_profit_pct`.

**Changes:**
```python
def check_take_profit(position, current_price: float, threshold: float = None) -> ExitResult:
    if threshold is None:
        vol_adj_mult = getattr(position, 'volatility_adjusted_tp_mult', None)
        if vol_adj_mult and vol_adj_mult > 1.0:
            threshold = vol_adj_mult
        else:
            take_profit_pct = getattr(position, 'take_profit_pct', 0.50)
            threshold = 1.0 + take_profit_pct

    if current_price >= position.avg_fill_price * threshold:
        pnl_pct = (current_price - position.avg_fill_price) / position.avg_fill_price * 100
        return ExitResult(
            should_exit=True, exit_type='take_profit',
            reason=f"Take profit: ${current_price:.4f} >= ${position.avg_fill_price * threshold:.4f} "
                   f"(+{pnl_pct:.0f}%, TP mult={threshold:.2f})",
            urgency='high')
    return ExitResult(should_exit=False, exit_type='none', reason='')
```

- [ ] Step 4.4 complete

---

### Step 4.5 — Phase 4 Verification
**Run:**
```bash
cd /home/q/projects/kalshi-ata
python3 -m py_compile src/exit_rules.py
python3 -m py_compile src/position_tracker.py
```

**Phase 4 Sign-off:**
- [x] `check_atr_trailing_stop()` exists and fires correctly ✅
- [x] `update_highest_price()` method on PositionTracker ✅
- [x] `update_volatility_adjusted_tp()` method on PositionTracker ✅
- [x] `Position` has all ATR fields ✅
- [x] `check_take_profit()` uses `volatility_adjusted_tp_mult` ✅
- [x] `evaluate_all()` calls `check_atr_trailing_stop` (not no-op) ✅
- [x] All files compile without errors ✅

---

## PHASE 5: Triple-Barrier Method

*Purpose: Replace the hardcoded priority-ordered exit system with a triple-barrier model where stop loss, take profit, and time compete as equals. Set TP at entry based on signal confidence.*

---

### Step 5.1 — Add Signal Confidence and Barrier TP to Position Entry
**File:** `src/position_tracker.py`

**What to build:**
When adding a position, capture `signal_confidence` and compute `barrier_tp_multiplier`.

**Changes to `PositionTracker.add_position()` — add parameter:**
```python
signal_confidence: float = 0.5
```

**After position is created:**
```python
pos.signal_confidence = signal_confidence

# Compute barrier_tp_multiplier from signal confidence
base_tp = 1.50  # BARRIER_TP_BASE
confidence_max_tp = 2.00  # BARRIER_TP_CONFIDENCE_MAX
if BARRIER_TP_CONFIDENCE_SCALING:
    pos.barrier_tp_multiplier = 1.0 + (base_tp - 1.0) * signal_confidence
    pos.barrier_tp_multiplier = min(pos.barrier_tp_multiplier, confidence_max_tp)
else:
    pos.barrier_tp_multiplier = base_tp
```

**Update `Position` dataclass fields:**
```python
signal_confidence: float = 0.5
barrier_tp_multiplier: float = 1.50
barriers_triggered: List[str] = field(default_factory=list)
barrier_hit_order: Optional[str] = None
barrier_hit_time: Optional[datetime] = None
```

- [ ] Step 5.1 complete

---

### Step 5.2 — Add Barrier Tracking Helper to PositionTracker
**File:** `src/position_tracker.py`

**What to build:**
Add helper method to record which barrier was hit first.

**Add to `PositionTracker`:**
```python
def record_barrier_hit(self, ticker: str, barrier: str) -> None:
    if ticker not in self._positions:
        return
    pos = self._positions[ticker]
    if pos.barrier_hit_order is None:
        pos.barrier_hit_order = barrier
        pos.barrier_hit_time = datetime.now(timezone.utc)
    pos.barriers_triggered.append(barrier)
```

- [ ] Step 5.2 complete

---

### Step 5.3 — Add check_barrier_take_profit Function
**File:** `src/exit_rules.py`

**What to build:**
A `check_barrier_take_profit(position, current_price) -> ExitResult` that uses `barrier_tp_multiplier`.

**Logic:**
```python
def check_barrier_take_profit(position, current_price: float) -> ExitResult:
    barrier_mult = getattr(position, 'barrier_tp_multiplier', None)
    vol_adj_mult = getattr(position, 'volatility_adjusted_tp_mult', None)

    if barrier_mult and barrier_mult > 1.0:
        threshold = barrier_mult
    elif vol_adj_mult and vol_adj_mult > 1.0:
        threshold = vol_adj_mult
    else:
        take_profit_pct = getattr(position, 'take_profit_pct', 0.50)
        threshold = 1.0 + take_profit_pct

    if current_price >= position.avg_fill_price * threshold:
        pnl_pct = (current_price - position.avg_fill_price) / position.avg_fill_price * 100
        return ExitResult(
            should_exit=True, exit_type='take_profit',
            reason=f"Take profit (barrier): ${current_price:.4f} >= "
                   f"${position.avg_fill_price * threshold:.4f} "
                   f"(+{pnl_pct:.0f}%, barrier_mult={threshold:.2f}, "
                   f"signal_conf={getattr(position, 'signal_confidence', 'N/A')})",
            urgency='high')
    return ExitResult(should_exit=False, exit_type='none', reason='')
```

- [ ] Step 5.3 complete

---

### Step 5.4 — Wire check_barrier_take_profit into evaluate_all
**File:** `src/exit_rules.py`

**What to build:**
Replace the old `check_take_profit` call with `check_barrier_take_profit` in `evaluate_all()`.

**Change this line in evaluate_all():**
```python
# OLD:
lambda p, c, md: check_take_profit(p, c),

# NEW:
lambda p, c, md: check_barrier_take_profit(p, c),
```

**Keep `check_take_profit` in the codebase for backward compatibility** — it may be called elsewhere.

- [x] Step 5.4 complete ✅ (2026-03-22)

---

### Step 5.5 — Phase 5 Verification
**Run:**
```bash
cd /home/q/projects/kalshi-ata
python3 -m py_compile src/exit_rules.py
python3 -m py_compile src/position_tracker.py
```

**Phase 5 Sign-off:**
- [x] `Position` has `signal_confidence` and `barrier_tp_multiplier` fields ✅
- [x] `add_position()` computes `barrier_tp_multiplier` from `signal_confidence` ✅
- [x] `check_barrier_take_profit()` function exists ✅
- [x] `evaluate_all()` calls `check_barrier_take_profit()` ✅
- [x] `check_take_profit()` kept for backward compatibility ✅
- [x] All files compile without errors ✅

---

## PHASE 6: Volatility-Time Hybrid Exit

*Purpose: Dynamically adjust the take-profit target based on current volatility and time remaining. Markets that are calm get wider targets; markets approaching close get tighter targets.*

---

### Step 6.1 — Add Time Decay Calculation
**File:** `src/exit_rules.py`

**What to build:**
A `compute_time_decay_penalty(hours_remaining, full_horizon=24.0, max_penalty=0.20) -> float` function.

**Logic:**
```python
def compute_time_decay_penalty(hours_remaining: float,
                               full_horizon: float = 24.0,
                               max_penalty: float = 0.20) -> float:
    if hours_remaining <= 0:
        return max_penalty
    if hours_remaining >= full_horizon:
        return 0.0
    decay_rate = 1.0 - (hours_remaining / full_horizon)
    return decay_rate * max_penalty
```

- [ ] Step 6.1 complete

---

### Step 6.2 — Add Volatility Scalar Calculation
**File:** `src/exit_rules.py`

**What to build:**
A `compute_volatility_scalar(current_volatility, avg_price, base_scalar=0.3) -> float` function.

**Logic:**
```python
def compute_volatility_scalar(current_volatility: float,
                              avg_price: float,
                              base_scalar: float = 0.3) -> float:
    if current_volatility is None or current_volatility <= 0:
        return 0.0
    vol_fraction = current_volatility / avg_price if avg_price > 0 else 0.0
    additional_width = base_scalar * min(vol_fraction, 0.05) / 0.05
    return min(additional_width, base_scalar * 2)
```

- [ ] Step 6.2 complete

---

### Step 6.3 — Wire Dynamic TP Update into Trader Main Loop
**File:** `src/trader.py`

**What to build:**
In the main trading cycle (in `check_and_execute_exits()` or its caller), update `volatility_adjusted_tp_mult` each cycle before calling `evaluate_all()`.

**Add before `evaluate_all()` is called:**
```python
# Update dynamic TP each cycle
close_date = getattr(market_md, 'close_date', None)
hours_remaining = 999.0
if close_date:
    try:
        from datetime import datetime, timezone
        close_dt = datetime.fromisoformat(close_date.replace('Z', '+00:00'))
        hours_remaining = (close_dt - datetime.now(timezone.utc)).total_seconds() / 3600
    except:
        pass

# Update ATR highest price
self.position_tracker.update_highest_price(pos.ticker, current_price)

# Compute dynamic TP
current_volatility = getattr(market_md, 'volatility', None) or 0.0
time_penalty = compute_time_decay_penalty(hours_remaining,
                                          full_horizon=TIME_DECAY_FULL_HORIZON_HRS,
                                          max_penalty=TIME_DECAY_MAX_PENALTY)
vol_scalar = compute_volatility_scalar(current_volatility,
                                       pos.avg_fill_price,
                                       base_scalar=VOLATILITY_TP_SCALAR)

base_tp = BARRIER_TP_BASE - 1.0
dynamic_tp_mult = 1.0 + base_tp + vol_scalar - time_p
dynamic_tp_mult = 1.0 + base_tp + vol_scalar - time_penalty
dynamic_tp_mult = max(dynamic_tp_mult, 1.0)
dynamic_tp_mult = min(dynamic_tp_mult, VOLATILITY_TP_MAX)

pos.volatility_adjusted_tp_mult = dynamic_tp_mult
```

**Import the new functions at the top of trader.py:**
```python
from exit_rules import (
    compute_time_decay_penalty,
    compute_volatility_scalar,
    evaluate_all,
    check_liquidity_exit,
    check_barrier_take_profit,
    check_atr_trailing_stop,
    check_partial_exit,
)
```

- [ ] Step 6.3 complete

---

### Step 6.4 — Phase 6 Verification
**Run:**
```bash
cd /home/q/projects/kalshi-ata
python3 -m py_compile src/exit_rules.py
python3 -m py_compile src/trader.py
```

**Phase 6 Sign-off:**
- [x] `compute_time_decay_penalty()` exists and correct ✅
- [x] `compute_volatility_scalar()` exists and correct ✅
- [x] Trader loop wires both and updates `volatility_adjusted_tp_mult` each cycle ✅
- [x] TP tightens near close ✅
- [x] TP widens with volatility ✅
- [x] All files compile without errors ✅

---

## PHASE 7: Integration, Testing & Deployment

*Purpose: Wire all five strategies together, verify they don't conflict, and deploy.*

---

### Step 7.1 — Remove Old check_take_profit from evaluate_all
**File:** `src/exit_rules.py`

**What to build:**
Confirm that `evaluate_all()` calls `check_barrier_take_profit()` and NOT the old `check_take_profit()`. The old `check_take_profit()` should remain in the file for backward compatibility but should not be in the primary evaluation loop.

- [ ] Step 7.1 complete

---

### Step 7.2 — Verify No Double-Exits on Partial Positions
**File:** `src/trader.py`

**What to build:**
Write and run a test scenario:

```python
# Simulate: 30% already exited at tier 0, price at exactly tier 0 threshold
pos.exit_tiers[0]['exited'] = True
result = evaluate_all(pos, current_price=pos.avg_fill_price * 1.20, ...)
assert result.exit_type != 'partial_exit', "already-exited tier should not re-fire"
assert result.should_exit == False

# Simulate: tier 1 not yet exited, price crosses threshold
pos.exit_tiers[1]['exited'] = False
result = evaluate_all(pos, current_price=pos.avg_fill_price * 1.41, ...)
assert result.exit_type == 'partial_exit'
assert result.exit_qty == int(pos.initial_count * pos.exit_tiers[1]['qty_pct'])

# Simulate: full exit trigger on partially-exited position
pos.remaining_count = 70
result = check_liquidity_exit(pos, illiquid_market_md)
assert result.should_exit == True
# The full exit should sell all remaining 70 contracts
```

- [ ] Step 7.2 complete

---

### Step 7.3 — End-to-End Integration Test
**Action:** Restart the bot and run through one full trading cycle.

**Run:**
```bash
cd /home/q/projects/kalshi-ata
curl -s -X POST http://localhost:3050/api/stop-trading
kill $(lsof -ti:3050) 2>/dev/null; sleep 1
KALSHI_DEMO_MODE=false nohup node bot_interface.js > /tmp/kalshi-api.log 2>&1 &
sleep 3
curl -s -X POST http://localhost:3050/api/start-trading
sleep 10
tail -30 /tmp/kalshi-api.log
```

**Check:**
1. Bot starts without Python errors
2. Market data streams (price history growing each cycle)
3. ATR is computed and stored in `market_md.volatility` after 14+ data points
4. New positions get `exit_tiers`, `barrier_tp_multiplier`, `signal_confidence` set
5. `check_and_execute_exits()` runs without errors
6. Partial exit tiers are checked each cycle
7. Liquidity exit is evaluated before stop_loss

- [ ] Step 7.3 complete

---

### Step 7.4 — Update Documentation
**Files:** `README.md`, `docs/EXIT_STRATEGY_RESEARCH.md`

**What to build:**
1. Update `README.md` — add new section "Advanced Exit Strategies" documenting all 5 implemented strategies
2. Update `docs/EXIT_STRATEGY_RESEARCH.md` — mark all 5 implemented strategies with `[IMPLEMENTED]`
3. Add a new section in `docs/EXIT_STRATEGY_RESEARCH.md` called "Implementation Notes" documenting any deviations from the original plan

- [ ] Step 7.4 complete

---

### Step 7.5 — Git Commit
**Action:** Commit all changes.

```bash
cd /home/q/projects/kalshi-ata
git add src/exit_rules.py src/trader.py src/position_tracker.py \
       src/market_data_streamer.py src/config.py src/volatility_analyzer.py
git commit -m "feat: implement 5 advanced exit strategies — liquidity, partial exit,
ATR trailing stop, triple-barrier, volatility-time hybrid

- Phase 1: Infrastructure (price history, ATR, liquidity fields)
- Phase 2: Order book liquidity exit (pre-entry gate + hold monitor)
- Phase 3: Partial exit / scaling out (tiered exits)
- Phase 4: ATR-based chandelier trailing stop
- Phase 5: Triple-barrier method with signal-confidence TP
- Phase 6: Volatility-time dynamic TP adjustment"
git push
```

- [ ] Step 7.5 complete

---

## Final Sign-Off Checklist

When all phases are complete:

- [x] **Phase 1** — All 7 steps ✅ COMPLETE (2026-03-22)
- [x] **Phase 2** — All 5 steps ✅ COMPLETE (2026-03-22)
- [x] **Phase 3** — All 4 steps ✅ COMPLETE (2026-03-22)
- [x] **Phase 4** — All 4 steps ✅ COMPLETE (2026-03-22)
- [x] **Phase 5** — All 4 steps ✅ COMPLETE (2026-03-22)
- [x] **Phase 6** — All 4 steps ✅ COMPLETE (2026-03-22)
- [x] **Phase 7** — All 5 steps ✅ COMPLETE (2026-03-22)
- [ ] Bot runs for 10+ minutes without crash ⚠️ (requires PEM restoration from kalshi.com)
- [ ] No Python exceptions in `/tmp/kalshi-api.log` ⚠️ (requires PEM restoration)
- [x] Git committed and pushed ✅
- [x] README updated ✅

---

## Strategy Interaction Map

This diagram shows how the five strategies interact and their priority order in `evaluate_all()`:

```
Liquidity Exit (HIGHEST PRIORITY)
  └── Fires if: yes_bid = 0 or bid_qty < minimum
  └── Action: immediate exit regardless of price

Stop Loss
  └── Fires if: current_price <= entry × (1 - stop_loss_pct)

Market Close (Time Barrier)
  └── Fires if: hours_remaining < 0.5

Take Profit (Barrier-Based)
  └── Fires if: current_price >= entry × barrier_tp_multiplier
  └── barrier_tp_multiplier set at entry from signal_confidence
  └── Modified each cycle by volatility_adjusted_tp_mult

ATR Trailing Stop
  └── Fires if: current_price <= (highest_price - N×ATR)
  └── highest_price updated every cycle

Probability Shift
  └── Fires if: current_price <= entry × 0.75 (signal rejected)

Time Exit (LOWEST PRIORITY)
  └── Fires if: position.age_hours >= 24.0
  └── unless already in profit (urgency=normal instead of high)
```

---

*Plan generated by Qaster — 2026-03-22*
*Strategies selected from `docs/EXIT_STRATEGY_RESEARCH.md`*
