# Safety Guardrails Specification

**Version:** 1.0.0  
**Component:** Safety Systems  
**K-ATA Project**

---

## 1. SAFETY PHILOSOPHY

The Kalshi Adaptive Trading Agent is designed with a **safety-first** approach:

- **Deterministic core:** Trading strategies are rule‑based, not black‑box
- **Bounded adaptation:** Agent can only adjust parameters within hard limits
- **Transparency:** All decisions logged with rationale
- **Rollback:** One‑click revert to any previous state
- **Circuit breakers:** Automatic and immediate halt when risk thresholds breached

This document enumerates every guardrail and its enforcement mechanism.

---

## 2. PARAMETER GUARDRAILS

All parameter updates (via `/api/settings`) are validated against the following table before acceptance:

| Parameter | Type | Min | Max | Step | Default | Error Code |
|-----------|------|-----|-----|------|---------|------------|
| `kellyFraction` | float | 0.1 | 0.8 | 0.05 | 0.50 | `GUARDRAIL_KELLY_OUT_OF_BOUNDS` |
| `maxPositionSizePct` | float | 0.01 | 0.25 | 0.01 | 0.10 | `GUARDRAIL_POS_SIZE_OUT_OF_BOUNDS` |
| `stopLossPct` | float | 0.01 | 0.20 | 0.01 | 0.05 | `GUARDRAIL_STOP_LOSS_OUT_OF_BOUNDS` |
| `takeProfitPct` | float | 0.02 | 0.50 | 0.02 | 0.10 | `GUARDRAIL_TAKE_PROFIT_OUT_OF_BOUNDS` |
| `newsSentimentThreshold` | float | 0.3 | 0.9 | 0.05 | 0.60 | `GUARDRAIL_THRESHOLD_OUT_OF_BOUNDS` |
| `statArbitrageThreshold` | float | 0.01 | 0.20 | 0.01 | 0.05 | `GUARDRAIL_THRESHOLD_OUT_OF_BOUNDS` |
| `volatilityThreshold` | float | 0.05 | 0.30 | 0.02 | 0.10 | `GUARDRAIL_THRESHOLD_OUT_OF_BOUNDS` |
| `tradeIntervalSeconds` | int | 30 | 3600 | 30 | 60 | `GUARDRAIL_INTERVAL_OUT_OF_BOUNDS` |

**Boolean flags** (`strategyEnablement.*`, `notifications.*`) have no min/max but must be true/false.

### 2.1 Enforcement Location

- **Primary:** `bot_interface.js` POST `/api/settings` handler
- **Secondary (belt‑and‑suspenders):** `SettingsManager.update()` method in Python (re‑validates even if manipulated outside API)

```javascript
const GUARDRAILS = {
  kellyFraction: { min: 0.1, max: 0.8, type: 'float', step: 0.05 },
  maxPositionSizePct: { min: 0.01, max: 0.25, type: 'float', step: 0.01 },
  // ... others
};

app.post('/api/settings', (req, res) => {
  for (const [key, value] of Object.entries(req.body)) {
    const guard = GUARDRAILS[key];
    if (guard) {
      if (value < guard.min || value > guard.max) {
        return res.status(400).json({
          success: false,
          error: 'Guardrail violation',
          code: `GUARDRAIL_${key.toUpperCase()}_OUT_OF_BOUNDS`,
          details: { parameter: key, provided: value, min: guard.min, max: guard.max }
        });
      }
    }
  }
  // ... apply updates
});
```

---

## 3. CIRCUIT BREAKER RULES

Circuit breaker monitors system health every 5 minutes via `safety_monitor.py` (cron) and also checks before allowing new trades.

### 3.1 Trigger Conditions

| Condition | Threshold | Action | Auto‑Reset |
|-----------|-----------|--------|------------|
| Portfolio drawdown | > 10% | STOP_TRADING, alert | Manual resume required |
| 24‑hour loss | > 5% of bankroll | STOP_TRADING, alert | Auto after 1h if stable |
| Position exposure | > 100% total (∑\|size\|) | Reject new trades | N/A (prevents entry) |
| Single trade size | > 25% of bankroll | Reject trade | N/A |
| API error rate (1h window) | > 20% | STOP_TRADING | Auto after 5m if error rate < 5% |
| Bot heartbeat timeout | > 60s | Restart bot process | Auto after restart |
| Data stale (market data) | > 5 min old | STOP_TRADING | Auto when fresh data arrives |

### 3.2 State Machine

```
              +-------------------+
              |     ACTIVE        |
              +-------------------+
                 |          |
    drawdown>10% |          | error_rate>20%
    or 24h loss>5% |          |
                 v          v
          +--------------+  +------------------+
          | PAUSED_ERROR |  | PAUSED_DRAWDOWN |
          +--------------+  +------------------+
                 |                 |
          after 5m if clean   manual resume only
                 |                 |
                 v                 |
          +-------------------+   |
          |     ACTIVE        |<--+
          +-------------------+
```

**Transitions:**
- `ACTIVE → PAUSED_ERROR`: error rate > 20% for > 1 minute
- `PAUSED_ERROR → ACTIVE`: after 5 minutes if error rate < 5% for 2 minutes
- `ACTIVE → PAUSED_DRAWDOWN`: drawdown > 10% OR 24h loss > 5%
- `PAUSED_DRAWDOWN → ACTIVE`: only via explicit `/api/start-trading` (after human investigation)

### 3.3 Implementation

```python
# safety_monitor.py
CIRCUIT_STATES = ['ACTIVE', 'PAUSED_ERROR', 'PAUSED_DRAWDOWN', 'HALTED']
current_state = 'ACTIVE'
state_since = datetime.now()

def check_circuit_breakers():
    metrics = get_current_metrics()  # from /api/performance?days=1

    if metrics['portfolio']['currentDrawdown'] > 0.10:
        if current_state != 'PAUSED_DRAWDOWN':
            set_state('PAUSED_DRAWDOWN', 'Drawdown > 10%')
        return True  # trigger stop

    if metrics['portfolio']['pnl_24h'] < -0.05 * BANKROLL:
        if current_state != 'PAUSED_DRAWDOWN':
            set_state('PAUSED_DRAWDOWN', '24h loss > 5%')
        return True

    api_error_rate = get_api_error_rate_last_hour()
    if api_error_rate > 0.20:
        if current_state == 'ACTIVE':
            set_state('PAUSED_ERROR', f'API error rate {api_error_rate:.1%}')
        return True

    # Auto‑reset checks
    if current_state == 'PAUSED_ERROR':
        if api_error_rate < 0.05 and (datetime.now() - state_since).seconds > 300:
            set_state('ACTIVE', 'Error rate normalized')
            send_alert('Circuit breaker reset: API error rate back to normal')

    return False
```

**Endpoint protection:** Before executing any trade, Python bot calls `validate_trade_safety()`, which checks `current_state` via Redis or shared file. If not `ACTIVE`, reject.

---

## 4. ROLLBACK MECHANISM

### 4.1 Rollback Trigger Conditions

Automatic rollback invoked if:
- Setting change → portfolio drawdown increases by > 2 percentage points within 24h
- Setting change → Sharpe ratio drops > 0.3 within 24h
- Agent applies > 3 adjustments in 6 hours (rate limiting → revert last change)
- Operator manually requests via `/api/settings/rollback?timestamp=...`

### 4.2 Rollback Procedure

1. Identify **last known‑good snapshot**: most recent row in `settings_history` with `source IN ('default', 'manual')` and timestamp before change, or explicitly marked as good (future: add `good boolean` column)
2. For each parameter changed after that snapshot:
   - Fetch old value from history
   - POST to `/api/settings` with old value (source='rollback')
3. Insert `agent_decisions` row with `decision_type='rollback'` and rationale
4. Send alert (email/Telegram) to operator

### 4.3 Rollback API

`GET /api/settings/rollback?timestamp=ISO8601`

- Restores all settings to the state **as of** that timestamp (the latest snapshot ≤ given time)
- Fails if any guardrail would be violated (partial rollback not allowed)
- Returns number of settings changed and the restored values

**Implementation:**

```javascript
app.get('/api/settings/rollback', (req, res) => {
  const { timestamp } = req.query;
  if (!timestamp) return res.status(400).json({ error: 'timestamp required' });

  // Get snapshot: all settings at that time
  const snapshot = db.query(`
    SELECT parameter, value FROM settings_history
    WHERE changed_at <= ? AND source IN ('default', 'manual', 'agent')
    GROUP BY parameter
    HAVING MAX(changed_at) <= ?
  `, [timestamp, timestamp]);

  // Validate all would pass guardrails
  for (const {parameter, value} of snapshot) {
    const guard = GUARDRAILS[parameter];
    if (guard && (value < guard.min || value > guard.max)) {
      return res.status(400).json({
        error: 'Rollback blocked by guardrail',
        parameter, value, min: guard.min, max: guard.max
      });
    }
  }

  // Apply all
  let changed = 0;
  for (const {parameter, value} of snapshot) {
    const current = get_current_setting(parameter);
    if (current != value) {
      update_setting(parameter, value, 'rollback', `Rollback to ${timestamp}`);
      changed++;
    }
  }

  res.json({ success: true, data: { rolledBack: changed, settings: snapshot } });
});
```

---

## 5. EXPOSURE LIMITS

### 5.1 Position Sizing

Maximum exposure enforced at trade execution:

```python
def validate_trade_safety(self, order):
    total_exposure = self.get_total_exposure()  # sum of |positions| as fraction of bankroll
    if total_exposure + order['positionSizePct'] > 1.0:
        raise SafetyError(f"Total exposure would exceed 100%: {total_exposure:.1%} + {order['positionSizePct']:.1%}")

    if order['positionSizePct'] > 0.25:
        raise SafetyError(f"Single trade size {order['positionSizePct']:.1%} exceeds 25% limit")

    if order['stopLossPct'] > 0.20:
        raise SafetyError(f"Stop loss {order['stopLossPct']:.1%} exceeds 20% limit")
```

### 5.2 Daily Loss Limit

If ` unrealized_pnl + realized_pnl_today < -0.05 * bankroll` → reject new trades, trigger circuit breaker.

```python
def check_daily_loss_limit():
    pnl_today = get_daily_pnl()  # from trades table
    if pnl_today < -0.05 * get_bankroll():
        raise SafetyError("Daily loss limit exceeded (5%)")
```

---

## 6. ALERTING

### 6.1 Alert Channels

- **OpenClaw notification** (via `message` tool or system event) – primary
- **Email** (optional via SMTP) – secondary
- **Log** – always

### 6.2 Alert Triggers

| Event | Severity | Message |
|-------|----------|---------|
| Circuit breaker triggered | HIGH | `⚠️ CIRCUIT BREAKER: {reason}. Trading halted.` |
| Rollback executed | HIGH | `🔙 ROLLBACK: Reverted to {timestamp} due to {reason}.` |
| Agent adjustment rejected | MEDIUM | `🚫 Agent adjustment blocked: {parameter}={value} violates guardrail.` |
| Bot crash | HIGH | `💥 Bot process crashed. Restarting...` |
| API error rate > 10% | MEDIUM | `⚠️ API error rate elevated: {rate:.1%}` |

### 6.3 Alert Rate Limiting

- Same alert type: at most once per 15 minutes (coalesce)
- Different types: no limit
- Critical alerts (circuit breaker, crash): always send

---

## 7. AUDIT TRAIL

All safety‑relevant events are logged to `agent_decisions` and `settings_history`:

- **Why:** Every parameter change has a `reason` and `metrics_before/after`
- **Who (or what):** `source` field indicates `agent` or `manual` or `rollback`
- **When:** ISO 8601 timestamps with timezone (UTC)
- **What:** Exact old and new values

**Forensic queries:**

```sql
-- Who changed Kelly in last 24h?
SELECT * FROM settings_history
WHERE parameter = 'kellyFraction'
  AND changed_at > datetime('now', '-1 day')
ORDER BY changed_at DESC;

-- What was portfolio drawdown before a bad adjustment?
SELECT metrics_before FROM agent_decisions
WHERE decision_type = 'parameter_tuning'
  AND applied = 1
ORDER BY decided_at DESC LIMIT 1;
```

---

## 8. TESTING SAFETY SYSTEMS

### 8.1 Unit Tests

- `test_guardrails()`: Attempt to set each parameter outside bounds → expect 400
- `test_circuit_breaker_trigger()`: Simulate 12% drawdown → expect `stop_trading()` called
- `test_rollback_guardrail()`: Snap to value that would violate guardrail → expect rollback to fail

### 8.2 Integration Tests (Paper Trading)

1. **Let bot run dry‑run for 1 week** with agent enabled
2. **Verify:**
   - No parameter exceeds guardrails
   - Circuit breaker never triggers unless explicitly simulated
   - All agent decisions appear in `agent_decisions`
   - Manual settings changes are respected and not overwritten by agent

3. **Simulate extreme conditions:**
   - Insert fake losing trades → trigger drawdown limit → verify trading stops
   - Force API errors → verify error‑rate circuit breaker
   - Attempt to set `kellyFraction` to 1.0 via direct DB edit → verify `/api/settings` returns 400

---

## 9. FORWARD‑LOOKING SAFETY

### 9.1 Future: Means‑Ends Analysis

If agent gains more autonomy (e.g., can generate new strategies), we'll add:
- **Capability boundaries:** Agent can only modify files in `agent_strategies/` directory
- **Code review requirement:** New strategy must pass static analysis (no network calls, limited loops)
- **Simulation before deployment:** New strategy runs in sandbox for 100 trades before live

### 9.2 Human‑in‑the‑Loop Thresholds

Initially, agent runs in **advisory‑only mode** for 30 days:
- Sends recommendations to Telegram: "Suggest raising Kelly to 0.58. Approve? (/confirm /deny)"
- Human must approve each change
- After 30 days of no issues, switch to **autonomous mode** (auto‑apply within guardrails)

---

## 10. INCIDENT RESPONSE

If something goes wrong:

1. **Circuit breaker tripped** → Check `logs/agent.log` and `logs/bot.log` for last agent action
2. **Unexpected loss** → Query `settings_history` to see recent parameter changes
3. **Agent misbehavior** → Disable by removing cron job, then `curl -X POST /api/stop-trading`
4. **Recovery:** Use `/api/settings/rollback?ts=<last_known_good>` then restart trading

---

**End of Safety Guardrails Spec**
