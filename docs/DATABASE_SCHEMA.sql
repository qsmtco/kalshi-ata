-- Kalshi Adaptive Trading Agent - Database Schema
-- Version: 1.0.0
-- SQLite 3

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA synchronous = NORMAL;

-- Trades table: every executed trade (real or dry-run)
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    market_id TEXT NOT NULL,
    market_title TEXT,
    strategy TEXT NOT NULL CHECK(strategy IN (
        'news_sentiment',
        'statistical_arbitrage',
        'volatility_based'
        -- Future: 'agent_generated_%' pattern
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

-- Indexes for fast querying
CREATE INDEX IF NOT EXISTS idx_trades_strategy ON trades(strategy);
CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(created_at);
CREATE INDEX IF NOT EXISTS idx_trades_open ON trades(closed_at) WHERE closed_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_trades_closed ON trades(closed_at) WHERE closed_at IS NOT NULL;

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
    FOREIGN KEY (agent_decision_id) REFERENCES agent_decisions(id) ON DELETE SET NULL
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
    parameters_modified TEXT,  -- JSON: {"kellyFraction": 0.55}
    rationale TEXT NOT NULL,
    hypothesis_tested TEXT,  -- short description
    p_value REAL,  -- statistical significance if backtested
    effect_size REAL,  -- estimated improvement
    metrics_before TEXT,  -- JSON snapshot
    metrics_after TEXT,   -- JSON snapshot (if applicable)
    applied BOOLEAN DEFAULT 1  -- False if rejected by guardrails
);

CREATE INDEX IF NOT EXISTS idx_agent_decisions_type_ts ON agent_decisions(decision_type, decided_at);

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

-- Latest performance by strategy (rolling 30 days)
CREATE VIEW IF NOT EXISTS latest_performance AS
WITH recent_trades AS (
    SELECT * FROM trades
    WHERE created_at >= datetime('now', '-30 days')
)
SELECT
    strategy,
    COUNT(*) as total_trades,
    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as winning_trades,
    SUM(pnl) as total_pnl,
    AVG(CASE WHEN pnl > 0 THEN pnl END) as avg_win,
    AVG(CASE WHEN pnl <= 0 THEN pnl END) as avg_loss,
    MAX(drawdown) as max_drawdown
FROM (
    SELECT
        t.*,
        (SELECT MAX(SUM(pnl2)) FROM trades t2
         WHERE t2.strategy = t.strategy
         AND t2.closed_at <= t.closed_at
         AND t2.closed_at IS NOT NULL) as drawdown
    FROM recent_trades t
    WHERE closed_at IS NOT NULL
) sub
GROUP BY strategy;

-- Trigger: auto-update performance_metrics when trade closed
CREATE TRIGGER IF NOT EXISTS trg_update_performance
AFTER UPDATE OF closed_at ON trades
WHEN NEW.closed_at IS NOT NULL
BEGIN
    INSERT OR REPLACE INTO performance_metrics (metric_date, strategy, total_trades, winning_trades, total_pnl, avg_win, avg_loss, max_drawdown)
    SELECT
        date(NEW.closed_at) as metric_date,
        NEW.strategy,
        COUNT(*) as total_trades,
        SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as winning_trades,
        SUM(pnl) as total_pnl,
        AVG(CASE WHEN pnl > 0 THEN pnl END) as avg_win,
        AVG(CASE WHEN pnl <= 0 THEN pnl END) as avg_loss,
        MAX(drawdown) as max_drawdown
    FROM (
        SELECT t.*,
               (SELECT MAX(SUM(pnl2)) FROM trades t2
                WHERE t2.strategy = t.strategy
                AND t2.closed_at <= t.closed_at
                AND t2.closed_at IS NOT NULL) as drawdown
        FROM trades t
        WHERE t.strategy = NEW.strategy
        AND t.closed_at IS NOT NULL
        AND t.closed_at >= date(NEW.closed_at, '-30 days')
    ) sub;
END;

-- Insert default settings
INSERT OR IGNORE INTO current_settings (parameter, value) VALUES
    ('kellyFraction', '0.50'),
    ('maxPositionSizePct', '0.10'),
    ('stopLossPct', '0.05'),
    ('takeProfitPct', '0.10'),
    ('newsSentimentThreshold', '0.60'),
    ('statArbitrageThreshold', '0.05'),
    ('volatilityThreshold', '0.10'),
    ('tradeIntervalSeconds', '60');

INSERT OR IGNORE INTO settings_history (parameter, old_value, new_value, source, reason)
SELECT parameter, NULL, value, 'default', 'Initial default'
FROM current_settings;
