import pandas as pd
import numpy as np
import logging
import time
from typing import List, Dict, Any
from config import BANKROLL, NEWS_SENTIMENT_THRESHOLD, STAT_ARBITRAGE_THRESHOLD, VOLATILITY_THRESHOLD, MAX_POSITION_SIZE_PERCENTAGE, STOP_LOSS_PERCENTAGE, PAPER_TRADING, MARKET_MAKING_ENABLED
from news_analyzer import NewsSentimentAnalyzer
from arbitrage_analyzer import StatisticalArbitrageAnalyzer
from volatility_analyzer import VolatilityAnalyzer
from risk_manager import RiskManager
from market_data_streamer import MarketDataStreamer
from performance_analytics import PerformanceAnalytics, Trade
from settings_manager import SettingsManager
from safety_monitor import CircuitBreaker
from position_manager import PositionManager
from paper_trader import PaperTrader
from market_maker import MarketMaker
from kyle_lambda import KalshiKyleLambda
from hawkes_process import KalshiHawkesEstimator
from vpin import KalshiVPINEstimator
from avellaneda_stoikov import KalshiMarketMaker
from orderbook_analyzer import OrderBookAnalyzer
from almgren_chriss import AlmgrenChrissExecutor
from order_tracker import OrderTracker

class Trader:
    def __init__(self, api, notifier, logger, bankroll):
        self.api = api
        self.notifier = notifier
        self.logger = logger
        self.bankroll = bankroll
        self.current_positions = {}
        self.news_analyzer = NewsSentimentAnalyzer()
        self.arbitrage_analyzer = StatisticalArbitrageAnalyzer()
        self.volatility_analyzer = VolatilityAnalyzer()
        self.risk_manager = RiskManager(bankroll)

        # Phase 3: Enhanced market data and performance tracking
        self.market_data_streamer = MarketDataStreamer(api, update_interval=60)  # Update every minute
        self.performance_analytics = PerformanceAnalytics()

        # Phase 4: Dynamic settings management
        self.settings_manager = SettingsManager()
        self.settings_manager.add_change_listener(self._on_settings_changed)

        # Phase 5: Circuit breaker for safety
        self.circuit_breaker = CircuitBreaker()
        
        # Phase 1: Daily loss limit (15%)
        self.DAILY_LOSS_LIMIT = 0.15  # 15% of bankroll
        
        # Phase 2: Position manager for max hold time (10 days)
        self.position_manager = PositionManager()
        
        # Phase 3: Paper trading mode
        self.paper_trading = PAPER_TRADING
        self.paper_trader = PaperTrader() if self.paper_trading else None
        if self.paper_trading:
            self.logger.info("📄 Paper Trading Mode: ENABLED (no real trades)")
        
        # Phase 5: Market making
        self.market_making_enabled = MARKET_MAKING_ENABLED
        if self.market_making_enabled:
            self.market_maker = MarketMaker(api)
            self.logger.info("🎯 Market Making: ENABLED")

        # Subscribe to market data updates for real-time monitoring
        self.market_data_streamer.add_subscriber(self._on_market_data_update)

        # Kyle's Lambda — order flow impact signal (background refresh)
        self.kyle_estimator = KalshiKyleLambda(api)
        self._last_kyle_refresh = 0.0  # Unix timestamp of last background refresh
        # Hawkes Process — order flow clustering signal (background refresh)
        self.hawkes_estimator = KalshiHawkesEstimator(api)
        self._last_hawkes_refresh = 0.0  # Unix timestamp of last background refresh
        # VPIN — volume-synchronized probability of informed trading
        self.vpin_estimator = KalshiVPINEstimator(api)
        self._last_vpin_refresh = 0.0  # Unix timestamp of last background refresh

        # Avellaneda-Stoikov Market Maker — optimal bid/ask quotes
        from config import AVELLANEDA_GAMMA, AVELLANEDA_KAPPA, AVELLANEDA_SPREAD_PCT
        self.as_market_maker = KalshiMarketMaker(
            kalshi_api=api,
            position_manager=self.position_manager,
            risk_manager=self.risk_manager,
            gamma=AVELLANEDA_GAMMA,
            kappa=AVELLANEDA_KAPPA,
            base_spread_pct=AVELLANEDA_SPREAD_PCT,
        )

        # Order Book Analyzer — L2 depth + spread decomposition
        from config import ORDERBOOK_DEPTH_LEVELS, ORDERBOOK_OFI_WINDOW
        self.orderbook_analyzer = OrderBookAnalyzer(
            kalshi_api=api,
            vpin_estimator=self.vpin_estimator,
            kyle_estimator=self.kyle_estimator,
            n_depth_levels=ORDERBOOK_DEPTH_LEVELS,
            ofi_window=ORDERBOOK_OFI_WINDOW,
        )

        # Almgren-Chriss Executor — optimal order splitting for large positions
        from config import AC_MIN_QTY, AC_HORIZON_HOURS, AC_N_TRADES, AC_GAMMA, AC_RISK_AVERSION
        self.ac_executor = AlmgrenChrissExecutor(
            position_manager=self.position_manager,
            gamma=AC_GAMMA,
            risk_aversion=AC_RISK_AVERSION,
        )
        self.ac_min_qty = AC_MIN_QTY
        self.ac_horizon_hours = AC_HORIZON_HOURS
        self.ac_n_trades = AC_N_TRADES

        # Order Tracker — tracks open orders, fills, requoting
        self.order_tracker = OrderTracker(stale_threshold_seconds=60.0)
        self._last_tracker_log = 0.0  # throttle tracker metric logging

    def _on_settings_changed(self, changed_settings: Dict[str, Any]):
        """Handle dynamic settings changes."""
        self.logger.info(f"Settings updated: {list(changed_settings.keys())}")

        # Update market data streamer interval if changed
        if 'market_data_update_interval' in changed_settings:
            new_interval = self.settings_manager.settings.market_data_update_interval
            self.market_data_streamer.update_interval = new_interval
            self.logger.info(f"Market data update interval changed to {new_interval}s")

        # Update risk manager settings if changed
        if any(key in changed_settings for key in ['kelly_fraction', 'max_position_size_pct', 'stop_loss_pct']):
            # Risk manager will use updated settings automatically
            self.logger.info("Risk management settings updated")

        # Notify via telegram if enabled
        if self.settings_manager.settings.telegram_notifications:
            changes_summary = ", ".join([f"{k}: {v['old_value']} → {v['new_value']}"
                                       for k, v in changed_settings.items()])
            self.notifier.send_trade_notification(f"⚙️ Settings Updated: {changes_summary}")

    # =========================================================================
    # DAILY LOSS LIMIT (Phase 1)
    # =========================================================================
    
    def check_daily_loss_limit(self) -> bool:
        """
        Check if daily loss exceeds the limit.
        
        Returns:
            bool: True if can trade (within limit), False if limit exceeded
        """
        loss_pct = self.performance_analytics.get_daily_loss_percentage(self.bankroll)
        
        if loss_pct > self.DAILY_LOSS_LIMIT:
            self.logger.warning(
                f"🛑 DAILY LOSS LIMIT: {loss_pct:.2%} exceeds {self.DAILY_LOSS_LIMIT:.0%} - trading halted"
            )
            self.notifier.send_error_notification(
                f"🚨 DAILY LOSS LIMIT EXCEEDED: {loss_pct:.2%} > {self.DAILY_LOSS_LIMIT:.0%}\n"
                f"Trading has been halted for today."
            )
            return False
        
        if loss_pct > 0:
            self.logger.info(f"Daily loss: {loss_pct:.2%} (limit: {self.DAILY_LOSS_LIMIT:.0%})")
        
        return True
    
    # =========================================================================
    # END DAILY LOSS LIMIT
    # =========================================================================
    
    # =========================================================================
    # MAX HOLD TIME (Phase 2)
    # =========================================================================
    
    def check_max_hold_time(self) -> List[str]:
        """
        Check for positions that exceed max hold time (10 days).
        
        Returns:
            List of event IDs that should be closed
        """
        positions_to_close = self.position_manager.get_positions_to_close()
        
        for event_id in positions_to_close:
            self.logger.warning(
                f"🛑 MAX HOLD TIME: Position {event_id} held for "
                f"{self.position_manager.get_days_held(event_id)} days - closing"
            )
        
        return positions_to_close
    
    def close_position_for_max_hold(self, event_id: str, current_price: float) -> None:
        """Close a position due to max hold time exceeded."""
        position = self.position_manager.get_position(event_id)
        if not position:
            return
        
        # Calculate P&L
        if position.side == 'buy':
            pnl = (current_price - position.entry_price) * position.quantity
        else:
            pnl = (position.entry_price - current_price) * position.quantity
        
        self.logger.info(
            f"Closing position {event_id} due to max hold time: "
            f"P&L = ${pnl:.2f}"
        )
        
        # Remove from tracking
        self.position_manager.close_position(event_id)
        
        # Remove from current_positions if tracked
        if event_id in self.current_positions:
            del self.current_positions[event_id]
    
    # =========================================================================
    # END MAX HOLD TIME
    # =========================================================================

    def _on_market_data_update(self, updated_markets: List[str], all_market_data: Dict[str, Any]):
        """Handle real-time market data updates."""
        # Check for stop-loss triggers on open positions
        current_prices = {market_id: data.current_price
                         for market_id, data in all_market_data.items()}

        self.check_positions_for_risk_management(current_prices)

        # Log significant market movements
        for market_id in updated_markets:
            if market_id in all_market_data:
                market_data = all_market_data[market_id]
                if market_data.price_change_pct and abs(market_data.price_change_pct) > 2.0:
                    self.logger.info(f"Market movement: {market_data.title} "
                                   f"changed {market_data.price_change_pct:.2f}% "
                                   f"to ${market_data.current_price:.2f}")

    # =========================================================================
    # KYLE'S LAMBDA — Background Signal Refresh (Option C)
    # =========================================================================

    def _refresh_kyle_signals(self, market_data: Dict[str, Any]) -> None:
        """
        Background refresh of Kyle lambda signals for active positions.

        Runs every KYLE_REFRESH_INTERVAL_SEC (default 15 min) on the trading
        cycle. Refreshes ALL active position tickers in the risk_manager cache.
        Sends Telegram alert if any position enters HIGH signal state.

        Cache is shared with pre-trade checks in risk_manager.check_kyle_lambda(),
        so concurrent pre-trade reads always get valid cached values.
        """
        now = time.time()

        if now - self._last_kyle_refresh < KYLE_REFRESH_INTERVAL_SEC:
            return  # Throttle: skip if refreshed recently

        active_tickers = list(self.position_manager.positions.keys())
        if not active_tickers:
            self._last_kyle_refresh = now
            return

        self.logger.info(
            f"🔄 Kyle λ: refreshing {len(active_tickers)} active positions..."
        )
        alerts = []

        for ticker in active_tickers:
            result = self.risk_manager.check_kyle_lambda(
                ticker,
                self.kyle_estimator,
                force_refresh=True,  # Background refresh: bypass cache
            )
            if result["signal"] == "high":
                alerts.append(
                    f"  🚨 {ticker}: λ={result['lambda']:.6f} "
                    f"R²={result['r_squared']:.4f} "
                    f"→ scale={result['position_scale']:.0%}"
                )

        self._last_kyle_refresh = now

        if alerts:
            alert_text = (
                "⚠️ **Kyle λ HIGH ALERT** — Informed trading on open positions:\n"
                + "\n".join(alerts)
            )
            self.logger.warning(alert_text)
            self.notifier.send_trade_notification(alert_text)

    # =========================================================================
    # ALMGREN-CHRISS — Optimal Execution Scheduler (Option A)
    # =========================================================================

    def close_position_with_schedule(
        self,
        market_id: str,
        quantity: float,
        horizon_hours: Optional[float] = None,
        n_trades: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Close a position using Almgren-Chriss optimal execution schedule.

        If position is small (< AC_MIN_QTY), executes immediately via sell().
        If large (>= AC_MIN_QTY), splits into N trades over the horizon
        to minimize market impact.

        VPIN > 0.70 → accelerates schedule (executes faster).

        Args:
            market_id:     Market ticker
            quantity:      Contracts to close (positive = sell this many)
            horizon_hours: Hours over which to execute (default from config)
            n_trades:     Number of discrete trades (default from config)

        Returns:
            {"scheduled": bool, "n_trades": int, "total_qty": float, "urgency": str}
        """
        from config import AC_MIN_QTY
        from almgren_chriss import adjust_schedule_for_market_conditions

        horizon = horizon_hours or self.ac_horizon_hours
        n = n_trades or self.ac_n_trades
        abs_qty = abs(quantity)

        # Small position — execute immediately via sell()
        if abs_qty < AC_MIN_QTY:
            self.position_manager.sell(market_id, quantity)
            self.logger.info(
                f"A-C: {market_id} qty={abs_qty} < {AC_MIN_QTY} — immediate close"
            )
            return {
                "scheduled": False,
                "n_trades": 1,
                "total_qty": abs_qty,
                "urgency": "none",
            }

        # Large position — compute A-C schedule
        vpin_result = self.risk_manager.check_vpin(market_id, self.vpin_estimator)
        vpin = vpin_result.get("vpin", 0.0)

        spread_pct = 0.0
        try:
            ob = self.orderbook_analyzer.analyze(market_id, depth=5)
            if ob.get("status") == "ok":
                spread_pct = ob.get("spread_metrics", {}).get("spread_pct", 0.0)
        except Exception:
            pass

        raw_schedule = self.ac_executor.schedule_hedge(
            market_id=market_id,
            target_qty=quantity,
            horizon_hours=horizon,
            n_trades=n,
        )

        if "error" in raw_schedule:
            self.logger.warning(f"A-C: schedule error for {market_id}: {raw_schedule['error']}")
            self.position_manager.sell(market_id, quantity)
            return {"scheduled": False, "error": raw_schedule["error"]}

        adjusted = adjust_schedule_for_market_conditions(raw_schedule, spread_pct, vpin)

        # Execute trades per A-C schedule
        # Schedule entries have trade_size: positive = buy, negative = sell
        # sell(qty) expects positive qty → negate
        executed = 0
        for entry in adjusted["schedule"]:
            trade_size = entry["trade_size"]
            if abs(trade_size) < 1:
                continue
            # negative trade_size = sell (close long) → pass abs to sell()
            self.position_manager.sell(market_id, abs(trade_size))
            executed += 1

        self.logger.info(
            f"A-C: {market_id} closed {executed} trades, total={abs_qty}, "
            f"urgency={adjusted.get('urgency','?')}, VPIN={vpin:.3f}"
        )

        return {
            "scheduled": True,
            "n_trades": executed,
            "total_qty": abs_qty,
            "urgency": adjusted.get("urgency", "unknown"),
            "vpin": vpin,
            "spread_pct": spread_pct,
            "kappa": raw_schedule.get("kappa", 0),
            "total_cost": raw_schedule.get("total_cost", 0),
        }

    # =========================================================================
    # HAWKES PROCESS — Background Signal Refresh (Option C)
    # =========================================================================

    def _refresh_hawkes_signals(self, market_data: Dict[str, Any]) -> None:
        """
        Background refresh of Hawkes branching ratio for active positions.

        Runs every HAWKES_REFRESH_INTERVAL_SEC (default 15 min) on the trading
        cycle. Sends Telegram alert if any position enters HIGH signal state.

        Hawkes BR > 0.8 → SKIP all new trades on that position.
        """
        now = time.time()

        if now - self._last_hawkes_refresh < HAWKES_REFRESH_INTERVAL_SEC:
            return  # Throttle

        active_tickers = list(self.position_manager.positions.keys())
        if not active_tickers:
            self._last_hawkes_refresh = now
            return

        self.logger.info(
            f"📊 Hawkes: refreshing {len(active_tickers)} active positions..."
        )
        alerts = []

        for ticker in active_tickers:
            result = self.risk_manager.check_hawkes(
                ticker,
                self.hawkes_estimator,
                force_refresh=True,
            )
            if result["signal"] == "high":
                alerts.append(
                    f"  🚨 {ticker}: BR={result['branching_ratio']:.4f} → SKIP trades"
                )
            elif result["signal"] == "moderate":
                alerts.append(
                    f"  ⚠️  {ticker}: BR={result['branching_ratio']:.4f} → moderate clustering"
                )

        self._last_hawkes_refresh = now

        if alerts:
            alert_text = (
                "📊 **Hawkes BR ALERT** — Order flow clustering on open positions:\n"
                + "\n".join(alerts)
            )
            self.logger.warning(alert_text)
            self.notifier.send_trade_notification(alert_text)

    # =========================================================================
    # VPIN — Background Signal Refresh (Option C)
    # =========================================================================

    def _refresh_vpin_signals(self, market_data: Dict[str, Any]) -> None:
        """
        Background refresh of VPIN for active positions.

        VPIN > 0.70 preceded the 2010 Flash Crash by ~1 hour.
        VPIN > 0.80 → extreme toxicity → skip all new trades.
        """
        now = time.time()

        if now - self._last_vpin_refresh < VPIN_REFRESH_INTERVAL_SEC:
            return  # Throttle

        active_tickers = list(self.position_manager.positions.keys())
        if not active_tickers:
            self._last_vpin_refresh = now
            return

        self.logger.info(
            f"📊 VPIN: refreshing {len(active_tickers)} active positions..."
        )
        alerts = []

        for ticker in active_tickers:
            result = self.risk_manager.check_vpin(
                ticker,
                self.vpin_estimator,
                force_refresh=True,
            )
            if result["signal"] in ("high", "extreme"):
                skip = "🚫 SKIP" if result["skip_trade"] else "⚠️  HIGH"
                alerts.append(
                    f"  {skip} {ticker}: VPIN={result['vpin']:.4f} "
                    f"imb={result['volume_imb']:.3f}"
                )

        self._last_vpin_refresh = now

        if alerts:
            alert_text = (
                "🚨 **VPIN ALERT** — Adverse selection / order flow toxicity:\n"
                + "\n".join(alerts)
            )
            self.logger.warning(alert_text)
            self.notifier.send_trade_notification(alert_text)

    # =========================================================================
    # END VPIN
    # =========================================================================

    # =========================================================================
    # END HAWKES
    # =========================================================================

    # =========================================================================
    # END KYLE LAMBDA
    # =========================================================================

    # =========================================================================
    # ANALYZER WRAPPERS (missing methods)
    # =========================================================================
    
    def _statistical_arbitrage(self, market_data):
        """Wrapper for statistical arbitrage analyzer.
        
        Note: market_data contains MarketData dataclass instances (not dicts).
        StatisticalArbitrageAnalyzer expects dicts with .get() method, so we convert.
        """
        try:
            # Get markets list
            markets_list = market_data.get('markets', [])
            if not markets_list:
                return None
            
            # Convert MarketData dataclass instances to dicts for arbitrage_analyzer
            from dataclasses import asdict
            markets_dicts = []
            for m in markets_list:
                if hasattr(m, '__dataclass_fields__'):
                    d = asdict(m)
                    markets_dicts.append(d)
                else:
                    markets_dicts.append(m)
            
            opportunities = self.arbitrage_analyzer.find_arbitrage_opportunities(markets_dicts)
            return opportunities if opportunities else None
        except Exception as e:
            self.logger.error(f"Statistical arbitrage error: {e}")
            return None
    
    def _volatility_analysis(self, market_data):
        """Wrapper for volatility analyzer.
        
        Note: market_data contains MarketData dataclass instances (not dicts).
        VolatilityAnalyzer expects dicts with .get() method, so we convert.
        """
        try:
            markets_list = market_data.get('markets', [])
            if not markets_list:
                return None
            
            # Take first market (simplified)
            market = markets_list[0]
            
            # Convert MarketData dataclass to dict for volatility_analyzer
            # It expects: {market_id, title, current_price, price_history, ...}
            if hasattr(market, '__dataclass_fields__'):
                # It's a dataclass - convert to dict
                from dataclasses import asdict
                market_dict = asdict(market)
            else:
                market_dict = market
            
            return self.volatility_analyzer.analyze_market_volatility(market_dict)
        except Exception as e:
            self.logger.error(f"Volatility analysis error: {e}")
            return None
    
    # =========================================================================
    # MARKET MAKING (Phase 5)
    # =========================================================================
    
    def run_market_making(self, markets: List) -> None:
        """
        Run market making strategy on available markets.

        Uses Avellaneda-Stoikov optimal bid/ask quotes (inventory-adjusted),
        filtered through the pre-trade microstructure signal gate.

        Quotes are computed via A-S formulas with VPIN-based spread widening
        already applied inside get_quotes().
        """
        if not hasattr(self, 'market_maker'):
            return

        from config import AVELLANEDA_MODE, AVELLANEDA_MAX_TTE_HOURS

        try:
            # ---- ORDER LIFECYCLE: check fills + expire stale orders ----
            if hasattr(self.market_maker, 'get_order_status'):
                new_fills = self.order_tracker.check_fills(self.market_maker)
                expired = self.order_tracker.expire_stale_orders()
                if new_fills or expired:
                    self.logger.info(
                        f"OrderTracker: {len(new_fills)} fills, {len(expired)} expired "
                        f"across {len(set(f['ticker'] for f in new_fills + expired))} markets"
                    )

                # Log metrics every 5 minutes
                if time.time() - self._last_tracker_log > 300:
                    all_metrics = self.order_tracker.get_all_metrics()
                    for t, m in all_metrics.items():
                        if m["n_fills"] > 0:
                            self.logger.info(
                                f"📊 OrderTracker {t}: fill_rate={m['fill_rate']:.0%} "
                                f"spread_cap={m['spread_captured_pct']:.2f}% "
                                f"open_bids={m['open_bids']} open_asks={m['open_asks']}"
                            )
                    self._last_tracker_log = time.time()

            for market_data in markets:
                # Convert to dict if needed
                if hasattr(market_data, '__dict__'):
                    market = {'ticker': market_data.market_id,
                             'yes_price': market_data.current_price,
                             'no_price': 1 - market_data.current_price,
                             'volume': getattr(market_data, 'volume', 0)}
                else:
                    market = market_data

                ticker = market.get('ticker') or market.get('market_id')
                if not ticker:
                    continue

                # Check if should market make (existing volume/spread check)
                if not self.market_maker.should_market_make(market):
                    continue

                # ---- ORDER BOOK ANALYSIS — L2 depth check before quoting ----
                # Use mock data if no live orderbook available (inactive/settled market)
                from config import ORDERBOOK_SPREAD_WARN_PCT
                ob_result = self.orderbook_analyzer.analyze_with_mock(ticker)
                # If market is active, try live orderbook:
                ob_live = self.orderbook_analyzer.analyze(ticker, depth=10)
                if ob_live.get("status") == "ok":
                    ob_result = ob_live

                if ob_result.get("status") in ("ok", "mock"):
                    sm = ob_result.get("spread_metrics", {})
                    spread_pct = sm.get("spread_pct", 0)
                    if spread_pct > ORDERBOOK_SPREAD_WARN_PCT:
                        self.logger.warning(
                            f"⚠️  A-S MM: {ticker} spread={spread_pct:.0f}% "
                            f"(>{ORDERBOOK_SPREAD_WARN_PCT:.0f}% threshold) — wide spread, high uncertainty"
                        )
                    depth_imb = sm.get("depth_imbalance", 0.0)
                    if abs(depth_imb) > 0.5:
                        self.logger.info(
                            f"⚠️  A-S MM: {ticker} depth imbalance={depth_imb:.2f} "
                            f"— {'YES' if depth_imb > 0 else 'NO'} heavy"
                        )

                # ---- PRE-TRADE SIGNAL GATE: VPIN + Hawkes skip ----
                # Re-use the microstructure checks from _make_trade_decision
                # to avoid posting quotes in toxic market conditions
                vpin_result = self.risk_manager.check_vpin(
                    ticker, self.vpin_estimator)
                if vpin_result.get('skip_trade') or vpin_result.get('signal') == 'extreme':
                    self.logger.info(
                        f"⛔ A-S MM: skipping {ticker} — VPIN={vpin_result['vpin']:.3f} extreme"
                    )
                    continue

                hawkes_result = self.risk_manager.check_hawkes(
                    ticker, self.hawkes_estimator)
                if hawkes_result.get('skip_trade'):
                    self.logger.info(
                        f"⛔ A-S MM: skipping {ticker} — Hawkes BR={hawkes_result['branching_ratio']:.3f}"
                    )
                    continue

                # ---- Get A-S optimal quotes ----
                quotes = self.as_market_maker.get_quotes(
                    ticker=ticker,
                    mode=AVELLANEDA_MODE,
                    time_to_expiry_hours=AVELLANEDA_MAX_TTE_HOURS,
                )

                if "error" in quotes:
                    self.logger.debug(f"A-S MM: {ticker} — {quotes['error']}")
                    continue

                bid_price = quotes.get("bid_price", 0)
                ask_price = quotes.get("ask_price", 0)
                spread    = quotes.get("spread", 0)
                vpin_adj  = quotes.get("vpin_adjustment", 1.0)
                skew      = quotes.get("skew", 0)

                # Validate quotes are in $0.01–$0.99 range
                if not (0.01 <= bid_price < ask_price <= 0.99):
                    self.logger.warning(
                        f"A-S MM: invalid quotes for {ticker} — bid={bid_price} ask={ask_price}"
                    )
                    continue

                quantity = self.market_maker.calculate_quantity(self.bankroll)

                # ---- Post orders ----
                self.market_maker.open_market_making_position(
                    market_id=ticker,
                    quantity=quantity,
                    bid_price=bid_price,
                    ask_price=ask_price,
                )
                self.logger.info(
                    f"🎯 A-S MM: {ticker} bid={bid_price:.4f} ask={ask_price:.4f} "
                    f"spread={spread:.4f} VPIN_adj=×{vpin_adj:.1f} skew={skew:.2f}"
                )

                # ---- ORDER LIFECYCLE: track open orders ----
                # Use get_open_orders_for_market to get the placed order IDs
                try:
                    open_orders = self.market_maker.get_open_orders_for_market(ticker)
                    for oid, info in open_orders.items():
                        side = info.get("side", "")
                        price = info.get("price", 0)
                        qty = info.get("quantity", 0)
                        if side == "bid":
                            self.order_tracker.track_bid(ticker, oid, price=price, quantity=qty)
                        elif side == "ask":
                            self.order_tracker.track_ask(ticker, oid, price=price, quantity=qty)
                except Exception as e:
                    self.logger.debug(f"OrderTracker: could not fetch open orders: {e}")

        except Exception as e:
            self.logger.error(f"Error in market making: {e}")
    
    # =========================================================================
    # END MARKET MAKING
    # =========================================================================

    def analyze_market(self, market_data):
        # Enhanced analysis with news sentiment
        return self._make_trade_decision(market_data)

    def run_trading_strategy(self):
        """
        Main entry point for running the trading strategy.
        Called by main.py loop.
        """
        try:
            # Get current market data from streamer
            market_data = self.market_data_streamer.get_all_markets_data()
            
            # Format for strategy (wrap in dict with 'markets' key)
            markets_list = list(market_data.values()) if market_data else []
            formatted_data = {'markets': markets_list}
            
            # Analyze and make trade decision
            trade_decision = self.analyze_market(formatted_data)
            
            # Execute if decision made
            if trade_decision:
                self.execute_trade(trade_decision)
            
            # Check positions for risk management (stop-loss, etc.)
            if self.current_positions:
                current_prices = {mid: md.current_price for mid, md in market_data.items()}
                self.check_positions_for_risk_management(current_prices)
            
            # Phase 2: Check max hold time (10 days)
            positions_to_close = self.check_max_hold_time()
            if positions_to_close and market_data:
                current_prices = {mid: md.current_price for mid, md in market_data.items()}
                for event_id in positions_to_close:
                    exit_price = current_prices.get(event_id, 0.50)
                    self.close_position_for_max_hold(event_id, exit_price)
            
            # Phase 5: Run market making strategy
            if self.market_making_enabled and hasattr(self, 'market_maker'):
                self.run_market_making(markets_list)

            # Option C: Kyle lambda background refresh (every 15 min on active positions)
            self._refresh_kyle_signals(market_data)

            # Option C: Hawkes branching ratio background refresh (every 15 min)
            self._refresh_hawkes_signals(market_data)

            # Option C: VPIN background refresh (every 15 min)
            self._refresh_vpin_signals(market_data)

        except Exception as e:
            self.logger.error(f"Error in trading strategy: {e}")

    def _make_trade_decision(self, market_data):
        """
        Enhanced trade decision making with multiple strategies using dynamic settings
        Priority: News Sentiment → Statistical Arbitrage → Volatility Analysis

        Pre-trade microstructure signal gate:
        VPIN > 0.80 or Hawkes BR > 0.80 → SKIP (adverse selection / extreme clustering)
        """
        # Phase 1: Check daily loss limit BEFORE any trading
        if not self.check_daily_loss_limit():
            self.logger.warning("Skipping trade - daily loss limit exceeded")
            return None

        # ------------------------------------------------------------------
        # MICROSTRUCTURE SIGNAL GATE — VPIN + Hawkes + Kyle (Option A)
        # Skip if extreme adverse selection or extreme clustering.
        # Checks run BEFORE any strategy — no point analyzing a toxic market.
        # ------------------------------------------------------------------
        market_id = None
        if market_data and 'markets' in market_data and market_data['markets']:
            first = market_data['markets'][0]
            market_id = (getattr(first, 'market_id', None)
                      or getattr(first, 'id', None)
                      or getattr(first, 'ticker', None))

        # Default signal results (used when no market_id is available)
        vpin_result   = {"vpin": 0.0, "signal": "no_data", "skip_trade": False}
        hawkes_result = {"branching_ratio": 0.0, "signal": "no_data", "skip_trade": False}
        kyle_result   = {"lambda": 0.0, "signal": "no_data", "position_scale": 1.0, "r_squared": 0.0}

        if market_id:
            # VPIN — adverse selection (most dangerous, checked first)
            vpin_result = self.risk_manager.check_vpin(market_id, self.vpin_estimator)
            if vpin_result.get('skip_trade'):
                self.logger.warning(
                    f"⛔ SKIP {market_id}: VPIN={vpin_result['vpin']:.3f} "
                    f"({vpin_result['signal']}) — extreme adverse selection"
                )
                return None
            if vpin_result.get('signal') in ('high', 'elevated'):
                self.logger.info(
                    f"⚠️  VPIN={vpin_result['vpin']:.3f} ({vpin_result['signal']}) on {market_id}"
                )

            # Hawkes — order flow clustering
            hawkes_result = self.risk_manager.check_hawkes(market_id, self.hawkes_estimator)
            if hawkes_result.get('skip_trade'):
                self.logger.warning(
                    f"⛔ SKIP {market_id}: Hawkes BR={hawkes_result['branching_ratio']:.3f} "
                    f"({hawkes_result['signal']}) — extreme clustering"
                )
                return None
            if hawkes_result.get('signal') == 'moderate':
                self.logger.info(
                    f"⚠️  Hawkes BR={hawkes_result['branching_ratio']:.3f} (moderate) on {market_id}"
                )

            # Kyle λ — price impact (used for position scaling)
            kyle_result = self.risk_manager.check_kyle_lambda(market_id, self.kyle_estimator)
            if kyle_result.get('signal') in ('high', 'moderate'):
                self.logger.info(
                    f"⚠️  Kyle λ={kyle_result['lambda']:.6f} R²={kyle_result.get('r_squared',0):.4f} "
                    f"({kyle_result['signal']}) on {market_id}"
                )
        
        trade_decision = None
        settings = self.settings_manager.settings

        # Strategy 1: News Sentiment Analysis (if enabled)
        if settings.news_sentiment_enabled:
            try:
                # Convert MarketData dataclass objects to dicts for news analyzer
                raw_markets = market_data.get('markets', [])
                markets_as_dicts = []
                for m in raw_markets:
                    if hasattr(m, '__dataclass_fields__'):
                        from dataclasses import asdict
                        markets_as_dicts.append(asdict(m))
                    else:
                        markets_as_dicts.append(m)
                
                sentiment_analysis = self.news_analyzer.get_market_relevant_news(
                    markets=markets_as_dicts
                )
                sentiment_decision = self.news_analyzer.should_trade_based_on_sentiment(
                    sentiment_analysis, settings.news_sentiment_threshold
                )

                if sentiment_decision['should_trade']:
                    self.logger.info(f"News sentiment signal: {sentiment_decision['reason']}")

                    # Find suitable market to trade based on sentiment
                    if market_data and 'markets' in market_data and market_data['markets']:
                        market = market_data['markets'][0]  # Simple selection - could be enhanced
                        # MarketData dataclass: use attribute access not .get()
                        event_id = getattr(market, 'market_id', None) or getattr(market, 'id', None)
                        current_price = getattr(market, 'current_price', None)

                        if event_id and current_price:
                            action = 'buy' if sentiment_decision['direction'] == 'long' else 'sell'

                            # Apply volatility-adjusted Kelly sizing
                            # Compute vol from market's price history
                            vol = None
                            if hasattr(market, 'price_history'):
                                vol = self.risk_manager.compute_annualized_volatility(market.price_history)
                            position_size_fraction = self.risk_manager.calculate_position_size_kelly(
                                confidence=sentiment_decision['confidence'],
                                volatility=vol
                            )
                            position_value = self.risk_manager.current_bankroll * position_size_fraction
                            quantity = max(1, int(position_value / current_price))

                            trade_decision = {
                                'event_id': event_id,
                                'action': action,
                                'quantity': quantity,
                                'price': current_price,
                                'strategy': 'news_sentiment',
                                'sentiment_score': sentiment_decision['sentiment_score'],
                                'confidence': sentiment_decision['confidence']
                            }

                            self.logger.info(f"News sentiment trade decision: {action} {event_id} "
                                           f"at {current_price} (sentiment: {sentiment_decision['sentiment_score']:.3f})")

            except Exception as e:
                self.logger.error(f"Error in news sentiment analysis: {e}")

        # Strategy 2: Statistical Arbitrage (if enabled and no sentiment signal)
        if not trade_decision and settings.statistical_arbitrage_enabled:
            try:
                arbitrage_opportunities = self._statistical_arbitrage(market_data)
                if arbitrage_opportunities:
                    # Take the highest confidence opportunity
                    best_opportunity = arbitrage_opportunities[0]
                    execution_decision = self.arbitrage_analyzer.should_execute_arbitrage(
                        best_opportunity, risk_tolerance=settings.stat_arbitrage_threshold
                    )

                    if execution_decision['should_execute']:
                        self.logger.info(f"Arbitrage signal: {execution_decision['reason']}")

                        # For simplicity, focus on one side of the arbitrage pair
                        market1 = execution_decision['market1']
                        market2 = execution_decision['market2']

                        if best_opportunity['signal'] == 'LONG_SPREAD':
                            event_id = market1['id']
                            action = 'buy'
                        else:  # SHORT_SPREAD
                            event_id = market1['id']
                            action = 'sell'

                        # Apply volatility-adjusted Kelly sizing
                        # Compute vol from market's price history (market1 is already a dict)
                        vol = self.risk_manager.compute_annualized_volatility(market1.get('price_history', []))
                        position_size_fraction = self.risk_manager.calculate_position_size_kelly(
                            confidence=execution_decision['confidence'],
                            volatility=vol
                        )
                        position_value = self.risk_manager.current_bankroll * position_size_fraction
                        quantity = max(1, int(position_value / market1['current_price']))

                        trade_decision = {
                            'event_id': event_id,
                            'action': action,
                            'quantity': quantity,
                            'price': market1['current_price'],
                            'strategy': 'statistical_arbitrage',
                            'z_score': best_opportunity['z_score'],
                            'confidence': execution_decision['confidence'],
                            'arbitrage_pair': [market1['id'], market2['id']]
                        }

                        self.logger.info(f"Arbitrage trade decision: {action} {event_id} "
                                       f"(z-score: {best_opportunity['z_score']:.3f})")

            except Exception as e:
                self.logger.error(f"Error in statistical arbitrage: {e}")

        # Strategy 3: Volatility Analysis (if enabled and no other signals)
        if not trade_decision and settings.volatility_based_enabled:
            try:
                volatility_decision = self._volatility_analysis(market_data)
                if volatility_decision and volatility_decision.get('should_trade'):
                    self.logger.info(f"Volatility signal: {volatility_decision['reason']}")

                    # Find market for volatility-based trade
                    if market_data and 'markets' in market_data and market_data['markets']:
                        market = market_data['markets'][0]  # Could be enhanced to select based on volatility
                        event_id = market.get('id')
                        current_price = market.get('current_price')

                        if event_id and current_price and volatility_decision.get('direction'):
                            action = 'buy' if volatility_decision['direction'] == 'long' else 'sell'

                            # Apply volatility-adjusted Kelly sizing
                            # Pass current annualized volatility from GARCH analysis
                            vol = volatility_decision.get('volatility_analysis', {}).get('current_volatility', None)
                            position_size_fraction = self.risk_manager.calculate_position_size_kelly(
                                confidence=volatility_decision['confidence'],
                                volatility=vol
                            )
                            position_value = self.risk_manager.current_bankroll * position_size_fraction
                            quantity = max(1, int(position_value / current_price))

                            trade_decision = {
                                'event_id': event_id,
                                'action': action,
                                'quantity': quantity,
                                'price': current_price,
                                'strategy': 'volatility_based',
                                'volatility_regime': volatility_decision.get('volatility_regime'),
                                'confidence': volatility_decision['confidence'],
                                'signal_type': volatility_decision.get('signal_type')
                            }

                            self.logger.info(f"Volatility trade decision: {action} {event_id} "
                                           f"(regime: {volatility_decision.get('volatility_regime')})")

            except Exception as e:
                self.logger.error(f"Error in volatility analysis: {e}")

        # ------------------------------------------------------------------
        # KYLE λ POSITION SCALING — apply after all strategies
        # Scale down quantity based on price impact signal.
        # ------------------------------------------------------------------
        if trade_decision and kyle_result.get('position_scale', 1.0) < 1.0:
            original_qty = trade_decision['quantity']
            scale = kyle_result['position_scale']
            trade_decision['quantity'] = max(1, int(original_qty * scale))
            trade_decision['kyle_scale'] = scale
            trade_decision['kyle_signal'] = kyle_result.get('signal', 'unknown')
            self.logger.info(
                f"📉 Kyle λ position scaling: {original_qty} → {trade_decision['quantity']} "
                f"(×{scale:.2f}, signal={kyle_result.get('signal')})"
            )

        # Attach microstructure signal metadata to trade_decision for execute_trade logging
        if trade_decision:
            trade_decision['_microstructure'] = {
                'vpin':     vpin_result.get('vpin', 0.0),
                'vpin_signal':  vpin_result.get('signal', 'unknown'),
                'hawkes_br':    hawkes_result.get('branching_ratio', 0.0),
                'hawkes_signal': hawkes_result.get('signal', 'unknown'),
                'kyle_lambda':  kyle_result.get('lambda', 0.0),
                'kyle_r2':      kyle_result.get('r_squared', 0.0),
                'kyle_signal':  kyle_result.get('signal', 'unknown'),
                'kyle_scale':   kyle_result.get('position_scale', 1.0),
            }

        return trade_decision

    def execute_trade(self, trade_decision):
        """
        Execute trade with basic risk management and circuit breaker.
        """
        # Phase 5: Check circuit breaker before any trade
        if not self.circuit_breaker.can_trade():
            status = self.circuit_breaker.get_status()
            self.logger.warning(f"CIRCUIT BREAKER BLOCKED TRADE: {status['state']} - {status['reason']}")
            self.notifier.send_error_notification(f"Trade blocked by circuit breaker: {status['state']}")
            return

        if not trade_decision:
            self.logger.info("No trade decision to execute.")
            return

        event_id = trade_decision['event_id']
        action = trade_decision['action']
        quantity = trade_decision['quantity']
        price = trade_decision['price']
        strategy = trade_decision.get('strategy', 'unknown')

        try:
            # Phase 5: Exposure limits per SAFETY_GUARDRAILS.md Section 5.1
            position_value = quantity * price
            single_trade_pct = position_value / self.bankroll
            
            # Single trade limit: 25% max
            if single_trade_pct > 0.25:
                self.logger.warning(f"Position size {single_trade_pct:.1%} exceeds 25% single trade limit")
                return
            
            # Total exposure limit: 100% max
            current_exposure = sum(
                pos['quantity'] * pos.get('entry_price', 0) 
                for pos in self.current_positions.values()
            ) / self.bankroll
            
            if current_exposure + single_trade_pct > 1.0:
                self.logger.warning(f"Total exposure {current_exposure + single_trade_pct:.1%} would exceed 100%")
                return

            # Validate position size via risk manager
            if not self.risk_manager.validate_position_size(position_value):
                self.logger.warning(f"Position size ${position_value:.2f} exceeds risk limits")
                return

            self.logger.info(f"Executing {strategy} trade: {action} {quantity} units of {event_id} "
                           f"at ${price:.2f}")

            # Phase 3: Paper trading - skip real API calls
            if self.paper_trading:
                self.logger.info(f"📄 PAPER TRADE: {action.upper()} {quantity} {event_id} at ${price:.2f}")
                
                # Log to paper trader
                if self.paper_trader:
                    paper_id = self.paper_trader.simulate_trade(
                        event_id=event_id,
                        action=action.lower(),
                        quantity=quantity,
                        entry_price=price,
                        strategy=strategy
                    )
                    self.logger.info(f"📄 Paper trade logged (ID: {paper_id})")
                
                # Still record for analytics
                trade_id = f"paper_{strategy}_{event_id}_{int(time.time())}"
            else:
                # Generate unique trade ID for real trade
                trade_id = f"{strategy}_{event_id}_{int(time.time())}"
                
                # Execute the trade via Kalshi API
                # Convert price to cents (API expects integer cents, e.g., 0.55 -> 55)
                price_cents = int(price * 100) if price <= 1 else int(price)
                
                # Determine side: 'buy' action = 'yes' side, 'sell' action = 'no' side
                # In Kalshi, 'yes' means betting YES will happen, 'no' means betting it won't
                side = 'yes' if action.lower() == 'buy' else 'no'
                
                # Build order payload per Kalshi API spec
                # Docs: https://docs.kalshi.com/api-reference/orders/create-order
                order_payload = {
                    'ticker': event_id,
                    'side': side,
                    'action': action.lower(),
                    'client_order_id': trade_id,
                    'count': quantity,
                    'yes_price': price_cents,  # Price in cents
                    'no_price': price_cents,
                }
                
                try:
                    api_response = self.api.create_order(order_payload)
                    self.logger.info(f"REAL TRADE EXECUTED: {action.upper()} {quantity} {event_id} "
                                   f"at ${price:.2f} (API response: {api_response})")
                except Exception as api_err:
                    self.logger.error(f"API order failed: {api_err}")
                    self.notifier.send_error_notification(f"Order failed for {event_id}: {api_err}")
                    return  # Abort if API call fails

            # Record trade in performance analytics
            trade = Trade(
                trade_id=trade_id,
                market_id=event_id,
                strategy=strategy,
                side=action.lower(),
                quantity=quantity,
                entry_price=price,
                confidence=trade_decision.get('confidence', 0.5)
            )
            self.performance_analytics.record_trade(trade)

            # Store position locally for basic tracking
            self.current_positions[event_id] = {
                'quantity': quantity,
                'entry_price': price,
                'type': 'long' if action.lower() == 'buy' else 'short',
                'strategy': strategy,
                'stop_loss_price': self.risk_manager.calculate_stop_loss_price(
                    price, action.lower() == 'buy'
                ),
                'trade_id': trade_id
            }

            # Send notification
            self.notifier.send_trade_notification(
                f"{strategy.upper()}: {action.upper()} {quantity} units of {event_id} at ${price:.2f}"
            )

        except Exception as e:
            self.logger.error(f"Error executing {strategy} trade for {event_id}: {e}")
            self.notifier.send_error_notification(f"Trade execution error for {event_id}: {e}")

    def check_positions_for_risk_management(self, current_prices: Dict[str, float]):
        """
        Check all open positions for stop-loss triggers.
        """
        positions_to_close = []

        for market_id, position in self.current_positions.items():
            current_price = current_prices.get(market_id, position['entry_price'])

            # Check stop-loss
            if self.risk_manager.check_stop_loss_trigger(
                position['entry_price'], current_price, position['type'] == 'long'
            ):
                positions_to_close.append({
                    'market_id': market_id,
                    'exit_price': current_price,
                    'reason': 'stop_loss_triggered'
                })

        # Close positions that hit stop-loss
        for close_info in positions_to_close:
            self.close_position_simple(close_info['market_id'],
                                     close_info['exit_price'],
                                     close_info['reason'])

    def close_position_simple(self, market_id: str, exit_price: float, reason: str):
        """
        Close a position with simple P&L calculation.
        """
        if market_id not in self.current_positions:
            return

        position = self.current_positions[market_id]

        # Calculate P&L
        entry_price = position['entry_price']
        quantity = position['quantity']

        if position['type'] == 'long':
            pnl = (exit_price - entry_price) * quantity
        else:  # short
            pnl = (entry_price - exit_price) * quantity

        # Update bankroll
        self.risk_manager.current_bankroll += pnl

        # Record trade closure in performance analytics
        trade_id = position.get('trade_id')
        if trade_id:
            self.performance_analytics.close_trade(trade_id, exit_price, reason)

        # Remove from positions
        del self.current_positions[market_id]

        # Send notification
        self.notifier.send_trade_notification(
            f"RISK MANAGEMENT: Closed {market_id} at ${exit_price:.2f}, P&L: ${pnl:.2f} ({reason})"
        )

        self.logger.info(f"Closed position {market_id}: P&L ${pnl:.2f}, reason: {reason}")

    def get_portfolio_status(self):
        """
        Get portfolio status with basic risk metrics.
        """
        return self.risk_manager.get_portfolio_status()


