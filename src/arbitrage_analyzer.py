#!/usr/bin/env python3
"""Statistical arbitrage module for Kalshi trading bot."""

import logging
import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple, Any
from statsmodels.tsa.stattools import coint, adfuller
from statsmodels.tsa.vector_ar import vecm
from scipy import stats
from sklearn.preprocessing import StandardScaler
from config import STAT_ARBITRAGE_THRESHOLD

logger = logging.getLogger(__name__)

class StatisticalArbitrageAnalyzer:
    """Analyzes statistical relationships between Kalshi markets for arbitrage opportunities."""

    def __init__(self, min_history_points: int = 50):
        self.min_history_points = min_history_points
        self.scaler = StandardScaler()

    def test_cointegration(self, series1: List[float], series2: List[float]) -> Dict[str, Any]:
        """
        Test for cointegration between two price series.

        Args:
            series1: First price series
            series2: Second price series

        Returns:
            Cointegration test results
        """
        if len(series1) < self.min_history_points or len(series2) < self.min_history_points:
            return {
                'cointegrated': False,
                'p_value': 1.0,
                'confidence': 0.0,
                'reason': 'Insufficient data points'
            }

        # Ensure series are same length (they can diverge if markets were added at different times)
        if len(series1) != len(series2):
            min_len = min(len(series1), len(series2))
            series1 = series1[-min_len:]
            series2 = series2[-min_len:]

        # Guard: cointegration requires variance in both series
        if np.std(series1) < 1e-8 or np.std(series2) < 1e-8:
            return {
                'cointegrated': False,
                'p_value': 1.0,
                'confidence': 0.0,
                'reason': 'Series is constant (zero variance)'
            }

        try:
            # Perform cointegration test
            coint_t, p_value, crit_values = coint(series1, series2)

            # Determine confidence level
            confidence = 0.0
            if p_value < 0.01:
                confidence = 0.99  # 99% confidence
            elif p_value < 0.05:
                confidence = 0.95  # 95% confidence
            elif p_value < 0.10:
                confidence = 0.90  # 90% confidence

            return {
                'cointegrated': p_value < 0.05,  # 5% significance level
                'p_value': p_value,
                'test_statistic': coint_t,
                'critical_values': crit_values,
                'confidence': confidence,
                'reason': 'Cointegration test completed'
            }

        except Exception as e:
            logger.error(f"Error in cointegration test: {e}")
            return {
                'cointegrated': False,
                'p_value': 1.0,
                'confidence': 0.0,
                'reason': f'Error: {str(e)}'
            }

    def calculate_spread(self, series1: List[float], series2: List[float]) -> Dict[str, Any]:
        """
        Calculate the spread between two cointegrated series.

        Args:
            series1: First price series
            series2: Second price series

        Returns:
            Spread analysis results
        """
        try:
            # Align series lengths (they can diverge if markets were added at different times)
            if len(series1) != len(series2):
                min_len = min(len(series1), len(series2))
                series1 = series1[-min_len:]
                series2 = series2[-min_len:]

            # Convert to numpy arrays
            s1 = np.array(series1, dtype=float)
            s2 = np.array(series2, dtype=float)

            # Normalize the series
            s1_norm = self.scaler.fit_transform(s1.reshape(-1, 1)).flatten()
            s2_norm = self.scaler.fit_transform(s2.reshape(-1, 1)).flatten()

            # Calculate spread (difference)
            spread = s1_norm - s2_norm

            # Calculate z-score of the spread
            spread_mean = np.mean(spread)
            spread_std = np.std(spread)
            z_score = (spread[-1] - spread_mean) / spread_std if spread_std > 0 else 0

            # Test for stationarity (mean reversion)
            try:
                adf_result = adfuller(spread)
                is_stationary = adf_result[1] < 0.05  # 5% significance
            except:
                is_stationary = False

            return {
                'spread': spread.tolist(),
                'z_score': float(z_score),
                'mean': float(spread_mean),
                'std': float(spread_std),
                'is_stationary': is_stationary,
                'adf_p_value': float(adf_result[1]) if 'adf_result' in locals() else 1.0,
                'latest_spread': float(spread[-1]),
                'upper_threshold': spread_mean + (STAT_ARBITRAGE_THRESHOLD * spread_std),
                'lower_threshold': spread_mean - (STAT_ARBITRAGE_THRESHOLD * spread_std)
            }

        except Exception as e:
            logger.error(f"Error calculating spread: {e}")
            return {
                'spread': [],
                'z_score': 0.0,
                'error': str(e)
            }

    def analyze_market_pair(self, market1_data: Dict[str, Any],
                           market2_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Analyze a pair of markets for arbitrage opportunities.

        Args:
            market1_data: First market data with price history
            market2_data: Second market data with price history

        Returns:
            Complete arbitrage analysis
        """
        market1_prices = market1_data.get('price_history', [])
        market2_prices = market2_data.get('price_history', [])

        if not market1_prices or not market2_prices:
            return {
                'arbitrage_opportunity': False,
                'reason': 'Missing price history data'
            }

        # Test for cointegration
        coint_result = self.test_cointegration(market1_prices, market2_prices)

        if not coint_result['cointegrated']:
            return {
                'arbitrage_opportunity': False,
                'cointegration': coint_result,
                'reason': f'No cointegration (p={coint_result["p_value"]:.3f})'
            }

        # Calculate spread and z-score
        spread_result = self.calculate_spread(market1_prices, market2_prices)

        # Determine arbitrage signal
        z_score = spread_result['z_score']
        arbitrage_signal = None
        confidence = 0.0

        # Strong mean reversion signals
        if z_score > STAT_ARBITRAGE_THRESHOLD:  # Spread is too high
            arbitrage_signal = 'SHORT_SPREAD'  # Sell market1, buy market2
            confidence = min(abs(z_score) / 3.0, 1.0)  # Scale confidence
        elif z_score < -STAT_ARBITRAGE_THRESHOLD:  # Spread is too low
            arbitrage_signal = 'LONG_SPREAD'  # Buy market1, sell market2
            confidence = min(abs(z_score) / 3.0, 1.0)

        return {
            'arbitrage_opportunity': arbitrage_signal is not None,
            'signal': arbitrage_signal,
            'confidence': confidence,
            'z_score': z_score,
            'cointegration': coint_result,
            'spread_analysis': spread_result,
            'market1': {
                'market_id': market1_data.get('market_id'),
                'title': market1_data.get('title'),
                'current_price': market1_data.get('current_price')
            },
            'market2': {
                'market_id': market2_data.get('market_id'),
                'title': market2_data.get('title'),
                'current_price': market2_data.get('current_price')
            }
        }

    def find_arbitrage_opportunities(self, markets_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Scan all markets for arbitrage opportunities.

        Args:
            markets_data: List of market data dictionaries

        Returns:
            List of arbitrage opportunities
        """
        opportunities = []

        # Only consider markets with sufficient price history
        eligible_markets = [
            market for market in markets_data
            if len(market.get('price_history', [])) >= self.min_history_points
        ]

        logger.info(f"Analyzing {len(eligible_markets)} markets for arbitrage opportunities")

        # Compare each pair of markets (this is O(n²) but fine for Kalshi's scale)
        for i, market1 in enumerate(eligible_markets):
            for market2 in eligible_markets[i+1:]:
                try:
                    analysis = self.analyze_market_pair(market1, market2)

                    if analysis['arbitrage_opportunity']:
                        opportunities.append(analysis)
                        logger.info(f"Arbitrage opportunity found: {market1.get('market_id')} vs {market2.get('market_id')} "
                                  f"(z-score: {analysis['z_score']:.2f})")

                except Exception as e:
                    logger.error(f"Error analyzing pair {market1.get('market_id')} vs {market2.get('market_id')}: {e}")

        # Sort by confidence (highest first)
        opportunities.sort(key=lambda x: x['confidence'], reverse=True)

        return opportunities

    def should_execute_arbitrage(self, arbitrage_analysis: Dict[str, Any],
                               risk_tolerance: float = 0.7) -> Dict[str, Any]:
        """
        Determine if an arbitrage opportunity should be executed.

        Args:
            arbitrage_analysis: Result from analyze_market_pair
            risk_tolerance: Minimum confidence threshold

        Returns:
            Execution decision
        """
        confidence = arbitrage_analysis.get('confidence', 0)
        z_score = arbitrage_analysis.get('z_score', 0)

        decision = {
            'should_execute': False,
            'reason': '',
            'position_size': 0.0,
            'expected_return': 0.0
        }

        if confidence < risk_tolerance:
            decision['reason'] = f'Confidence too low ({confidence:.2f} < {risk_tolerance})'
            return decision

        if abs(z_score) < STAT_ARBITRAGE_THRESHOLD:
            decision['reason'] = f'Z-score not extreme enough ({abs(z_score):.2f} < {STAT_ARBITRAGE_THRESHOLD})'
            return decision

        # Calculate expected return based on z-score
        expected_return = abs(z_score) * 0.01  # Rough estimate: 1% expected return per unit z-score

        # Position size based on confidence and z-score
        position_size = min(confidence * abs(z_score) / 4.0, 1.0)

        decision.update({
            'should_execute': True,
            'reason': f'Strong arbitrage signal (confidence: {confidence:.2f}, z-score: {z_score:.2f})',
            'position_size': position_size,
            'expected_return': expected_return,
            'signal': arbitrage_analysis.get('signal'),
            'market1': arbitrage_analysis.get('market1'),
            'market2': arbitrage_analysis.get('market2')
        })

        return decision
