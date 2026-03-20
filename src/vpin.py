#!/usr/bin/env python3
"""
VPIN Calculator — Volume-Synchronized Probability of Informed Trading for K-ATA.

VPIN estimates the probability that a random trade is informed (toxic order flow).
High VPIN → adverse selection risk for market makers → widen spreads or stand aside.

VPIN = mean(|V_buy - V_sell| / V_total) across M volume buckets

where each bucket contains approximately equal total volume.
M = number of buckets (default 50, standard per Easley-Lopez de Prado-O'Hara 2012)

For prediction markets:
    - BUY  = "YES" trades (buy the outcome)
    - SELL = "NO"  trades (sell/reject the outcome)

VPIN interpretation:
    < 0.30  → Normal liquidity
    0.30-0.50 → Elevated adverse selection
    0.50-0.70 → High toxicity
    > 0.70  → Extreme toxicity — market maker fleeing territory
               (VPIN > 0.70 preceded the 2010 Flash Crash by ~1 hour)

Ref:
    Easley, D., López de Prado, M., & O'Hara, M. (2012).
    "Flow Toxicity and Liquidity in a High-Frequency World."
    Review of Financial Studies, 25(5), 1457-1493.
"""

import logging
import numpy as np
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_M_BUCKETS = 50


def _build_buckets(
    trades: List[Dict[str, Any]],
    m: int = DEFAULT_M_BUCKETS,
) -> List[Dict[str, float]]:
    """
    Build M volume buckets from a list of trades.

    Each bucket holds trades until its volume threshold is reached,
    then a new bucket starts. The final bucket may be smaller.

    Bulk Volume Classification:
        BUY  = "yes" taker_side  (buy the outcome)
        SELL = "no"  taker_side  (sell/reject the outcome)

    Args:
        trades: List of trade dicts with keys: count_fp, taker_side
        m:      Number of volume buckets (default 50)

    Returns:
        List of bucket dicts:
            [{"buy": float, "sell": float, "total": float, "n_trades": int}, ...]
    """
    if not trades:
        return []

    # Total volume across all trades
    total_volume = sum(float(t.get("count_fp", 0)) for t in trades)
    if total_volume <= 0:
        return []

    # Target volume per bucket
    target = total_volume / m

    buckets = []
    current = {"buy": 0.0, "sell": 0.0, "total": 0.0, "n_trades": 0}

    for trade in trades:
        vol  = float(trade.get("count_fp", 0))
        side = trade.get("taker_side", "").lower()

        if vol <= 0:
            continue

        if current["total"] + vol <= target:
            # Add to current bucket
            if side == "yes":
                current["buy"] += vol
            elif side == "no":
                current["sell"] += vol
            current["total"] += vol
            current["n_trades"] += 1
        else:
            # Finalize current bucket if it has volume
            if current["total"] > 0:
                buckets.append(current)
            # Start new bucket with this trade
            current = {
                "buy": vol if side == "yes" else 0.0,
                "sell": vol if side == "no" else 0.0,
                "total": vol,
                "n_trades": 1,
            }

    # Append final bucket
    if current["total"] > 0:
        buckets.append(current)

    return buckets


def compute_vpin(trades: List[Dict[str, Any]], m: int = DEFAULT_M_BUCKETS) -> float:
    """
    Compute VPIN for a list of classified trades.

    VPIN = mean(|V_buy - V_sell| / V_total) across all buckets

    Args:
        trades: List of trade dicts with keys: count_fp, taker_side
        m:      Number of volume buckets

    Returns:
        VPIN value in [0, 1], or 0.0 if insufficient data.
    """
    buckets = _build_buckets(trades, m=m)
    if not buckets:
        return 0.0

    vpin = 0.0
    for b in buckets:
        total = b["total"]
        if total > 0:
            imbalance = abs(b["buy"] - b["sell"]) / total
            vpin += imbalance

    vpin /= len(buckets)
    return float(vpin)


def estimate_vpin(
    trades: List[Dict[str, Any]],
    m: int = DEFAULT_M_BUCKETS,
    min_buckets: int = 10,
) -> Dict[str, Any]:
    """
    Estimate VPIN with interpretation and statistics.

    Args:
        trades:      List of trade dicts with keys: count_fp, taker_side
        m:          Number of volume buckets (default 50)
        min_buckets: Minimum buckets needed for valid VPIN (default 10)

    Returns:
        {
            "vpin":            float,   # VPIN value [0, 1]
            "n_buckets":       int,     # Number of buckets used
            "n_trades":        int,     # Number of trades used
            "buy_volume":      float,   # Total buy (YES) volume
            "sell_volume":     float,   # Total sell (NO) volume
            "total_volume":    float,   # Total volume
            "volume_imbalance": float, # |buy - sell| / total (overall)
            "signal":          str,    # "normal" | "elevated" | "high" | "extreme"
            "interpretation":  str,    # Human-readable summary
        }
    """
    if not trades:
        return {
            "vpin": 0.0, "n_buckets": 0, "n_trades": 0,
            "buy_volume": 0.0, "sell_volume": 0.0, "total_volume": 0.0,
            "volume_imbalance": 0.0, "signal": "no_data",
            "interpretation": "No trade data available",
        }

    buckets = _build_buckets(trades, m=m)

    if len(buckets) < min_buckets:
        return {
            "vpin": 0.0, "n_buckets": len(buckets), "n_trades": len(trades),
            "buy_volume": 0.0, "sell_volume": 0.0, "total_volume": 0.0,
            "volume_imbalance": 0.0, "signal": "insufficient_buckets",
            "interpretation": f"Only {len(buckets)} buckets, need ≥{min_buckets}",
        }

    vpin = compute_vpin(trades, m=m)

    # Aggregate statistics
    buy_vol  = sum(b["buy"]  for b in buckets)
    sell_vol = sum(b["sell"] for b in buckets)
    total_vol = sum(b["total"] for b in buckets)
    vol_imb   = abs(buy_vol - sell_vol) / total_vol if total_vol > 0 else 0.0

    # Signal classification
    if vpin > 0.70:
        signal = "extreme"
    elif vpin > 0.50:
        signal = "high"
    elif vpin > 0.30:
        signal = "elevated"
    else:
        signal = "normal"

    if signal == "extreme":
        interpretation = (
            f"VPIN={vpin:.3f} — EXTREME adverse selection. "
            f"Market makers will widen spreads or withdraw. "
            f"(Preceded Flash Crash of 2010 at VPIN > 0.70)"
        )
    elif signal == "high":
        interpretation = (
            f"VPIN={vpin:.3f} — HIGH order flow toxicity. "
            f"Widen spreads, reduce size, or stand aside."
        )
    elif signal == "elevated":
        interpretation = (
            f"VPIN={vpin:.3f} — ELEVATED adverse selection. "
            f"Moderate caution advised."
        )
    else:
        interpretation = (
            f"VPIN={vpin:.3f} — NORMAL liquidity conditions."
        )

    logger.info(
        f"VPIN estimate: vpin={vpin:.4f}, signal={signal}, "
        f"n_buckets={len(buckets)}, n_trades={len(trades)}, "
        f"imb={vol_imb:.3f}"
    )

    return {
        "vpin": float(vpin),
        "n_buckets": len(buckets),
        "n_trades": len(trades),
        "buy_volume": float(buy_vol),
        "sell_volume": float(sell_vol),
        "total_volume": float(total_vol),
        "volume_imbalance": float(vol_imb),
        "signal": signal,
        "interpretation": interpretation,
    }


# ---------------------------------------------------------------------------
# Higher-level wrapper: fetch from Kalshi API
# ---------------------------------------------------------------------------

class KalshiVPINEstimator:
    """
    Fetches trade history from Kalshi and computes VPIN.

    Usage:
        estimator = KalshiVPINEstimator(kalshi_api)
        result = estimator.estimate_for_market("KXSECPRESSMENTION-25MAR20-PHONECALL")
    """

    def __init__(self, kalshi_api, m: int = DEFAULT_M_BUCKETS, min_buckets: int = 10):
        self.api = kalshi_api
        self.m = m
        self.min_buckets = min_buckets

    def fetch_trades(self, ticker: str, max_pages: int = 10) -> List[Dict[str, Any]]:
        """Fetch trade list for a market ticker."""
        trades = []
        cursor = None

        for _ in range(max_pages):
            params = {"ticker": ticker, "limit": 100}
            if cursor:
                params["cursor"] = cursor

            response = self.api.get_trades(params=params)
            if response is None:
                break

            page_trades = response.get("trades", [])
            if not page_trades:
                break

            trades.extend(page_trades)

            cursor = response.get("cursor", "")
            if not cursor:
                break

        return trades

    def estimate_for_market(self, ticker: str, max_pages: int = 10) -> Dict[str, Any]:
        """
        Fetch trades and compute VPIN.

        Returns:
            VPIN result dict (see estimate_vpin) with added ticker field.
        """
        trades = self.fetch_trades(ticker, max_pages=max_pages)
        result = estimate_vpin(trades, m=self.m, min_buckets=self.min_buckets)
        result["ticker"] = ticker
        return result
