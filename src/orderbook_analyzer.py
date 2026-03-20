#!/usr/bin/env python3
"""
Order Book Analyzer — L2 Depth Tracking + Spread Decomposition + VPIN/λ Pipeline.

Tracks the full L2 (price-level) order book for a Kalshi market and computes:
  1. Best bid/ask / spread
  2. Depth distribution (how much volume at each level)
  3. Order flow imbalance (OFI) — rolling net volume
  4. VPIN from order book (using buy/sell volume imbalance across buckets)
  5. Implied Kyle λ from order book (price impact per unit of net flow)

Kalshi market structure:
  - YES bids and NO bids are shown (no asks — they mirror the bids)
  - A YES bid at $p  =  equivalent to a NO ask at $(1-p)
  - Mid-price = (best_yes_bid + (1 - best_no_bid)) / 2

Order flow classification (bulk volume classification):
  - Volume hitting YES bids    → classified as BUY  (buy YES = long the outcome)
  - Volume hitting NO bids     → classified as SELL (buy NO = short the outcome)
  - VPIN from order book = mean(|V_buy - V_sell| / V_total) per bucket

Ref:
  - Easley, López de Prado & O'Hara (2012). VPIN.
  - Kyle (1985). Lambda.
  - Hearn (2012). "Mean spread decomposition."
"""

import logging
import time
import numpy as np
from collections import deque
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Core L2 order book analysis
# ---------------------------------------------------------------------------

def compute_spread_metrics(
    yes_bids: List[Dict],
    no_bids: List[Dict],
) -> Dict[str, float]:
    """
    Compute spread metrics from Kalshi order book (bids only).

    In Kalshi binary markets:
      - YES bid at $p  =  equivalent to NO ask at $(1-p)
      - NO  bid at $q  =  equivalent to YES ask at $(1-q)

    Best prices:
      - Best YES bid  = highest YES bid price
      - Best NO  bid  = highest NO  bid price

    Mid-price: average of the two sides' fair values
      = (best_yes_bid + (1 - best_no_bid)) / 2

    Spread: (1 - best_yes_bid) + best_no_bid
      = distance from best YES bid down to $0
      + distance from best NO bid up from $0
      = the total distance a trade must traverse

    Args:
        yes_bids: [{"price_dollars": float, "quantity": float}, ...]
        no_bids:  [{"price_dollars": float, "quantity": float}, ...]

    Returns:
        {
            "best_yes_bid": float,   # best YES bid price ($0-1)
            "best_no_bid":  float,   # best NO bid price ($0-1)
            "best_yes_ask": float,   # implied YES ask = 1 - best_no_bid
            "best_no_ask":  float,   # implied NO ask = 1 - best_yes_bid
            "mid_price":    float,   # fair value estimate
            "spread":       float,   # total spread in dollars
            "spread_pct":   float,  # spread as % of mid-price
            "yes_depth":    float,   # total YES volume at best 5 levels
            "no_depth":     float,   # total NO  volume at best 5 levels
            "depth_imbalance": float, # (yes_depth - no_depth) / (yes_depth + no_depth)
        }
    """
    if not yes_bids or not no_bids:
        return {
            "best_yes_bid": 0.0, "best_no_bid": 0.0,
            "best_yes_ask": 0.0, "best_no_ask": 0.0,
            "mid_price": 0.0, "spread": 0.0, "spread_pct": 0.0,
            "yes_depth": 0.0, "no_depth": 0.0, "depth_imbalance": 0.0,
        }

    best_yes = float(yes_bids[0]["price_dollars"])
    best_no  = float(no_bids[0]["price_dollars"])

    # Implied asks (mirror of bids)
    best_yes_ask = 1.0 - best_no   # best YES ask (someone selling YES)
    best_no_ask  = 1.0 - best_yes  # best NO  ask (someone selling NO)

    # Mid-price: average of YES fair value and NO fair value
    # YES fair value = best_yes_bid, NO fair value = 1 - best_no_bid
    mid_price = (best_yes + (1.0 - best_no)) / 2.0

    # Total spread = cost to buy YES then sell it back via NO, or vice versa
    # = (1 - best_yes) + best_no
    spread = (1.0 - best_yes) + best_no
    spread_pct = (spread / mid_price * 100) if mid_price > 0 else 0.0

    # Depth at top N levels
    yes_depth = sum(float(b.get("quantity", 0)) for b in yes_bids[:5])
    no_depth  = sum(float(b.get("quantity", 0)) for b in no_bids[:5])

    total_depth = yes_depth + no_depth
    depth_imb = (yes_depth - no_depth) / total_depth if total_depth > 0 else 0.0

    return {
        "best_yes_bid": best_yes,
        "best_no_bid":  best_no,
        "best_yes_ask": best_yes_ask,
        "best_no_ask":  best_no_ask,
        "mid_price":    mid_price,
        "spread":       spread,
        "spread_pct":    spread_pct,
        "yes_depth":    yes_depth,
        "no_depth":     no_depth,
        "depth_imbalance": depth_imb,
    }


def compute_depth_profile(
    bids: List[Dict],
    n_levels: int = 10,
) -> Dict[str, Any]:
    """
    Analyze the depth distribution across price levels.

    Useful for detecting:
      - Wall building (large orders at specific price levels)
      - Thin vs thick markets
      - Support/resistance zones

    Args:
        bids:       List of {price_dollars, quantity} dicts, sorted best-to-worst
        n_levels:  Number of levels to analyze

    Returns:
        {
            "levels":           int,    # number of levels with volume
            "total_volume":     float,  # total contracts across all levels
            "avg_level_size":   float,  # mean volume per level
            "max_level_price":  float,  # worst price in the top n_levels
            "weighted_avg_price": float, # volume-weighted average price
            "top3_volume_pct": float,   # % of volume in top 3 levels
        }
    """
    if not bids:
        return {"levels": 0, "total_volume": 0.0, "avg_level_size": 0.0,
                "max_level_price": 0.0, "weighted_avg_price": 0.0, "top3_volume_pct": 0.0}

    levels = bids[:n_levels]
    volumes = [float(b.get("quantity", 0)) for b in levels]
    prices  = [float(b.get("price_dollars", 0)) for b in levels]

    total_vol = sum(volumes)
    avg_size  = total_vol / len(levels) if levels else 0.0

    # Volume-weighted average price
    wap = sum(v * p for v, p in zip(volumes, prices)) / total_vol if total_vol > 0 else 0.0

    # Top-3 concentration
    top3_vol = sum(volumes[:3])
    top3_pct = (top3_vol / total_vol * 100) if total_vol > 0 else 0.0

    return {
        "levels":              len(levels),
        "total_volume":       total_vol,
        "avg_level_size":      avg_size,
        "max_level_price":    prices[-1] if prices else 0.0,
        "weighted_avg_price":  wap,
        "top3_volume_pct":    top3_pct,
    }


# ---------------------------------------------------------------------------
# Order Flow Imbalance (OFI) — rolling net volume
# ---------------------------------------------------------------------------

class OrderFlowTracker:
    """
    Tracks rolling Order Flow Imbalance (OFI) over a sliding window.

    OFI = net volume on the bid side over a time window.
    Positive OFI = more buying pressure → price likely to rise
    Negative OFI = more selling pressure → price likely to fall

    Also computes realized volatility from price changes.
    """

    def __init__(self, window_size: int = 20):
        """
        Args:
            window_size: Number of updates to keep in rolling window
        """
        self.window_size = window_size
        self.ofi_history: deque = deque(maxlen=window_size)
        self.price_history: deque = deque(maxlen=window_size)
        self.timestamp_history: deque = deque(maxlen=window_size)
        self._last_best_yes = None
        self._last_best_no  = None

    def update(self, yes_bids: List[Dict], no_bids: List[Dict], timestamp: float) -> Dict[str, float]:
        """
        Update with current order book snapshot.

        OFI = Σ (new_bid_volume - old_bid_volume) weighted by price change direction.

        Args:
            yes_bids: Current YES bids
            no_bids:  Current NO bids
            timestamp: Unix timestamp

        Returns:
            {"ofi": float, "rolling_ofi": float, "price_change": float, "realized_vol": float}
        """
        best_yes = float(yes_bids[0]["price_dollars"]) if yes_bids else 0.0
        best_no  = float(no_bids[0]["price_dollars"])  if no_bids  else 0.0

        # Implied mid
        mid = (best_yes + (1.0 - best_no)) / 2.0

        price_change = 0.0
        if self._last_best_yes is not None and self._last_best_no is not None:
            last_mid = (self._last_best_yes + (1.0 - self._last_best_no)) / 2.0
            price_change = mid - last_mid

        # Net volume at best levels
        yes_vol = sum(float(b.get("quantity", 0)) for b in yes_bids[:3])
        no_vol  = sum(float(b.get("quantity", 0)) for b in no_bids[:3])

        # OFI: positive = more YES volume (buy pressure)
        ofi = yes_vol - no_vol

        self.ofi_history.append(ofi)
        self.price_history.append(mid)
        self.timestamp_history.append(timestamp)

        rolling_ofi = sum(self.ofi_history)

        # Realized volatility from price changes
        realized_vol = 0.0
        if len(self.price_history) > 1:
            price_arr = np.array(self.price_history)
            realized_vol = float(np.std(np.diff(price_arr)))

        self._last_best_yes = best_yes
        self._last_best_no  = best_no

        return {
            "ofi":           ofi,
            "rolling_ofi":   rolling_ofi,
            "price_change":  price_change,
            "realized_vol": realized_vol,
            "mid_price":    mid,
        }


# ---------------------------------------------------------------------------
# Complete Order Book Analyzer
# ---------------------------------------------------------------------------

class OrderBookAnalyzer:
    """
    L2 order book analyzer with real-time VPIN and Kyle λ estimation.

    Tracks:
      - Spread and depth metrics
      - Order flow imbalance
      - VPIN from order book volume
      - Implied Kyle λ from price impact per unit of OFI

    Usage:
        analyzer = OrderBookAnalyzer(kalshi_api)
        result = analyzer.analyze("KXSECPRESSMENTION-25MAR20-PHONECALL")
    """

    def __init__(
        self,
        kalshi_api,
        vpin_estimator=None,
        kyle_estimator=None,
        n_depth_levels: int = 10,
        ofi_window: int = 20,
    ):
        self.api = kalshi_api
        self.vpin_est = vpin_estimator
        self.kyle_est = kyle_estimator
        self.n_levels = n_depth_levels
        self.ofi_tracker = OrderFlowTracker(window_size=ofi_window)
        self._ticker: Optional[str] = None

    def fetch_orderbook(self, ticker: str, depth: int = 10) -> Optional[Dict]:
        """Fetch L2 order book from Kalshi API."""
        resp = self.api.get_orderbook(ticker, depth=depth)
        if resp is None:
            return None
        ob = resp.get("orderbook_fp", {})
        if not ob:
            return None
        return {
            "yes_bids": ob.get("yes_bids", []),
            "no_bids":  ob.get("no_bids",  []),
            "last_yes_bid": ob.get("last_yes_bid"),
            "last_no_bid":  ob.get("last_no_bid"),
            "fetched_at": time.time(),
        }

    def analyze(self, ticker: str, depth: int = 10) -> Dict[str, Any]:
        """
        Full L2 analysis of a Kalshi market.

        Args:
            ticker: Market ticker
            depth:  Number of L2 levels to fetch

        Returns:
            {
                "ticker": str,
                "spread_metrics": {...},
                "yes_depth_profile": {...},
                "no_depth_profile": {...},
                "ofi": {"ofi": float, "rolling_ofi": float, "realized_vol": float},
                "vpin": float or None,
                "kyle_lambda": float or None,
                "status": str,
            }
        """
        self._ticker = ticker
        ob = self.fetch_orderbook(ticker, depth=depth)

        if ob is None or not ob.get("yes_bids") or not ob.get("no_bids"):
            return {
                "ticker": ticker,
                "status": "no_data",
                "error": "No order book data available (market may be settled or inactive)",
            }

        yes_bids = ob["yes_bids"]
        no_bids  = ob["no_bids"]
        ts       = ob["fetched_at"]

        # Spread metrics
        spread_m = compute_spread_metrics(yes_bids, no_bids)

        # Depth profiles
        yes_dp = compute_depth_profile(yes_bids, n_levels=self.n_levels)
        no_dp  = compute_depth_profile(no_bids,  n_levels=self.n_levels)

        # Order flow
        ofi = self.ofi_tracker.update(yes_bids, no_bids, ts)

        # VPIN from order book (use as-is, already computed from trades)
        vpin_val = None
        if self.vpin_est:
            try:
                vp = self.vpin_est.estimate_for_market(ticker, max_pages=3)
                vpin_val = vp.get("vpin", 0.0)
            except Exception as e:
                logger.warning(f"VPIN estimate failed: {e}")

        # Kyle λ from OFI (simplified — no regression needed, use OFI→price impact)
        kyle_lambda = None
        if ofi["rolling_ofi"] != 0 and ofi["realized_vol"] > 0:
            # λ ≈ Δprice / OFI  (price impact per unit of net volume)
            kyle_lambda = float(ofi["realized_vol"] / abs(ofi["rolling_ofi"]))

        return {
            "ticker": ticker,
            "status": "ok",
            "spread_metrics": spread_m,
            "yes_depth_profile": yes_dp,
            "no_depth_profile":  no_dp,
            "ofi": ofi,
            "vpin": vpin_val,
            "kyle_lambda": kyle_lambda,
            "fetched_at": ts,
        }

    def analyze_with_mock(self, ticker: str) -> Dict[str, Any]:
        """
        Analyze with synthetic order book data (for testing without live market).

        Creates a realistic order book with known spread and depth for verification.
        """
        import random
        random.seed(42)

        mid = 0.55
        spread = 0.04
        yes_bids = []
        no_bids = []

        # Generate YES bids below mid
        for i in range(10):
            price = max(0.01, mid - spread/2 - i * 0.002)
            qty = random.uniform(10, 200)
            yes_bids.append({"price_dollars": price, "quantity": qty})

        # Generate NO bids above 1-mid
        for i in range(10):
            price = min(0.99, 1 - mid + spread/2 + i * 0.002)
            qty = random.uniform(10, 200)
            no_bids.append({"price_dollars": price, "quantity": qty})

        ts = time.time()
        ofi = self.ofi_tracker.update(yes_bids, no_bids, ts)

        spread_m = compute_spread_metrics(yes_bids, no_bids)
        yes_dp = compute_depth_profile(yes_bids, n_levels=self.n_levels)
        no_dp  = compute_depth_profile(no_bids,  n_levels=self.n_levels)

        return {
            "ticker": ticker,
            "status": "mock",
            "spread_metrics": spread_m,
            "yes_depth_profile": yes_dp,
            "no_depth_profile":  no_dp,
            "ofi": ofi,
            "vpin": None,
            "kyle_lambda": None,
        }
