"""
Exit Strategy Selector — Option A: Regime Lookup Table

Selects the best exit strategy based on market regime (volatility + trend)
rather than a fixed priority-ordered stack.

Built: 2026-03-22
"""

import numpy as np
from typing import Optional

from config import (
    DRAWDOWN_OVERRIDE_PCT,
    PARTIAL_EXIT_PROFIT_THRESHOLD,
    TREND_LOOKBACK,
    REGIME_CONFIDENCE_MIN,
    LIQUIDITY_SPREAD_MAX,
    LIQUIDITY_MIN_BID_QTY,
    ATR_MULT_LOW_VOL,
    ATR_MULT_NORMAL_VOL,
    ATR_MULT_HIGH_VOL,
)
from exit_rules import (
    ExitResult,
    check_liquidity_exit,
    check_stop_loss,
    check_barrier_take_profit,
    check_partial_exit,
    check_atr_trailing_stop,
    check_probability_shift,
    check_time_exit,
)


# -------------------------------------------------------------------------
# Helper: Compute all exit factors from position + market data
# -------------------------------------------------------------------------

def compute_exit_factors(position, market_md, hours_remaining: float) -> dict:
    """
    Compute all signals needed for exit strategy selection.

    Returns:
        dict with keys: pnl_pct, in_profit, near_tp, deep_drawdown,
        age_hours, hours_remaining, hours_bucket, signal_confidence,
        peak, current, entry, spread_pct, yes_bid_qty
    """
    entry = position.avg_fill_price
    current = getattr(market_md, 'current_price', entry)
    peak = getattr(position, 'highest_price_since_entry', entry) or entry
    age_hours = getattr(position, 'age_hours', 0.0)

    pnl_pct = (current - entry) / entry if entry > 0 else 0.0
    drawdown = peak - current
    drawdown_pct = drawdown / peak if peak > 0 else 0.0

    hours_bucket = 'near_close' if hours_remaining < 2.0 else \
                   'mid' if hours_remaining < 12.0 else 'far'

    return {
        'pnl_pct': pnl_pct,
        'in_profit': pnl_pct > 0.01,
        'near_tp': pnl_pct >= 0.20,
        'deep_drawdown': drawdown_pct > DRAWDOWN_OVERRIDE_PCT,
        'age_hours': age_hours,
        'hours_remaining': hours_remaining,
        'hours_bucket': hours_bucket,
        'signal_confidence': getattr(position, 'signal_confidence', 0.5),
        'peak': peak,
        'current': current,
        'entry': entry,
        'spread_pct': getattr(market_md, 'spread_pct', None),
        'yes_bid_qty': getattr(market_md, 'yes_bid_qty', 0),
    }


# -------------------------------------------------------------------------
# Market Regime Detector
# -------------------------------------------------------------------------

class MarketRegimeDetector:
    """
    Detect market regime from volatility + trend signals.

    Uses the existing VolatilityAnalyzer to classify regime as
    low/normal/high, and computes price trend from price_history.
    """

    def __init__(self, volatility_analyzer):
        self.volatility_analyzer = volatility_analyzer

    def detect(self, market_md) -> dict:
        """
        Detect current market regime.

        Returns:
            {
                'regime':     'low' | 'normal' | 'high' | 'unknown',
                'trend':      'up' | 'down' | 'sideways',
                'momentum':   float (positive=up, negative=down),
                'confidence': float (0-1),
            }
        """
        hist = market_md.price_history[-30:] if market_md.price_history else []
        vol = getattr(market_md, 'volatility', None) or 0.0

        if len(hist) < 2:
            return {
                'regime': 'unknown',
                'trend': 'sideways',
                'momentum': 0.0,
                'confidence': 0.0,
            }

        # Use existing volatility analyzer for regime classification
        regime_result = self.volatility_analyzer.analyze_volatility_regime(
            volatility=vol, historical_volatilities=hist)

        # Detect trend from price history
        trend, momentum = self._detect_trend(market_md.price_history)

        confidence = regime_result.get('confidence', 0.5)
        regime = regime_result.get('regime', 'unknown')

        # Low confidence → treat as unknown
        if confidence < REGIME_CONFIDENCE_MIN:
            regime = 'unknown'

        return {
            'regime': regime,
            'trend': trend,
            'momentum': momentum,
            'confidence': confidence,
        }

    def _detect_trend(self, price_history: list) -> tuple:
        """
        Detect short-term trend from price history.
        Uses linear regression slope normalized by mean price.
        """
        if len(price_history) < TREND_LOOKBACK:
            return 'sideways', 0.0

        recent = price_history[-TREND_LOOKBACK:]
        x = np.arange(len(recent))
        slope = np.polyfit(x, recent, 1)[0]
        mean_price = np.mean(recent)
        momentum = slope / mean_price if mean_price > 0 else 0.0

        if momentum > 0.001:
            return 'up', momentum
        elif momentum < -0.001:
            return 'down', momentum
        return 'sideways', momentum


# -------------------------------------------------------------------------
# Exit Strategy Selector (Option A — Regime Lookup Table)
# -------------------------------------------------------------------------

class ExitStrategySelector:
    """
    Option A: Regime-based exit strategy selection.

    Classifies market into one of 6 regimes (volatility × trend),
    then picks the best exit strategy from a lookup table with
    factor-based overrides.
    """

    # Lookup table: (regime, trend) → primary strategy
    LOOKUP_TABLE = {
        ('low',      'up'):       'atr_trailing',   # Calm uptrend — ride it
        ('low',      'down'):     'stop_loss',      # Calm downtrend — cut early
        ('low',      'sideways'): 'barrier_tp',     # Calm range — wide TP target

        ('normal',   'up'):       'atr_trailing',   # Normal uptrend — ATR stop
        ('normal',   'down'):     'stop_loss',      # Normal downtrend — cut
        ('normal',   'sideways'): 'partial_exit',   # Normal range — scale out

        ('high',     'up'):       'partial_exit',   # Volatile up — scale out, keep some
        ('high',     'down'):     'stop_loss',      # Volatile down — exit fast
        ('high',     'sideways'): 'partial_exit',   # Volatile range — partials

        ('unknown',  'up'):       'barrier_tp',    # Unknown regime — conservative
        ('unknown',  'down'):     'stop_loss',
        ('unknown',  'sideways'): 'time_exit',
    }

    def __init__(self, volatility_analyzer):
        self.regime_detector = MarketRegimeDetector(volatility_analyzer)

    def select(self, position, market_md, hours_remaining: float) -> ExitResult:
        """
        Select and return the best exit strategy for current conditions.

        Flow:
        1. Compute exit factors (signals)
        2. Detect market regime
        3. Check universal overrides
        4. Lookup primary strategy
        5. Check partial exit tier if near TP
        6. Execute primary strategy
        """
        # 1. Compute all signals
        factors = compute_exit_factors(position, market_md, hours_remaining)

        # 2. Detect regime
        regime_info = self.regime_detector.detect(market_md)
        regime = regime_info['regime']
        trend = regime_info['trend']

        # 3. Check universal overrides first
        override = self._check_overrides(position, market_md, factors, regime_info)
        if override:
            return override

        # 4. Get primary strategy from lookup table
        primary = self.LOOKUP_TABLE.get((regime, trend), 'time_exit')

        # 5. Near TP with profit: check partial exit tier first
        if (factors['in_profit']
                and factors['hours_bucket'] != 'near_close'
                and factors['near_tp']):
            partial_result = check_partial_exit(position, factors['current'])
            if partial_result.should_exit:
                return partial_result

        # 6. Execute primary strategy
        atr_mult = self._get_atr_mult(regime)
        return self._execute_strategy(primary, position, factors['current'], atr_mult=atr_mult)

    def _check_overrides(self, position, market_md, factors, regime_info) -> Optional[ExitResult]:
        """
        Universal overrides — fire before lookup, regardless of regime.
        """
        # 1. Liquidity override — always first
        if not self._is_market_liquid(market_md):
            return check_liquidity_exit(position, market_md)

        # 2. Market close override — always second
        if factors['hours_remaining'] < 0.5:
            from exit_rules import _check_market_close
            return _check_market_close(position, factors['hours_remaining'])

        # 3. Deep drawdown override — ATR stop regardless of regime
        if factors['deep_drawdown'] and factors['peak'] > factors['entry']:
            return check_atr_trailing_stop(position, factors['current'])

        # 4. Near TP + high confidence — barrier TP
        if (factors['near_tp']
                and factors['signal_confidence'] > 0.7
                and factors['pnl_pct'] > 0.30):
            return check_barrier_take_profit(position, factors['current'])

        return None

    def _execute_strategy(self, strategy: str, position, current_price: float,
                         atr_mult: float = 3.0) -> ExitResult:
        """Execute the selected exit strategy."""
        if strategy == 'atr_trailing':
            return check_atr_trailing_stop(position, current_price, atr_multiplier=atr_mult)
        elif strategy == 'barrier_tp':
            return check_barrier_take_profit(position, current_price)
        elif strategy == 'stop_loss':
            return check_stop_loss(position, current_price)
        elif strategy == 'partial_exit':
            return check_partial_exit(position, current_price)
        elif strategy == 'prob_shift':
            return check_probability_shift(position, current_price)
        elif strategy == 'time_exit':
            return check_time_exit(position)
        else:
            return ExitResult(should_exit=False, exit_type='none', reason='unknown strategy')

    def _get_atr_mult(self, regime: str) -> float:
        """Get ATR multiplier based on volatility regime."""
        return {
            'low': ATR_MULT_LOW_VOL,
            'normal': ATR_MULT_NORMAL_VOL,
            'high': ATR_MULT_HIGH_VOL,
        }.get(regime, ATR_MULT_NORMAL_VOL)

    def _is_market_liquid(self, market_md) -> bool:
        """Check if market has sufficient liquidity to exit."""
        bid = getattr(market_md, 'yes_bid', None)
        bid_qty = getattr(market_md, 'yes_bid_qty', 0)
        spread = getattr(market_md, 'spread_pct', None)

        if bid is None or bid == 0:
            return False
        if spread is not None and spread > LIQUIDITY_SPREAD_MAX:
            return False
        if bid_qty is not None and bid_qty < LIQUIDITY_MIN_BID_QTY:
            return False
        return True
