# Agent Decision Logic Specification

**Version:** 1.0.0  
**Component:** Adaptive Agent (agent_loop.py)  
**K-ATA Project**

---

## 1. OVERVIEW

The Adaptive Agent runs every 6 hours (configurable) and performs:

1. **Hypothesis Generation** – detect potential parameter improvements
2. **Backtesting** – validate hypotheses on out-of-sample data
3. **Adjustment Computation** – calculate safe parameter values
4. **Application** – POST updates to bot interface API
5. **Logging** – record decisions for audit

The agent is **stateless between runs** (all state persisted in DB). It can be run as a cron job without in‑memory state.

---

## 2. HYPOTHESIS GENERATION

### 2.1 Hypothesis Types

| Type | Trigger Condition | Target Parameter | Suggested Value | Rationale |
|------|-------------------|------------------|-----------------|-----------|
| `threshold_adjust` | Strategy win rate < threshold OR false positive rate high | `newsSentimentThreshold`, `statArbitrageThreshold`, `volatilityThreshold` | Increase by 0.05-0.10 | Filter low-quality signals |
| `strategy_disable` | Strategy inactive or underperforming | `strategyEnablement.{strategy}` | `false` | Save capital, avoid noise |
| `strategy_enable` | Regime favorable + previously disabled | `strategyEnablement.{strategy}` | `true` | Capture opportunity |
| `position_size_adjust` | High correlation (> 0.8) between strategies | `maxPositionSizePct` | Decrease by 50% | Reduce concentration risk |
| `kelly_adjust` | Sharpe ratio > 2.0 or < 1.0 | `kellyFraction` | Increase toward 0.8 or decrease toward 0.1 | Optimize growth vs safety |

### 2.2 Generation Pseudocode

```python
def generate_hypotheses(performance, trades, regime):
    hypotheses = []

    # H1: News sentiment threshold too low
    news_perf = get_strategy_perf(performance, 'news_sentiment')
    if news_perf and news_perf['winRate'] < 0.5:
        # Find optimal threshold via quick analysis
        confidences = [t['confidence'] for t in get_recent_trades(trades, 'news_sentiment', 100)]
        optimal = compute_optimal_threshold(confidences)
        if optimal > news_perf['currentThreshold']:
            hypotheses.append({
                'type': 'threshold_adjust',
                'strategy': 'news_sentiment',
                'parameter': 'newsSentimentThreshold',
                'current': news_perf['currentThreshold'],
                'suggested': min(optimal, 0.9),
                'rationale': f'Win rate {news_perf["winRate"]:.2f} below 50%; optimal threshold ~{optimal:.2f}'
            })

    # H2: Strategy correlation risk
    correlations = compute_strategy_correlations(trades, window=50)
    high_corr_pairs = [(s1, s2, c) for s1, d in correlations.items()
                       for s2, c in d.items() if s1 < s2 and c > 0.8]
    if high_corr_pairs:
        # Reduce position size to offset correlation
        current_max = get_setting('maxPositionSizePct')
        suggested = max(0.05, current_max * 0.5)
        hypotheses.append({
            'type': 'position_size_adjust',
            'parameter': 'maxPositionSizePct',
            'current': current_max,
            'suggested': suggested,
            'rationale': f'High correlation detected: {high_corr_pairs}'
        })

    # H3: Kelly adjustment based on Sharpe
    for strat_perf in performance['performance']:
        current_kelly = float(get_setting('kellyFraction'))
        if strat_perf['sharpeRatio'] > 2.0:
            suggested = min(0.80, current_kelly * 1.1)
            if suggested > current_kelly + 0.05:
                hypotheses.append({
                    'type': 'kelly_adjust',
                    'strategy': strat_perf['strategy'],
                    'parameter': 'kellyFraction',
                    'current': current_kelly,
                    'suggested': round(suggested, 2),
                    'rationale': f'Sharpe {strat_perf["sharpeRatio"]:.2f} > 2.0 → increase Kelly'
                })
        elif strat_perf['sharpeRatio'] < 1.0:
            suggested = max(0.10, current_kelly * 0.9)
            if suggested < current_kelly - 0.05:
                hypotheses.append({
                    'type': 'kelly_adjust',
                    'strategy': strat_perf['strategy'],
                    'parameter': 'kellyFraction',
                    'current': current_kelly,
                    'suggested': round(suggested, 2),
                    'rationale': f'Sharpe {strat_perf["sharpeRatio"]:.2f} < 1.0 → decrease Kelly'
                })

    # H4: Disable strategy with insufficient activity
    for strat_perf in performance['performance']:
        if strat_perf['totalTrades'] < 10 and strat_perf['sharpeRatio'] < 0.5:
            hypotheses.append({
                'type': 'strategy_disable',
                'strategy': strat_perf['strategy'],
                'rationale': f'Only {strat_perf["totalTrades"]} trades with Sharpe {strat_perf["sharpeRatio"]:.2f}'
            })

    # H5: Enable volatility strategy in high-vol regime
    if regime['volatilityRegime'] == 'high':
        vol_enabled = get_setting('strategyEnablement.volatilityBased')
        if not vol_enabled:
            hypotheses.append({
                'type': 'strategy_enable',
                'strategy': 'volatility_based',
                'rationale': 'High volatility regime detected; volatility strategy may perform well'
            })

    return hypotheses
```

---

## 3. BACKTESTING METHODOLOGY

### 3.1 Threshold Adjustment Backtest

For candidate threshold `T` on strategy `S`:

1. Fetch last `N` trades for `S` (default `N=100`, minimum 50)
2. Compute win rate for trades with `confidence >= T` (high-confidence group)
3. Compute win rate for trades with `confidence < T` (low-confidence group)
4. Perform Welch's t‑test on returns:
   ```python
   returns_high = [t['pnl']/t['entry_price'] for t in high_conf_trades]
   returns_low = [t['pnl']/t['entry_price'] for t in low_conf_trades]
   t_stat, p_value = scipy.stats.ttest_ind(returns_high, returns_low, equal_var=False)
   ```
5. hypothesis accepted if:
   - `p_value < 0.05`
   - `win_rate_high - win_rate_low > 0.10` (at least 10 percentage point improvement)
   - `len(high_conf_trades) >= 10` (sufficient sample)

### 3.2 Strategy Enable/Disable Backtest

For strategy `S`:

1. Get trades for last 30 days
2. If `total_trades < 5` → flag as inactive (disable)
3. Compute rolling Sharpe (20-trade window)
4. If median Sharpe < 0.5 and win rate < 0.4 for 3 consecutive windows → disable
5. For enable: check regime match and recent uptrend in win rate

### 3.3 Correlation-Based Position Sizing

1. Compute correlation matrix of strategy returns (last 50 trades per strategy)
2. If average pairwise correlation > 0.8:
   - Suggested `maxPositionSizePct = current * 0.5`
3. Validate by comparing P&L of correlated periods vs uncorrelated:
   - If correlated periods had lower Sharpe, backtest supports reduction

---

## 4. ADJUSTMENT COMPUTATION

### 4.1 Conservative Scaling

The agent never jumps directly to the suggested value. Instead:

```
adjusted = current + (suggested - current) * 0.5
```

This "half‑step" approach prevents over‑reacting to noise.

**Rounding rules:**
- Kelly fraction: round to 2 decimals (0.05 increments)
- Position size %: round to 2 decimals (0.01 increments)
- Thresholds: round to 2 decimals (0.05 increments)
- Boolean settings: no rounding

### 4.2 Guardrail Enforcement

Before applying, check:

```python
def validate_guardrail(parameter, value):
    guardrails = {
        'kellyFraction': (0.1, 0.8),
        'maxPositionSizePct': (0.01, 0.25),
        'stopLossPct': (0.01, 0.20),
        'newsSentimentThreshold': (0.3, 0.9),
        # ...
    }
    if parameter in guardrails:
        min_v, max_v = guardrails[parameter]
        if not (min_v <= value <= max_v):
            return False, f"Value {value} outside [{min_v}, {max_v}]"
    return True, ""
```

If validation fails, the adjustment is **rejected** (logged but not applied). Agent does not retry with nearer value automatically.

---

## 5. APPLICATION & LOGGING

### 5.1 API Call Sequence

```python
def apply_adjustment(parameter, value, rationale, hypothesis_id=None):
    payload = {parameter: value}
    response = requests.post(
        f"{BASE_URL}/api/settings",
        json=payload,
        timeout=10
    )

    if response.status_code == 200:
        result = response.json()
        log_decision(parameter, value, rationale, hypothesis_id, applied=True)
        return True
    else:
        error = response.json().get('error', 'Unknown error')
        log_decision(parameter, value, f"{rationale}; FAILED: {error}", hypothesis_id, applied=False)
        return False
```

### 5.2 Decision Log Schema

`agent_decisions` table fields:

| Field | Type | Description |
|-------|------|-------------|
| `decision_type` | TEXT | One of: parameter_tuning, strategy_enable, strategy_disable, circuit_breaker, rollback, hypothesis_generated |
| `parameters_modified` | JSON | `{"kellyFraction": 0.58}` |
| `rationale` | TEXT | Human‑readable explanation |
| `hypothesis_tested` | TEXT | Short description of hypothesis |
| `p_value` | REAL | Statistical significance (0-1) if backtested |
| `effect_size` | REAL | Estimated improvement (win rate diff, Sharpe improvement, etc.) |
| `metrics_before` | JSON | Snapshot of performance at time of decision |
| `metrics_after` | JSON | Snapshot after change (if applicable) |
| `applied` | BOOLEAN | True if successfully applied to bot |

---

## 6. SAFETY CHECKS IN AGENT LOOP

The agent performs these checks **before** applying any adjustment:

1. **Circuit breaker active?**  
   Call `/api/status` → if `trading: false`, skip all adjustments (log and exit)

2. **Rate limit exceeded?**  
   Count agent decisions in last 24h → if > 3, skip parameter tuning (only log hypotheses)

3. **Guardrail validation:**  
   Before POST, compute guardrail check locally; if would fail, reject early

4. **Rollback safety:**  
   Never apply adjustment if it would cause settings to differ from last known‑good (manual or successful agent) by > 20% (compute L2 norm of parameter vector)

---

## 7. AGENT STATE MACHINE

```
START
  |
  v
FETCH_DATA (performance, trades, regime, settings)
  |
  v
CHECK_CIRCUIT_BREAKER
  |
  +-- if triggered --> LOG & EXIT
  |
  v
GENERATE_HYPOTHESES (5-10 candidates)
  |
  v
BACKTEST_EACH (parallel if possible)
  |
  +-- rejected --> LOG (reason: backtest fail)
  |
  v
COMPUTE_ADJUSTMENT (conservative 50% step)
  |
  v
VALIDATE_GUARDRAILS
  |
  +-- invalid --> LOG (reason: guardrail) & SKIP
  |
  v
APPLY_VIA_API
  |
  +-- success --> LOG (applied=true)
  +-- failure --> LOG (applied=false, error)
  |
  v
EXIT (cycle complete)
```

**Cycle time target:** < 60 seconds from start to exit.

---

## 8. ERROR HANDLING

| Error | Handling |
|-------|----------|
| `/api/performance` returns 5xx | Retry 3× with 2s backoff; if still fails, skip cycle, alert |
| `requests.Timeout` | Log and continue; next cycle will try again |
| Database lock in `agent.db` | Rotate log file, retry once; if still fails, exit with error |
| JSON decode error from bot API | Alert; skip cycle; possible bot crash |
| Hypothesis backtest fails (sample too small) | Log and skip; continue with others |

**Agent failures** should be logged to `logs/agent.log` and not crash the cron job. OpenClaw will report if the agent process exits non‑zero.

---

## 9. CONFIGURATION

Agent is configured via environment variables:

```bash
# Bot interface location
BASE_URL = os.getenv('BASE_URL', 'http://localhost:3001')

# Database locations
KALSHI_DB = '/home/q/projects/KALSHI – adaptive – agent/data/kalshi.db'
AGENT_DB = '/home/q/projects/KALSHI – adaptive – agent/data/agent.db'

# Scheduling (used by OpenClaw cron, not agent itself)
CYCLE_INTERVAL_HOURS = 6

# Guardrails (can override defaults)
MAX_ADJUSTMENTS_PER_DAY = 5
MIN_DAYS_BETWEEN_STRATEGY_TOGGLE = 3
```

---

## 10. FUTURE EXTENSIONS

### 10.1 Advanced Backtesting

- Walk‑forward analysis (rolling window with expanding training set)
- Monte Carlo simulation of parameter robustness
- Bayesian optimization instead of simple 50% step

### 10.2 Machine Learning Integration

- Train a lightweight model to predict optimal Kelly for next trade based on regime features
- Reinforcement learning to learn adjustment policy
- Anomaly detection to flag regime shifts earlier

### 10.3 Multi‑Strategy Portfolio Optimization

- Mean‑variance optimization across strategies
- Dynamic risk budgeting based on forecast confidence

---

**End of Agent Logic Spec**
