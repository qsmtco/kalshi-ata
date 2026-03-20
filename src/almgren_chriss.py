#!/usr/bin/env python3
"""
Almgren-Chriss Execution Scheduler — Optimal Order Splitting for K-ATA.

Solves the optimal execution problem: how to buy/sell X contracts over time
with minimum market impact while balancing risk?

Original Almgren-Chriss (2001) assumes:
  - Linear temporary impact: g(v) = γ * v
  - Linear permanent impact: f(v) = η * v
  - Risk aversion parameter λ

The optimal discrete execution schedule (time窗口 of N trades):
  x_k = X * sinh(κ(T - t_k)) / sinh(κT)

where:
  κ = sqrt(λ / γ)     [urgency parameter]
  t_k = k * T/N        [time of k-th trade]

Trade size at time t_k:
  Δx_k = x_k - x_{k-1} = (X / sinh(κT)) * cosh(κ * (T - t_k) - κ * T/N)

Critical time (minimum variance completion time):
  T* = sqrt(2π * γ / λ)

For prediction markets:
  - γ = temporary impact (set to 0.001–0.01 per contract)
  - λ = risk aversion (higher = more urgency to complete)
  - σ = price volatility (from GARCH or 2¢/sqrt-hour)

Ref:
  - Almgren & Chriss (2001). "Optimal Execution of Portfolio Transactions."
    Journal of Risk, 3(2), 5-39.
  - https://en.wikipedia.org/wiki/Almgren%E2%80%93Chriss_model
"""

import logging
import math
import numpy as np
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_GAMMA = 0.001   # temporary impact per contract
DEFAULT_LAMBDA = 0.1    # risk aversion


def compute_urgency_parameter(gamma: float, risk_aversion: float) -> float:
    """
    Compute κ = sqrt(λ / γ)

    κ controls urgency:
      - High κ (high λ or low γ) → trade faster early, concentrated schedule
      - Low κ  (low λ or high γ) → spread evenly, patient schedule
    """
    return math.sqrt(risk_aversion / gamma)


def compute_critical_time(gamma: float, risk_aversion: float, X: float, sigma: float) -> float:
    """
    Compute T* = sqrt(2π * γ / λ) — minimum variance completion time.

    The optimal time to complete the trade if urgency is not a factor.
    Shorter than this → high market impact. Longer → price drift risk.
    """
    return math.sqrt(2.0 * math.pi * gamma / risk_aversion)


def compute_optimal_trade_schedule(
    X: float,
    T: float,
    N: int,
    gamma: float = DEFAULT_GAMMA,
    risk_aversion: float = DEFAULT_LAMBDA,
    sigma: float = 0.02,
) -> Dict[str, Any]:
    """
    Compute the optimal discrete execution schedule.

    Args:
        X:              Total position size (contracts, positive=buy, negative=sell)
        T:              Time horizon in HOURS
        N:              Number of discrete trades to execute
        gamma:          Temporary impact coefficient
                          (cost per contract per hour of delay)
        risk_aversion: Risk aversion parameter λ
                          (higher = more urgency to complete)
        sigma:          Price volatility in $/sqrt(hour)

    Returns:
        {
            "schedule": [
                {"k": 0, "time_hours": 0.0,  "trade_size": float, "remaining": float},
                {"k": 1, "time_hours": T/N,   "trade_size": float, "remaining": float},
                ...
                {"k": N-1, "time_hours": T*(N-1)/N, "trade_size": ..., "remaining": 0},
            ],
            "kappa": float,       # urgency parameter
            "total_cost": float,  # expected temporary impact cost
            "remaining_risk": float,  # variance of remaining position
            "urgency": str,      # "high" | "moderate" | "low"
            "T_star_hours": float,  # critical time (min variance time)
            "N": int,
            "T_hours": float,
        }
    """
    if X == 0:
        return {
            "schedule": [], "kappa": 0.0, "total_cost": 0.0,
            "remaining_risk": 0.0, "urgency": "none", "T_star_hours": 0.0, "N": N, "T_hours": T,
        }

    if T <= 0 or N <= 0:
        return {
            "schedule": [],
            "error": f"Invalid T={T} or N={N}",
            "N": N, "T_hours": T,
        }

    X = abs(X)  # use absolute value, direction encoded separately
    sign = 1 if X > 0 else -1  # for signed positions

    # Urgency parameter
    kappa = compute_urgency_parameter(gamma, risk_aversion)

    # Critical time
    T_star = compute_critical_time(gamma, risk_aversion, X, sigma)

    # Adjust N to be at least 2
    N = max(2, N)

    # Schedule using sinh/cosh formula
    # x_k = X * sinh(κ(T - t_k)) / sinh(κT)
    # Δx_k = x_k - x_{k-1}
    schedule = []
    remaining = X
    total_cost = 0.0

    # Precompute sinh(kappa * T)
    sinh_kappa_T = math.sinh(kappa * T)
    if sinh_kappa_T < 1e-10:
        # kappa * T is very small → use linear approximation
        # x_k ≈ X * (1 - t_k/T)
        logger.warning(
            f"Almgren-Chriss: κT={kappa * T:.6f} (very small), using linear approximation"
        )
        for k in range(N):
            t_k = k * T / N
            x_k = X * (1 - t_k / T)
            prev_x = X * (1 - (k - 1) * T / N) if k > 0 else X
            trade_size = sign * (prev_x - x_k)
            remaining -= abs(trade_size)
            cost = gamma * abs(trade_size) * (T - t_k)
            total_cost += cost
            schedule.append({
                "k": k,
                "time_hours": t_k,
                "trade_size": float(trade_size),
                "remaining": float(remaining),
                "cost": round(cost, 6),
            })
    else:
        # Full sinh/cosh formula
        for k in range(N):
            t_k = k * T / N          # time of trade k
            t_next = (k + 1) * T / N  # time of trade k+1

            # Remaining after trade k
            x_k = X * math.sinh(kappa * (T - t_k)) / sinh_kappa_T
            prev_x = X if k == 0 else X * math.sinh(kappa * (T - (k - 1) * T / N)) / sinh_kappa_T
            trade_size = sign * (prev_x - x_k)
            remaining -= abs(trade_size)

            # Temporary impact cost: γ * |Δx| * (T - t_k)
            # (delay cost proportional to remaining time)
            cost = gamma * abs(trade_size) * (T - t_k)
            total_cost += cost

            schedule.append({
                "k": k,
                "time_hours": round(t_k, 4),
                "trade_size": round(float(trade_size), 4),
                "remaining": round(float(remaining), 4),
                "cost": round(cost, 6),
            })

    # Remaining risk: variance of position at end
    remaining_risk = remaining * sigma * math.sqrt(T)

    # Urgency classification
    if T < T_star * 0.5:
        urgency = "high"
    elif T < T_star:
        urgency = "moderate"
    else:
        urgency = "low"

    return {
        "schedule": schedule,
        "kappa": float(kappa),
        "total_cost": round(total_cost, 6),
        "remaining_risk": round(float(remaining_risk), 6),
        "urgency": urgency,
        "T_star_hours": round(float(T_star), 4),
        "N": N,
        "T_hours": T,
        "X": X,
        "sign": sign,
    }


def get_trade_at_time(
    schedule_result: Dict[str, Any],
    current_time_hours: float,
) -> Optional[Dict[str, Any]]:
    """
    Given a schedule and current time, return the trade to execute now.

    Args:
        schedule_result: Output of compute_optimal_trade_schedule()
        current_time_hours: Hours elapsed since start

    Returns:
        Trade dict {"k": int, "trade_size": float, "remaining": float}
        or None if schedule is complete.
    """
    if not schedule_result.get("schedule"):
        return None

    for entry in schedule_result["schedule"]:
        if entry["time_hours"] <= current_time_hours < entry["time_hours"] + schedule_result["T_hours"] / schedule_result["N"]:
            return entry

    # If past the last trade time, return last entry
    last = schedule_result["schedule"][-1]
    if current_time_hours >= schedule_result["T_hours"]:
        return last

    return None


def adjust_schedule_for_market_conditions(
    schedule: Dict[str, Any],
    spread_pct: float,
    vpin: float,
) -> Dict[str, Any]:
    """
    Widen the schedule urgency based on market microstructure signals.

    VPIN > 0.70 or wide spread → increase effective risk_aversion
    → reduces time horizon, executes faster to avoid adverse selection.

    Args:
        schedule:       Output of compute_optimal_trade_schedule()
        spread_pct:   Current spread as % of price
        vpin:         Current VPIN (0-1)

    Returns:
        Modified schedule with urgency adjustment applied
    """
    if vpin > 0.70 or spread_pct > 50.0:
        # Increase urgency: execute faster
        # Multiply all time_hours by a factor < 1
        factor = 0.7 if vpin > 0.80 else 0.85
        urgency_note = f"Accelerated ×{factor:.2f} (VPIN={vpin:.3f}, spread={spread_pct:.0f}%)"

        new_schedule = []
        for entry in schedule["schedule"]:
            new_entry = {**entry}
            new_entry["time_hours"] = round(entry["time_hours"] * factor, 4)
            new_entry["urgency_note"] = urgency_note
            new_schedule.append(new_entry)

        return {
            **schedule,
            "schedule": new_schedule,
            "urgency_note": urgency_note,
            "urgency": "high",
        }

    return {**schedule, "urgency_note": ""}


# ---------------------------------------------------------------------------
# Higher-level wrapper for K-ATA integration
# ---------------------------------------------------------------------------

class AlmgrenChrissExecutor:
    """
    Executes large orders using Almgren-Chriss optimal scheduling.

    Given a target position size and time horizon, computes the optimal
    schedule and can step through it progressively.

    Usage:
        executor = AlmgrenChrissExecutor(position_manager, kalshi_api)
        schedule = executor.schedule_hedge(target_qty=1000, horizon_hours=4)
        trade = executor.get_next_trade(schedule, elapsed_hours=1.5)
    """

    def __init__(
        self,
        position_manager,
        gamma: float = DEFAULT_GAMMA,
        risk_aversion: float = DEFAULT_LAMBDA,
    ):
        self.position_manager = position_manager
        self.gamma = gamma
        self.risk_aversion = risk_aversion

    def schedule_hedge(
        self,
        market_id: str,
        target_qty: float,
        horizon_hours: float = 4.0,
        n_trades: int = 10,
        sigma: float = 0.02,
    ) -> Dict[str, Any]:
        """
        Compute optimal schedule to hedge/exit a position.

        Args:
            market_id:       Kalshi market ticker
            target_qty:      Contracts to buy or sell (positive=buy, negative=sell)
            horizon_hours:   Max time to complete execution
            n_trades:        Number of discrete trades
            sigma:           Price volatility in $/sqrt(hour)

        Returns:
            Schedule dict from compute_optimal_trade_schedule()
        """
        if target_qty == 0:
            return {"schedule": [], "urgency": "none", "error": "Zero quantity"}

        schedule = compute_optimal_trade_schedule(
            X=abs(target_qty),
            T=horizon_hours,
            N=n_trades,
            gamma=self.gamma,
            risk_aversion=self.risk_aversion,
            sigma=sigma,
        )

        schedule["market_id"] = market_id
        schedule["direction"] = "buy" if target_qty > 0 else "sell"

        logger.info(
            f"A-C scheduler: {market_id} {schedule['direction']} "
            f"{abs(target_qty)} contracts over {horizon_hours}h, "
            f"urgency={schedule['urgency']}, κ={schedule['kappa']:.3f}"
        )

        return schedule
