#!/usr/bin/env python3
"""
Kyle's Lambda Estimator — Order Flow Impact Module for K-ATA.

Kyle (1985) showed that price impact is linear in signed order flow:
    Δp_t = λ * Q_t + ε_t

where:
    λ  = Kyle's lambda — price impact per unit of signed volume
    Q  = signed volume (buy volume - sell volume) at time t
    ε  = residual noise

High λ → informed traders active → widen spreads or stand aside.
R² > 0.15 from the regression → significant informed flow present.

Ref: Kyle, A.S. (1985). "Continuous Auctions and Insider Trading." Econometrica.
"""

import logging
import numpy as np
from scipy.stats import linregress
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


def estimate_kyle_lambda(
    prices: List[float],
    volumes: List[float],
    sides: List[str],
    min_trades: int = 30,
) -> Dict[str, Any]:
    """
    Estimate Kyle's lambda via OLS regression: Δp_t = λ * Q_t + ε_t

    Args:
        prices:    Mid-price series [p_0, p_1, ..., p_T] — length T+1
        volumes:   Trade sizes, one per price change — length T
        sides:     'yes' (buy) or 'no' (sell) per trade
        min_trades: Minimum number of trades needed for a valid estimate

    Returns:
        {
            "lambda":       float,  — price impact coefficient (λ)
            "r_squared":    float,  — regression R² (informed flow signal)
            "p_value":      float,  — t-test p-value for λ
            "std_error":    float,  — standard error of λ estimate
            "n_trades":     int,    — number of trades used
            "interpretation": str,   — 'High informed trading' | 'Normal liquidity'
            "is_significant": bool,  — True if p_value < 0.05 and R² > 0.05
        }
    """
    if len(prices) < 2:
        return _error_result("Need at least 2 price points", 0)

    if len(volumes) != len(sides):
        return _error_result(
            f"Mismatched lengths: {len(volumes)} volumes vs {len(sides)} sides", 0
        )

    if len(volumes) < min_trades:
        return _error_result(f"Only {len(volumes)} trades, need ≥{min_trades}", len(volumes))

    try:
        # ---- Build signed order flow Q_t ----
        # Q_t = volume_t * sign_t   where sign=+1 for buy ('yes'), -1 for sell ('no')
        signs = np.array([1.0 if s.lower() == "yes" else -1.0 for s in sides])
        volumes_arr = np.array(volumes, dtype=float)
        signed_volume = volumes_arr * signs

        # ---- Price changes Δp_t ----
        # prices = execution prices from trades
        # volumes = trade sizes
        # We need: Δp_t = p_{t+1} - p_t  aligned with  signed_volume[t]
        # Both arrays may have different lengths depending on input (API vs synthetic)
        # → Align to minimum length to avoid index errors
        prices_arr   = np.array(prices, dtype=float)
        price_changes = np.diff(prices_arr)            # len = n-1 (n prices)
        signed_flow   = signed_volume[:-1]              # len = n-1 (n volumes, drop last)

        # Safety: align to minimum length (handles n+1 prices in synthetic, n in API)
        min_len = min(len(price_changes), len(signed_flow))
        price_changes = price_changes[:min_len]
        signed_flow   = signed_flow[:min_len]

        # Remove zero-change ticks (tied trades — no price move occurred)
        mask = price_changes != 0
        if mask.sum() < min_trades:
            return _error_result(
                f"Only {mask.sum()} non-zero price changes, need ≥{min_trades}",
                len(volumes),
            )

        x = signed_flow[mask]   # signed volume that produced a non-zero price change
        y = price_changes[mask] # resulting price change

        # ---- OLS regression: Δp = λ * Q ----
        slope, intercept, r_value, p_value, std_err = linregress(x, y)
        r_squared = r_value**2

        # ---- Interpretation ----
        # Rule from the article: R² > 0.15 → significant informed flow
        # More sensitive threshold: p < 0.05 AND R² > 0.05
        is_significant = bool(p_value < 0.05 and r_squared > 0.05)
        if r_squared > 0.15:
            interpretation = "High informed trading — widen spreads or stand aside"
        elif r_squared > 0.05:
            interpretation = "Moderate informed trading — caution advised"
        else:
            interpretation = "Normal liquidity — standard market conditions"

        result = {
            "lambda": float(slope),
            "r_squared": float(r_squared),
            "p_value": float(p_value),
            "std_error": float(std_err),
            "n_trades": int(len(volumes)),
            "interpretation": interpretation,
            "is_significant": is_significant,
        }
        logger.info(
            f"Kyle lambda estimate: λ={slope:.6f}, R²={r_squared:.4f}, "
            f"p={p_value:.4f}, n={len(volumes)} → {interpretation}"
        )
        return result

    except Exception as e:
        logger.error(f"Kyle lambda regression failed: {e}")
        return _error_result(str(e), len(volumes))


def _error_result(msg: str, n_trades: int) -> Dict[str, Any]:
    return {
        "lambda": 0.0,
        "r_squared": 0.0,
        "p_value": 1.0,
        "std_error": 0.0,
        "n_trades": n_trades,
        "interpretation": f"Error: {msg}",
        "is_significant": False,
        "error": msg,
    }


# ---------------------------------------------------------------------------
# Optional: higher-level fetcher class — pulls data from Kalshi API
# Only import if kalshi_api is available (avoids hard dependency here)
# ---------------------------------------------------------------------------

class KalshiKyleLambda:
    """
    Fetches trade history for a Kalshi market and estimates Kyle's lambda.

    Usage:
        fetcher = KalshiKyleLambda(kalshi_api_client)
        result  = fetcher.estimate_for_market("KXSECPRESSMENTION-25MAR20-PHONECALL")
    """

    def __init__(self, kalshi_api, min_trades: int = 30):
        self.api = kalshi_api
        self.min_trades = min_trades

    def fetch_trades(self, ticker: str, max_pages: int = 10) -> Dict[str, Any]:
        """
        Fetch up to `max_pages` pages of trades for a given market ticker.

        Pagination: call /markets/trades?ticker=X&limit=100 repeatedly,
        passing the cursor from each response until cursor is empty.

        Returns:
            {"prices": [...], "volumes": [...], "sides": [...], "n_pages": int}
            or {"error": str} on failure.
        """
        prices: List[float] = []
        volumes: List[float] = []
        sides: List[str] = []
        cursor = None

        for page_num in range(max_pages):
            params = {"ticker": ticker, "limit": 100}
            if cursor:
                params["cursor"] = cursor

            response = self.api.get_trades(params=params)
            if response is None:
                return {"error": f"API call failed on page {page_num + 1}"}

            trades = response.get("trades", [])
            if not trades:
                break

            for trade in trades:
                try:
                    price = float(trade.get("yes_price_dollars", 0))
                    volume = float(trade.get("count_fp", 0))
                    side = trade.get("taker_side", "").lower()
                    if price <= 0 or volume <= 0 or side not in ("yes", "no"):
                        continue
                    prices.append(price)
                    volumes.append(volume)
                    sides.append(side)
                except (ValueError, TypeError) as e:
                    logger.warning(f"Skipping malformed trade: {trade}, error: {e}")
                    continue

            cursor = response.get("cursor", "")
            if not cursor:
                break

        if len(prices) < self.min_trades:
            return {
                "error": f"Only {len(prices)} trades fetched, need ≥{self.min_trades}",
                "prices": prices,
                "volumes": volumes,
                "sides": sides,
            }

        return {"prices": prices, "volumes": volumes, "sides": sides, "n_pages": page_num + 1}

    def estimate_for_market(self, ticker: str, max_pages: int = 10) -> Dict[str, Any]:
        """
        Fetch trades for `ticker` and estimate Kyle's lambda.

        Returns:
            Kyle lambda result dict (see estimate_kyle_lambda) with added fields:
                ticker, n_trades_fetched, n_pages
            or an error result dict.
        """
        data = self.fetch_trades(ticker, max_pages=max_pages)
        if "error" in data and "prices" not in data:
            return {"ticker": ticker, "error": data["error"], "lambda": 0.0}

        prices = data["prices"]
        volumes = data["volumes"]
        sides = data["sides"]

        result = estimate_kyle_lambda(prices, volumes, sides, min_trades=self.min_trades)
        result["ticker"] = ticker
        result["n_trades_fetched"] = len(prices)
        result["n_pages"] = data.get("n_pages", 0)
        return result
