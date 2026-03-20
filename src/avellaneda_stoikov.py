#!/usr/bin/env python3
"""
Avellaneda-Stoikov Market Maker for K-ATA.

Computes optimal bid/ask quotes for a market maker given:
  - Current market mid-price
  - Current inventory position
  - Market volatility (σ)
  - Time to expiry (T - t)
  - Risk aversion (γ)
  - Order book liquidity (κ)

Two modes:
  1. SYMMETRIC — quotes centered on market mid-price
  2. INVENTORY — quotes centered on reservation price (inventory-adjusted)

Key formulas (from Avellaneda & Stoikov 2008):
  Reservation price:  r = s - q * γ * σ² * (T - t)
  Reservation spread: δ = (2/γ) * log(1 + γ/κ)
  Bid price:         r - δ/2
  Ask price:         r + δ/2

Prediction market adaptation:
  - γ = risk aversion (higher → more aggressive inventory adjustment)
  - κ = order book liquidity (higher → tighter spreads)
  - T = hours until market resolution (prediction markets have defined expiry)

Ref:
  - Avellaneda & Stoikov (2008).
    "High Frequency Trading in a Limit Order Book."
    https://www.math.nyu.edu/~avellanane/HighFrequencyTrading.pdf
  - Hummingbot Avellaneda-Stoikov implementation
  - DYSIM/Avellaneda-Stoikov-Implementation (GitHub)
"""

import logging
import math
import numpy as np
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Core Avellaneda-Stoikov formulas — adapted for prediction markets
#
# The original A-S formula: spread = (2/γ) * log(1 + γ/κ)
# was tuned for stock prices ($100) with wide spreads ($1+).
# At $0.55 binary contract prices, even 2% spread = just $0.011.
#
# PREDICTION MARKET ADAPTATION:
# We use the A-S reservation price logic but replace the spread formula
# with one tuned for $0-1 prices where spreads are in cents.
#
# Spread formula:
#   base_spread = BASE_SPREAD_PCT * mid_price
#     where BASE_SPREAD_PCT = 0.02 (2% for liquid market)
#
# Inventory widening:
#   inventory_term = γ * |inventory| * mid_price * σ * sqrt(T-t) * INV_SCALE
#     (proportional to notional exposure × volatility × time)
#
# VPIN widening (applied separately via adjust_spread_for_vpin):
#   multiplier = 1 + 3 * max(0, (vpin - VPIN_EXTREME) / (1 - VPIN_EXTREME))
# ---------------------------------------------------------------------------

def compute_reservation_price(
    mid_price: float,
    inventory: float,
    gamma: float,
    sigma_price: float,
    time_to_expiry: float,
) -> float:
    """
    Compute reservation (indifference) price.

    r = s - q * s * γ * σ² * (T - t)
      = s * (1 - |q| * γ * σ² * (T - t) * sign(q))

    Args:
        mid_price:       Market mid-price ($0.01–$0.99)
        inventory:       Contract count (positive=long, negative=short)
        gamma:           Risk aversion (0.05–0.5)
        sigma_price:     Price volatility ($/sqrt-hour, e.g., 0.02 = 2¢)
        time_to_expiry:  Hours until market resolution

    Returns:
        Reservation price in dollars, clipped to [$0.01, $0.99]
    """
    notional_pct = abs(inventory) * mid_price  # notional as fraction (no bankroll needed)
    # Long inventory → lower res price (want to sell, not buy more)
    # Short inventory → higher res price (want to buy, not sell more)
    skew = gamma * notional_pct * (sigma_price ** 2) * time_to_expiry
    direction = 1 if inventory >= 0 else -1
    reservation = mid_price - direction * skew
    return float(np.clip(reservation, 0.01, 0.99))


def compute_reservation_spread(
    gamma: float,
    kappa: float,
    time_to_expiry: Optional[float] = None,
    sigma_price: Optional[float] = None,
    inventory: Optional[float] = None,
    mid_price: Optional[float] = None,
) -> float:
    """
    Compute optimal bid-ask spread for prediction markets.

    Spread = BASE_SPREAD_PCT * mid_price  [baseline, proportional to price]
             + inventory_term              [widens as inventory grows]

    Inventory term (adapted A-S):
      inventory_term = γ * |inventory| * mid_price * σ * sqrt(T-t) * INV_SCALE

    Recommended defaults for prediction markets:
      BASE_SPREAD_PCT = 0.02 (2% for liquid, 5%+ for illiquid)
      gamma = 0.05–0.2
      kappa = 1.0 (unused in simplified formula, kept for API compat)
      INV_SCALE = 0.001 (small — inventory impact is a secondary effect)

    Args:
        gamma:          Risk aversion (0.05–0.5)
        kappa:         Unused, kept for API compatibility
        time_to_expiry: Hours until resolution (for inventory term)
        sigma_price:   Price volatility in $/sqrt-hour
        inventory:      Contract count
        mid_price:     Market mid-price

    Returns:
        Bid-ask spread in dollars
    """
    BASE_SPREAD_PCT = 0.02   # 2% of price — baseline spread for liquid markets
    INV_SCALE       = 0.001  # scales inventory impact

    base_spread = BASE_SPREAD_PCT * mid_price

    if (time_to_expiry is not None and sigma_price is not None
            and inventory is not None and mid_price is not None):
        inv_term = (
            gamma
            * abs(inventory)
            * mid_price
            * sigma_price
            * math.sqrt(time_to_expiry)
            * INV_SCALE
        )
    else:
        inv_term = 0.0

    return max(base_spread + inv_term, 0.001)


def compute_quotes(
    mid_price: float,
    inventory: float,
    gamma: float,
    kappa: float,
    sigma_price: float,
    time_to_expiry: float,
    mode: str = "inventory",
) -> Dict[str, Any]:
    """
    Compute optimal bid/ask quotes using Avellaneda-Stoikov.

    Args:
        mid_price:       Current market mid-price ($0.01–$0.99)
        inventory:        Contract count (positive=long, negative=short)
        gamma:           Risk aversion (0.05–0.5; higher = more conservative)
        kappa:           Order book liquidity (0.5–3.0; higher = denser book)
        sigma_price:     Price volatility in DOLLARS per sqrt(hour)
                          e.g., 0.02 = price fluctuates ±2¢ per sqrt-hour
        time_to_expiry:  Hours until market resolution
        mode:            "inventory" (default, skews based on position)
                         "symmetric" (constant spread around mid)

    Returns:
        {
            "bid_price":  float,  # Bid quote price (offer to buy YES)
            "ask_price":  float,  # Ask quote price (offer to sell YES)
            "bid_distance": float, # Distance below reservation price
            "ask_distance": float, # Distance above reservation price
            "spread":      float,  # ask_price - bid_price
            "reservation_price": float,  # Center price
            "inventory":   float,  # Current inventory (echoed back)
            "mode":        str,    # "inventory" or "symmetric"
            "mid_price":   float,  # Market mid-price (echoed back)
            "skew":        float,  # Reservation vs mid-price skew
        }
    """
    if mode == "symmetric":
        reservation_price = mid_price
    else:
        reservation_price = compute_reservation_price(
            mid_price, inventory, gamma, sigma_price, time_to_expiry
        )

    spread = compute_reservation_spread(
        gamma, kappa,
        time_to_expiry=time_to_expiry,
        sigma_price=sigma_price,
        inventory=inventory,
        mid_price=mid_price,
    )

    half_spread = spread / 2.0
    bid_price = reservation_price - half_spread
    ask_price = reservation_price + half_spread

    skew = (reservation_price - mid_price) / (spread + 1e-10)

    return {
        "bid_price": float(bid_price),
        "ask_price": float(ask_price),
        "bid_distance": float(half_spread),
        "ask_distance": float(half_spread),
        "spread": float(spread),
        "reservation_price": float(reservation_price),
        "inventory": float(inventory),
        "mode": mode,
        "mid_price": float(mid_price),
        "skew": float(skew),
    }


# ---------------------------------------------------------------------------
# Integration with K-ATA risk signals
# ---------------------------------------------------------------------------

def adjust_spread_for_vpin(
    base_quotes: Dict[str, Any],
    vpin: float,
    vpin_extreme: float = 0.70,
) -> Dict[str, Any]:
    """
    Widen spread dynamically based on VPIN (adverse selection risk).

    When VPIN is high, informed traders are more likely to hit our quotes.
    Widening the spread compensates for adverse selection.

    Formula: spread_multiplier = 1 + vpin * (1 / (1 - vpin_threshold)) * 3
    VPIN = 0.70 → multiplier ≈ 4.0

    Args:
        base_quotes:     Output of compute_quotes()
        vpin:            Current VPIN (0–1)
        vpin_extreme:    VPIN threshold for widening (default 0.70)

    Returns:
        Modified quotes dict with widened spread
    """
    if vpin < vpin_extreme:
        return {**base_quotes, "vpin_adjustment": 1.0}

    # Widen spread by factor proportional to VPIN excess
    multiplier = 1.0 + 3.0 * ((vpin - vpin_extreme) / (1.0 - vpin_extreme))
    multiplier = max(1.0, multiplier)

    spread = base_quotes["spread"] * multiplier
    half_spread = spread / 2.0
    res_price = base_quotes["reservation_price"]

    return {
        **base_quotes,
        "bid_price": float(res_price - half_spread),
        "ask_price": float(res_price + half_spread),
        "spread": float(spread),
        "bid_distance": float(half_spread),
        "ask_distance": float(half_spread),
        "vpin_adjustment": float(multiplier),
    }


# ---------------------------------------------------------------------------
# Integration with K-ATA risk signals
# ---------------------------------------------------------------------------

def adjust_spread_for_inventory_skew(
    quotes: Dict[str, Any],
    max_inventory_pct: float = 0.50,
    max_position_pct: float = 1.0,
) -> Dict[str, Any]:
    """
    Additional spread widening when inventory is near limits.

    If inventory exceeds max_inventory_pct of total portfolio,
    bias quotes further toward the opposite side to unwind.

    Args:
        quotes:            Output of compute_quotes()
        max_inventory_pct: Max inventory as fraction of portfolio (default 50%)

    Returns:
        Modified quotes dict with additional skew
    """
    inventory = quotes["inventory"]
    half_spread = quotes.get("bid_distance", quotes["spread"] / 2)

    if inventory > max_inventory_pct / max_position_pct:
        extra_skew = (inventory - max_inventory_pct) * 0.10
        return {
            **quotes,
            "ask_price": float(quotes["ask_price"] - extra_skew),
        }

    if inventory < -max_inventory_pct / max_position_pct:
        extra_skew = (abs(inventory) - max_inventory_pct) * 0.10
        return {
            **quotes,
            "bid_price": float(quotes["bid_price"] + extra_skew),
        }

    return quotes


# ---------------------------------------------------------------------------
# Integration: KalshiMarketMaker — connects A-S quotes to K-ATA trading loop
# ---------------------------------------------------------------------------

class KalshiMarketMaker:
    """
    Computes optimal Avellaneda-Stoikov bid/ask quotes for a Kalshi market.

    Combines:
      - Real-time market data (mid-price from Kalshi API)
      - Inventory from K-ATA position_manager
      - Volatility from GARCH estimator (or fallback to historical price std)
      - VPIN spread widening from the vpin module

    Usage:
        mm = KalshiMarketMaker(
            kalshi_api=kalshi_api,
            position_manager=position_manager,
            risk_manager=risk_manager,
            garch_estimator=None,  # optional
        )
        quotes = mm.get_quotes("KXSECPRESSMENTION-25MAR20-PHONECALL")
    """

    def __init__(
        self,
        kalshi_api,
        position_manager,
        risk_manager,
        garch_estimator=None,
        gamma: float = 0.1,
        kappa: float = 1.0,
        base_spread_pct: float = 0.02,
    ):
        self.api = kalshi_api
        self.position_manager = position_manager
        self.risk_manager = risk_manager
        self.garch = garch_estimator
        self.gamma = gamma
        self.kappa = kappa
        self.base_spread_pct = base_spread_pct

    def get_quotes(
        self,
        ticker: str,
        mode: str = "inventory",
        time_to_expiry_hours: float = 24.0,
    ) -> Dict[str, Any]:
        """
        Compute and return optimal bid/ask quotes for a Kalshi market.

        Args:
            ticker:              Kalshi market ticker
            mode:               "inventory" (default) or "symmetric"
            time_to_expiry_hours: Hours until market resolution

        Returns:
            Full quotes dict with:
              - bid_price, ask_price (the actual quotes to post)
              - spread, skew, reservation_price
              - market_data (mid_price, volume, timestamp)
              - vpin_adjustment (from VPIN risk signal)
              - inventory_adjustment (from Hawkes signal)
        """
        # 1. Fetch market data
        market = self.api.get_market(ticker)
        if market is None:
            return {"error": f"Could not fetch market {ticker}"}

        # Handle nested response: API returns {"market": {...}}
        inner = market.get("market", market)  # unwrap if nested

        # Use yes_bid if available (active market), else last_price (settled market)
        mid_price = (
            float(inner.get("yes_bid_dollars", 0))  # active: best YES bid
            or float(inner.get("last_price_dollars", 0))  # settled: last trade
        )
        if mid_price <= 0:
            return {"error": f"Invalid mid_price for {ticker}: {mid_price}"}

        volume = float(inner.get("volume_fp", 0.0)) or 0.0

        # 2. Get inventory from position manager
        inventory = 0.0
        pos = self.position_manager.positions.get(ticker, {})
        if pos:
            inventory = pos.get("quantity", 0.0)

        # 3. Get volatility (from GARCH if available, else fallback to simple std)
        sigma_price = 0.02  # default: 2¢ per sqrt-hour
        if self.garch:
            try:
                garch_result = self.garch.get_latest_volatility(ticker)
                if garch_result and "volatility" in garch_result:
                    sigma_price = garch_result["volatility"]
            except Exception:
                pass  # use default

        # 4. Compute base A-S quotes
        quotes = compute_quotes(
            mid_price=mid_price,
            inventory=inventory,
            gamma=self.gamma,
            kappa=self.kappa,
            sigma_price=sigma_price,
            time_to_expiry=time_to_expiry_hours,
            mode=mode,
        )

        # 5. Apply VPIN spread widening
        vpin_est = getattr(self, "_vpin_estimator", None)
        if vpin_est:
            try:
                vpin_result = self.risk_manager.check_vpin(ticker, vpin_est)
                if vpin_result.get("vpin", 0) > 0:
                    quotes = adjust_spread_for_vpin(quotes, vpin_result["vpin"])
            except Exception:
                pass  # VPIN failure shouldn't block quoting

        return {
            **quotes,
            "ticker": ticker,
            "inventory": inventory,
            "volume": float(volume),
            "market_mid": float(mid_price),
            "sigma_price": sigma_price,
            "status": inner.get("status", "unknown"),
        }
