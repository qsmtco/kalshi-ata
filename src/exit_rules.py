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
    exit_type: str       # 'take_profit' | 'stop_loss' | 'time_exit' | 'prob_shift' | 'liquidity_exit' | 'atr_trailing' | 'none'
    reason: str
    urgency: str = 'normal'  # 'high' | 'normal' | 'low'
    exit_qty: Optional[int] = None  # for partial exits — None means exit full position


def check_atr_trailing_stop(position, current_price: float,
                             atr_multiplier: float = None) -> ExitResult:
    """
    Step 4.2: Chandelier-style ATR trailing stop.
    Exit if current_price drops to (highest_price_since_entry - N × ATR).

    Unlike a fixed take-profit, this trails the price upward:
    - On entry: trailing_stop = entry_price - (N × ATR)
    - As price rises: trailing_stop rises with it
    - If price falls to the trailing_stop: EXIT (trend broken)

    This is a TAKE PROFIT / TRAILING STOP — it locks in gains when the trend breaks,
    rather than locking in at a fixed price target.
    """
    if atr_multiplier is None:
        atr_multiplier = getattr(position, 'atr_multiplier', 3.0)

    highest = getattr(position, 'highest_price_since_entry', None)
    if highest is None:
        highest = position.avg_fill_price

    atr = getattr(position, 'volatility', None)
    if atr is None or atr <= 0:
        # No ATR data — use 2% of price as conservative fallback
        atr = position.avg_fill_price * 0.02

    trailing_stop = highest - (atr_multiplier * atr)

    if current_price <= trailing_stop:
        drawdown_pct = (highest - current_price) / highest * 100
        return ExitResult(
            should_exit=True,
            exit_type='atr_trailing_stop',
            reason=f"ATR trailing stop: ${current_price:.4f} <= ${trailing_stop:.4f} "
                   f"(high=${highest:.4f}, ATR×{atr_multiplier:.1f}=${atr:.4f}, "
                   f"drawdown={drawdown_pct:.1f}%)",
            urgency='high')

    return ExitResult(should_exit=False, exit_type='none', reason='')


def check_liquidity_exit(position, market_md, min_bid_dollars: float = 0.05,
                          min_bid_qty: int = 10) -> ExitResult:
    """
    Step 2.2: Exit if market liquidity has dried up beneath a held position.
    This is the highest-priority exit — if bids are gone, we must exit immediately
    regardless of price targets, because the position is otherwise unexitable.
    """
    bid = getattr(market_md, 'yes_bid', None)
    bid_qty = getattr(market_md, 'yes_bid_qty', None)

    if bid is None or bid < min_bid_dollars:
        return ExitResult(
            should_exit=True,
            exit_type='liquidity_exit',
            reason=f"Liquidity exit: bid ${bid} below ${min_bid_dollars} minimum — no exit path",
            urgency='high')

    if bid_qty is not None and bid_qty < min_bid_qty:
        return ExitResult(
            should_exit=True,
            exit_type='liquidity_exit',
            reason=f"Liquidity exit: bid qty {bid_qty} below {min_bid_qty} minimum — insufficient depth",
            urgency='high')

    return ExitResult(should_exit=False, exit_type='none', reason='')


def check_barrier_take_profit(position, current_price: float) -> ExitResult:
    """
    Step 5.3: Triple-barrier take profit check.
    Uses barrier_tp_multiplier set at entry based on signal confidence.
    High confidence signal → wider TP (barrier_tp_multiplier closer to 2.00)
    Low confidence signal → tighter TP (barrier_tp_multiplier closer to 1.50)

    Prefers barrier_tp_multiplier over volatility_adjusted_tp_mult if both exist.
    Falls back to volatility_adjusted_tp_mult or static take_profit_pct.
    """
    barrier_mult = getattr(position, 'barrier_tp_multiplier', None)
    vol_adj_mult = getattr(position, 'volatility_adjusted_tp_mult', None)

    # Prefer barrier-based (from signal confidence), fall back to volatility-adjusted
    if barrier_mult is not None and barrier_mult > 1.0:
        threshold = barrier_mult
    elif vol_adj_mult is not None and vol_adj_mult > 1.0:
        threshold = vol_adj_mult
    else:
        take_profit_pct = getattr(position, 'take_profit_pct', 0.50)
        threshold = 1.0 + take_profit_pct

    if current_price >= position.avg_fill_price * threshold:
        pnl_pct = (current_price - position.avg_fill_price) / position.avg_fill_price * 100
        return ExitResult(
            should_exit=True,
            exit_type='take_profit',
            reason=f"Take profit (barrier): ${current_price:.4f} >= "
                    f"${position.avg_fill_price * threshold:.4f} "
                    f"(+{pnl_pct:.0f}%, barrier_mult={threshold:.2f}, "
                    f"signal_conf={getattr(position, 'signal_confidence', 'N/A')})",
            urgency='high')
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


def check_partial_exit(position, current_price: float) -> ExitResult:
    """
    Step 3.2: Check if the next partial exit tier threshold has been crossed.
    Returns should_exit=True with exit_qty set if a tier is breached.
    Marks the tier as exited and records the price.
    """
    exit_tiers = getattr(position, 'exit_tiers', None)
    if not exit_tiers:
        return ExitResult(should_exit=False, exit_type='none', reason='')

    entry = position.avg_fill_price
    for tier in exit_tiers:
        if tier.get('exited', False):
            continue  # already exited this tier
        threshold = entry * tier['threshold_mult']
        if current_price >= threshold:
            qty_pct = tier['qty_pct']
            qty_to_exit = max(int(position.initial_count * qty_pct), 1)
            # Cap at remaining_count
            remaining = getattr(position, 'remaining_count', None)
            if remaining is not None:
                qty_to_exit = min(qty_to_exit, remaining)
            tier['exited'] = True
            tier['exit_price'] = current_price
            return ExitResult(
                should_exit=True,
                exit_type='partial_exit',
                reason=f"Partial exit: {qty_to_exit} contracts ({int(qty_pct*100)}%) "
                       f"at ${current_price:.4f} (threshold ×{tier['threshold_mult']:.2f})",
                urgency='normal',
                exit_qty=qty_to_exit)
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


def evaluate_all(position, current_price: float, hours_remaining: float = 999.0,
                market_md=None) -> ExitResult:
    """
    Run all exit rules. Returns first triggered exit in priority order:
    1. stop_loss — HIGHEST priority, returns FULL exit immediately
    2. liquidity_exit — if bids gone, must exit regardless of price
    3. market_close
    4. take_profit
    5. atr_trailing_stop (Phase 4 — no-op if not yet wired)
    6. partial_exit tiers (only if stop_loss not triggered)
    7. probability_shift
    8. time_exit
    """
    checks = [
        # 1. Stop loss — HIGHEST priority (H7: moved to first)
        lambda p, c, md: check_stop_loss(p, c),
        # 2. Liquidity exit (Step 2.3)
        lambda p, c, md: check_liquidity_exit(p, md) if md else ExitResult(False, 'none', ''),
        # 3. Market close (time barrier)
        lambda p, c, md: _check_market_close(p, hours_remaining),
        # 4. Take profit (barrier-based — Phase 5)
        lambda p, c, md: check_barrier_take_profit(p, c),
        # 5. ATR trailing stop (Phase 4)
        lambda p, c, md: check_atr_trailing_stop(p, c),
        # 6. Partial exit tiers (Phase 3 — only if stop_loss not triggered)
        lambda p, c, md: check_partial_exit(p, c),
        # 7. Probability shift
        lambda p, c, md: check_probability_shift(p, c),
        # 8. Time exit
        lambda p, c, md: check_time_exit(p),
    ]
    for check in checks:
        result = check(position, current_price, market_md)
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
