"""
exit_rules.py — Exit trigger logic for K-ATA positions.
Each rule evaluates one exit condition.
evaluate_all() runs all rules and returns the first triggered exit.
"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class ExitResult:
    """Result of an exit rule evaluation."""
    should_exit: bool
    exit_type: str       # 'take_profit' | 'stop_loss' | 'time_exit' | 'prob_shift' | 'none'
    reason: str
    urgency: str = 'normal'  # 'high' | 'normal' | 'low'


def check_take_profit(position, current_price: float, threshold: float = None) -> ExitResult:
    """
    Exit if price has risen enough to lock in profit.
    threshold: multiplier on entry price. None = use position.take_profit_pct (+50% default).
    threshold=1.50 means exit if current >= entry * 1.50 (+50% profit).
    """
    if threshold is None:
        # Per-position setting: take_profit_pct stored as 0.50 (= +50% above entry)
        # Convert to threshold: entry * (1 + take_profit_pct) = entry * 1.50
        take_profit_pct = getattr(position, 'take_profit_pct', 0.50)
        threshold = 1.0 + take_profit_pct
    if current_price >= position.avg_fill_price * threshold:
        pnl_pct = (current_price - position.avg_fill_price) / position.avg_fill_price * 100
        return ExitResult(
            should_exit=True,
            exit_type='take_profit',
            reason=f"Take profit: ${current_price:.4f} >= ${position.avg_fill_price * threshold:.4f} (+{pnl_pct:.0f}%)",
            urgency='high'
        )
    return ExitResult(should_exit=False, exit_type='none', reason='')


def check_time_exit(position, hours_limit: float = 24.0) -> ExitResult:
    """
    Exit if position has been open longer than hours_limit.
    hours_limit=24: cut after 24 hours regardless.
    """
    age_hours = getattr(position, 'age_hours', 0.0)
    if age_hours >= hours_limit:
        unrealized = getattr(position, 'unrealized_pnl', 0.0)
        in_profit = unrealized > 0
        return ExitResult(
            should_exit=True,
            exit_type='time_exit',
            reason=f"Time exit: position {age_hours:.1f}h old, {'in profit' if in_profit else 'not in profit'}",
            urgency='normal' if in_profit else 'high'
        )
    return ExitResult(should_exit=False, exit_type='none', reason='')


def check_probability_shift(position, current_price: float, shift_threshold: float = 0.75) -> ExitResult:
    """
    Exit if the market's implied probability shifted against our thesis.
    We bought on a bullish signal. If price drops below entry * shift_threshold,
    the signal has been rejected by the market.
    shift_threshold=0.75: exit if price drops to 75% of entry (market moved -25%).
    """
    signal = getattr(position, 'signal_at_entry', 0.0)
    if signal <= 0:
        return ExitResult(should_exit=False, exit_type='none', reason='')  # no signal to validate
    entry = position.avg_fill_price
    if current_price <= entry * shift_threshold:
        return ExitResult(
            should_exit=True,
            exit_type='prob_shift',
            reason=f"Probability shift: market moved from ${entry:.4f} to ${current_price:.4f} — signal rejected",
            urgency='high'
        )
    return ExitResult(should_exit=False, exit_type='none', reason='')


def check_stop_loss(position, current_price: float) -> ExitResult:
    """
    Exit if price dropped below stop_loss_price.
    Uses position.stop_loss_price (computed from avg_fill_price * (1 - stop_loss_pct)).
    """
    sl_price = getattr(position, 'stop_loss_price', position.avg_fill_price * 0.60)
    if current_price <= sl_price:
        loss_pct = (position.avg_fill_price - current_price) / position.avg_fill_price * 100
        return ExitResult(
            should_exit=True,
            exit_type='stop_loss',
            reason=f"Stop loss: ${current_price:.4f} <= ${sl_price:.4f} (-{loss_pct:.0f}%)",
            urgency='high'
        )
    return ExitResult(should_exit=False, exit_type='none', reason='')


def evaluate_all(position, current_price: float, hours_remaining: float = 999.0) -> ExitResult:
    """
    Run all exit rules. Returns first triggered exit in priority order:
    stop_loss → market_close → take_profit → probability_shift → time_exit
    """
    checks = [
        lambda p, c: check_stop_loss(p, c),
        lambda p, c: _check_market_close(p, hours_remaining),
        lambda p, c: check_take_profit(p, c),
        lambda p, c: check_probability_shift(p, c),
        lambda p, c: check_time_exit(p),
    ]
    for check in checks:
        result = check(position, current_price)
        if result.should_exit:
            return result
    return ExitResult(should_exit=False, exit_type='none', reason='')


def _check_market_close(position, hours_remaining: float, limit: float = 0.5) -> ExitResult:
    """Exit if market is about to close (<30min remaining)."""
    if 0 < hours_remaining < limit:
        return ExitResult(
            should_exit=True, exit_type='time_exit',
            reason=f"Market closing in {hours_remaining:.1f}h — exiting before settlement",
            urgency='high'
        )
    if hours_remaining <= 0:
        return ExitResult(
            should_exit=True, exit_type='time_exit',
            reason="Market has closed — should auto-settle", urgency='high'
        )
    return ExitResult(should_exit=False, exit_type='none', reason='')
