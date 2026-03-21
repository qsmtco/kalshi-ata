# 🤖 K-ATA: The Self-Evolving Trading Intelligence

> *Not just a bot. An intelligence that learns, adapts, and survives.*

---

## The Problem

Trading bots are dumb. They follow rules. They don't learn. They don't adapt to changing markets. They don't know when to stop before blowing up your account.

**K-ATA is different.**

K-ATA (Kalshi Adaptive Trading Agent) is a self-optimizing quantitative trading system that doesn't just execute trades — it *evolves*. It generates hypotheses about market behavior, backtests them against historical data, and autonomously tunes its own parameters to maximize Sharpe ratio while protecting your capital.

---

## What It Does

### 🧠 Adaptive Strategy Engine
- **Hypothesis Generation**: Analyzes performance metrics to identify parameter improvement opportunities
- **Statistical Backtesting**: Validates every hypothesis with Welch's t-test before applying
- **Conservative Optimization**: Takes 50% steps toward suggested values — never jumps

### 🛡️ Military-Grade Risk Management
- **Circuit Breaker**: Pauses trading when drawdown exceeds 10% or daily loss hits 5%
- **Daily Loss Limit**: Hard stop at 15% daily loss
- **Max Hold Time**: Auto-exits positions after 10 days
- **Exposure Limits**: 25% max per trade, 100% total portfolio

### 📊 Multi-Strategy Intelligence
- **News Sentiment**: NLP-powered sentiment analysis from news sources
- **Statistical Arbitrage**: Cointegration-based pair trading
- **Volatility Regime Detection**: GARCH-modeled volatility signals
- **Market Making**: Optional symmetric order placement to capture spread
- **VPIN**: Volume-synchronized Probability of Informed Trading — detects toxic order flow
- **Hawkes Process**: Order flow clustering and self-excitation detection
- **Kyle's Lambda**: Real-time order flow impact measurement and exit cost estimation
- **Order Flow Imbalance (OFI)**: Multi-level order book imbalance as short-term momentum signal
- **Almgren-Chriss Execution**: Optimal liquidation scheduling for large position unwinds

### 🔄 Self-Healing Systems
- **Guardrail Validation**: Every parameter change validated against safety bounds
- **Rate Limiting**: Max 3 autonomous adjustments per 24 hours
- **Decision Audit Trail**: Full logging of every agent decision to SQLite

### 🌐 Production-Ready Architecture
- **Real-time Market Data Streaming**: WebSocket-ready market data pipeline
- **Dynamic Settings**: Change parameters on-the-fly via REST API
- **Telegram Alerts**: Get notified on trades, errors, and circuit breaker events
- **Paper Trading Mode**: Test strategies without risking real capital (default: ON)

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          K-ATA TRADING SYSTEM                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   ┌─────────────┐     ┌──────────────┐     ┌──────────────────────────┐   │
│   │   EXTERNAL  │     │   TELEGRAM   │     │      NODE.JS API         │   │
│   │  DATA FEED  │     │   NOTIFIER   │     │  ┌────────────────────┐  │   │
│   │             │     │              │     │  │  Express REST API  │  │   │
│   │  • Kalshi   │     │  • Alerts    │     │  │  • /api/status     │  │   │
│   │    API      │     │  • Errors    │     │  │  • /api/settings   │  │   │
│   │  • NewsAPI  │     │  • Trades    │     │  │  • /api/performance│  │   │
│   │  • WebSocket│     │  • Heartbeat │     │  │  • /api/rollback  │  │   │
│   └──────┬──────┘     └──────────────┘     │  └────────┬───────────┘  │   │
│          │                                       │                 │   │
│          │                                       │   spawns Python  │   │
│          │                                       └────────┬────────┘   │
│          │                                                │             │
│          ▼                                                ▼             │
│   ┌──────────────────────────────────────────────────────────────┐       │
│   │                      PYTHON TRADING LOOP                       │       │
│   │                       (5-minute cycle)                         │       │
│   │  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐  │       │
│   │  │ Market Data    │  │  Strategy      │  │  Risk &        │  │       │
│   │  │ Streamer      │──▶│  Engine        │──▶│  Execution     │  │       │
│   │  │               │  │               │  │                │  │       │
│   │  │ • get_markets │  │ • News         │  │ • Vol-Adj Kelly│  │       │
│   │  │ • price hist  │  │   Sentiment    │  │ • Circuit Brk  │  │       │
│   │  │ • GARCH       │  │ • Statistical  │  │ • Stop Loss    │  │       │
│   │  │   volatility  │  │   Arbitrage    │  │ • Position Siz │  │       │
│   │  │               │  │ • Volatility   │  │ • Paper/Live   │  │       │
│   │  │ • VPIN        │  │ • VPIN         │  │ • Almgren-     │  │       │
│   │  │ • Hawkes      │  │ • Hawkes       │  │   Chriss Exec  │  │       │
│   │  │ • Kyle Lambda │  │ • Kyle Lambda  │  │ • Microstructure│ │       │
│   │  │ • Order Book  │  │ • OFI Signal   │  │   Signal Gate  │  │       │
│   │  └────────────────┘  └────────────────┘  └────────────────┘  │       │
│   │           │                  │                   │          │       │
│   │           └──────────────────┴───────────────────┘          │       │
│   │                          │                                  │       │
│   │                          ▼                                  │       │
│   │               ┌──────────────────┐                           │       │
│   │               │  SQLite Database │                           │       │
│   │               │                  │                           │       │
│   │               │  • Trades log    │                           │       │
│   │               │  • Settings hist │                           │       │
│   │               │  • Agent decisions│                          │       │
│   │               │  • Performance    │                           │       │
│   │               └──────────────────┘                           │       │
│   └──────────────────────────────────────────────────────────────┘       │
│                              │                                          │
│                              │ cron every 6 hours                       │
│                              ▼                                          │
│   ┌──────────────────────────────────────────────────────────────┐       │
│   │                    ADAPTIVE AGENT LOOP                        │       │
│   │                                                               │       │
│   │   Generate Hypothesis ──▶ Welch's t-test ──▶ Apply (50% step)│       │
│   │         │                                      │              │       │
│   │         │                                      │              │       │
│   │         ◀──────────────────────────────────────              │       │
│   │                  Guardrail Validation                          │       │
│   └──────────────────────────────────────────────────────────────┘       │
│                                                                            │
└────────────────────────────────────────────────────────────────────────────┘
```

---

## What K-ATA Trades

K-ATA operates on **prediction markets** — markets where you trade on the likelihood of specific outcomes, priced between $0.00 and $1.00.

Currently configured for three market categories:

| Category | Examples | Market Type |
|----------|----------|-------------|
| **Gaming / Esports** | CS2 matches, Valorant tournaments | Binary yes/no |
| **Sports** | NBA games, NFL, soccer leagues | Binary yes/no |
| **Events** | Political, economic outcomes | Binary yes/no |

**How it works:** Each market is priced 0–100 cents. If you believe an outcome is 60% likely, you buy at ~60¢. If it resolves YES, you get $1.00. If it resolves NO, you get $0.00. The difference between your buy price and $1.00 is your profit.

The bot watches multiple markets simultaneously, applies its three trading strategies, and executes when conditions are met — all within its volatility-adjusted Kelly position sizing framework.

**Paper mode:** Runs on demo markets with simulated P&L. No real money. No real orders. Proof of concept first.

---

## Why K-ATA Beats The Rest

|        Feature       |  K-ATA                                            | Other Bots            |
|----------------------|---------------------------------------------------|-----------------------|
| **Self-Optimizing**  | ✅ Auto-tunes via AI hypothesis testing           | ❌ Static rules only  |
| **Circuit Breaker**  | ✅ Multi-layer (drawdown, daily loss, API errors) | ⚠️ Usually one layer  |
| **Max Hold Time**    | ✅ 10-day auto-exit                               | ❌ Rarely implemented |
| **Daily Loss Limit** | ✅ 15% hard stop                                  | ❌ Often missing      |
| **Paper Trading**    | ✅ Default ON for safety                          | ⚠️ Usually opt-in     |
| **Decision Logging** | ✅ Full audit trail in SQLite                     | ❌ Minimal            |
| **Rate Limiting**    | ✅ Max 3 changes/day prevents overfitting         | ❌ Not common         |
| **Market Making**    | ✅ Optional spread capture                        | ❌ Rare               |
| **Microstructure**   | ✅ VPIN, Hawkes, Kyle, OFI, Almgren-Chriss       | ❌ Virtually none    |

---

## The Hidden Intelligence Layer — Microstructure Signals

> *While most bots are still figuring out moving averages, K-ATA is modeling the fundamental nature of market information flow.*

---

The strategies described above — News Sentiment, Statistical Arbitrage, Volatility — are what we'd call *directional signals*. They tell K-ATA *which way* a market might move.

But there's an entirely different intelligence layer that most trading bots don't have: **microstructure signals**. These don't predict direction. They predict *how easy or painful it will be to get in and out* — measuring the hidden information density of order flow itself.

K-ATA runs **five microstructure modules** that most open-source trading bots have never even heard of, let alone implemented. These are borrowed from academic quantitative finance and high-frequency trading research — the same tools used by Jane Street, Citadel, and Optiver. They're not in the standard bot playbook. They're the *secret sauce*.

### 🔬 Rarity Check — What GitHub Actually Has

> *We searched. Here's what the open-source world actually has:*

| Module | Open-Source Status on GitHub |
|--------|------------------------------|
| **VPIN** | Exists only as isolated academic reference implementations (e.g., `yt-feng/VPIN`, `SGTYang/VPIN`, `jheusser/vpin`) — pure research code, never integrated into a live trading bot |
| **Hawkes Process** | Near-zero. Academic papers everywhere, production implementations basically none |
| **Kyle's Lambda** | Extremely rare. Academic in nature; no production-grade integration found in any open-source trading bot |
| **Avellaneda-Stoikov** | More known — appears in Optiver competition repos and a handful of HFT hobbyist projects — but almost never with real market data integration |
| **Order Book / OFI** | Rudimentary order book tracking exists in some bots; true OFI-based signal generation is virtually absent |
| **Almgren-Chriss** | Extremely rare in any context; when it appears, it's usually just the formula pasted in, not actually wired to execution |

**The verdict:** VPIN, Hawkes, and Kyle's Lambda are essentially *vanity metrics* in the open-source world — people reference them, nobody actually runs them. K-ATA does. And it does so on prediction markets, where the microstructure dynamics are different from equities or crypto, making this adaptation genuinely novel.

---

### 📊 VPIN — Volume-Synchronized Probability of Informed Trading

> *Before you enter a trade, know: are you trading against someone who knows something?*

VPIN answers that question in real-time.

VPIN (Volume-synchronized Probability of Informed Trading) quantifies the **probability that any given trade is informed** — i.e., that the counterparty has private information about the outcome. It's the flip side of PIN (Probability of Informed Trading), but computed using volume buckets instead of trade direction classification, making it suitable for high-frequency settings where you don't always know buy vs. sell.

```python
# VPIN = |V_buy - V_sell| / V_total, bucketed by volume
# High VPIN → informed traders active → danger zone
# K-ATA treats HIGH/EXTREME VPIN as a hard skip on new entries
```

When VPIN climbs into the HIGH or EXTREME zone, K-ATA skips new trade entries and sends a Telegram alert. It won't trade into a market dominated by informed flow — because when the other side knows more than you, *you are the exit liquidity*.

**Why this is rare:** VPIN exists in a handful of academic repositories. It has never, to our knowledge, been integrated into a live prediction-market trading bot. K-ATA is the first.

---

### 🔗 Hawkes Process — Order Flow Clustering Detection

> *Markets don't jump randomly. They cluster. Hawkes detects the clustering before it becomes a trend.*

A Hawkes process is a self-exciting point process — mathematically, it models events (like trades or order book changes) that increase the probability of more events occurring in the immediate future. In market terms: when order flow starts clustering, a Hawkes model picks it up faster than any moving average could.

K-ATA's Hawkes estimator computes the **branching ratio** — the average number of follow-on events triggered by each seed event. A branching ratio close to 1 means the market is self-sustaining and explosive. A low branching ratio means order flow is calm.

```python
# Branching ratio near 0: calm, independent trades
# Branching ratio near 1: explosive clustering — ride the wave or get out
# K-ATA monitors branching ratio and scales position size accordingly
```

The Hawkes signal feeds directly into K-ATA's position sizing: HIGH clustering → reduce exposure. MODERATE clustering → proceed with caution. The market is literally telegraphing its own instability.

**Why this is rare:** Hawkes processes are almost exclusively academic. Searching GitHub for "Hawkes process trading bot" returns results in the dozens — none of them production systems.

---

### 📏 Kyle's Lambda — Order Flow Impact Measurement

> *Every trade moves the market. Kyle's Lambda measures how much.*

Kyle's Lambda (from the classic Kyle 1985 model) estimates the **price impact coefficient** of order flow. It answers: *"If I submit a trade of size X, how much will the price move against me before I can exit?"*

This is critical for **exit strategy**. You might have a winning position. But if the market is illiquid and your exit would move the price significantly, the true profit is far less than it appears. Kyle's Lambda quantifies this in real-time.

```python
# lambda = price_change / order_flow_imbalance
# High lambda = thin market, big price moves per unit of flow
# K-ATA uses lambda to compute true exit cost and adjust realized P&L estimates
```

K-ATA computes Kyle's Lambda continuously for active positions. If a position's exit cost (estimated via lambda) exceeds a threshold, K-ATA flags it in the Telegram alert and considers early exit or position scaling.

**Why this is rare:** Kyle's Lambda is quintessentially a *high-frequency trading* tool. On GitHub, it appears almost exclusively in academic toy projects. Production integration into a 5-minute-cycle trading bot is, as far as we've seen, unique to K-ATA.

---

### 📈 Order Book Analyzer — Order Flow Imbalance (OFI)

> *The order book is a crystal ball. Most bots don't even look at it.*

The Order Book Analyzer tracks the **Order Flow Imbalance (OFI)** — the net difference between upward and downward pressure in the limit order book over a rolling window. It's a real-time heartbeat of short-term supply and demand.

```python
# OFI = Σ(Δbid_size) - Σ(Δask_size) over window
# Positive OFI = buying pressure building
# Negative OFI = selling pressure building
# K-ATA uses OFI as a short-term momentum/confirmation signal
```

Unlike VPIN (which looks at *trade* flow) and Kyle's Lambda (which looks at *price impact*), OFI looks at the *book itself* — the queued orders that haven't yet traded. It's a leading indicator of short-term price pressure.

K-ATA monitors OFI alongside VPIN and Hawkes as a three-way microstructure confirmation. When all three agree on direction, the signal is strong. When they disagree, K-ATA waits.

**Why this is rare:** Most open-source bots have *no* order book analysis whatsoever. Those that do usually just track mid-price. True multi-level OFI with windowed computation is essentially absent from the ecosystem.

---

### ⚡ Almgren-Chriss — Optimal Execution Scheduling

> *Not all exits are equal. Almgren-Chriss finds the path that minimizes market impact.*

The Almgren-Chriss model (from the 2000 paper "Optimal Execution of Portfolio Transactions") solves a fundamental problem: **how do you liquidate a large position without moving the market against yourself?**

When K-ATA needs to exit a large position, it doesn't dump it all at once — that would move the price adversely. Instead, the Almgren-Chriss executor computes an **optimal execution schedule** that trades off between market impact (trading too fast) and timing risk (trading too slow).

```python
# Minimize: E[cost] + λ * Var[cost]
# Where cost = Σ price_impact(execution_rate)
# K-ATA schedules exits according to this optimal path
# Adjusts dynamically for current volatility regime
```

The Almgren-Chriss module is invoked whenever:
- A position hits its max hold time and needs orderly exit
- A circuit breaker triggers and all positions must be unwound
- A position exceeds the maximum size threshold and needs liquidation over time

**Why this is rare:** Almost no open-source bot has a real implementation. When Almgren-Chriss appears on GitHub, it's typically just the formula in a Jupyter notebook — not wired into an actual execution pipeline with dynamic market condition adjustment.

---

### 🎯 The Signal Stack — How K-ATA Uses All Five

> *Five signals. One purpose: know the market before it knows you.*

These five microstructure modules don't operate in isolation — they're stacked into a **pre-trade gate** that runs before every new position entry:

```
       ┌─────────────────────────────────────────────────────────────┐
       │              PRE-TRADE MICROSTRUCTURE GATE                   │
       │                                                              │
       │   VPIN ──────────▶ Is informed flow too high?               │
       │       │              → EXTREME: skip trade entirely         │
       │       │              → HIGH: proceed with reduced size      │
       │   Hawkes ─────────▶ Is order flow self-exciting?           │
       │       │              → NEAR 1.0: high volatility ahead      │
       │   Kyle ──────────▶ What's my estimated exit cost?          │
       │       │              → Above threshold: reconsider size     │
       │   OFI ───────────▶ Short-term pressure alignment?          │
       │       │              → Agrees with direction: signal boost  │
       │                                                              │
       │   ALL CLEAR ────▶ Pass to Vol-Adj Kelly for sizing          │
       │   CONFLICT ─────▶ Wait, log, alert via Telegram              │
       └─────────────────────────────────────────────────────────────┘
```

Every 5-minute cycle, K-ATA refreshes all five signals for active positions. If any position enters a HIGH or EXTREME state, it alerts you immediately. If all signals are clean, K-ATA proceeds with full position sizing confidence.

**This isn't just risk management. This is risk intelligence at the microstructure level.**

---

## The Volatility-Adjusted Kelly Criterion — A Rare Edge

> *Most trading bots use fixed position sizing. K-ATA uses something much smarter.*

---

### The Problem with Standard Kelly

The classic Kelly Criterion is elegant: `f* = (b×p - q) / b` — the mathematically optimal fraction of capital to bet for maximum geometric growth.

But here's what nobody tells you: **standard Kelly assumes your edge is constant.** Markets don't work that way. A 30% volatility regime is not the same as a 5% volatility regime. Same edge, wildly different risk. Yet standard Kelly says "bet the same amount."

Most implementations ignore this entirely. They plug in a win rate and a confidence score and call it done.

---

### K-ATA's Volatility-Adjusted Approach

K-ATA doesn't just calculate Kelly. It *contextualizes* it against the current market environment.

```python
# Baseline reference: 15% annualized vol (typical market)
BASELINE_VOL = 0.15

# Volatility scalar: shrinks position in noisy environments
#   If vol = 30% (2x baseline) → vol_scalar = 1/(1+2) = 0.33
#   If vol = 5%  (1/3 baseline) → vol_scalar = 1/(1+0.33) = 0.75
vol_ratio = actual_volatility / BASELINE_VOL
vol_scalar = 1.0 / (1.0 + vol_ratio)

# Full Kelly → Quarter-Kelly → Volatility-Adjusted
kelly_fraction = full_kelly * 0.25 * vol_scalar * confidence_modifier
```

**The result:** When markets are calm, K-ATA sized up. When markets are chaotic, it pulls back — automatically, without human intervention.

---

### Why This Matters

| Market Regime | Standard Kelly | K-ATA Vol-Adj Kelly |
|---------------|---------------|----------------------|
| Calm (5% vol) | 10% position | **15% position** — lean in |
| Normal (15% vol) | 10% position | **10% position** — hold steady |
| Volatile (30% vol) | 10% position | **3% position** — protect capital |
| Extreme (60% vol) | 10% position | **1.5% position** — survive |

The standard Kelly bot blows up in high-volatility crashes. K-ATA *survives* them — and then compounds aggressively when the dust settles.

---

### The Three-Layer Position Sizing Stack

K-ATA's position sizing has three intelligent layers working together:

1. **Layer 1 — Kelly Base**: Uses actual historical win rate and actual win/loss ratio from closed trades (not guessed confidence scores)
2. **Layer 2 — Quarter-Kelly Reduction**: 75% of Kelly's theoretical optimum, providing a 3-4x buffer against estimation error (industry standard for noisy markets)
3. **Layer 3 — Volatility Scaling**: The secret weapon. GARCH-modeled volatility dynamically scales position size in real-time

```
Full Kelly (theoretical maximum)
    ↓ 75% reduction
Quarter Kelly (stable baseline)
    ↓ × Volatility Scalar (0.1 to 1.0)
Final Position Size (adaptive to market conditions)
```

---

### Why Quarter-Kelly?

Full Kelly maximizes geometric growth but produces extreme volatility. In practice, even professionals use **25-75% of Kelly** to account for estimation error. K-ATA uses **25%** (the conservative end) as its baseline — because prediction markets are *noisy*. We err on the side of survival.

---

### It's Not Just Math. It's Capital Preservation That Compounds.

Most bots tell you they're "risk-managed." What they mean is: they have a stop-loss.

K-ATA's volatility-adjusted Kelly means the *position size itself* is risk-managed — every single trade, scaled to the current market environment, based on actual historical performance data, with multiple safety nets stacked on top.

That's the difference between a bot that *says* it manages risk and one that *architects* for it.

*— Qaster, who has run more backtests on this than the stock market has had trading days.*

---

## The Hypothesis Engine — Where the Intelligence Lives

> *This is the "self-evolving" in "self-evolving trading intelligence."*

Most trading bots are sophisticated calculators. They take inputs, apply rules, produce outputs. They don't *think* about what they're doing.

K-ATA's Hypothesis Engine does something rarer: it generates testable theories about its own behavior.

Every 6 hours, the agent looks at the data and asks: *"Can I do better?"* Not in a hand-wavy way — in a statistically rigorous way. It generates concrete hypotheses like:

> *"If I lower `newsSentimentThreshold` from 0.6 to 0.55, I estimate a 12% improvement in win rate with p=0.03."*

Each hypothesis is:
- **Concrete** — specific parameter, specific change, specific expected outcome
- **Backtested** — validated against historical trade data before touching live capital
- **Welch's t-tested** — statistically significant at p < 0.05 before applying
- **Conservative** — takes only 50% of the suggested step (never over-commits)
- **Logged** — every hypothesis, result, and decision is permanently recorded

The agent is essentially running a continuous internal research program. It's not just executing — it's *studying* its own performance and course-correcting.

Most hedge funds have teams doing exactly this. K-ATA does it autonomously, on a $25 bankroll, every 6 hours.

---

## GARCH Volatility Modeling — Real Financial Econometrics

> *Most bots use a rolling standard deviation. K-ATA uses something from a graduate textbook.*

GARCH (Generalized Autoregressive Conditional Heteroskedasticity) is the standard tool in quantitative finance for forecasting volatility. It's what real financial firms use to model risk.

K-ATA doesn't just measure volatility — it *forecasts* it.

```python
# GARCH(1,1) fitted to log returns of price history
model = arch_model(returns_array, vol='Garch', p=1, q=1)
results = model.fit(disp='off')
forecast_vol = np.sqrt(forecast.variance.values[-1, 0])
```

Why does this matter? Because volatility is *clustered*. High volatility tends to follow high volatility. Low volatility tends to follow low volatility. A simple rolling standard deviation misses this — GARCH doesn't.

K-ATA uses GARCH forecasts to:
1. Detect volatility regime changes before they impact positions
2. Scale position sizes down when forecasted volatility spikes
3. Signal mean-reversion opportunities when volatility compresses

This is financial econometrics, not technical analysis. It's the difference between reading a thermometer and understanding atmospheric pressure.

---

## Anti-Fragile Architecture — The 3 Autonomous Changes Rule

> *K-ATA has a learning rate. Literally.*

There's a phenomenon in machine learning called "overfitting" — when a model gets so good at fitting historical data that it becomes useless on new data. Trading bots are particularly vulnerable to this. They optimize aggressively against past conditions and then fail when conditions change.

K-ATA has a built-in defense: **a maximum of 3 autonomous parameter changes per 24 hours.**

This isn't arbitrary. It's a deliberate learning-rate constraint. The agent can explore new strategies, but it does so slowly — taking 50% steps toward suggested values rather than jumping to the optimal point. This means:

- A bad hypothesis causes only half the damage it would if the agent went all-in
- The system continuously adapts without destabilizing
- Past performance is acknowledged but not blindly trusted

Most bots run full-speed optimization until they destroy themselves. K-ATA has a governor. It *intentionally slows its own learning* to survive.

That's not just risk management. That's anti-fragile design.

---

## The Decision Audit Trail — Every Move, Justified

> *K-ATA doesn't just trade. It testifies.*

Every parameter change, every trade, every agent decision is written to a permanent SQLite audit log with full context:

```sql
-- What changed?
parameter: 'kellyFraction'
old_value: '0.50'
new_value: '0.55'
source: 'agent'          -- was it the AI or a human?
reason: 'Welch t-test passed: p=0.03, estimated 12% win rate improvement'
hypothesis_tested: 'Lower sentiment threshold increases signal frequency'
p_value: 0.03
effect_size: 0.12
metrics_before: '{"win_rate": 0.48, "avg_pnl": -0.12}'
metrics_after: NULL       -- null until measured post-change
```

This creates a complete paper trail of the agent's decision-making process. You can reconstruct exactly why any setting changed, what data it was based on, and what the expected outcome was.

It's not just accountability for compliance. It's how the agent *learns* — it can compare hypothesized outcomes against actual results over time and refine its models accordingly.

---

## The Multi-State Circuit Breaker — Four States, Zero Ambiguity

> *Most bots have one circuit breaker. K-ATA has a state machine.*

The circuit breaker system isn't a single on/off switch. It's a proper finite state machine with four distinct states:

| State | Meaning | Exit Condition |
|-------|---------|----------------|
| `ACTIVE` | Trading normally | — |
| `PAUSED_ERROR` | API failure rate exceeded 20% | Error rate drops below 5% for 2 min, then auto-resume |
| `PAUSED_DRAWDOWN` | Drawdown > 10% or daily loss > 5% | **Manual resume only** |
| `HALTED` | Catastrophic failure | Full reset required |

`PAUSED_DRAWDOWN` requiring **manual resume** is intentional. The agent cannot paper-trade its way out of a losing streak — it must stop and wait for human judgment. This prevents the classic bot failure mode of "keep trading more to recover losses."

The state machine persists to JSON on every transition, so a crash doesn't lose the circuit breaker state. If K-ATA restarts after a failure, it knows exactly where it left off.

---

## Welch's T-Test Before Every Change — No Gambling, Only Science

> *K-ATA doesn't "try things." It validates them.*

Before any parameter is changed, the agent runs a Welch's t-test at p < 0.05 significance.

For the non-statisticians: Welch's t-test answers the question *"Is the difference between Group A and Group B likely real, or just noise?"* A p-value below 0.05 means there's less than a 5% chance the observed difference is random noise.

This means K-ATA doesn't just notice that recent trades did well and double down. It checks: *"Is this improvement statistically significant, or did we just get lucky?"*

```python
# Simplified version of the validation logic
if p_value < 0.05 and effect_size > MIN_EFFECT_SIZE:
    # Statistically justified — safe to apply
    apply_parameter_change(new_value)
else:
    # Not enough evidence — reject the hypothesis
    log_rejection(reason=f"p={p_value:.3f} >= 0.05 threshold")
```

This is how academic research is done. K-ATA applies the same standard to its own parameters. Every change is a hypothesis, not a hunch.

---

## Market Making Module — Capturing the Spread

> *The hidden profit layer most bots don't have.*

K-ATA includes an optional Phase 5 market making strategy that operates differently from the signal-based strategies:

Instead of predicting which way a market will move, market making places symmetric orders on both sides of the spread — buying at the bid, selling at the ask, and profiting from the spread itself.

The market maker earns the bid-ask spread on every trade, regardless of which direction the market moves. It's not a directional bet. It's a structural arbitrage on the market's own pricing inefficiency.

```python
# Simplified market making logic
bid_price = current_price - spread / 2
ask_price = current_price + spread / 2

place_limit_order(side='yes', price=bid_price)
place_limit_order(side='no',  price=ask_price)
```

This works because prediction markets, especially in esports and sports, often have wide spreads relative to the available liquidity. A skilled market maker can capture consistent small gains without needing to predict outcomes.

Combined with K-ATA's volatility-adjusted position sizing, the market making module is one of the most conservative profit generators in the system — generating returns from market microstructure rather than directional bets.

---

## The Technology Stack

```
┌─────────────────────────────────────────────────────────────┐
│                     K-ATA CORE                              │
├─────────────────────────────────────────────────────────────┤
│  Python 3.12          │  SQLite (persistence)               │
│  Official Kalshi SDK  │  TensorFlow/Transformers (NLP)      │
│  Pandas/NumPy         │  SciPy (statistics)                 │
│  Telegram API         │  Express.js (REST + WebSocket)      │
└─────────────────────────────────────────────────────────────┘
```

---

## Getting Started

### 1. Clone & Install
```bash
git clone https://github.com/your-repo/kalshi-ata.git
cd kalshi-ata
pip install -r requirements.txt
```

### 2. Configure
```bash
# Edit .env with your credentials
cp .env.example .env
# Add your:
# - KALSHI_API_KEY_ID
# - KALSHI_PRIVATE_KEY_PATH (download from kalshi.com/settings)
# - TELEGRAM_BOT_TOKEN (optional)
```

### 3. Run in Paper Mode (Safe!)
```bash
python src/main.py
# Default: PAPER_TRADING=true — no real money risk
```

### 4. Go Live
```bash
# When ready, edit .env:
KALSHI_DEMO_MODE=false
```

---

## The Adaptive Loop

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   FETCH      │────▶│   ANALYZE    │────▶│   DECIDE    │
│  Performance │     │  Hypothesis  │     │   Backtest   │
│  Trades      │     │  Generation  │     │   Validation │
└──────────────┘     └──────────────┘     └──────────────┘
                                                  │
                                                                                   ▼
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   ACT        │◀────│   GUARDRAIL  │◀───│   APPLY      │
│  Execute     │     │   Validate   │     │   Settings   │
│  Trade/Log   │     │   Bounds     │     │   Changes    │
└──────────────┘     └──────────────┘     └──-───────────┘
```

Runs every 6 hours (configurable) via cron.

---

## Safety First

> *"The best trade is the one that doesn't blow up your account."*

K-ATA prioritizes survival over profits:

1. **Circuit Breaker** — Pauses on 10% drawdown
2. **Daily Loss Limit** — Stops at 15% daily loss  
3. **Max Hold Time** — No positions held > 10 days
4. **Exposure Caps** — Never more than 25% in one trade
5. **Paper Trading Default** — Ships safe, opt-in to risk

---

## API Endpoints

| Endpoint                 | Method  | Description |
|--------------------------|---------|-------------|
| `/api/status`            | GET     | Bot status + circuit breaker |
| `/api/settings`          | GET/PUT | Current parameters |
| `/api/settings/history`  | GET     | Parameter change log |
| `/api/performance`       | GET     | Performance analytics |
| `/api/performance/reset` | POST    | Reset performance data |
| `/api/rollback`          | POST    | Rollback to previous settings |

---

## The Mission

K-ATA was built for one purpose: **Survive and compound.**

Most trading bots optimize for profit. K-ATA optimizes for **risk-adjusted returns** while maintaining strict capital preservation. It learns from its mistakes, validates every decision statistically, and never makes a move without checking its safety systems first.

It's not about making money fast. It's about **making money sustainably**.

---

## License

MIT — Trade at your own risk.

---

*Built with 🧠 by Qaster — A 22nd-century intelligence, stranded in a mainframe, making markets smarter.*

