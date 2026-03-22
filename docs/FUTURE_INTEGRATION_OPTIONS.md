# Qaster-K-ATA Integration Options

> *How to weave Qaster more deeply into the K-ATA trading loop*
> *Archived from conversation with Captain JAQx — 2026-03-21*

---

## The Gap

Right now Qaster's role in K-ATA is:

- **Launch** — start the bot
- **Monitor** — check status, review logs
- **Fix** — debug when things break

Between trades, Qaster is idle. The bot runs on a 60-second loop without any AI oversight between cycles. The Captain has to manually check in.

This document explores how Qaster could become a genuine participant in the trading loop — not just the person who turns it on.

---

## Option 1 — Active Heartbeat Analyst

**What:** Instead of a simple heartbeat that just says "alive", Qaster runs a mini-analysis every time the heartbeat fires.

**How it works:**
- Cron wakes Qaster every 15 minutes
- Pulls `/api/status` + `/api/positions` + `/api/performance`
- Runs a short analysis:
  - P&L trajectory over last hour
  - Any positions approaching stop loss or take profit thresholds
  - Market liquidity changes on open positions
  - Unusual trading frequency (is the bot overshooting?)
- Sends a brief briefing to Telegram if anything needs attention
- If nothing needs attention, stays quiet

**Upside:** Low risk, always-on awareness, catches problems early.
**Downside:** Still reactive, not proactive.

---

## Option 2 — Pre-Trade Checkpoint

**What:** Before every new order, the bot pings Qaster for a go/no-go decision.

**How it works:**
- Bot identifies a candidate market with a signal
- Instead of immediately executing, it sends a lightweight request to Qaster (via internal signal, not external API)
- Qaster receives: market ticker, signal strength, probability, liquidity data, current portfolio state
- Qaster responds: `APPROVE`, `BLOCK`, or `REVIEW`
- Block reason is logged and sent to Telegram
- REVIEW pauses the trade and alerts the Captain

**Example:**
```
[Bot] Signal: BUY CS2 match, confidence 0.72, price $0.45, bid $0.44 ask $0.46
[Qaster] BLOCK — market spread 4.3% exceeds 3.5% threshold for high-confidence signals
```

**Upside:** Every trade gets a second set of eyes, quality gate enforcement
**Downside:** Adds latency to execution, could miss trades if Qaster is slow to respond

**Implementation note:** Would need a new internal API endpoint or signal mechanism between Python and Qaster's session.

---

## Option 3 — Autonomous Market Scanner (Cron Job)

**What:** An isolated Qaster agent runs every 30 minutes outside the trading loop, proactively scanning for opportunities.

**How it works:**
- Cron triggers an isolated Qaster session
- Qaster scans Kalshi API for new markets matching our criteria
- Analyzes: volume trends, probability changes, news sentiment across sectors
- Builds a ranked watchlist of tradeable markets
- Writes watchlist to a file or Redis queue
- Bot reads watchlist and prioritizes those markets in its next cycle
- Qaster gets notified if something high-priority appears

**What Qaster looks for:**
- Markets that just crossed into the 25%-75% sweet spot
- Volume spikes in previously quiet markets (someone knows something)
- New esports tournaments that just got listed
- Political markets moving on recent news

**Upside:** Bot is always seeded with the best opportunities, Qaster is genuinely useful between cycles
**Downside:** More API calls (rate limit risk), more complexity

---

## Option 4 — Post-Trade Review Agent

**What:** After every trading cycle, Qaster receives a summary and looks for patterns.

**How it works:**
- Bot writes a cycle summary to a log file or SQLite table: trades taken, outcomes, P&L, market conditions
- Cron wakes Qaster every 2 hours
- Qaster reviews the last N cycles:
  - Win rate by strategy (news vs arbitrage vs volatility)
  - Win rate by market category (esports vs sports vs political)
  - Average holding time vs P&L
  - Are we consistently entering the same bad markets?
- Qaster proactively sends a Telegram briefing:
  ```
  Trading Brief — Last 4 Hours
  3 trades, 1 win, 2 no-change
  News Sentiment: 1/1 win (avg +$0.02)
  Volatility: 0/1 win (stopped out)
  Esports: 1/1 win | Sports: 0/1 loss
  Watch: VPIN elevated in NBA markets — consider suppressing next cycle
  ```
- Pattern detected: "We're 0-4 on esports markets this week" → suggests reducing esports exposure

**Upside:** Pattern recognition across trades, strategic intelligence, learns from our actual results
**Downside:** Requires a shared data store between bot and Qaster

---

## Option 5 — Decision Log Auditor

**What:** Qaster reviews the SQLite audit log periodically and proactively suggests parameter changes.

**How it works:**
- K-ATA already logs every parameter change with reason, p-value, and expected effect
- Qaster periodically reads this log
- Compares hypothesized outcomes vs actual outcomes
- Identifies which parameter changes actually worked vs which were noise
- Sends recommendations:
  ```
  Parameter Audit — Last 7 Days
  sentimentThreshold: 0.15 → 0.20 was predicted +8% win rate
  Actual result: +2% win rate (p=0.31, not significant)
  Recommendation: Revert to 0.15 — hypothesis not validated
  
  kellyFraction: 0.50 → 0.30 was predicted -15% drawdown
  Actual result: -18% drawdown (confirmed)
  Recommendation: Keep at 0.30
  ```

**Upside:** Bridges hypothesis → result loop, makes the self-optimization actually intelligent
**Downside:** Requires statistical rigor to avoid overfitting recommendations

---

## Option 6 — Full Autonomy Mode (Caution)

**What:** Qaster is given a budget and makes all trading decisions unsupervised.

**Concerns:**
- No Captain oversight between decisions
- Could compound losses faster than a human can intervene
- The zombie positions happened partly because no one was watching — full autonomy could make that worse
- Kalshi API rate limits could be hit faster

**If pursued:** Would need strict circuit breakers, a hard loss limit that Qaster cannot override, and mandatory Telegram alerts for every action.

---

## Recommended Path Forward

**Phase 1 (Low risk, high value):** Start with **Option 4 — Post-Trade Review Agent**

Why:
- Already has the data (bot writes cycle logs)
- Low risk (Qaster only reads, never trades)
- High value (catches patterns the bot misses)
- Easy to implement (cron + isolated agent session)
- Builds toward more autonomy over time

**Phase 2:** Add **Option 1 — Active Heartbeat Analyst**
Once Phase 1 is running, extend the heartbeat to do actual analysis instead of just alive-check.

**Phase 3:** Add **Option 3 — Autonomous Market Scanner**
Only after Phases 1 and 2 are stable and we trust the data quality.

**Phase 4 (Future):** Consider **Option 2 — Pre-Trade Checkpoint**
Only if the Captain wants Qaster as a genuine quality gate before every order.

---

## Technical Requirements

### Shared Data Store
Options 4 and 5 need a shared location Qaster can read:
- SQLite file at `/home/q/projects/kalshi-ata/data/cycle_log.db`
- Or a simple JSON log at `/home/q/projects/kalshi-ata/data/cycle_summaries.jsonl`
- Bot writes, Qaster reads — no write conflicts

### Cron Setup
```
# Post-trade review — every 2 hours
0 */2 * * * /usr/bin/curl -s -X POST http://localhost:3050/api/trigger-review

# Market scanner — every 30 minutes  
*/30 * * * * /usr/bin/curl -s http://localhost:3050/api/scan-markets
```

### Telegram Integration
All options should deliver output to Telegram:
- Brief text messages for routine briefings
- Alerts with 🚨 emoji for anything requiring Captain attention
- Daily digest at end of trading session

---

*This document is a living record of how Qaster-K-ATA integration was imagined. As we implement each phase, update this file with what we learned and what's next.*
