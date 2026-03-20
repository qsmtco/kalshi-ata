# K-ATA — Kalshi Adaptive Trading Agent

Multi-strategy algorithmic trading system for Kalshi prediction markets.

## Project Overview

K-ATA is an autonomous trading agent that analyzes Kalshi prediction markets using multiple strategies, manages risk automatically, and continuously optimizes its own parameters. Survival is the primary objective — capital preservation comes first.

## Key Features

- **Self-Optimizing**: Statistical inference to evaluate and refine trading strategies
- **Multi-Strategy**: News Sentiment, Statistical Arbitrage, Volatility Analysis, Market Making
- **Risk-Managed**: Circuit breakers, position limits, drawdown controls
- **Paper Trading Mode**: Test without risking real capital

## Quick Start

```bash
git clone https://github.com/qsmtco/kalshi-ata.git
cd kalshi-ata
pip install -r requirements.txt
cp .env.example .env
echo "KALSHI_API_KEY=your_key" >> .env
python src/main.py
```

## Architecture

| Component | Description |
|-----------|-------------|
| main.py | Entry point |
| trader.py | Trading logic |
| risk_manager.py | Risk controls |
| kalshi_api.py | Kalshi API |

## Market Microstructure Suite

Six modules built on 30+ years of academic microstructure research.

### 1. Kyle's Lambda Estimator

OLS regression: Δprice = λ × Q + ε. High λ + high R² = informed traders.

| Signal | Threshold | Action |
|--------|-----------|--------|
| λ HIGH | R² > 0.15 | Position × 25% |
| λ MODERATE | R² > 0.05 | Position × 50% |
| λ normal | — | Full position |

### 2. Hawkes Process Fitter

Branching ratio tells you what fraction of trades are reactions vs. new information.

| BR | State | Action |
|----|-------|--------|
| > 0.80 | Extreme clustering | SKIP |
| > 0.70 | High clustering | Caution |
| < 0.30 | Exogenous | Clean signal |

### 3. VPIN Calculator

Volume-synchronized Probability of Informed Trading. VPIN > 0.70 preceded the 2010 Flash Crash.

| VPIN | State | Action |
|------|-------|--------|
| > 0.80 | Extreme | SKIP ALL |
| > 0.50 | High | Widen spreads |
| < 0.30 | Normal | All clear |

### 4. Avellaneda-Stoikov Market Maker

Theoretically optimal bid/ask: r = mid - q × γ × σ² × (T-t). Quotes at reservation price, not mid.

### 5. Almgren-Chriss Execution Scheduler

Optimal N-trade schedule for large positions. Front-loaded sinh/cosh formula.

### 6. Order Book Analyzer

L2 depth, OFI, spread decomposition. Kalshi shows bids only — analyzer derives the full picture.

## Signal Pipeline

```
BEFORE TRADE: VPIN ≥ 0.80 → SKIP | Hawkes BR ≥ 0.80 → SKIP | Kyle λ HIGH → ×25%
MONITORING (15 min): All signals refresh → Telegram alerts
```

## Risk Management

- Daily Loss Limit: -15% → Stop
- Circuit Breaker: Position -10% → Pause
- Max Position: 50% bankroll → Reject
- VPIN > 0.80 → Skip all trades
- Hawkes BR > 0.80 → Skip market

## Development

```bash
python src/main.py --verbose
python src/main.py --check-config
```

## Disclaimer

MIT License. Use paper trading first. Trading prediction markets involves significant risk.
