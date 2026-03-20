#!/usr/bin/env python3
"""Simplified risk management module for Phase 2 - Kalshi trading bot."""

import logging
import numpy as np
from typing import Dict, Any
from config import BANKROLL, MAX_POSITION_SIZE_PERCENTAGE, STOP_LOSS_PERCENTAGE

logger = logging.getLogger(__name__)

class RiskManager:
    """Simplified risk management for Phase 2 - essential features only."""

    def __init__(self, initial_bankroll: float = BANKROLL, db_path: str = None):
        self.initial_bankroll = initial_bankroll
        self.current_bankroll = initial_bankroll
        self._closed_trade_count = 0  # Cache for trade count
        self._db_path = db_path or 'data/kalshi.db'
        self._load_win_stats_cache()  # Pre-load on init

    def _load_win_stats_cache(self) -> None:
        """Load win stats from database on init for performance."""
        try:
            import sqlite3
            import os
            db_path = self._db_path
            if not os.path.isabs(db_path):
                script_dir = os.path.dirname(os.path.abspath(__file__))
                project_root = os.path.dirname(script_dir)
                db_path = os.path.join(project_root, db_path)
            
            if not os.path.exists(db_path):
                return
            
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            # Get closed trades with PnL
            cursor.execute('''
                SELECT pnl FROM trades 
                WHERE closed_at IS NOT NULL AND pnl IS NOT NULL
            ''')
            pnls = [row[0] for row in cursor.fetchall()]
            self._closed_trade_count = len(pnls)
            conn.close()
        except Exception as e:
            self._closed_trade_count = 0

    def _get_win_stats(self):
        """
        Get actual win rate and win/loss ratio from closed trades.
        
        Returns:
            Tuple: (win_rate, avg_win, avg_loss)
            win_rate = fraction of profitable trades (0-1)
            avg_win = average $ gain on winning trades
            avg_loss = average $ loss on losing trades
        """
        try:
            import sqlite3
            import os
            db_path = self._db_path
            if not os.path.isabs(db_path):
                script_dir = os.path.dirname(os.path.abspath(__file__))
                project_root = os.path.dirname(script_dir)
                db_path = os.path.join(project_root, db_path)
            
            if not os.path.exists(db_path):
                return None, None, None
            
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            # Get all closed trades with PnL
            cursor.execute('''
                SELECT pnl FROM trades 
                WHERE closed_at IS NOT NULL AND pnl IS NOT NULL
            ''')
            pnls = [row[0] for row in cursor.fetchall()]
            self._closed_trade_count = len(pnls)
            
            if self._closed_trade_count < 5:
                conn.close()
                return None, None, None
            
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p <= 0]
            
            win_rate = len(wins) / len(pnls) if pnls else None
            avg_win = sum(wins) / len(wins) if wins else None
            avg_loss = sum(losses) / len(losses) if losses else None
            
            conn.close()
            return win_rate, avg_win, avg_loss
            
        except Exception as e:
            self._closed_trade_count = 0
            return None, None, None

    def calculate_position_size_kelly(self, confidence: float = 0.5, win_loss_ratio: float = 2.0,
                                     volatility: float = None) -> float:
        """
        Volatility-Adjusted Fractional Kelly position sizing.
        
        Uses historical win rate (p) and win/loss ratio (b) from closed trades.
        Falls back to conservative defaults when insufficient history (< 20 trades).
        Applies volatility adjustment to reduce position size in high-vol environments.
        
        Formula: f* = (b*p - q) / b  [Full Kelly]
        Then: scaled = full_kelly * 0.25 * vol_scalar * confidence_modifier
        
        Where vol_scalar = 1 / (1 + vol_ratio) with vol_ratio = actual_vol / baseline_vol
        
        Reference: https://en.wikipedia.org/wiki/Kelly_criterion
                   https://www.tastylive.com/news-insights/kelly-criterion-explained-smarter-position-sizing-traders
        
        Args:
            confidence: Signal strength modifier (0-1)
            win_loss_ratio: Expected win/loss ratio (fallback only)
            volatility: Annualized volatility (e.g., 0.15 for 15%). If None, no vol adjustment.

        Returns:
            Position size as fraction of bankroll
        """
        # Baseline volatility reference (15% annualized = typical stock market vol)
        # If market vol is at baseline, vol_ratio=1.0 and vol_scalar=0.5
        BASELINE_VOL = 0.15
        
        # Get actual win stats from closed trades
        win_rate, avg_win, avg_loss = self._get_win_stats()
        
        if win_rate is not None and self._closed_trade_count >= 20:
            p = win_rate
            b = (avg_win / abs(avg_loss)) if avg_loss != 0 else 2.0
            logger.info(f"Kelly: p={p:.3f}, b={b:.2f}, vol={volatility}, trades={self._closed_trade_count}")
        else:
            p = 0.50
            b = win_loss_ratio
            logger.info(f"Kelly: p={p:.3f}, b={b:.2f}, vol={volatility}, trades={self._closed_trade_count} (defaults)")
        
        b = max(b, 0.1)
        
        # Full Kelly: f* = (b*p - q) / b = p - (1-p)/b
        q = 1 - p
        kelly_full = (b * p - q) / b
        kelly_full = max(-0.5, min(1.0, kelly_full))
        
        # Step 1: Reduce to quarter-Kelly (25%) for stability in noisy prediction markets
        # Industry standard: https://en.wikipedia.org/wiki/Kelly_criterion
        kelly_fraction = max(0, kelly_full * 0.25)
        
        # Step 2: Apply volatility adjustment — reduces size in high-vol environments
        # Formula: vol_scalar = 1 / (1 + vol_ratio) where vol_ratio = actual/baseline
        # If vol = 15% (baseline): vol_scalar = 1/(1+1) = 0.5
        # If vol = 30% (2x baseline): vol_scalar = 1/(1+2) = 0.33
        # If vol = 7.5% (half baseline): vol_scalar = 1/(1+0.5) = 0.67
        if volatility is not None and volatility > 0:
            vol_ratio = volatility / BASELINE_VOL
            vol_scalar = 1.0 / (1.0 + vol_ratio)
            vol_scalar = max(0.1, min(vol_scalar, 1.0))  # Clamp 10%–100%
            kelly_fraction *= vol_scalar
            logger.info(f"Kelly: vol_scalar={vol_scalar:.3f} (vol={volatility:.3f}, baseline={BASELINE_VOL})")
        else:
            logger.info(f"Kelly: no vol data, skipping vol adjustment")
        
        # Step 3: Apply signal confidence as modifier (0.5x to 1.0x)
        kelly_fraction *= (0.5 + confidence * 0.5)
        
        # Step 4: Cap at configured max
        position_size = min(kelly_fraction, MAX_POSITION_SIZE_PERCENTAGE)
        
        if kelly_full > 0:
            return max(position_size, 0.01)
        else:
            logger.info(f"Kelly: negative edge (p={p:.3f}, b={b:.2f}), skipping")
            return 0.0

    def calculate_stop_loss_price(self, entry_price: float, is_long: bool = True) -> float:
        """
        Calculate simple stop-loss price based on percentage.

        Args:
            entry_price: Entry price
            is_long: True for long positions, False for short

        Returns:
            Stop-loss price
        """
        if is_long:
            return entry_price * (1 - STOP_LOSS_PERCENTAGE)
        else:
            return entry_price * (1 + STOP_LOSS_PERCENTAGE)

    def check_stop_loss_trigger(self, entry_price: float, current_price: float, is_long: bool = True) -> bool:
        """
        Check if stop-loss should be triggered.

        Args:
            entry_price: Entry price
            current_price: Current price
            is_long: True for long positions

        Returns:
            True if stop-loss triggered
        """
        stop_price = self.calculate_stop_loss_price(entry_price, is_long)

        if is_long:
            return current_price <= stop_price
        else:
            return current_price >= stop_price

    def calculate_portfolio_metrics(self, returns: list = None) -> Dict[str, float]:
        """
        Calculate basic portfolio risk metrics.

        Args:
            returns: List of daily returns (optional)

        Returns:
            Basic risk metrics
        """
        if not returns:
            returns = [0.01, -0.005, 0.008, -0.003, 0.012]  # Sample returns

        returns_array = np.array(returns)

        # Sharpe Ratio (simplified - assuming 2% risk-free rate)
        excess_returns = returns_array - 0.02/252  # Daily risk-free rate
        sharpe_ratio = np.mean(excess_returns) / np.std(excess_returns) * np.sqrt(252) if np.std(excess_returns) > 0 else 0

        # Maximum Drawdown
        cumulative = np.cumprod(1 + returns_array)
        running_max = np.maximum.accumulate(cumulative)
        drawdown = (cumulative - running_max) / running_max
        max_drawdown = np.min(drawdown)

        # Win Rate
        winning_trades = sum(1 for r in returns_array if r > 0)
        win_rate = winning_trades / len(returns_array) if returns_array.size > 0 else 0

        return {
            'sharpe_ratio': float(sharpe_ratio),
            'max_drawdown': float(max_drawdown),
            'win_rate': float(win_rate),
            'total_return': float(np.prod(1 + returns_array) - 1),
            'volatility': float(np.std(returns_array) * np.sqrt(252))
        }

    def compute_annualized_volatility(self, price_history: list) -> float:
        """
        Compute annualized volatility from a price history series.
        
        Uses log returns -> std -> annualize by sqrt(252).
        
        Args:
            price_history: List of price values
            
        Returns:
            Annualized volatility (e.g., 0.15 for 15%) or None if insufficient data
        """
        if not price_history or len(price_history) < 10:
            return None
        try:
            prices = np.array(price_history, dtype=float)
            # Log returns
            returns = np.diff(np.log(prices))
            # Annualized std dev
            vol = np.std(returns) * np.sqrt(252)
            return float(vol)
        except Exception as e:
            logger.warning(f"Could not compute volatility: {e}")
            return None

    def validate_position_size(self, position_value: float) -> bool:
        """
        Validate position size against risk limits.

        Args:
            position_value: Dollar value of position

        Returns:
            True if position size is acceptable
        """
        max_position_value = self.current_bankroll * MAX_POSITION_SIZE_PERCENTAGE
        return position_value <= max_position_value

    def get_portfolio_status(self) -> Dict[str, Any]:
        """
        Get basic portfolio status.

        Returns:
            Portfolio status summary
        """
        metrics = self.calculate_portfolio_metrics()

        return {
            'current_bankroll': self.current_bankroll,
            'initial_bankroll': self.initial_bankroll,
            'total_pnl': self.current_bankroll - self.initial_bankroll,
            'total_return_pct': ((self.current_bankroll / self.initial_bankroll) - 1) * 100,
            'risk_metrics': metrics
        }
