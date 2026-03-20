#!/usr/bin/env python3
"""
Hawkes Process Fitter — Order Flow Self-Excitation Module for K-ATA.

Simplified implementation using variance-based heuristics for branching ratio estimation.

Key metric: branching_ratio
- Fraction of trades caused by prior trades (rest are exogenous news/events)
- > 0.5 → most trades are reactions (momentum / informed clustering)
- < 0.3 → mostly exogenous (clean information environment)

Uses two methods:
1. Variance-to-mean ratio of inter-arrival times
2. Simple autocorrelation check for clustering

Ref: 
    - Hawkes (1971). "Spectra of Some Self-Exciting and Mutually Exciting Point Processes."
    - Bacry et al. (2015). "Hawkes Processes in Finance."
"""

import logging
import numpy as np
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)


def compute_inter_arrival_times(timestamps: np.ndarray) -> np.ndarray:
    """
    Compute inter-arrival times (IAT) from sorted timestamps.
    
    Args:
        timestamps: Sorted array of event timestamps (in seconds)
    
    Returns:
        Array of inter-arrival times (seconds between consecutive events)
    """
    if len(timestamps) < 2:
        return np.array([])
    return np.diff(timestamps)


def estimate_branching_ratio_iat(iat: np.ndarray) -> float:
    """
    Estimate branching ratio from inter-arrival time statistics.
    
    For a Poisson process (no self-excitation, BR=0):
        IAT ~ Exponential(μ)
        Variance = Mean²
    
    For self-exciting process (BR > 0):
        Variance >> Mean² (clustering)
    
    Heuristic: BR ≈ 1 - (mean² / variance)
    - If variance ≈ mean² (Poisson): BR ≈ 0
    - If variance >> mean² (clustered): BR → 1
    
    Ref: Simplified from Lewis & Mohler (2011).
    """
    if len(iat) < 3:
        return 0.0
    
    mean_iat = np.mean(iat)
    var_iat = np.var(iat)
    
    if var_iat <= 0 or mean_iat <= 0:
        return 0.0
    
    # Variance-to-mean ratio (Fano factor)
    vmr = var_iat / (mean_iat ** 2)
    
    # Branching ratio heuristic: BR = 1 - 1/VMR
    # This maps: Poisson (VMR=1) → BR=0, Heavy clustering (VMR>>1) → BR→1
    # Use min to avoid extreme values
    branching_ratio = max(0.0, min(0.95, 1.0 - 1.0 / vmr))
    
    return branching_ratio


def estimate_branching_ratio_autocorr(iat: np.ndarray, max_lag: int = 5) -> float:
    """
    Estimate branching ratio from autocorrelation of IAT.
    
    High autocorrelation → clustering (trades follow trades)
    Low/no autocorrelation → random exogenous events
    
    Args:
        iat: Inter-arrival times
        max_lag: Maximum lag to check (default 5)
    
    Returns:
        Estimated branching ratio based on autocorrelation strength
    """
    if len(iat) < 10:
        return 0.0
    
    # Normalize IAT
    iat_norm = (iat - np.mean(iat)) / (np.std(iat) + 1e-10)
    
    # Autocorrelation at lag 1
    autocorr = np.correlate(iat_norm[:-1], iat_norm[1:], mode='full')[-1] / (len(iat_norm) - 1)
    
    # Map autocorr [-1, 1] to branching ratio [0, 0.9]
    # Positive autocorr → clustering (trades follow trades)
    branching_ratio = max(0.0, min(0.9, autocorr * 1.5))
    
    return branching_ratio


def estimate_hawkes(
    timestamps: np.ndarray,
    min_events: int = 20,
) -> Dict[str, Any]:
    """
    Estimate Hawkes process parameters using variance-based heuristics.

    Two complementary methods:
    1. IAT variance method — captures overall clustering strength
    2. Autocorrelation method — captures sequential dependencies
    
    Returns weighted average of both methods.

    Args:
        timestamps: Event timestamps (Unix timestamps or seconds from start)
                   Will be sorted internally if not already sorted
        min_events: Minimum events needed for estimation (default 20)

    Returns:
        {
            "branching_ratio": float,   # 0-1: fraction of trades caused by prior trades
            "mean_iat_sec": float,     # Mean inter-arrival time (seconds)
            "n_events": int,            # Number of events used
            "interpretation": str,      # Human-readable summary
            "method_1_vmr": float,      # Variance-method branching ratio
            "method_2_acf": float,      # Autocorr-method branching ratio
        }
        or {"error": str} on failure.
    """
    # Sort timestamps
    timestamps = np.sort(timestamps)
    
    if len(timestamps) < min_events:
        return {
            "error": f"Only {len(timestamps)} events, need ≥{min_events}",
            "branching_ratio": 0.0,
            "mean_iat_sec": 0.0,
            "n_events": len(timestamps),
            "interpretation": "Insufficient data",
        }
    
    # Compute inter-arrival times
    iat = compute_inter_arrival_times(timestamps)
    
    if len(iat) < 3:
        return {
            "error": f"Only {len(iat)} IAT values, need ≥3",
            "branching_ratio": 0.0,
            "mean_iat_sec": 0.0,
            "n_events": len(timestamps),
            "interpretation": "Insufficient IAT data",
        }
    
    # Method 1: Variance-to-mean ratio
    br_vmr = estimate_branching_ratio_iat(iat)
    
    # Method 2: Autocorrelation
    br_acf = estimate_branching_ratio_autocorr(iat)
    
    # Weighted average (variance method is more robust)
    branching_ratio = 0.7 * br_vmr + 0.3 * br_acf
    branching_ratio = max(0.0, min(0.95, branching_ratio))
    
    mean_iat = np.mean(iat)
    trades_per_min = 60.0 / mean_iat if mean_iat > 0 else 0.0
    
    # Interpretation
    if branching_ratio > 0.7:
        interpretation = (
            f"High self-excitation (BR={branching_ratio:.2f}): {branching_ratio*100:.0f}% of trades "
            f"cluster — likely momentum or informed flow ({trades_per_min:.1f} trades/min)"
        )
    elif branching_ratio > 0.5:
        interpretation = (
            f"Moderate clustering (BR={branching_ratio:.2f}): majority of trades react to "
            f"prior trades — reduced information signal ({trades_per_min:.1f} trades/min)"
        )
    elif branching_ratio > 0.3:
        interpretation = (
            f"Low clustering (BR={branching_ratio:.2f}): mixed endogenous/exogenous — "
            f"moderate signal quality ({trades_per_min:.1f} trades/min)"
        )
    else:
        interpretation = (
            f"Mostly exogenous (BR={branching_ratio:.2f}): trades driven by external news — "
            f"clean information signal ({trades_per_min:.1f} trades/min)"
        )
    
    logger.info(
        f"Hawkes heuristic: BR_vmr={br_vmr:.3f}, BR_acf={br_acf:.3f}, "
        f"BR_final={branching_ratio:.3f}, n={len(timestamps)}"
    )
    
    return {
        "branching_ratio": float(branching_ratio),
        "mean_iat_sec": float(mean_iat),
        "trades_per_min": float(trades_per_min),
        "n_events": len(timestamps),
        "interpretation": interpretation,
        "method_1_vmr": float(br_vmr),
        "method_2_acf": float(br_acf),
    }


# ---------------------------------------------------------------------------
# Higher-level wrapper: fetch from Kalshi and estimate
# ---------------------------------------------------------------------------

class KalshiHawkesEstimator:
    """
    Fetches trade history from Kalshi and estimates Hawkes process parameters.
    
    Usage:
        estimator = KalshiHawkesEstimator(kalshi_api)
        result = estimator.estimate_for_market("KXSECPRESSMENTION-25MAR20-PHONECALL")
    """
    
    def __init__(self, kalshi_api, min_events: int = 20):
        self.api = kalshi_api
        self.min_events = min_events
    
    def fetch_timestamps(self, ticker: str, max_pages: int = 10) -> np.ndarray:
        """
        Fetch trade timestamps for a market.
        
        Returns:
            numpy array of Unix timestamps (seconds)
        """
        import time
        
        timestamps = []
        cursor = None
        
        for _ in range(max_pages):
            params = {"ticker": ticker, "limit": 100}
            if cursor:
                params["cursor"] = cursor
            
            response = self.api.get_trades(params=params)
            if response is None:
                break
            
            trades = response.get("trades", [])
            if not trades:
                break
            
            for trade in trades:
                ts_str = trade.get("created_time", "")
                if ts_str:
                    try:
                        # Parse ISO timestamp
                        ts = time.mktime(time.strptime(ts_str[:19], "%Y-%m-%dT%H:%M:%S"))
                        timestamps.append(ts)
                    except (ValueError, OSError):
                        continue
            
            cursor = response.get("cursor", "")
            if not cursor:
                break
        
        return np.sort(np.array(timestamps))
    
    def estimate_for_market(self, ticker: str, max_pages: int = 10) -> Dict[str, Any]:
        """
        Fetch trades and estimate Hawkes branching ratio.
        
        Returns:
            Hawkes estimation result dict (see estimate_hawkes)
        """
        timestamps = self.fetch_timestamps(ticker, max_pages)
        
        if len(timestamps) < self.min_events:
            return {
                "error": f"Only {len(timestamps)} trades fetched, need ≥{self.min_events}",
                "branching_ratio": 0.0,
                "ticker": ticker,
            }
        
        result = estimate_hawkes(timestamps, min_events=self.min_events)
        result["ticker"] = ticker
        return result
