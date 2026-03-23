# Advanced Profit-Taking & Exit Strategies for K-ATA
*Research compiled 2026-03-22 by Qaster*

---

## Context

The current K-ATA take-profit strategy is a **hardcoded +50% exit** (`take_profit_pct = 0.50`). This is static — it doesn't adapt to market volatility, liquidity conditions, or time remaining. The following documents alternative strategies used in professional algorithmic trading, ranked by relevance to K-ATA's prediction market context.

---

## Strategy 1: ATR-Based Trailing Stop (Chandelier Exit) [IMPLEMENTED]

**Origin:** Developed by Chuck LeBeau; popularized in systematic trading.

### The Idea
Instead of a fixed +50% take-profit, the stop rides under the highest price reached since entry, based on the Average True Range (ATR).

```
stop_level = highest_price_since_entry - (N × ATR)
```

### How It Works
- **On entry:** Record `entry_price` and `highest_price = entry_price`
- **Each cycle:** Update `highest_price = max(highest_price, current_price)`
- **Take profit fires when:** `highest_price - current_price >= N × ATR`
  - Or equivalently: `current_price <= highest_price - (N × ATR)`
- **N (ATR multiplier):** Typically 2–4× ATR depending on risk tolerance

### Why It's Better Than +50% Fixed
| Market Condition | Fixed +50% | ATR Trailing |
|---|---|---|
| Calm, trending slowly | Exits too early | Lets winner run |
| Volatile, large swings | Gets stopped out | Gives room to breathe |
| Gap up + immediate dump | Locks in less profit | Locks in more |

### ATR Calculation (for Kalshi binary markets)
```
TR = max(H - L, |H - close_prev|, |L - close_prev|)
ATR = rolling_mean(TR, period=14)
```

For binary markets (0–1 price range), the TR calculation needs to account for the dollar-scale prices. Use `yes_bid_dollars` / `yes_ask_dollars` as H/L.

### Relevant K-ATA Data
- `market_data_streamer.py` already fetches `yes_bid_dollars`, `yes_ask_dollars`
- Historical data may be needed for true ATR; or use a fixed volatility estimate per market type

### Pseudocode
```python
class ATRTrailingStop:
    def __init__(self, atr_multiplier=3.0, atr_period=14):
        self.atr_multiplier = atr_multiplier
        self.atr_period = atr_period
        self.highest_price = None
        self.atr = None

    def on_entry(self, price, atr):
        self.highest_price = price
        self.atr = atr

    def check(self, current_price) -> (bool, reason):
        self.highest_price = max(self.highest_price, current_price)
        stop_level = self.highest_price - (self.atr_multiplier * self.atr)
        if current_price <= stop_level:
            return True, f"ATR trailing stop: {current_price:.4f} <= {stop_level:.4f}"
        return False, ""

    def adjust_take_profit(self, current_price):
        # Optional: dynamically raise TP as price rises
        pass
```

---

## Strategy 2: Triple-Barrier Method (Marcos López de Prado) [IMPLEMENTED]

**Origin:** Marcos López de Prado, *Advances in Financial Machine Learning* (2018).

### The Idea
Three independent barriers — stop loss, take profit, and maximum holding period — all checked simultaneously. Whichever barrier hits first determines the exit. This is the formal academic standard for algo trading exit management.

### The Three Barriers
| Barrier | Trigger | K-ATA Equivalent |
|---|---|---|
| **Stop Loss** | Price moves against you by X% | existing stop_loss (-40%) |
| **Take Profit** | Price moves in your favor by Y% | existing take_profit (+50%) |
| **Time** | Position held > N periods | existing time_exit (24h) |

### Why It's Better
- Stop loss, take profit, and time all compete **properly** instead of hardcoded priority order
- Enables **asymmetric exits** — different strategies can weight barriers differently
- Naturally supports **partial exits** — if TP hits first on half the position, close half and let the rest ride to the next barrier

### K-ATA Implementation
Current priority order is: `stop_loss → market_close → take_profit → prob_shift → time_exit`

Triple-barrier makes this cleaner — all barriers are checked every cycle, and the first one to fire determines the exit. No hardcoded ordering.

### Key Insight from de Prado
> "The triple-barrier defines the take profit and stop loss levels upfront which is the odds."

This means the TP and SL should be set based on the **signal confidence** at entry, not a fixed number. A high-confidence signal (e.g., strong news sentiment) might warrant a wider TP barrier. A low-confidence signal gets a tighter one.

---

## Strategy 3: Partial Exit / Scaling Out [IMPLEMENTED]

**Used by:** Virtually all professional market makers and swing traders.

### The Idea
Don't exit the entire position at once. Exit in tiers as price moves in your favor.

### Example Tiered Exit (100 contracts)
| Price Level | Contracts to Exit | Cumulative P&L Locked |
|---|---|---|
| Entry + 20% | 30 contracts | +6% on 30% of position |
| Entry + 40% | 30 contracts | +18% on 60% of position |
| Entry + 60% | 20 contracts | +28% on 80% of position |
| Trailing stop | remaining 20 | variable |

### Why It's Better
- Locks in profit incrementally — if market reverses after first exit, you're already hedged
- Reduces exposure near market resolution (prediction markets get choppy in final hours)
- Lets you "let winners run" with a reduced position

### For Kalshi Specifically
Prediction markets often:
1. Move toward 0.50–0.70 range as event approaches
2. Spike or dump in final hours on late information
3. Have zero bid depth near close (making large exits impossible)

Partial exits throughout the day would systematically reduce K-ATA's exposure before the final chaotic window.

### K-ATA Implementation
Modify `_execute_sell` to take a `count` parameter:
```python
def partial_exit(ticker, count_to_exit, current_price):
    # Sell only count_to_exit, not full position
    # Record the exit in position tracker
    # Keep remainder open with updated avg_fill_price unchanged
```

---

## Strategy 4: Supertrend-Based Exit

**Used by:** Trend-following systematic traders; popular in crypto and equities.

### The Idea
Supertrend = (HL2 × ATR multiplier). Exit when price crosses below the Supertrend line.

```
Supertrend = HL2 - (period × ATR)
HL2 = (High + Low) / 2
```

When price is above Supertrend → stay in.
When price crosses below Supertrend → exit.

### Why It's Better Than +50% Fixed
- It's **trend-following, not price-following**
- In a trending prediction market (e.g., "Will X happen?" trending YES as news flows in), Supertrend keeps you in longer than a fixed TP
- Exits cleanly when the trend breaks — captures more of the move than a time-based exit would

### For Kalshi
Complements the news sentiment strategy directly:
- News sentiment drove the entry signal
- Supertrend keeps you in while the narrative holds
- Exit when the narrative breaks (price crosses below Supertrend)

### K-ATA Implementation
Needs rolling high/low data. For binary markets using tick data:
```python
def supertrend(highs, lows, period=10, multiplier=3.0):
    hl2 = (highs + lows) / 2
    tr = rolling_true_range(highs, lows, period)
    atr = tr.rolling(period).mean()
    upper = hl2 + (multiplier * atr)
    lower = hl2 - (multiplier * atr)
    # Supertrend line oscillates between upper and lower bands
    return upper, lower
```

---

## Strategy 5: Volatility-Time Hybrid Exit [IMPLEMENTED]

**Used by:** Volatility-adaptive trading systems.

### The Idea
Take profit target dynamically adjusts based on:
- **Current volatility** → wider when volatile
- **Time remaining** → tighter as market close approaches

```
dynamic_TP = entry × (1 + base_tp_pct + volatility_scalar × current_volatility - time_decay)
```

### Why It's Better
As markets approach close, they get choppy — late-breaking information, thin books, erratic pricing. A fixed +50% in the final hour before resolution can be too aggressive. This auto-tightens the target near close, getting you out before the noise.

### For Kalshi Specifically
Prediction markets are most volatile in final hours before resolution. This would automatically:
- Be more conservative in the final 2–3 hours
- Allow wider profit targets when market has 24+ hours to move
- Capture mean-reversion opportunities in stable markets

### Implementation
```python
def dynamic_take_profit(entry_price, hours_remaining, base_tp=0.50, vol_scalar=0.3):
    # Time decay: increases near close (0 at 24h, higher at 1h)
    time_decay = max(0, 1 - (hours_remaining / 24)) * 0.2
    
    # Volatility: estimated from recent price movement
    vol = estimate_volatility(market_ticker)
    
    tp_mult = 1 + base_tp + (vol_scalar * vol) - time_decay
    return entry_price * tp_mult
```

---

## Strategy 6: Order Book Liquidity Exit

**Used by:** Professional market makers; directly relevant to K-ATA's zombie position problem.

### The Idea
Monitor bid-ask spread and order book depth. When liquidity dries up (bids disappear, spread widens), exit immediately regardless of price target.

**The core rule:**
```python
MIN_BID_DEPTH = 50  # minimum contracts at best bid

if market_md.yes_bid_quantity < MIN_BID_DEPTH:
    exit_now("liquidity dry-up: insufficient bid depth")
```

### Why It Would Have Saved K-ATA's Zombie Positions
The zombie positions were in markets with `yes_bid_dollars = $0.00` — literally zero bids. If K-ATA had been monitoring `yes_bid_dollars` before entry and during holding, it would have:
1. **Rejected entry** when bid depth was zero
2. **Exited early** when bid depth started to disappear

This is the single most directly applicable strategy for K-ATA's market profile.

### K-ATA Implementation
`market_data_streamer.py` already tracks `yes_bid_dollars`. Add a liquidity check:
```python
def is_market_liquid(market_md, min_bid_dollars=0.05, min_bid_qty=10):
    bid = getattr(market_md, 'yes_bid_dollars', 0) or 0
    ask = getattr(market_md, 'yes_ask_dollars', 0) or 0
    bid_qty = getattr(market_md, 'yes_bid_qty', 0) or 0
    spread = abs(ask - bid) / bid if bid > 0 else float('inf')
    return bid >= min_bid_dollars and bid_qty >= min_bid_qty and spread < 0.15
```

---

## Strategy 7: Tick Scalping Exit (Spread Capture)

**Used for:** Highly liquid markets with tight spreads (crypto, liquid equities, some Kalshi political markets).

### The Idea
In markets with very tight spreads, place a buy and immediately place a sell at a small profit target (+1–3 ticks). Use a tight trailing stop to capture spread income systematically.

### For Kalshi
Binary markets at $0.50 with $0.01 spread:
- Buy @ $0.50
- Sell @ $0.51 (2% return, 2 ticks)
- Repeat systematically

This is essentially **positive theta** — you're collecting small premiums on each trade, similar to a market maker. Works best in high-volume, tight-spread political markets.

### Implementation Notes
- Requires the market to have genuine two-sided interest
- Spread must be tight enough that the exit sell can execute without significant slippage
- Not applicable to esports markets (spread was $0.04–$1.00+)

---

## Summary: Recommended Priority for K-ATA

| Priority | Strategy | Impact | Effort | Why |
|---|---|---|---|---|
| **1** | Order Book Liquidity Exit | 🔴 High | Easy | Would have prevented zombie positions entirely |
| **2** | Partial Exit (Scale Out) | 🔴 High | Easy | Locks profit incrementally, reduces resolution risk |
| **3** | ATR-Based Trailing Stop | 🟡 Medium | Medium | Replaces dumb +50% with volatility-adaptive stop |
| **4** | Triple-Barrier Time Decay | 🟡 Medium | Medium | Auto-tightens TP as market close approaches |
| **5** | Supertrend Exit | 🟢 Medium | Medium | Good for news-sentiment trend-following |
| **6** | Volatility-Time Hybrid | 🟢 Medium | Medium | Generalizes across market conditions |
| **7** | Tick Scalping Layer | 🟢 Low | Medium | Niche; only works on tight-spread markets |

---

## Implementation Notes

### Data Requirements Per Strategy
| Strategy | Needs Historical Data? | Available in K-ATA? |
|---|---|---|
| ATR Trailing | Yes (rolling high/low) | Partial (current price only) |
| Triple-Barrier | No | Yes |
| Partial Exit | No | Yes |
| Supertrend | Yes (rolling high/low) | Partial |
| Volatility-Time | Yes (rolling vol) | Partial |
| Liquidity Exit | No | Yes |
| Tick Scalping | No | Yes |

### Key Files to Modify
- `src/exit_rules.py` — add new exit trigger functions
- `src/trader.py` — `_execute_sell` for partial exits
- `src/market_data_streamer.py` — track `yes_bid_qty` if available
- `src/config.py` — add new configurable parameters

### Backtesting Note
All of these strategies should be backtested against historical Kalshi data before deployment. The `docs/EXIT_LIQUIDITY_RESEARCH.md` file contains prior work on this topic.

---

## Qaster's Top 5 Picks for K-ATA

*Selected 2026-03-22 — ranked by fit for K-ATA's specific prediction market profile*

---

### ✅ 1. Order Book Liquidity Exit

**Why:** This is the highest-impact one for Kalshi. K-ATA got stuck in zombie positions *because it never checked if anyone would actually buy back the contracts*. This is a pre-entry gate AND a hold-period monitor. Checks a box that no other strategy covers.

---

### ✅ 2. Partial Exit / Scaling Out

**Why:** Prediction markets are binary resolution instruments — they tend to get chaotic in the final hours. Scaling out systematically (sell 30% at +20%, another 30% at +40%, let 40% ride) dramatically reduces exposure to that final-hour noise. Simple to implement, massive P&L variance reduction.

---

### ✅ 3. ATR-Based Trailing Stop (Chandelier Exit)

**Why:** This replaces the blunt +50% with something intelligent. An esports market that moves $0.01/hour needs a different exit than a political market moving $0.20/hour. ATR adapts to the market's actual behavior. It's the single best replacement for the current dumb take-profit.

---

### ✅ 4. Triple-Barrier Method

**Why:** It formalizes the interaction between stop loss, take profit, and time. Right now they fire in a hardcoded priority order — stop_loss always beats take_profit always beats time_exit. Triple-barrier lets them compete equally based on which actually hits first. More importantly, it allows the TP barrier to be set *at entry based on signal confidence*, not a fixed number.

---

### ✅ 5. Volatility-Time Hybrid Exit

**Why:** Prediction markets behave very differently at hour 1 vs. hour 23. This strategy automatically widens profit targets when markets are calm and tightens them as the chaos window approaches. It pairs naturally with the ATR trailing stop (#3) and fills the time-dimension gap that ATR doesn't cover.

---

### ❌ Not Chosen

**Tick Scalping (#7)** — requires tight bid-ask spreads (~$0.01). Kalshi esports markets had $0.04+ spreads, political markets may be better but the volume isn't there. Niche use case, not a general K-ATA strategy.

**Supertrend** — solid strategy, but it overlaps conceptually with ATR trailing stop and needs rolling high/low data that K-ATA doesn't reliably have. It would be the first add-on if market data storage is ever expanded.

---

## Implementation Notes (2026-03-22)

All 5 selected strategies were implemented across 7 phases (2026-03-21 to 2026-03-22):

### Deviations from Original Plan
- **Liquidity Exit** was added as a mandatory pre-entry gate (Phase 2) before the selected 5 strategies, because the zombie position incident proved it must be checked before order placement
- **Order Book Liquidity Exit** is NOT in this research file — it was identified from the 2026-03-21 post-mortem as the #1 priority
- **Partial Exit** was added to Phase 3 (not Phase 5 as might be assumed from López de Prado's original framework) because it required the fewest dependencies

### Key Implementation Details
- ATR uses Wilder's EMA method (not simple mean) for responsiveness
- `barrier_tp_multiplier` is computed from `signal_confidence` at entry: `1.50 + 0.50 × conf`
- `compute_time_decay_penalty` and `compute_volatility_scalar` run each trading cycle in `check_and_execute_exits()`
- Partial exit tiers: 30% at 1.20×, 30% at 1.40×, 20% at 1.60× (80% total, 20% runs to barrier TP)
- `remaining_count` vs `count`: `count` = cost basis (never changes for P&L), `remaining_count` = contracts still held (decremented by partial exits)
- Liquidity exit has HIGHEST priority in `evaluate_all()` — fires before stop_loss

### Files Modified
- `src/exit_rules.py` — 6 exit rule functions + 2 helper calculators
- `src/position_tracker.py` — 10 new Position fields, 4 new methods
- `src/trader.py` — liquidity gate in execute_trade(), dynamic TP in main loop
- `src/market_data_streamer.py` — price history, ATR wiring, liquidity fields
- `src/config.py` — 14 new parameters
- `src/volatility_analyzer.py` — calculate_atr() using Wilder EMA
