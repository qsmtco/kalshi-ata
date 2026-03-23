# K-ATA Codebase Deep Audit Prompt

You are performing a **full-stack code audit** of the K-ATA trading bot at `/home/q/projects/kalshi-ata`. This is a Node.js + Python two-layer system: an Express API server (`bot_interface.js`) manages a Python trading engine (`src/main.py`). The bot connects to the Kalshi prediction market API using RSA-PSS authentication and places real orders with real money.

You will produce a **prioritized findings report** with: severity (CRITICAL/HIGH/MEDIUM/LOW), file+line reference, description, evidence, and recommended fix.

---

## PHASE 1: Global Reconnaissance

First, answer these baseline questions:

1. List every file in the project. Build a complete file tree.
2. Read `package.json` — what are the dependencies and versions? Are there known vulnerabilities?
3. Read `.env` — what environment variables are required? Are any missing defaults in `src/config.py`?
4. Read `src/config.py` completely — what does it assume about env vars?
5. List every `import` statement across all `.py` files — are all modules resolvable?
6. List every `require()` call in `bot_interface.js` — are all Node modules available?

---

## PHASE 2: Syntax & Compilation Verification

For **every** Python file:
- Run `python3 -m py_compile <file>` — does it pass without errors?
- If it fails, report the exact error and line.

For `bot_interface.js`:
- Run `node --check bot_interface.js` — does it pass?
- If it fails, report the exact error.

For **every** file you modify during the audit, re-run the check to confirm it still compiles.

---

## PHASE 3: Entry Point & Control Flow Trace

Trace the full startup sequence from a clean state:

1. **How does the Node server start?** Read `bot_interface.js` from the bottom up — find the `if __name__` block or startup code.
2. **How does Node spawn Python?** Find every `spawn()` call. For each:
   - What command and args are passed?
   - What environment variables are passed to Python? List them explicitly.
   - Is `KALSHI_DEMO_MODE` passed? Is `KALSHI_API_BASE_URL` passed?
   - Are there any other env vars that Python needs that might be missing?
3. **How does Python start trading?** Read `src/main.py` from the bottom up.
   - Trace every initialization step in order.
   - What gets instantiated, in what order?
   - What happens if an early step fails (e.g., API unreachable)?

---

## PHASE 4: API Authentication Audit (CRITICAL)

This is the most security-sensitive part. Read `src/kalshi_api.py` completely.

1. **RSA Signing**: Find `_build_auth_headers()`. Trace the exact signing process:
   - What message string is constructed? (Show the actual concatenation)
   - What is signed? (timestamp + method + path)
   - Does the path used for signing include the `/trade-api/v2` prefix?
   - What algorithm is used?
2. **Private Key Loading**: Where does the private key come from? Is the path correct?
3. **Auth Headers**: List every endpoint that requires auth. Confirm each one builds headers via `_build_auth_headers()`.
4. **Env Var Routing**: 
   - Does the Python process receive the correct `KALSHI_API_KEY`, `KALSHI_API_BASE_URL`, and `KALSHI_DEMO_MODE`?
   - If Python is started via `spawn()` from Node without explicit env passing, will it have the right values?
5. **Production vs Demo**: Find where the base URL is resolved. Under what conditions does the bot hit `demo-api.kalshi.co` vs `api.elections.kalshi.com`?
6. **Missing Env Vars**: If `KALSHI_API_KEY` is missing from the environment, what value does Python use? Is it a placeholder that would cause auth failures silently?

---

## PHASE 5: API Endpoint Coverage Audit

For each of these Kalshi API endpoints used in the codebase, verify:

| Endpoint | Used By | Auth? | Verified Working? |
|---|---|---|---|
| `GET /exchange/status` | bot_state.py | No | ? |
| `GET /portfolio/balance` | bot_state.py | Yes | ? |
| `GET /portfolio/positions` | main.py, bot_state.py | Yes | ? |
| `GET /portfolio/orders` | bot_state.py | Yes | ? |
| `POST /portfolio/orders` | trader.py | Yes | ? |
| `DELETE /portfolio/orders/{id}` | trader.py | Yes | ? |
| `GET /markets` | market_data_streamer.py | No | ? |
| `GET /markets/{ticker}/orderbook` | market_data_streamer.py | No | ? |

For each endpoint:
- Read the `_handle_request` wrapper that calls it
- Confirm the HTTP method is correct
- Confirm the path is correct (compare to Kalshi API docs if uncertain)
- Confirm the auth requirement matches the code (some endpoints may need auth that aren't getting it, or vice versa)

---

## PHASE 6: Data Flow Audit — Response Parsing

For every place the codebase parses an API response:

1. **Exact key path**: Show the exact dictionary key access chain (e.g., `resp.get('raw', {}).get('market_positions', [])`)
2. **Actual API shape**: If possible, run the actual API call and show the real response structure
3. **Mismatch?**: Does the code match the actual response?

Focus on these high-risk areas (known to have had bugs):

### 6a. Position Sync in `src/main.py` (around line 39-47)
- What is the **actual** response shape from `api.get_positions()`?
- What key does `sync_from_api` look at?
- Does `sync_from_api` correctly parse each position's `ticker`, `count`, `avg_fill_price`, `event_id`, `side`?
- Run: `cd /home/q/projects/kalshi-ata && source .env && python3 -c "..."` to call `get_positions()` and print the actual response

### 6b. Position Display in `src/bot_state.py` `fetch_positions()`
- Does it use the correct key path for the positions list?
- Does it correctly distinguish between `market_positions` and `event_positions`?

### 6c. Balance in `src/bot_state.py` `fetch_balance()`
- What keys does it look for? (`available_cash`, `portfolio_value`, etc.)
- Show the actual `/portfolio/balance` response from the API

### 6d. Order History in `src/bot_state.py` `fetch_performance()`
- How does it extract `yes_price`, `count`, `status` from each order?
- Show a real order from the API response

### 6e. Market Data in `src/market_data_streamer.py`
- How does it extract `yes_bid`, `yes_ask`, `last_price`, `close_date` from market responses?
- Show the actual market response structure
- Is `yes_bid_dollars` handled as a fallback when `last_price` is absent?

---

## PHASE 7: Known Bug Hunt

The following bugs were **reported as fixed** on 2026-03-21. For each, verify it is actually fixed in the current codebase:

| # | Bug Description | File | How to Verify |
|---|---|---|---|
| 1 | `self.strategies_executed =+ 1` should be `+= 1` | trader.py | Search for `strategies_executed` and check the operator |
| 2 | `KALSHI_API_BASE_URL` not passed to Python subprocess | bot_interface.js | Check if `KALSHI_API_BASE_URL` is in the `env` object passed to `spawn()` |
| 3 | `market_cap` field assumed in market data (field doesn't exist in API) | trader.py | Search for `market_cap` usage; show what field is actually in the API response |
| 4 | Circuit breaker triggers at `>= 3` when threshold=3 (off-by-one) | risk_manager.py | Find the comparison operator |
| 5 | `sync_from_api` type check excludes `int` for count | position_tracker.py | Find the `isinstance(count_raw, ...)` line |
| 6 | `sync_from_api` wrong key path for positions | main.py | Find what key is accessed |
| 7 | `check_and_execute_exits` hardcoded `hours_remaining=999` | trader.py | Find the hardcoded value |
| 8 | `KALSHI_DEMO_MODE` not passed to Python subprocess | bot_interface.js | Check startup command/env |
| 9 | `fetch_positions` in bot_state.py uses wrong key | bot_state.py | Find the key access |
| 10 | Bid validation `> 1.0` rejects dollar prices | market_data_streamer.py | Find the validation logic |

For each: if the bug is **not fixed**, describe exactly what the current code does wrong and what it should do instead.

---

## PHASE 8: Logic Errors — Algorithms

Read each strategy file and the core trading logic. For each, ask: "Does the code do what the comments claim?"

### 8a. Exit Rules (`src/exit_rules.py`)
- List all 5 exit triggers and their exact conditions
- For each trigger: trace the actual comparison (e.g., `price <= entry * 0.60`)
- Are there any edge cases where the trigger could fire incorrectly?

### 8b. Market Selector (`src/market_selector.py`)
- For each filter function: trace the exact logic
- Does `is_tradeable()` correctly combine all filters with AND/OR?
- Is the liquidity check checking `yes_bid_dollars` (not just `last_price`)?
- Does `get_market_quality_score()` handle missing fields gracefully?

### 8c. Position Sizing (`src/risk_manager.py` or wherever Kelly sizing lives)
- How is position size calculated?
- Is it calculated against the **actual** account balance or a hardcoded $1000?
- Is Kelly fraction applied correctly?

### 8d. News Sentiment (`src/news_analyzer.py`)
- How are articles fetched and scored?
- Does the keyword extraction handle the fallback correctly?
- Is the sentiment score bounded to [-1, 1]?

### 8e. Statistical Arbitrage (`src/arbitrage.py` or wherever it lives)
- What markets are compared?
- How is the spread calculated?
- What threshold triggers a trade?

### 8f. Volatility Strategy
- How is volatility measured?
- What event types qualify?

---

## PHASE 9: Error Handling Audit

For each `.py` file:

1. Find every `try/except` block
2. For each `except`: what exceptions are caught? What happens to them?
3. Are there `except Exception as e:` blocks that swallow errors silently?
4. Are there network calls (API requests) wrapped in try/except? Do they retry?
5. Are there any places where a 401, 403, 429, or 500 from the API would be silently ignored?

In `bot_interface.js`:
1. Find every `.catch()` on promises
2. Find every error handler in Express routes
3. Are there places where JSON parse failures or network errors are handled?

---

## PHASE 10: Race Conditions & Concurrency

1. **Market data streamer**: Does it use threading? Is there a race between starting the stream and the first trading cycle?
2. **Position tracker**: Is it accessed from multiple threads? Are there locks?
3. **Order placement**: If two strategies fire simultaneously, could they both try to buy the same market?
4. **API rate limits**: Is there any throttling to avoid hitting Kalshi's rate limits?

---

## PHASE 11: Security Audit

1. **Credentials**: Are API keys and private keys ever logged? Are they in error messages?
2. **Express API (port 3050)**: Is there any authentication on the `/api/*` endpoints? Can anyone with network access start/stop trading?
3. **Input validation**: Are there any user-controlled inputs (query params, body) that are passed directly to shell commands or API calls without sanitization?
4. **Command injection**: Are there any `os.system()`, `subprocess` with `shell=True`, or template strings with user input?
5. **File access**: Does the bot ever read files based on user input without path sanitization?
6. **Telegram token**: Is the bot token ever exposed in logs or error messages?

---

## PHASE 12: Configuration & Constants

1. List every magic number / hardcoded constant in the codebase
2. For each: does it have a named constant (e.g., `MAX_POSITION_SIZE`) or is it just a raw number?
3. Are the defaults in `src/config.py` reasonable for a production account with ~$739?
4. Check: is `BANKROLL` still hardcoded to $1000 anywhere?

---

## PHASE 13: Observability — Can You Tell What's Happening?

1. **Logging**: Is there a logger configured? What level? Is `DEBUG` logging enabled in production (which would be noisy)?
2. **Position logging**: Does `log_position_status()` actually log anything? When was it last called?
3. **Telegram alerts**: Are `_send_exit_alert()`, `_send_new_trade_alert()`, `maybe_send_daily_summary()` actually wired into the trader?
4. **Error alerts**: Are Telegram alerts sent when the bot encounters errors?
5. **Startup logging**: Does the bot log its configuration on startup (mode, balance, API URL)?

---

## PHASE 14: Integration Test — Full Stack Walk-Through

**Manually walk through one complete trading cycle** using the actual code:

1. Start: `node bot_interface.js` → what happens? What does the log show?
2. `POST /api/start-trading` → Python starts → trace every log line
3. First cycle: what markets are fetched? What strategies run?
4. If a strategy fires: trace the exact order placement path — from signal detection to API call to response parsing
5. If a position is opened: trace how it's recorded in the PositionTracker
6. If an exit condition is met: trace the sell path
7. Check the API: does the order actually appear at Kalshi?

For each step, show the actual log output or API response. If something breaks, that's a finding.

---

## PHASE 15: Dependency & Supply Chain

1. Run `npm audit` in the project root — any vulnerabilities?
2. Run `pip list` or check `requirements.txt` — are there outdated packages with known vulnerabilities?
3. Is there a `package-lock.json` or `Pipfile.lock` pinning versions?
4. Are any packages loaded from unpinned URLs?

---

## PHASE 16: README vs Code Reality

1. Read `README.md` completely
2. For each section: does the actual code match the description?
3. Are there features documented that don't exist in the code?
4. Are there features in the code that aren't documented?
5. Is the startup sequence in the README accurate?

---

## OUTPUT FORMAT

Produce a report with this structure:

```
# K-ATA Code Audit Report
Generated: [date]
Files Audited: [list]

## CRITICAL Findings
[file:line] — [title]
Description: ...
Evidence: ...
Fix: ...

## HIGH Findings
[same format]

## MEDIUM Findings
[same format]

## LOW Findings
[same format]

## Verified Working Components
[what actually works correctly]

## Summary
Total findings: N
CRITICAL: N | HIGH: N | MEDIUM: N | LOW: N
Bugs confirmed fixed: N/N
Recommended action: ...
```

For each finding, include: exact file path, exact line number, what the code does, what it should do, and a concrete fix.

Be ruthless. If something looks wrong, report it. If you're not sure, flag it as a concern. Cite actual code, actual API responses, and actual log output as evidence.
