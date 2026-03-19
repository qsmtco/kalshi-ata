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
    # ANALYZER WRAPPERS (missing methods)
    # =========================================================================
    
    def _statistical_arbitrage(self, market_data):
        """Wrapper for statistical arbitrage analyzer."""
        try:
            # Get markets list
            markets_list = market_data.get('markets', [])
            if not markets_list:
                return None
            
            opportunities = self.arbitrage_analyzer.find_arbitrage_opportunities(markets_list)
            return opportunities if opportunities else None
        except Exception as e:
            self.logger.error(f"Statistical arbitrage error: {e}")
            return None
    
    def _volatility_analysis(self, market_data):
        """Wrapper for volatility analyzer."""
        try:
            markets_list = market_data.get('markets', [])
            if not markets_list:
                return None
            
            # Take first market (simplified)
            market = markets_list[0]
            return self.volatility_analyzer.analyze_market_volatility(market)
        except Exception as e:
            self.logger.error(f"Volatility analysis error: {e}")
            return None
    
    # =========================================================================
    # MARKET MAKING (Phase 5)
    # =========================================================================
    
    def run_market_making(self, markets: List) -> None:
        """
        Run market making strategy on available markets.
        
        Places buy/sell orders around spread to capture arbitrage.
        """
        if not hasattr(self, 'market_maker'):
            return
        
        try:
            for market_data in markets:
                # Convert to dict if needed
                if hasattr(market_data, '__dict__'):
                    market = {'ticker': market_data.market_id, 
                             'yes_price': market_data.current_price,
                             'no_price': 1 - market_data.current_price,
                             'volume': getattr(market_data, 'volume', 0)}
                else:
                    market = market_data
                
                # Check if should market make
                if self.market_maker.should_market_make(market):
                    # Analyze for opportunity
                    analysis = self.market_maker.analyze_market(market)
                    if analysis:
                        quantity = self.market_maker.calculate_quantity(self.bankroll)
                        
                        # Open position
                        self.market_maker.open_market_making_position(
                            market_id=analysis['market_id'],
                            quantity=quantity,
                            bid_price=analysis['our_bid'],
                            ask_price=analysis['our_ask']
                        )
                        self.logger.info(
                            f"🎯 Market making: Opened {analysis['market_id']} "
                            f"bid={analysis['our_bid']:.2f} ask={analysis['our_ask']:.2f}"
                        )
                        
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
                
        except Exception as e:
            self.logger.error(f"Error in trading strategy: {e}")

    def _make_trade_decision(self, market_data):
        """
        Enhanced trade decision making with multiple strategies using dynamic settings
        Priority: News Sentiment → Statistical Arbitrage → Volatility Analysis
        """
        # Phase 1: Check daily loss limit BEFORE any trading
        if not self.check_daily_loss_limit():
            self.logger.warning("Skipping trade - daily loss limit exceeded")
            return None
        
        trade_decision = None
        settings = self.settings_manager.settings

        # Strategy 1: News Sentiment Analysis (if enabled)
        if settings.news_sentiment_enabled:
            try:
                sentiment_analysis = self.news_analyzer.get_market_relevant_news()
                sentiment_decision = self.news_analyzer.should_trade_based_on_sentiment(
                    sentiment_analysis, settings.news_sentiment_threshold
                )

                if sentiment_decision['should_trade']:
                    self.logger.info(f"News sentiment signal: {sentiment_decision['reason']}")

                    # Find suitable market to trade based on sentiment
                    if market_data and 'markets' in market_data and market_data['markets']:
                        market = market_data['markets'][0]  # Simple selection - could be enhanced
                        event_id = market.get('id')
                        current_price = market.get('current_price')

                        if event_id and current_price:
                            action = 'buy' if sentiment_decision['direction'] == 'long' else 'sell'

                            # Apply dynamic risk management
                            position_size_fraction = self.risk_manager.calculate_position_size_kelly(sentiment_decision['confidence'])
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

                        # Apply dynamic risk management
                        position_size_fraction = self.risk_manager.calculate_position_size_kelly(execution_decision['confidence'])
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

                            # Apply dynamic risk management
                            position_size_fraction = self.risk_manager.calculate_position_size_kelly(volatility_decision['confidence'])
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
                
                # Execute the trade via API
                if action.lower() == 'buy':
                    self.logger.info(f"BUY ORDER: {quantity} units of {event_id} at ${price:.2f}")
                elif action.lower() == 'sell':
                    self.logger.info(f"SELL ORDER: {quantity} units of {event_id} at ${price:.2f}")

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


