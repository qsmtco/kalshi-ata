# Technical Specification: Kalshi Adaptive Trading Agent (K-ATA)

**Version:** 1.0.0  
**Date:** 2026-03-18  
**Author:** Qaster  
**Project:** KALSHI – adaptive – agent  
**Location:** `/home/q/projects/KALSHI – adaptive – agent`

---

## 1. INTRODUCTION

### 1.1 Purpose

This Technical Specification defines the exact architecture, interfaces, data contracts, and behavioral guarantees for the Kalshi Adaptive Trading Agent (K-ATA). It serves as the authoritative source for implementation details, API contracts, database schema, agent decision logic, safety systems, and error handling.

### 1.2 Scope

The system comprises:
- **Kalshi Trading Engine** (Python) - executes trades on Kalshi prediction markets
- **Bot Interface Server** (Node.js/Express) - REST API and process manager
- **Adaptive Agent** (OpenClaw cron) - meta-strategy generator and parameter optimizer
- **Persistence Layer** (SQLite) - trades, settings, performance, agent logs

### 1.3 Definitions

| Term | Definition |
|------|------------|
| K-ATA | Kalshi Adaptive Trading Agent (this system) |
| Agent | The AI optimization component (Qaster via OpenClaw cron) |
| Bot | The Python trading engine |
| Interface | The Express server that proxies requests to the bot |
| DB | SQLite database (`data/kalshi.db`) |
| Guardrail | Hard limit that cannot be overridden |
| Circuit breaker | Automatic trading halt when safety limits breached |
| Hypothesis | A candidate parameter adjustment or strategy change proposed by the Agent |

---

## 2. SYSTEM ARCHITECTURE

### 2.1 Component Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                    OpenClaw Scheduler (cron)               │
│               every 6 hours: agent=Qaster                 │
└─────────────────────────────┬───────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                Adaptive Agent (agent_loop.py)              │
│  • GET /api/performance?days=30                           │
│  • GET /api/trades?limit=5000                             │
│  • GET /api/regime                                        │
│  • POST /api/settings (adjustments)                      │
│  • Logs to data/agent.db                                 │
└─────────────────────────────┬───────────────────────────────┘
                              │ HTTP localhost:3050
                              ▼
┌─────────────────────────────────────────────────────────────┐
│             Bot Interface Server (bot_interface.js)        │
│                                                             │
│  REST Endpoints:                                           │
│  • GET  /api/status                                       │
│  • GET  /api/positions                                    │
│  • GET  /api/performance                                 │
│  • GET  /api/trades                                      │
│  • GET  /api/regime                                      │
│  • GET  /api/settings                                    │
│  • POST /api/settings  { updates }                       │
│  • POST /api/start-trading                               │
│  • POST /api/stop-trading                                │
│  • GET  /api/settings/history                            │
│  • GET  /api/settings/rollback?ts=                       │
│                                                             │
│  Process Manager:                                         │
│  • Spawns Python bot as child process                    │
│  • Monitors health, restarts on crash                    │
│  • Captures stdout/stderr → logs/                         │
└─────────────────────────────┬───────────────────────────────┘
                              │ stdin/stdout JSON (bot_state.py)
                              ▼
┌─────────────────────────────────────────────────────────────┐
│           Kalshi Trading Engine (Python)                  │
│                                                             │
│  ├── Strategies                                           │
│  │   • NewsSentimentStrategy                             │
│  │   • StatisticalArbitrageStrategy                      │
│  │   • VolatilityBasedStrategy                           │
│  │   • (future: AgentGeneratedStrategy)                  │
│  ├── RiskManager (Kelly, stops, exposure)                │
│  ├── MarketDataStreamer (60s poll)                       │
│  ├── KalshiAPI (retry wrapper)                           │
│  ├── TradeLogger (SQLite writer)                         │
│  ├── SettingsManager (persistence + validation)          │
│  └── bot_state.py (CLI for Interface)                    │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 Data Flow

1. **Interface starts** → spawns Python bot process
2. **Bot polls** market data every 60s from Kalshi API
3. **Strategies** generate signals → RiskManager checks limits → TradeLogger records
4. **SettingsManager** reads from SQLite `current_settings` on startup, watches for external updates via `bot_state.py` commands
5. **Agent** (every 6h) calls `/api/performance`, `/api/trades`, `/api/regime`
6. **Agent** computes adjustments → POSTs to `/api/settings`
7. **Interface** validates guardrails → updates `current_settings` table → sends command to bot via `bot_state.py update_settings` → bot applies instantly
8. **All changes** logged to `settings_history` and `agent_decisions`

---

## 3. DATABASE SCHEMA

### 3.1 Complete SQL (versioned)

```sql
-- Version: 1.0.0
-- File: database/V1__init.sql

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- Trades table: every executed trade (real or dry-run)
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    market_id TEXT NOT NULL,
    market_title TEXT,
    strategy TEXT NOT NULL CHECK(strategy IN (
        'news_sentiment',
        'statistical_arbitrage',
        'volatility_based',
        'agent_generated_%'  -- pattern for future
    )),
    action TEXT NOT NULL CHECK(action IN ('buy', 'sell')),
    quantity INTEGER NOT NULL CHECK(quantity > 0),
    entry_price REAL NOT NULL CHECK(entry_price >= 0),
    exit_price REAL CHECK(exit_price >= 0),
    pnl REAL,  -- realized P&L in USD (null if open)
    confidence REAL CHECK(confidence >= 0 AND confidence <= 1),
    position_size_pct REAL CHECK(position_size_pct > 0 AND position_size_pct <= 1),
    stop_loss_pct REAL CHECK(stop_loss_pct > 0 AND stop_loss_pct <= 1),
    take_profit_pct REAL CHECK(take_profit_pct > 0 AND take_profit_pct <= 1),
    exit_reason TEXT CHECK(exit_reason IN (
        'stop_loss', 'take_profit', 'manual', 'end_of_day', 'circuit_breaker'
    )),
    metadata TEXT,  -- JSON: signal details, volatility, correlation, etc.
    closed_at DATETIME,
    UNIQUE(strategy, market_id, created_at)  -- prevent duplicates
);

CREATE INDEX IF NOT EXISTS idx_trades_strategy ON trades(strategy);
CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(created_at);
CREATE INDEX IF NOT EXISTS idx_trades_open ON trades(closed_at) WHERE closed_at IS NULL;

-- Settings history: immutable log of all parameter changes
CREATE TABLE IF NOT EXISTS settings_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    changed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    parameter TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT NOT NULL,
    source TEXT NOT NULL CHECK(source IN ('default', 'manual', 'agent', 'rollback')),
    reason TEXT,
    agent_decision_id INTEGER,  -- FK to agent_decisions if applicable
    FOREIGN KEY (agent_decision_id) REFERENCES agent_decisions(id)
);

CREATE INDEX IF NOT EXISTS idx_settings_history_param ON settings_history(parameter);
CREATE INDEX IF NOT EXISTS idx_settings_history_ts ON settings_history(changed_at);

-- Current settings: denormalized for fast reads (single source of truth)
CREATE TABLE IF NOT EXISTS current_settings (
    parameter TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (parameter) REFERENCES settings_history(parameter) ON DELETE CASCADE
);

-- Performance metrics: daily rollup (computed, can be rebuilt)
CREATE TABLE IF NOT EXISTS performance_metrics (
    metric_date DATE NOT NULL,
    strategy TEXT NOT NULL,
    total_trades INTEGER DEFAULT 0,
    winning_trades INTEGER DEFAULT 0,
    total_pnl REAL DEFAULT 0,
    avg_win REAL,
    avg_loss REAL,
    max_drawdown REAL DEFAULT 0,
    sharpe_ratio REAL,
    win_rate REAL GENERATED ALWAYS AS (
        CASE WHEN total_trades > 0 THEN winning_trades * 1.0 / total_trades ELSE 0 END
    ) STORED,
    PRIMARY KEY (metric_date, strategy)
);

CREATE INDEX IF NOT EXISTS idx_performance_date_strategy ON performance_metrics(metric_date, strategy);

-- Agent decisions: every action taken by the adaptive agent
CREATE TABLE IF NOT EXISTS agent_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decided_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    decision_type TEXT NOT NULL CHECK(decision_type IN (
        'parameter_tuning',
        'strategy_enable',
        'strategy_disable',
        'circuit_breaker',
        'rollback',
        'hypothesis_generated'
    )),
    parameters_modified TEXT,  -- JSON: {"kelly_fraction": 0.55}
    rationale TEXT NOT NULL,
    hypothesis_tested TEXT,  -- short description
    p_value REAL,  -- statistical significance if backtested
    effect_size REAL,  -- estimated improvement
    metrics_before TEXT,  -- JSON snapshot
    metrics_after TEXT,   -- JSON snapshot (if applicable)
    applied BOOLEAN DEFAULT 1  -- False if rejected by guardrails
);

CREATE INDEX IF NOT EXISTS idx_agent_decisions_type_ts ON agent_decisions(decision_type, decided_at);

-- Agent DB (separate file: data/agent.db) for isolation
-- Same schema as above but in separate file to avoid lock contention

-- Views for convenience

-- Daily P&L view
CREATE VIEW IF NOT EXISTS daily_pnl AS
SELECT
    date(created_at) as day,
    strategy,
    SUM(pnl) as daily_pnl,
    COUNT(*) as daily_trades,
    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as winners
FROM trades
WHERE closed_at IS NOT NULL
GROUP BY day, strategy;

-- Current open positions view
CREATE VIEW IF NOT EXISTS open_positions AS
SELECT
    market_id,
    strategy,
    action,
    SUM(quantity) as total_quantity,
    AVG(entry_price) as avg_entry,
    MAX(created_at) as latest_entry
FROM trades
WHERE closed_at IS NULL
GROUP BY market_id, strategy, action;
```

---

## 4. API CONTRACT

### 4.1 Base URL

```
http://localhost:3050
```

### 4.2 Global Response Format

**Success:**
```json
{
  "success": true,
  "data": { ... },
  "timestamp": "2025-03-18T19:00:00Z"
}
```

**Error:**
```json
{
  "success": false,
  "error": "Human-readable error message",
  "code": "ERROR_CODE",
  "details": { ... },
  "timestamp": "2025-03-18T19:00:00Z"
}
```

**HTTP Status Codes:**
- `200` OK
- `400` Bad Request (validation error)
- `403` Forbidden (circuit breaker active, guardrail violation)
- `404` Not Found
- `409` Conflict (resource conflict, e.g., duplicate trade)
- `500` Internal Server Error
- `503` Service Unavailable (bot not running)

### 4.3 Endpoint Specifications

#### GET /api/status

**Description:** Returns bot operational status and health.

**Response:**

```json
{
  "success": true,
  "data": {
    "trading": true,  // true if bot is actively trading
    "apiConnected": true,
    "lastUpdate": "2025-03-18T18:59:00Z",
    "uptime": "3h 12m 45s",
    "exchangeStatus": {
      "kalshi": "connected"
    },
    "balanceSummary": {
      "available": 1045.67,
      "totalEquity": 1050.23,
      "unrealizedPnL": 4.56
    },
    "positionsCount": 3,
    "activeStrategies": ["news_sentiment", "volatility_based"],
    "tradesCountToday": 12
  }
}
```

**Errors:**
- `503` if bot process not running

---

#### GET /api/positions

**Description:** Returns currently open positions with live P&L.

**Response:**

```json
{
  "success": true,
  "data": {
    "positions": [
      {
        "marketId": "KXHI-25MAR18-0.75-IS_RAISING",
        "marketTitle": "Will inflation rise above 3.5%?",
        "strategy": "news_sentiment",
        "action": "buy",
        "quantity": 50,
        "entryPrice": 0.48,
        "currentPrice": 0.52,
        "pnl": 2.00,
        "timestamp": "2025-03-18T15:30:00Z"
      }
    ]
  }
}
```

---

#### GET /api/performance?days=30

**Description:** Performance metrics aggregated by strategy.

**Query Parameters:**
- `days` (optional, default 30) - lookback period

**Response:**

```json
{
  "success": true,
  "data": {
    "period_days": 30,
    "portfolio": {
      "totalPnL": 245.67,
      "sharpeRatio": 1.85,
      "maxDrawdown": 0.082,
      "currentDrawdown": 0.032,
      "winRate": 0.58
    },
    "performance": [
      {
        "strategy": "news_sentiment",
        "totalTrades": 45,
        "winningTrades": 27,
        "winRate": 0.60,
        "totalPnL": 156.34,
        "avgWin": 5.23,
        "avgLoss": -3.12,
        "sharpeRatio": 1.95,
        "maxDrawdown": 0.095
      },
      {
        "strategy": "volatility_based",
        "totalTrades": 32,
        "winningTrades": 17,
        "winRate": 0.53,
        "totalPnL": 89.33,
        "avgWin": 4.56,
        "avgLoss": -3.45,
        "sharpeRatio": 1.65,
        "maxDrawdown": 0.12
      }
    ]
  }
}
```

---

#### GET /api/trades?strategy=&limit=1000&from_date=&to_date=

**Description:** Retrieve raw trades with optional filtering.

**Query Parameters:**
- `strategy` (optional) - filter by strategy name
- `limit` (optional, default 1000, max 10000)
- `from_date` (optional) - ISO 8601 date
- `to_date` (optional) - ISO 8601 date

**Response:**

```json
{
  "success": true,
  "data": {
    "total": 1250,
    "trades": [
      {
        "id": 12345,
        "timestamp": "2025-03-18T15:30:00Z",
        "marketId": "KXHI-25MAR18-0.75-IS_RAISING",
        "marketTitle": "Will inflation rise above 3.5%?",
        "strategy": "news_sentiment",
        "action": "buy",
        "quantity": 50,
        "entryPrice": 0.48,
        "exitPrice": 0.52,
        "pnl": 2.00,
        "confidence": 0.72,
        "positionSizePct": 0.05,
        "stopLossPct": 0.05,
        "takeProfitPct": 0.10,
        "exitReason": "take_profit",
        "metadata": {
          "signalType": "news_spike",
          "newsVolume": 15,
          "avgCorrelation": 0.32,
          "volatilityRegime": "normal"
        },
        "closedAt": "2025-03-18T16:00:00Z"
      }
    ]
  }
}
```

---

#### GET /api/regime

**Description:** Current market regime classification.

**Response:**

```json
{
  "success": true,
  "data": {
    "volatilityRegime": "high",  // "low", "normal", "high"
    "volatilityPercentile": 0.87,
    "newsDensity": 23,  // news articles in last 24h
    "avgCorrelation": 0.68,
    "activeMarketsCount": 45,
    "timestamp": "2025-03-18T19:00:00Z"
  }
}
```

---

#### GET /api/settings

**Description:** Returns all current configuration parameters.

**Response:**

```json
{
  "success": true,
  "data": {
    "kellyFraction": 0.55,
    "maxPositionSizePct": 0.08,
    "stopLossPct": 0.05,
    "takeProfitPct": 0.10,
    "newsSentimentThreshold": 0.65,
    "statArbitrageThreshold": 0.045,
    "volatilityThreshold": 0.12,
    "strategyEnablement": {
      "newsSentiment": true,
      "statisticalArbitrage": true,
      "volatilityBased": false
    },
    "notifications": {
      "telegram": false,
      "trades": true,
      "errors": true,
      "performance": true
    }
  }
}
```

---

#### POST /api/settings

**Description:** Update one or more settings. Validates guardrails before applying.

**Request Body:**

```json
{
  "kellyFraction": 0.60,
  "maxPositionSizePct": 0.10,
  "newsSentimentThreshold": 0.70
}
```

**Response (success):**

```json
{
  "success": true,
  "data": {
    "updated": ["kellyFraction", "maxPositionSizePct"],
    "unchanged": [],
    "rejected": [],
    "message": "Settings updated successfully"
  }
}
```

**Response (validation error):**

```json
{
  "success": false,
  "error": "Validation failed",
  "code": "VALIDATION_ERROR",
  "details": {
    "kellyFraction": {
      "provided": 0.95,
      "min": 0.1,
      "max": 0.8,
      "error": "Value exceeds maximum allowed (0.8)"
    }
  }
}
```

**Idempotency:** Duplicate updates (same value) return success with no change logged.

---

#### GET /api/settings/history?parameter=&limit=100

**Description:** Audit trail of setting changes.

**Query Parameters:**
- `parameter` (optional) - filter by parameter name
- `limit` (optional, default 100)

**Response:**

```json
{
  "success": true,
  "data": {
    "history": [
      {
        "id": 45,
        "changedAt": "2025-03-18T14:00:00Z",
        "parameter": "kellyFraction",
        "oldValue": "0.50",
        "newValue": "0.55",
        "source": "agent",
        "reason": "Sharpe > 2.0 for news_sentiment (1.95→2.10)",
        "agentDecisionId": 123
      }
    ]
  }
}
```

---

#### GET /api/settings/rollback?timestamp=ISO8601

**Description:** Revert all settings to state at given timestamp.

**Query Parameters:**
- `timestamp` (required) - ISO 8601 timestamp

**Response:**

```json
{
  "success": true,
  "data": {
    "rolledBack": 5,
    "settings": {
      "kellyFraction": 0.50,
      "maxPositionSizePct": 0.05
    },
    "message": "Rolled back to 2025-03-18T12:00:00Z"
  }
}
```

**Behavior:**
- Finds the most recent settings snapshot ≤ given timestamp
- Applies all parameters from that snapshot (source='rollback')
- Fails if any guardrail would be violated (partial rollback not allowed)

---

#### POST /api/start-trading

**Description:** Starts automated trading. Checks circuit breaker first.

**Response:**

```json
{
  "success": true,
  "data": {
    "trading": true,
    "message": "Trading started"
  }
}
```

**Errors:**
- `403` if circuit breaker active

---

#### POST /api/stop-trading

**Description:** Stops all trading (no new orders, existing positions remain).

**Response:**

```json
{
  "success": true,
  "data": {
    "trading": false,
    "message": "Trading stopped"
  }
}
```

---

#### GET /api/health

**Description:** Liveness probe for monitoring.

**Response:**

```json
{
  "status": "healthy",
  "timestamp": "2025-03-18T19:00:00Z",
  "components": {
    "bot": "running",
    "database": "connected",
    "api": "ok"
  },
  "uptime": 12345
}
```

---

## 5. AGENT DECISION LOGIC

### 5.1 Hypothesis Generation

The Agent runs every 6 hours and generates candidate adjustments. Each hypothesis includes:

```typescript
interface Hypothesis {
  id: string;  // UUID
  type: 'threshold_adjust' | 'strategy_enable' | 'strategy_disable' | 'position_size_adjust';
  strategy?: string;
  parameter?: string;
  currentValue: number | boolean;
  suggestedValue: number | boolean;
  rationale: string;
  triggerCondition: string;  // human-readable
  backtestWindow: { trades: number, days: number };
}
```

### 5.2 Backtesting Methodology

For threshold adjustments:
1. Fetch last N trades for strategy (N ≥ 50)
2. Split: evaluate threshold on all trades
3. Compute win rate and average P&L above/below candidate threshold
4. Perform Welch's t-test (unequal variance) on returns
5. Hypothesis accepted if:
   - p_value < 0.05
   - Effect size (win_rate_high - win_rate_low) > 0.10
   - Number of trades above threshold ≥ 10

For strategy enable/disable:
1. Check recent performance (last 20 trades)
2. Strategy disabled if:
   - Total trades < 5 in last 7 days (inactive)
   - Win rate < 0.35 with confidence (p < 0.1)
   - Max drawdown > 0.15
3. Strategy enabled if:
   - Was previously disabled
   - Regime condition favorable (e.g., volatility regime matches strategy)
   - Recent win rate > 0.55

### 5.3 Adjustment Computation

**Conservative approach:**
- Move parameter 50% of the way from current to suggested value
- Round to nearest valid step (e.g., 0.05 for Kelly)
- Never exceed guardrails

Example:
```
Current Kelly = 0.50, Suggested = 0.65
Adjustment = 0.50 + (0.65 - 0.50) * 0.5 = 0.575 → round to 0.58
```

### 5.4 Application and Logging

After adjustment:
1. POST to `/api/settings` with single parameter update
2. If success (200):
   - Insert into `agent_decisions` with `applied=true`
   - Include `metrics_before` snapshot
3. If fail (400/403):
   - Insert into `agent_decisions` with `applied=false`
   - Include error details in `rationale`

---

## 6. SAFETY GUARDRAILS

### 6.1 Parameter Guardrails (Validation Rules)

| Parameter | Type | Min | Max | Step | Default | Description |
|-----------|------|-----|-----|------|---------|-------------|
| `kellyFraction` | float | 0.1 | 0.8 | 0.05 | 0.5 | Kelly criterion fraction |
| `maxPositionSizePct` | float | 0.01 | 0.25 | 0.01 | 0.10 | Max % of bankroll per trade |
| `stopLossPct` | float | 0.01 | 0.20 | 0.01 | 0.05 | Stop loss percentage |
| `takeProfitPct` | float | 0.02 | 0.50 | 0.02 | 0.10 | Take profit percentage |
| `newsSentimentThreshold` | float | 0.3 | 0.9 | 0.05 | 0.6 | Minimum sentiment confidence |
| `statArbitrageThreshold` | float | 0.01 | 0.20 | 0.01 | 0.05 | Z-score threshold |
| `volatilityThreshold` | float | 0.05 | 0.30 | 0.02 | 0.10 | Volatility percentile |
| `tradeIntervalSeconds` | int | 30 | 3600 | 30 | 60 | Market data poll interval |

**Validation:** Enforced in `/api/settings` POST handler. Out‑of‑range values rejected with 400.

### 6.2 Circuit Breaker Rules

**Immediate Halt (403 on /api/start-trading, auto stop if running):**

| Condition | Threshold | Action |
|-----------|-----------|--------|
| Portfolio drawdown | > 10% | Stop trading, alert |
| 24h loss | > 5% of bankroll | Stop trading, alert |
| Position exposure | > 100% total | Reject new trades |
| Single trade size | > 25% of bankroll | Reject trade |
| API error rate (1h) | > 20% | Stop trading, retry after 5m |
| Bot heartbeat timeout | > 60s | Restart bot process |

**State Machine:**

```
ACTIVE → PAUSED_DRAWDOWN (manual resume required)
ACTIVE → PAUSED_ERROR (auto-resume after 5m if clean)
PAUSED_ERROR → ACTIVE (after 5m if error rate < 5%)
ANY → HALTED (manual intervention required)
```

**Check Frequency:** Every 5 minutes via `safety_monitor.py` script (cron).

### 6.3 Rollback Triggers

Automatic rollback if:
- Setting change → performance degrades > 20% in next 24h
- Agent applies 3+ changes in 6h (rate limit)
- Sharpe ratio drops below 0.5 for 3 consecutive days

Rollback restores to last known‑good settings snapshot (marked in `settings_history` with `source='manual'` or successful agent change with positive metrics).

---

## 7. ERROR HANDLING & RECOVERY

### 7.1 API Errors

| Scenario | HTTP Status | Response | Retry Logic |
|----------|-------------|----------|-------------|
| Bot not running | 503 | `{error: "Bot offline"}` | N/A (must restart bot) |
| Validation error | 400 | `{error: "...", details: {...}}` | Fix request |
| Circuit breaker | 403 | `{error: "Trading paused: drawdown > 10%"}` | Wait for reset |
| Database locked | 500 | `{error: "Database busy"}` | Retry 3× with 100ms backoff |
| Unknown exception | 500 | `{error: "Internal error"}` | Log and alert |

### 7.2 Agent Failure Modes

| Failure | Detection | Recovery |
|---------|-----------|----------|
| Cannot fetch /api/performance | HTTP 5xx | Retry 3× with exponential backoff, then skip cycle |
| Database write error | sqlite3.OperationalError | Rotate log file, continue |
| Invalid adjustment value | Guardrail rejection | Log, do not apply, continue |
| Python bot crashed | ping /api/status returns 503 | Interface should auto‑restart; if fails, alert |
| Long backtest (> 30s) | Timeout | Kill process, skip hypothesis |

### 7.3 Data Corruption Recovery

If `trades` table corrupted:
1. Stop bot and agent
2. Restore from latest backup: `scripts/restore.sh`
3. Recompute `performance_metrics` from scratch:
   ```sql
   DELETE FROM performance_metrics;
   INSERT INTO performance_metrics
   SELECT date(created_at), strategy, ... GROUP BY date, strategy;
   ```

---

## 8. PERFORMANCE & SCALING

### 8.1 Requirements

| Metric | Requirement |
|--------|-------------|
| Agent cycle time | < 60 seconds (including all API calls) |
| Bot_interface response (p50) | < 200ms |
| Database write latency | < 50ms per trade |
| Memory usage (bot) | < 500MB |
| Memory usage (agent) | < 200MB |
| Uptime | > 99.5% |

### 8.2 Optimization

- **SQLite WAL mode** for concurrent reads/writes
- **Connection pooling** in bot_interface (5 connections)
- **Batch inserts** for trade logging (queue up to 100 trades)
- **Indexes** on `trades(timestamp)`, `trades(strategy)` for fast queries
- **Cache** settings in memory (refresh on change via Redis pub/sub future)

### 8.3 Future Scaling

If > 1000 trades/day:
- Partition `trades` by month
- Move analytics to PostgreSQL (Citus)
- Cache performance metrics in Redis (1h TTL)

---

## 9. OBSERVABILITY

### 9.1 Logging Format (JSON)

```json
{
  "timestamp": "2025-03-18T19:00:00Z",
  "level": "INFO",
  "component": "bot_interface",
  "message": "Settings updated",
  "details": {"parameter": "kellyFraction", "value": 0.55, "source": "agent"}
}
```

**Components:** `bot_interface`, `bot`, `agent`, `database`, `safety`

**Levels:** `DEBUG`, `INFO`, `WARNING`, `ERROR`

Log files:
- `logs/bot_interface.log`
- `logs/bot.log`
- `logs/agent.log`

### 9.2 Health Checks

**Endpoint:** `GET /health`

**Response:**

```json
{
  "status": "healthy",
  "timestamp": "2025-03-18T19:00:00Z",
  "uptime": 12345,
  "components": {
    "bot": {"status": "running", "lastHeartbeat": "2025-03-18T18:59:55Z"},
    "database": {"status": "connected", "latencyMs": 2},
    "api": {"status": "ok"}
  },
  "memory": {"rssMb": 245, "heapUsedMb": 180}
}
```

**Unhealthy** if:
- Bot not running
- DB query latency > 100ms
- Memory > 500MB

### 9.3 Metrics (Prometheus format - future)

```
# HELP kalshi_trades_total Total number of trades executed
# TYPE kalshi_trades_total counter
kalshi_trades_total{strategy="news_sentiment"} 45

# HELP kalshi_pnl_usd TotalPnL in USD
# TYPE kalshi_gauge gauge
kalshi_pnl_usd 245.67

# HELP kalshi_drawdown_ratio Current drawdown as fraction
# TYPE kalshi_drawdown_ratio gauge
kalshi_drawdown_ratio 0.082

# HELP agent_adjustments_total Number of parameter adjustments
# TYPE agent_adjustments_total counter
agent_adjustments_total{parameter="kellyFraction"} 12
```

---

## 10. DEPLOYMENT TOPOLOGY

### 10.1 Directory Layout

```
/home/q/projects/KALSHI – adaptive – agent/
├── data/
│   ├── kalshi.db          # Main database (trades, settings)
│   ├── agent.db           # Agent decisions
│   └── backups/           # Daily backups
├── logs/
│   ├── bot_interface.log
│   ├── bot.log
│   └── agent.log
├── src/
│   ├── main.py            # Entry point
│   ├── trader.py
│   ├── logger.py
│   ├── settings_manager.py
│   ├── bot_state.py
│   └── strategies/
├── bot_interface.js       # Express server (root)
├── agent_loop.py          # Agent main loop
├── scripts/
│   ├── setup_db.sh
│   ├── backup.sh
│   └── restore.sh
├── .env.example
├── requirements.txt
├── package.json
├── docker-compose.yml
├── Dockerfile.bot_interface
├── Dockerfile.bot
└── README.md
```

### 10.2 Startup Order

1. `scripts/setup_db.sh` - initialize SQLite files
2. `node bot_interface.js &` - start API server on :3050
3. `python src/main.py &` - or use `/api/start-trading` to spawn
4. `cron add "*/6 * * * *" agent=Qaster task="Run agent cycle"`

**Verification:**
```bash
curl http://localhost:3050/api/health
# Should return {"status":"healthy",...}
```

### 10.3 Environment Variables

```bash
# Kalshi API
KALSHI_API_KEY=your_key
KALSHI_API_BASE_URL=https://api.elections.kalshi.com/trade-api/v2

# Bot configuration
BANKROLL=1000
TRADE_INTERVAL_SECONDS=60
DRY_RUN=true  # Set false for live trading

# Logging
LOG_LEVEL=INFO
LOG_FILE_PATH=logs/bot.log

# Alerting (optional)
ALERT_EMAIL=you@example.com
```

---

## 11. TEST CASES

### 11.1 Unit Tests

**test_logger.py:**
```python
def test_trade_logging():
    logger = TradeLogger(':memory:')
    trade = {'market_id': 'TEST', 'strategy': 'news', 'action': 'buy', ...}
    logger.log_trade(trade)
    trades = logger.get_trades()
    assert len(trades) == 1
    assert trades[0]['market_id'] == 'TEST'
```

**test_settings_manager.py:**
```python
def test_setting_validation():
    sm = SettingsManager(':memory:')
    sm.update('kellyFraction', 0.9, 'test', 'test')  # Should raise ValueError
```

### 11.2 Integration Tests

1. **Settings persistence:**
   - POST `/api/settings` with `{"kellyFraction": 0.6}`
   - Verify DB: `SELECT value FROM current_settings WHERE parameter='kellyFraction'`
   - Verify history: `SELECT * FROM settings_history WHERE parameter='kellyFraction' ORDER BY id DESC LIMIT 1`

2. **Circuit breaker:**
   - Insert position with `pnl = -60` into trades (simulate 6% loss on $1000 bankroll)
   - Call safety check → should return True (trigger)
   - Call `/api/start-trading` → expect 403

3. **Agent adjustment flow:**
   - Seed trades with high correlation
   - Run agent_loop.py
   - Verify `max_position_size_pct` decreased in `current_settings`
   - Verify `agent_decisions` row inserted with `applied=true`

### 11.3 End-to-End Paper Trading

**Scenario 1: Normal operation**
- Start bot dry‑run
- Let it generate 20 trades
- Run agent cycle
- Verify at least one parameter adjustment
- Verify no guardrail violations

**Scenario 2: Drawdown trigger**
- Simulate losing trades (insert into DB)
- Run safety_monitor.py
- Verify `/api/stop-trading` called
- Verify alert sent

---

## 12. OPEN CLAUSES & QUESTIONS

1. **Should agent be allowed to create new strategies?** (future: generate Python code and hot-reload)
2. **How often should agent run?** Every 6h? Every 1h? Configurable?
3. **What is the exact Sharpe calculation?** (risk‑free rate? annualization? lookback period)
4. **Do we need position closing logic?** Currently only stop‑loss; add time‑based exits?
5. **Should agent have "sandbox" mode?** First 30 days only recommend, don't apply automatically.

---

## APPENDIX A: SAMPLE AGENT DECISION JSON

```json
{
  "decision_type": "parameter_tuning",
  "parameters_modified": {"kellyFraction": 0.58},
  "rationale": "Sharpe ratio for news_sentiment improved from 1.85 to 2.10 over last 50 trades; Bayesian optimization suggests optimal Kelly 0.59, conservatively set to 0.58",
  "hypothesis_tested": "Higher Kelly improves returns without increasing drawdown proportionally",
  "p_value": 0.032,
  "effect_size": 0.15,
  "metrics_before": {
    "portfolio_sharpe": 1.85,
    "portfolio_drawdown": 0.082
  },
  "metrics_after": null,
  "applied": true
}
```

---

## APPENDIX B: GUARDRAIL VIOLATION RESPONSE

```json
{
  "success": false,
  "error": "Guardrail violation",
  "code": "GUARDRAIL_VIOLATION",
  "details": {
    "parameter": "kellyFraction",
    "provided": 0.95,
    "allowed": {"min": 0.1, "max": 0.8},
    "message": "Value exceeds maximum allowed (0.8)"
  },
  "timestamp": "2025-03-18T19:00:00Z"
}
```

---

**End of Technical Specification**
