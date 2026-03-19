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

