# Test Cases Specification

**Version:** 1.0.0  
**K-ATA Project**

---

## 1. TESTING STRATEGY

- **Unit tests:** pytest for Python modules, Jest for bot_interface
- **Integration tests:** HTTP API + database round‑trip
- **End‑to‑end paper trading:** Run bot dry‑mode with synthetic Kalshi API
- **Safety tests:** Force guardrail violations, verify rejection

All tests should be **deterministic** (mock external services, fixed timestamps).

---

## 2. UNIT TEST CASES

### 2.1 TradeLogger

| Test ID | Description | Steps | Expected |
|---------|-------------|-------|----------|
| TL-01 | Log a trade to empty DB | Create TradeLogger(':memory:'), call `log_trade()` | Trade inserted, `SELECT COUNT(*)` = 1 |
| TL-02 | Get trades filtered by strategy | Log 3 trades (2 news, 1 arb); call `get_trades(strategy='news_sentiment')` | Returns 2 news trades |
| TL-03 | Concurrent writes don't corrupt | Spawn 10 threads, each logs 100 trades | Total 1000 trades, no duplicates, no SQLite errors |

### 2.2 SettingsManager

| Test ID | Description | Steps | Expected |
|---------|-------------|-------|----------|
| SM-01 | Default value returned when not set | `get('kellyFraction')` with no DB entry | `0.5` (from defaults) |
| SM-02 | Update persists and returns old value | `update('kellyFraction', 0.6)`, then `get('kellyFraction')` | `0.6`; history row with old=`0.5` |
| SM-03 | Guardrail violation raises | `update('kellyFraction', 0.95)` | `ValueError` with message |
| SM-04 | Idempotent update (same value) | `update('kellyFraction', 0.5)` twice | Second call returns no DB change, still success |

### 2.3 Agent Logic

| Test ID | Description | Steps | Expected |
|---------|-------------|-------|----------|
| AG-01 | Hypothesis: low win rate suggests threshold increase | Seed trades: 50 news trades with 45% win rate, avg conf 0.55 | Generated hypothesis to raise `newsSentimentThreshold` |
| AG-02 | Backtest rejects threshold with insufficient high‑conf samples | Seed 100 trades, only 5 with conf > 0.7 | Backtest returns `False` (sample size too small) |
| AG-03 | Kelly adjustment: Sharpe > 2.0 → increase | Performance: Sharpe=2.2, current Kelly=0.5 | Suggested Kelly ≈ 0.55 (0.5 + (0.8‑0.5)*0.5 = 0.65 but capped at 0.8, then conservative 0.55) |
| AG-04 | Guardrail blocks out‑of‑range value | Setting: `kellyFraction=0.5`; Agent suggests `0.95` | Adjustment rejected, `agent_decisions.applied=false` |

---

## 3. API INTEGRATION TESTS

### 3.1 Settings Endpoint

| Test ID | Description | Request | Expected |
|---------|-------------|---------|----------|
| API‑SET‑01 | Valid update | `POST /api/settings {"kellyFraction": 0.60}` | 200, `updated:["kellyFraction"]` |
| API‑SET‑02 | Invalid value (too high) | `POST /api/settings {"kellyFraction": 0.95}` | 400, `code: "GUARDRAIL_KELLY_OUT_OF_BOUNDS"` |
| API‑SET‑03 | Unknown parameter | `POST /api/settings {"foo": 123}` | 200 with warning `rejected:["foo"]` or 400? (decision: allow extra keys for forward compatibility) |
| API‑SET‑04 | Update multiple | `POST /api/settings {"kellyFraction":0.6, "maxPositionSizePct":0.15}` | Both updated, both in `updated` list |

### 3.2 Settings Rollback

| Test ID | Description | Steps | Expected |
|---------|-------------|-------|----------|
| API‑RB‑01 | Successful rollback | 1) POST to change `kellyFraction` to 0.7 2) GET `/api/settings/history?limit=1` to get old timestamp 3) `GET /api/settings/rollback?ts=<old>` | `kellyFraction` restored to old value, `rolledBack: 1` |
| API‑RB‑02 | Rollback to time before any manual change | With only defaults, rollback to now | 200, `rolledBack: 0` |
| API‑RB‑03 | Guardrail violation prevents rollback | Directly inject bad value into DB (e.g., `kellyFraction=1.5`), then rollback to that time | Should fail with 400 (value would violate guardrail) |

### 3.3 Performance Endpoint

| Test ID | Description | Steps | Expected |
|---------|-------------|-------|----------|
| API‑PERF‑01 | Returns dates and metrics | Seed `trades` table with 30 days of data; call `/api/performance?days=30` | JSON with `portfolio` and `performance` arrays, each strategy has `sharpeRatio`, `winRate` |
| API‑PERF‑02 | Filters by days | Use `days=7` | Only last 7 days included in calculations |

### 3.4 Trades Endpoint

| Test ID | Description | Request | Expected |
|---------|-------------|---------|----------|
| API‑TRD‑01 | Pagination limit | Insert 1500 trades; `GET /api/trades?limit=100` | `total: 1500`, `trades: []` length = 100 |
| API‑TRD‑02 | Strategy filter | `GET /api/trades?strategy=news_sentiment` | All returned trades have `strategy="news_sentiment"` |
| API‑TRD‑03 | Date range | `GET /api/trades?from_date=2025-03-01T00:00:00Z` | Only trades on/after that date |

---

## 4. CIRCUIT BREAKER TESTS

| Test ID | Description | Setup | Expected |
|---------|-------------|-------|----------|
| CB‑01 | Drawdown > 10% triggers stop | Insert trades with cumulative loss 11% of bankroll; run `safety_monitor.py` | `stop_trading()` called, state becomes `PAUSED_DRAWDOWN` |
| CB‑02 | 24h loss > 5% triggers stop | Insert today's trades with loss = 6% of bankroll | Same as CB‑01 |
| CB‑03 | API error rate > 20% triggers stop | Mock `/api/performance` to return 500 for 6 of last 20 calls | State becomes `PAUSED_ERROR`, auto‑reset after 5m if clean |
| CB‑04 | Auto‑reset from PAUSED_ERROR | After CB‑03, clear errors, wait 5m | State returns to `ACTIVE`, alert sent |
| CB‑05 | Manual resume from PAUSED_DRAWDOWN | After CB‑01, `POST /api/start-trading` | 403 (must manually investigate and fix, then call an explicit "reset" endpoint we'll add) |
| CB‑06 | Open position rejection when exposure > 100% | Set current exposure to 90%, attempt trade with 15% | Trade rejected, `SafetyError` |

---

## 5. AGENT LOOP TESTS

| Test ID | Description | Mock Data | Expected |
|---------|-------------|-----------|----------|
| AG‑LOOP‑01 | Full cycle completes in < 60s | Real bot with 1000 trades | Cycle log shows start → end within 60s |
| AG‑LOOP‑02 | Hypotheses generated for low win rate | Performance: news win rate 0.45 | Contains hypothesis to raise threshold |
| AG‑LOOP‑03 | Adjustment applied successfully | Hypothesis suggests Kelly 0.6; current 0.5 | POST to `/api/settings` with 0.55 (half‑step), success, `agent_decisions.applied=true` |
| AG‑LOOP‑04 | Guardrail prevents invalid adjustment | Suggests `kellyFraction=0.95` | POST fails, `applied=false`, logged |
| AG‑LOOP‑05 | Max adjustments per day limit | Already 3 adjustments applied today | 4th adjustment rejected (rate limit), logged but not attempted |
| AG‑LOOP‑06 | No adjustments when trading stopped | Circuit breaker active (trading=false) | Agent exits early, logs "skipped – circuit active" |
| AG‑LOOP‑07 | Backtest statistical test | Seed data where high‑conf trades win 60%, low‑conf 40% (p=0.03) | Hypothesis accepted (p<0.05) |
| AG‑LOOP‑08 | Correlation hypothesis | Trades show strategies A & B with corr 0.85 | Hypothesis to reduce `maxPositionSizePct` generated |

---

## 6. END‑TO‑END PAPER TRADING SCENARIO

**Scenario 1: Normal operation**

1. Start bot in dry‑run: `POST /api/start-trading`
2. Let it run for 24 simulated hours (fast‑forward using recorded market data)
3. Verify:
   - ≥ 20 trades executed and logged
   - `performance` endpoint returns Sharpe > 1.0
   - Agent cycle runs every 6h
   - At least one parameter adjustment made
   - No guardrail violations logged
   - No circuit breaker triggers

**Scenario 2: Drawdown recovery**

1. Inject losing trades (direct DB insert) to create 12% drawdown
2. Run `safety_monitor.py`
3. Verify:
   - `/api/status` shows `trading: false`
   - Alert sent
   - Agent cycle skips adjustments
4. Manually fix (simulate recovery), call rollback to known‑good settings
5. `POST /api/start-trading` succeeds after drawdown reduced below 10%

**Scenario 3: Agent aggressiveness**

1. Set initial Kelly = 0.2, enable all strategies
2. Run 30 days paper trading with agent ON
3. Verify:
   - Kelly gradually increased (toward 0.8) if Sharpe high
   - No single adjustment > 0.05 (half‑step rule)
   - After 5 adjustments, rate limit kicks in

---

## 7. PERFORMANCE TESTS

| Test | Description | Threshold |
|------|-------------|-----------|
| PERF‑01 | API response time (p95) | < 200ms for all endpoints |
| PERF‑02 | Database write latency | < 50ms per trade insert |
| PERF‑03 | Agent cycle time | < 60s including all API calls |
| PERF‑04 | Memory usage bot | < 500MB after 24h |
| PERF‑05 | Memory usage agent | < 200MB |
| PERF‑06 | Query speed: trades by strategy | < 100ms for 10k rows |
| PERF‑07 | Index effectiveness (EXPLAIN) | All queries use index |

---

## 8. SECURITY TESTS

| Test ID | Description | Expected |
|---------|-------------|----------|
| SEC‑01 | SQL injection via settings | Filter input prevents injection; DB error |
| SEC‑02 | Path traversal (file read) | Bot_interface denies `/api/load?file=../../../etc/passwd` |
| SEC‑03 | Settings mass assignment | Unknown keys rejected or ignored (not applied) |
| SEC‑04 | No auth on localhost | Document that API assumed localhost only; if exposed, add basic auth |

---

## 9. EDGE CASES

| Test ID | Description | Expected |
|---------|-------------|----------|
| EDGE‑01 | Trading with $0 bankroll | Bot should not start; `status` shows error |
| EDGE‑02 | Trade with quantity 0 | Rejected at validation |
| EDGE‑03 | Negative price | Rejected by Kalshi API validator |
| EDGE‑04 | Timezone handling | All timestamps stored UTC, displayed local if needed |
| EDGE‑05 | Daylight saving time change | No effect (UTC timestamps) |
| EDGE‑06 | Database corruption | `safety_monitor.py` detects, alerts, attempts restore |
| EDGE‑07 | Bot process dies mid‑trade | Bot_interface restarts it; trade may be lost → log error |

---

## 10. ACCEPTANCE CRITERIA

Before declaring MVP production‑ready:

- [ ] All **unit tests** pass (≥ 80% coverage)
- [ ] All **integration tests** pass
- [ ] **Paper trading** 7 days continuous with no crashes, no guardrail violations, no unexpected losses
- [ ] **Agent** makes at least 5 adjustments in 7 days, all logged, none reverted by rollback
- [ ] **Circuit breaker** never triggers in paper unless intentionally simulated
- [ ] **Throughput:** API latency < 200ms p95 under normal load
- [ ] **Recovery:** Kill bot process → auto‑restart within 10s
- [ ] **Backup:** Daily backup script works and can restore
- [ ] **Documentation:** All endpoints, DB schema, agent logic documented

---

## 11. TEST AUTOMATION PLAN

- **Local development:** `pytest` + `npm test` on commit
- **CI pipeline:** GitHub Actions
  - Lint (ruff, eslint)
  - Unit tests
  - Integration tests (spin up SQLite, mock bot)
  - Build Docker images
- **Staging environment:** Run paper trading for 48h on synthetic data before any live deployment
- **Production smoke test:** After deployment, run health checks, verify agent cron active

---

**End of Test Cases Spec**
