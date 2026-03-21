# K-ATA Research Report: Exit Logic, Liquidity Filtering & Market Selection

**Compiled:** 2026-03-21  
**Context:** K-ATA trading bot on Kalshi — adding sell/exit logic, liquidity checks, and market selection

---

## PART 1: SELL / EXIT LOGIC

### What the Open-Source World Uses

Across Polymarket bots, crypto trading frameworks (Jesse), and general trading systems, exit logic falls into **four categories**:

---

### 1.1 — Time-Based Exit

**The most common approach.** The market has a known close date. You exit when a certain % of the market's life has elapsed.

**From Krypto-Hashers' Polymarket bot** (`Krypto-Hashers-Community/polymarket-trading-bot`):
```python
# If market is about to close, sell at market price
close_date = market['close_date']
time_remaining = (close_date - now).total_seconds()
total_duration = (close_date - start_date).total_seconds()
elapsed_pct = 1 - (time_remaining / total_duration)

# Sell when 95% of market time has passed
if elapsed_pct > 0.95:
    # Place market sell order (FAK — Fill-And-Kill)
    # Ensures you exit before it settles
```

**From discountry/polymarket-trading-bot:**
```python
# Market close time check
if market.get('is_closed'):
    # Market has settled — all positions should be zero already
    pass
elif market.get('question_end_date'):
    # Exit if less than X hours remain
    remaining = market['question_end_date'] - datetime.now()
    if remaining.total_seconds() < 3600:  # 1 hour
        self.close_all_positions()
```

**Key insight:** Prediction markets have a defined end date. This is the cleanest exit trigger — you know when the market resolves. The bot should set a **time-to-settlement guard**: if < 1 hour remains, don't open new positions and begin closing existing ones.

---

### 1.2 — PnL-Based Exit (Take-Profit / Stop-Loss)

**Standard in every serious trading system.** From the Jesse crypto trading framework (`jesse-ai/jesse`):

```python
class GoldenCross(Strategy):
    def go_long(self):
        entry_price = self.price - 10
        qty = utils.size_to_qty(self.balance * 0.05, entry_price)  # 5% of capital
        self.buy = qty, entry_price
        self.take_profit = qty, entry_price * 1.20   # exit at +20%
        self.stop_loss = qty, entry_price * 0.90     # exit at -10%
```

**For Kalshi prediction markets, this translates to:**

```python
entry_price = position['avg_fill_price']  # e.g., $0.04
take_profit_price = entry_price * 1.5     # +50% — exit if contract rises to $0.06
stop_loss_price = entry_price * 0.60     # -40% — exit if contract drops to $0.024

if current_market_price >= take_profit_price:
    # SELL to lock in profit
elif current_market_price <= stop_loss_price:
    # SELL to prevent further loss
```

**Critical nuance for Kalshi:** Prices are denominated 0-1 (or 0-$1), not like crypto. A $0.04 contract settling at $1.00 = **25x return**. Stop losses need to be calibrated in terms of probability, not price. If a contract is at $0.04, the market is saying ~4% probability. A stop at $0.024 means you're exiting if probability drops further (the bet is becoming less likely). That's a valid stop — you're wrong and getting out.

---

### 1.3 — Market-Probability Exit (Distance-from-50%)

**From Krypto-Hashers bot** — this is specifically for prediction markets:

```python
# If price is far from 50%, the market has conviction
# If price crosses below your entry by X%, exit
# If price is too certain (near $1.00), take profit

current_price = float(market['yes_price'])
entry_price = position['avg_fill_price']

# If probability has moved significantly against you (>20% move from entry)
if current_price < entry_price * 0.80:
    # Sell to limit losses — probability moved against you
    self.sell_at_market(position)

# If probability has moved in your favor, take partial profit
if current_price > entry_price * 1.50:
    # Sell 50% of position to lock in gains
    self.sell_partial(position, count=position['count'] // 2)
```

**Key insight:** In prediction markets, the "stop loss" isn't a price floor — it's a **probability threshold**. You're not saying "sell if price < $0.024" — you're saying "sell if the implied probability of the event happening has dropped below X%."

---

### 1.4 — Fill-And-Kill (FAK) for Quick Exit

**For when you need to exit NOW, not wait for a buyer.** From Gabagool2-2's Polymarket bot:

```python
# Use FAK orders to immediately exit if price moves against you
# FAK = Fill-And-Kill — tries to fill immediately, cancels if not filled

order = self.client.create_order(
    market_id=market['id'],
    side='sell',
    size=position_size,
    type='FILL_OR_KILL',  # Immediate fill or cancel
    price=current_price * 0.95  # Accept slight discount to ensure fill
)
```

For Kalshi, this would be placing a sell order with `time_in_force: "fill_or_kill"` — critical for exiting thin markets where your order might sit otherwise.

---

### 1.5 — Position Age + Decay Exit

**For fading positions that just don't move:**

```python
# Exit if position has been open for too long without profit
position_age_hours = (now - position['open_time']).total_seconds() / 3600

if position_age_hours > 24 and position['unrealized_pnl'] < 0:
    # Market isn't moving in your favor after 24 hours — exit
    self.close_position(position)
```

---

## PART 2: LIQUIDITY FILTERING

### The Problem K-ATA Hit

Every Kalshi market has a bid-ask spread. When `yes_bid = $0.00` and `yes_ask = $1.00`, you cannot trade at any reasonable price. The bot was attempting to place orders in markets with zero liquidity, burning API calls and generating no fills.

### What Sophisticated Bots Do

---

### 2.1 — Bid-Ask Spread Filter

**The most basic check.** From the discountry Polymarket bot:

```python
def is_market_liquid(market):
    yes_bid = float(market.get('yes_bid', 0))
    yes_ask = float(market.get('yes_ask', 1))
    
    if yes_bid == 0 or yes_ask == 0:
        return False  # No quotes available
    
    spread = yes_ask - yes_bid
    spread_pct = spread / yes_ask  # Relative spread
    
    # Reject markets with >10% spread (very wide)
    if spread_pct > 0.10:
        return False
    
    return True
```

**For K-ATA, this translates to:**
```python
def is_market_liquid(market):
    yes_bid = market.get('yes_bid_dollars') or market.get('yes_bid', 0)
    yes_ask = market.get('yes_ask_dollars') or market.get('yes_ask', 1)
    
    if not yes_bid or not yes_ask:
        return False
    
    try:
        bid_f = float(yes_bid)
        ask_f = float(yes_ask)
    except (ValueError, TypeError):
        return False
    
    if bid_f == 0 or ask_f == 0:
        return False  # Can't trade with zero on one side
    
    spread = ask_f - bid_f
    spread_pct = spread / ask_f if ask_f > 0 else 1.0
    
    MAX_SPREAD_PCT = 0.15  # 15% max spread
    if spread_pct > MAX_SPREAD_PCT:
        return False
    
    return True
```

---

### 2.2 — Minimum Bid Depth Filter

**Check not just bid, but bid SIZE.** A market might have a bid of $0.05 but only 1 contract at that price. You can't sell 411 contracts at $0.05.

```python
def get_market_depth(market, depth=10):
    """
    Fetch order book and return total bid volume up to `depth` levels.
    """
    orderbook = self.client.get_order_book(market['id'], depth=depth)
    
    total_bid_qty = sum(bid['size'] for bid in orderbook.get('bids', []))
    total_ask_qty = sum(ask['size'] for ask in orderbook.get('asks', []))
    
    return {
        'bid_qty': total_bid_qty,
        'ask_qty': total_ask_qty,
        'top_bid': orderbook['bids'][0]['price'] if orderbook.get('bids') else 0,
        'top_ask': orderbook['asks'][0]['price'] if orderbook.get('asks') else 0,
    }

def can_exit_position(position, market, required_qty):
    depth = get_market_depth(market)
    
    if depth['bid_qty'] < required_qty:
        # Can't exit full position at current bid — either wait or reduce
        return False
    
    # Check if you can exit at a reasonable price (within 10% of mid)
    mid = (depth['top_bid'] + depth['top_ask']) / 2
    estimated_exit = depth['top_bid']  # You'll sell at the bid
    acceptable_loss = mid * 0.10  # Accept up to 10% slippage from mid
    
    if estimated_exit < mid - acceptable_loss:
        return False  # Slippage too large
    
    return True
```

**Kalshi-specific:** The orderbook endpoint returns `yes_bid_qty` and `no_bid_qty` for each level. Check if the top bid's quantity can cover your position.

---

### 2.3 — Volume Filter

**Don't trade markets that aren't actively traded.** From Jesse framework:

```python
# Only trade markets with sufficient volume
min_daily_volume = 1000  # contracts

if market['volume_24h'] < min_daily_volume:
    skip_market("Insufficient 24h volume")
```

For Kalshi, check `volume_24h` from the market data — reject anything below a threshold (e.g., $100 notional volume).

---

### 2.4 — Open Interest Filter

**For prediction markets: how much money is actually at stake?** Markets with zero open interest have no liquidity providers.

```python
def has_open_interest(market, min_oi=500):
    """
    Check if market has meaningful open interest (total contracts outstanding).
    """
    oi = market.get('open_interest', 0)
    try:
        oi_f = float(oi) if not isinstance(oi, str) else 0
    except (ValueError, TypeError):
        oi_f = 0
    
    return oi_f >= min_oi
```

---

### 2.5 — Combined Liquidity Score

**The sophisticated approach: combine multiple signals into a single score.**

```python
def liquidity_score(market):
    score = 0.0
    
    # Factor 1: Bid-ask spread (0-40 points)
    spread = (ask - bid) / ask
    score += max(0, 40 - spread * 400)
    
    # Factor 2: Top-of-book depth (0-30 points)
    top_bid_qty = orderbook['bids'][0]['size']
    score += min(30, top_bid_qty / 10)  # 1 point per 10 contracts at top
    
    # Factor 3: 24h volume (0-30 points)
    vol = market.get('volume_24h', 0)
    score += min(30, vol / 100)
    
    return score  # Higher = more liquid

# Only trade markets with score > 50
if liquidity_score(market) < 50:
    skip_market("Low liquidity score")
```

---

## PART 3: LIQUID MARKET SELECTION

### What Makes a Market Worth Trading

The goal is to find markets that are: (1) liquid enough to enter and exit, (2) have enough time remaining, (3) aren't too certain or too uncertain (the edge is in the middle).

---

### 3.1 — Probability Distance Filter (The "Sweet Spot")

**The key insight from quantitative prediction market trading:** The most profitable trades happen in markets where the probability is between 20-80%. If a market is at 95%, there's almost no edge left. If it's at 5%, you're fighting the consensus.

From Krypto-Hashers:
```python
def probability_sweet_spot(market):
    """
    Only trade markets where probability is between 20% and 80%.
    This is where the edge is — markets that haven't fully resolved in price.
    """
    current_price = float(market['yes_price'])
    
    if current_price < 0.20:
        return False, "Too unlikely — fighting consensus"
    if current_price > 0.80:
        return False, "Too certain — no edge left"
    
    return True, "In sweet spot"
```

**For K-ATA:** Only enter markets where the signal agrees with probability in the 25-75% range. If news sentiment is bullish but the market is already pricing 90% probability, don't buy — the market already knows.

---

### 3.2 — Time Remaining Filter

**Don't buy into markets that are about to close.** From both Polymarket bots:

```python
def time_remaining_filter(market, min_hours=4, max_pct_elapsed=0.90):
    """
    Skip markets that are about to close or already past peak liquidity.
    """
    close_date = parse_iso_date(market['close_date'])
    now = datetime.now(timezone.utc)
    
    hours_remaining = (close_date - now).total_seconds() / 3600
    
    if hours_remaining < min_hours:
        return False, f"Less than {min_hours}h remaining"
    
    # Calculate what % of market life has elapsed
    total_life_hours = (close_date - market['open_date']).total_seconds() / 3600
    pct_elapsed = 1 - (hours_remaining / total_life_hours)
    
    if pct_elapsed > max_pct_elapsed:
        return False, f"Market {pct_elapsed*100:.0f}% complete — too late to enter"
    
    return True, f"{hours_remaining:.1f}h remaining"
```

**For K-ATA:** Markets with < 2 hours remaining should be skipped for new entries. Markets with < 30 minutes should trigger exit of existing positions.

---

### 3.3 — Spread-Ranked Market Selection

**From market making theory (Avellaneda-Stoikov):** When selecting which markets to make in, rank by spread. Markets with wider spreads give more edge, but only if there's enough volume.

```python
def rank_markets_by_quality(markets):
    """
    Score each market and return sorted by quality.
    """
    scored = []
    
    for m in markets:
        score = 0
        reasons = []
        
        # 1. Spread score (wider spread = more potential edge)
        bid = float(m.get('yes_bid_dollars') or 0)
        ask = float(m.get('yes_ask_dollars') or 1)
        if bid > 0 and ask > 0:
            spread = (ask - bid) / ask
            score += (1 - spread) * 40  # Higher score for tighter spreads
        else:
            score -= 100  # Heavy penalty for zero liquidity
            reasons.append("no quotes")
        
        # 2. Volume score
        vol = m.get('volume_24h', 0)
        score += min(30, vol / 100)
        
        # 3. Probability sweet spot
        price = float(m.get('last_price_dollars') or m.get('yes_bid_dollars') or 0.5)
        if 0.25 <= price <= 0.75:
            score += 20  # Bonus for being in sweet spot
        else:
            score -= 5
        
        # 4. Time remaining
        hours_left = hours_remaining(m)
        if hours_left < 2:
            score -= 50  # Too close to close
        elif hours_left < 24:
            score -= 10
        else:
            score += 10  # Bonus for having time
        
        scored.append((score, m, reasons))
    
    # Sort descending — highest score first
    scored.sort(reverse=True)
    return scored
```

---

### 3.4 — Category/Venue Filter

**Polymarket's most successful bots** (from GitHub analysis):

```python
# Filter by category — some categories are more liquid than others
LIQUID_CATEGORIES = [
    'politics',
    'sports',
    'economics',
]

def filter_by_category(market):
    cat = market.get('category', '').lower()
    return any(c in cat for c in LIQUID_CATEGORIES)

# Filter by minimum creator volume (established markets only)
def filter_by_creator(market):
    creator = market.get('creator', '')
    return creator not in LOW_VOLUME_CREATORS
```

For Kalshi: stick to major event categories (sports, politics, economics). Avoid niche/micro markets with thin books.

---

### 3.5 — Implied Probability vs. Signal Confirmation

**The key strategic filter:** Your signal might say "bullish" but if the market already prices probability at 85%, buying is not an edge — it's just following the crowd.

```python
def signal_market_alignment(signal_score, market_price):
    """
    Check if our signal agrees with the market's implied probability.
    
    If signal is bullish (+0.15) but market is at 0.85 (85% likely),
    the market has already priced in the event. Our signal is stale.
    
    If signal is bullish (+0.15) and market is at 0.45 (45% likely),
    there's room for the market to move. We have an edge.
    """
    market_probability = market_price
    
    # Calculate expected move based on signal
    # If sentiment = 0.15, we expect probability to move to ~0.60
    expected_probability = min(0.95, market_probability + signal_score * 2)
    
    # Edge = difference between expected and current
    edge = expected_probability - market_probability
    
    if edge < 0.05:  # Less than 5% expected move
        return False, f"No edge — market already at {market_probability:.0%}, expected {expected_probability:.0%}"
    
    return True, f"Edge of {edge:.0%} (market {market_probability:.0%} → expected {expected_probability:.0%})"
```

---

## PART 4: THE INTEGRATED EXECUTION PIPELINE

### How It All Fits Together

Based on the research, here's the complete pre-trade and post-trade flow for K-ATA:

```
EVERY CYCLE (60 seconds):
│
├── PRE-TRADE FILTER (skip if any fail)
│   ├── Market is open (status != closed)
│   ├── Time remaining > 2 hours
│   ├── Market < 90% complete (pct_elapsed < 0.90)
│   ├── Probability in sweet spot (0.20 < price < 0.80)
│   ├── Market is liquid (spread < 15%, bid_qty > position_size)
│   ├── Volume 24h > minimum threshold
│   └── Signal-market alignment check (edge > 5%)
│
├── POSITION MANAGEMENT (for existing positions)
│   ├── Check each open position every cycle
│   ├── Evaluate: time_remaining, current_price, entry_price
│   │
│   ├── EXIT TRIGGERS (check in order):
│   │   ├── 1. Take profit: price >= entry * 1.50
│   │   ├── 2. Stop loss: price <= entry * 0.60
│   │   ├── 3. Time exit: < 30 minutes remaining
│   │   ├── 4. Probability shift: market now implies wrong outcome
│   │   └── 5. Soft timeout: > 24 hours with no profit
│   │
│   └── EXIT METHOD:
│       ├── If liquid: place limit sell at bid
│       ├── If thin (bid_qty < position_size): scale down partially
│       └── If very thin: use FAK at small discount
│
└── NEW TRADES
    ├── Only if no positions or position count below threshold
    ├── Only in markets passing all pre-trade filters
    └── Size based on Kelly with liquidity cap (200 max)
```

---

## PART 5: IMPLEMENTATION PRIORITY

### What to Build First (for K-ATA)

**Priority 1 — Liquidity Filter (Critical)**
The bot is currently trying to trade zero-liquidity markets. This must be fixed immediately:
- `is_market_liquid()` — reject if bid=0 or spread > 15%
- `has_minimum_depth()` — reject if top bid qty < position size you're trying to place

**Priority 2 — Sell/Exit Logic (Critical)**
The bot has no way to close positions:
- `close_position()` — place sell order for existing position
- `should_take_profit()` — check if price above threshold
- `should_stop_loss()` — check if price below threshold

**Priority 3 — Market Selection (High Value)**
- `is_in_sweet_spot()` — reject if probability > 80% or < 20%
- `time_remaining_filter()` — reject if < 2 hours
- `signal_market_alignment()` — reject if market already priced in the move

**Priority 4 — PnL Tracking & Reporting**
- Track unrealized P&L per position
- Log all exit decisions with reasoning
- Better Telegram alerts on exit

---

## KEY SOURCES

1. **discountrys/polymarket-trading-bot** — Simple but effective Polymarket bot with market selection
2. **Krypto-Hashers-Community/polymarket-trading-bot** — exit_time_ratio, exit_price_ratio, probability-based exits
3. **Gabagool2-2/polymarket-trading-bot-python** — FAK orders, market maker approach
4. **jesse-ai/jesse** — Professional crypto trading framework with take-profit/stop-loss pattern
5. **Patrick-code-Bot/nautilus_AItrader** — DeepSeek AI strategy with position management
6. **Mo-Khalifa96/Forex-Trading-Bot** — MetaTrader 5 with multi-stage take profit
7. **areed1192/python-trading-robot** — TD Ameritrade with OCO orders (SL/TP pair)
8. **Avellaneda-Stoikov market making model** — Academic basis for spread-based market making
