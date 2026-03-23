#!/usr/bin/env python3
"""Volatility analysis module for Kalshi trading bot."""

import logging
import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Any, Tuple
from arch import arch_model
from statsmodels.tsa.stattools import adfuller
from sklearn.preprocessing import StandardScaler
from config import VOLATILITY_THRESHOLD

logger = logging.getLogger(__name__)

class VolatilityAnalyzer:
    """Analyzes volatility patterns for trading signals."""

    def __init__(self, min_history_points: int = 100):
        self.min_history_points = min_history_points
        self.scaler = StandardScaler()

    def calculate_historical_volatility(self, prices: List[float],
                                       window: int = 20) -> Dict[str, Any]:
        """
        Calculate historical volatility using various methods.

        Args:
            prices: Price series
            window: Rolling window for volatility calculation

        Returns:
            Volatility metrics
        """
        if len(prices) < window:
            return {
                'historical_volatility': 0.0,
                'realized_volatility': 0.0,
                'error': 'Insufficient data points'
            }

        try:
            prices_array = np.array(prices, dtype=float)

            # Calculate returns
            returns = np.diff(np.log(prices_array))

            # Historical volatility (rolling standard deviation of returns)
            hist_vol = np.std(returns[-window:]) * np.sqrt(252)  # Annualized

            # Realized volatility (absolute sum of returns)
            realized_vol = np.sum(np.abs(returns[-window:])) * np.sqrt(252)

            # Parkinson volatility (if we had high/low data)
            # For now, approximate with range-based measure
            rolling_max = pd.Series(prices_array).rolling(window=window).max()
            rolling_min = pd.Series(prices_array).rolling(window=window).min()
            parkinson_vol = np.log(rolling_max / rolling_min).mean() * np.sqrt(252 / window)

            return {
                'historical_volatility': float(hist_vol),
                'realized_volatility': float(realized_vol),
                'parkinson_volatility': float(parkinson_vol),
                'current_volatility': float(hist_vol),  # Alias for compatibility
                'returns_std': float(np.std(returns[-window:])),
                'window_size': window
            }

        except Exception as e:
            logger.error(f"Error calculating historical volatility: {e}")
            return {
                'historical_volatility': 0.0,
                'realized_volatility': 0.0,
                'error': str(e)
            }

    def calculate_atr(self, prices: List[float], period: int = 14) -> float:
        """
        Calculate Average True Range for a price series.
        For binary markets (0-1 price range), True Range is approximated
        as |price_change| with a minimum tick of $0.01.

        Args:
            prices: Price series (list of float dollar prices)
            period: EMA lookback period for ATR (default 14)

        Returns:
            ATR value in dollar terms, or 0.0 if insufficient data
        """
        if len(prices) < 2:
            return 0.0

        prices_arr = np.array(prices, dtype=float)
        # True Range: |price_change| with $0.01 floor (binary market minimum tick)
        changes = np.abs(np.diff(prices_arr))
        changes = np.maximum(changes, 0.01)

        if len(changes) < period:
            # Not enough data for EMA — use simple mean
            return float(np.mean(changes))

        # EMA-style ATR (same formula as Wilder's original)
        atr = changes[0]
        alpha = 1.0 / period
        for change in changes[1:]:
            atr = (alpha * change) + ((1 - alpha) * atr)
        return float(atr)

    def fit_garch_model(self, returns: List[float]) -> Dict[str, Any]:
        """
        Fit GARCH(1,1) model to returns data.

        Args:
            returns: Log returns series

        Returns:
            GARCH model results
        """
        if len(returns) < self.min_history_points:
            return {
                'conditional_volatility': 0.0,
                'forecast_volatility': 0.0,
                'error': 'Insufficient data for GARCH modeling'
            }

        try:
            returns_array = np.array(returns, dtype=float)

            # Fit GARCH(1,1) model
            model = arch_model(returns_array, vol='Garch', p=1, q=1)
            results = model.fit(disp='off')

            # Get conditional volatility
            conditional_vol = results.conditional_volatility

            # Forecast next period volatility
            forecast = results.forecast(horizon=1)
            forecast_vol = np.sqrt(forecast.variance.values[-1, 0])

            # Model diagnostics
            log_likelihood = results.loglikelihood
            aic = results.aic
            bic = results.bic

            return {
                'conditional_volatility': float(conditional_vol[-1]),
                'forecast_volatility': float(forecast_vol),
                'model_params': {
                    'omega': float(results.params['omega']),
                    'alpha': float(results.params['alpha[1]']),
                    'beta': float(results.params['beta[1]'])
                },
                'model_stats': {
                    'log_likelihood': float(log_likelihood),
                    'aic': float(aic),
                    'bic': float(bic)
                },
                'persistence': float(results.params['alpha[1]'] + results.params['beta[1]'])
            }

        except Exception as e:
            logger.error(f"Error fitting GARCH model: {e}")
            return {
                'conditional_volatility': 0.0,
                'forecast_volatility': 0.0,
                'error': str(e)
            }

    def analyze_volatility_regime(self, volatility: float,
                                 historical_volatilities: List[float]) -> Dict[str, Any]:
        """
        Determine current volatility regime (low, normal, high).

        Args:
            volatility: Current volatility measure
            historical_volatilities: Historical volatility values

        Returns:
            Volatility regime analysis
        """
        if not historical_volatilities:
            return {
                'regime': 'unknown',
                'confidence': 0.0,
                'percentile': 50.0
            }

        try:
            hist_array = np.array(historical_volatilities)

            # Calculate percentiles
            percentile = np.percentile(hist_array, volatility * 100 if volatility < 1 else
                                     min(volatility * 10, 99))

            # Determine regime
            if percentile < 25:
                regime = 'low'
            elif percentile < 75:
                regime = 'normal'
            else:
                regime = 'high'

            # Confidence in regime classification
            distance_from_median = abs(percentile - 50)
            confidence = min(distance_from_median / 25, 1.0)

            return {
                'regime': regime,
                'confidence': confidence,
                'percentile': percentile,
                'historical_median': float(np.median(hist_array)),
                'historical_mean': float(np.mean(hist_array)),
                'historical_std': float(np.std(hist_array))
            }

        except Exception as e:
            logger.error(f"Error analyzing volatility regime: {e}")
            return {
                'regime': 'unknown',
                'confidence': 0.0,
                'error': str(e)
            }

    def detect_volatility_signals(self, current_volatility: float,
                                historical_volatilities: List[float],
                                price_trend: str = 'sideways') -> Dict[str, Any]:
        """
        Detect trading signals based on volatility patterns.

        Args:
            current_volatility: Current volatility measure
            historical_volatilities: Historical volatility values
            price_trend: Current price trend ('up', 'down', 'sideways')

        Returns:
            Volatility-based trading signals
        """
        regime_analysis = self.analyze_volatility_regime(current_volatility, historical_volatilities)

        signal = {
            'volatility_signal': None,
            'direction': None,  # 'long' or 'short'
            'confidence': 0.0,
            'reason': '',
            'volatility_regime': regime_analysis['regime']
        }

        # High volatility signals - mean reversion
        if regime_analysis['regime'] == 'high' and regime_analysis['confidence'] > 0.6:
            if price_trend == 'up':
                signal.update({
                    'volatility_signal': 'MEAN_REVERSION_SHORT',
                    'direction': 'short',
                    'confidence': regime_analysis['confidence'] * 0.8,
                    'reason': f'High volatility ({current_volatility:.3f}) suggests mean reversion against uptrend'
                })
            elif price_trend == 'down':
                signal.update({
                    'volatility_signal': 'MEAN_REVERSION_LONG',
                    'direction': 'long',
                    'confidence': regime_analysis['confidence'] * 0.8,
                    'reason': f'High volatility ({current_volatility:.3f}) suggests mean reversion against downtrend'
                })

        # Low volatility signals - breakout potential
        elif regime_analysis['regime'] == 'low' and regime_analysis['confidence'] > 0.5:
            signal.update({
                'volatility_signal': 'BREAKOUT_SETUP',
                'direction': None,  # Direction determined by price action
                'confidence': regime_analysis['confidence'] * 0.6,
                'reason': f'Low volatility ({current_volatility:.3f}) suggests potential breakout'
            })

        # Normal volatility - no clear signal
        else:
            signal['reason'] = f'Normal volatility regime ({regime_analysis["regime"]}) - no clear signal'

        return signal

    def analyze_market_volatility(self, market_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Comprehensive volatility analysis for a market.

        Args:
            market_data: Market data with price history

        Returns:
            Complete volatility analysis
        """
        price_history = market_data.get('price_history', [])

        if len(price_history) < self.min_history_points:
            return {
                'market_id': market_data.get('id', 'unknown'),
                'error': 'Insufficient price history for volatility analysis'
            }

        try:
            # Calculate historical volatility
            hist_vol_analysis = self.calculate_historical_volatility(price_history)

            # Fit GARCH model
            returns = np.diff(np.log(np.array(price_history, dtype=float)))
            garch_analysis = self.fit_garch_model(returns.tolist())

            # Analyze volatility regime
            current_vol = hist_vol_analysis.get('historical_volatility', 0)
            # Use recent volatility history for regime analysis
            recent_vols = []
            for i in range(10, len(price_history), 10):  # Every 10 points
                window_vol = self.calculate_historical_volatility(price_history[:i+1])
                recent_vols.append(window_vol.get('historical_volatility', 0))

            regime_analysis = self.analyze_volatility_regime(current_vol, recent_vols)

            # Generate trading signals
            # Simple trend detection (could be enhanced)
            recent_prices = price_history[-20:]
            trend = 'sideways'
            if recent_prices[-1] > recent_prices[0] * 1.02:
                trend = 'up'
            elif recent_prices[-1] < recent_prices[0] * 0.98:
                trend = 'down'

            signal_analysis = self.detect_volatility_signals(current_vol, recent_vols, trend)

            # Build result dict - market_data is a dict with keys: market_id, title, current_price, price_history
            result = {
                'market_id': market_data.get('market_id', 'unknown'),
                'market_title': market_data.get('title', ''),
                'current_price': market_data.get('current_price', 0),
                'volatility_analysis': hist_vol_analysis,
                'garch_analysis': garch_analysis,
                'regime_analysis': regime_analysis,
                'signal_analysis': signal_analysis,
                'trend': trend,
                'analysis_timestamp': pd.Timestamp.now().isoformat()
            }

        except Exception as e:
            logger.error(f"Error analyzing market volatility: {e}")
            return {
                'market_id': market_data.get('market_id', 'unknown') if isinstance(market_data, dict) else 'unknown',
                'error': str(e)
            }

    def should_trade_based_on_volatility(self, volatility_analysis: Dict[str, Any],
                                        risk_tolerance: float = 0.6) -> Dict[str, Any]:
        """
        Determine if volatility analysis warrants a trading decision.

        Args:
            volatility_analysis: Result from analyze_market_volatility
            risk_tolerance: Minimum confidence threshold

        Returns:
            Trading decision based on volatility
        """
        signal_analysis = volatility_analysis.get('signal_analysis', {})
        confidence = signal_analysis.get('confidence', 0)

        decision = {
            'should_trade': False,
            'reason': '',
            'strategy': 'volatility_based',
            'direction': signal_analysis.get('direction'),
            'confidence': confidence
        }

        if confidence < risk_tolerance:
            decision['reason'] = f'Volatility signal confidence too low ({confidence:.2f} < {risk_tolerance})'
            return decision

        signal = signal_analysis.get('volatility_signal')
        if signal:
            decision.update({
                'should_trade': True,
                'reason': signal_analysis.get('reason', ''),
                'signal_type': signal
            })

        return decision
