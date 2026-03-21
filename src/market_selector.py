"""
market_selector.py — Market quality filtering and selection for K-ATA.

Decides which markets are worth trading based on:
- Liquidity (Phase 1 is_market_liquid)
- Probability sweet spot (25%-75%)
- Time remaining (close_date)
- Signal-market alignment
"""
from datetime import datetime, timezone
from typing import Union

# Type alias for market data (MarketData dataclass or raw dict from Kalshi API)
MarketInput = Union[dict, object]


def probability_sweet_spot(price: float, min_pct: float = 0.25, max_pct: float = 0.75) -> tuple[bool, str]:
    """
    Only trade markets where implied probability is 25%-75%.
    Outside this range: either fighting consensus (low) or no edge left (high).
    """
    try:
        price_f = float(price)
    except (ValueError, TypeError):
        return False, f"Cannot parse price: {price}"

    if price_f < min_pct:
        return False, f"Probability {price_f:.0%} too low — fighting consensus"
    if price_f > max_pct:
        return False, f"Probability {price_f:.0%} too high — no edge left"
    return True, f"In sweet spot ({price_f:.0%})"


def time_remaining_ok(close_date: str, min_hours: float = 2.0) -> tuple[bool, str, float]:
    """
    Skip markets that are about to close or have already closed.
    Returns (ok, reason, hours_remaining).
    """
    if not close_date:
        return True, "No close_date — skipping time check", 999.0
    try:
        close_dt = datetime.fromisoformat(close_date.replace('Z', '+00:00'))
        hours_left = (close_dt - datetime.now(timezone.utc)).total_seconds() / 3600
    except (ValueError, TypeError):
        return False, f"Cannot parse close_date: {close_date}", 0.0

    if hours_left <= 0:
        return False, "Market already closed", hours_left
    if hours_left < min_hours:
        return False, f"Only {hours_left:.1f}h remaining — too close to enter", hours_left
    return True, f"{hours_left:.1f}h remaining", hours_left


def signal_market_alignment(signal_score: float, market_price: float,
                             min_edge: float = 0.05) -> tuple[bool, str, float]:
    """
    Check if our signal has genuine edge vs. what the market already prices.
    We buy when signal expects probability to rise. If market already at 85%, no room left.

    Returns (has_edge, reason, expected_edge_pct)
    """
    if signal_score == 0.0:
        return True, "No signal — no alignment check needed", 0.0
    # Expected prob after signal materializes: price + signal*2 (signal is fractional shift)
    expected_prob = min(0.95, market_price + signal_score * 2)
    edge = expected_prob - market_price
    if edge < min_edge:
        return False, (
            f"No edge: market at {market_price:.0%}, signal expects {expected_prob:.0%} "
            f"(+{edge:.0%} vs +{min_edge:.0%} min)"
        ), edge
    return True, f"Edge +{edge:.0%} (market {market_price:.0%} → {expected_prob:.0%})", edge


def get_market_quality_score(market: MarketInput) -> dict:
    """
    Score a market 0-100 on tradeability.
    Works with MarketData objects or raw market dicts.

    Returns dict with keys: score, bid, spread_pct, volume, hours_left, reasons (list)
    """
    score = 0.0
    details = {'bid': None, 'spread_pct': None, 'volume': 0, 'hours_left': None, 'reasons': []}

    # Extract price data from either market dict or MarketData object
    if hasattr(market, 'current_price'):
        bid_f = getattr(market, 'current_price', 0.0) or 0.0
        ask_f = None
        close_date = getattr(market, 'close_date', None)
        vol_raw = getattr(market, 'volume', 0) or 0
    else:
        bid_raw = market.get('yes_bid_dollars') or market.get('yes_bid', 0)
        ask_raw = market.get('yes_ask_dollars') or market.get('yes_ask', 0)
        close_date = market.get('close_date') or market.get('market_close')
        vol_raw = market.get('volume') or 0
        try:
            bid_f = float(bid_raw) if bid_raw else 0.0
            ask_f = float(ask_raw) if ask_raw else None
        except (ValueError, TypeError):
            bid_f = 0.0
            ask_f = None

    # Factor 1: Bid presence (0-30 pts)
    if bid_f > 0:
        score += min(30.0, bid_f * 300)  # 1¢ bid = 30pts, 10¢ bid = capped
        details['bid'] = bid_f
    else:
        details['reasons'].append('no_bid')

    # Factor 2: Spread tightness (0-30 pts)
    if bid_f > 0 and ask_f and ask_f > bid_f:
        spread_pct = (ask_f - bid_f) / ask_f
        spread_score = max(0, 30 * (1 - spread_pct / 0.20))  # 0 spread = 30pts, 20% spread = 0pts
        score += spread_score
        details['spread_pct'] = round(spread_pct, 4)
    elif bid_f > 0:
        details['spread_pct'] = 0.0  # bid-only market, spread score = max

    # Factor 3: Volume (0-20 pts) — volume is typically raw count
    try:
        vol_f = float(vol_raw) if vol_raw else 0.0
    except (ValueError, TypeError):
        vol_f = 0.0
    score += min(20.0, vol_f / 50)  # 1000 volume = 20pts, 5000 = capped
    details['volume'] = vol_f

    # Factor 4: Time remaining (0-20 pt penalty)
    if close_date:
        try:
            close_dt = datetime.fromisoformat(close_date.replace('Z', '+00:00'))
            hours_left = (close_dt - datetime.now(timezone.utc)).total_seconds() / 3600
            details['hours_left'] = hours_left
            if hours_left <= 0:
                score = 0.0
                details['reasons'].append('market_closed')
            elif hours_left < 2:
                score *= 0.1  # 90% penalty for <2h
                details['reasons'].append('under_2h')
            elif hours_left < 24:
                score *= 0.7  # 30% penalty for <24h
        except (ValueError, TypeError):
            pass

    details['score'] = round(min(100.0, max(0.0, score)), 1)
    return details


def is_tradeable(market: MarketInput,
                 market_data_streamer=None,
                 signal_score: float = 0.0,
                 min_quality: float = 30.0,
                 min_spread_pct: float = 0.15) -> tuple[bool, str]:
    """
    Master market filter: combines all checks into one go/no-go decision.
    Works with MarketData objects or raw market dicts.

    Args:
        market: MarketData object or raw market dict
        market_data_streamer: MarketDataStreamer instance (for liquidity check)
        signal_score: signal strength (0 = no check)
        min_quality: minimum quality score (0-100)
        min_spread_pct: max spread % to accept

    Returns (should_trade, reason)
    """
    # Extract price — works for both MarketData objects and raw dicts
    if hasattr(market, 'current_price'):
        price = getattr(market, 'current_price', 0.0) or 0.0
        close_date = getattr(market, 'close_date', None)
    else:
        # Try last_price first, then yes_bid_dollars (bid is always available in liquid markets)
        price_raw = (market.get('last_price_dollars') or market.get('last_price')
                     or market.get('yes_bid_dollars') or market.get('yes_bid') or 0)
        try:
            price = float(price_raw) if price_raw else 0.0
        except (ValueError, TypeError):
            price = 0.0
        close_date = market.get('close_date') or market.get('market_close')

    # 1. Quality score check
    quality = get_market_quality_score(market)
    if quality['score'] < min_quality:
        return False, f"Quality {quality['score']:.0f} < {min_quality:.0f} (bid={quality['bid']}, vol={quality['volume']:.0f})"

    # 2. Liquidity check (requires market_data_streamer)
    if market_data_streamer:
        is_liq, liq_details = market_data_streamer.is_market_liquid(market, min_spread_pct)
        if not is_liq:
            return False, f"Not liquid: {liq_details.get('reason', 'unknown')}"

    # 3. Probability sweet spot
    if price <= 0:
        return False, f"No usable price: {price}"
    in_spot, spot_reason = probability_sweet_spot(price)
    if not in_spot:
        return False, f"Prob: {spot_reason}"

    # 4. Time remaining
    if close_date:
        time_ok, time_reason, hours_left = time_remaining_ok(close_date)
        if not time_ok:
            return False, f"Time: {time_reason}"

    # 5. Signal-market alignment
    if signal_score != 0.0:
        aligned, align_reason, edge = signal_market_alignment(signal_score, price)
        if not aligned:
            return False, f"Align: {align_reason}"

    return True, "All checks passed"
