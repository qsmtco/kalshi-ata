"""
position_tracker.py — Real-time position state management for K-ATA.

Maintains a local mirror of all open positions, updated every cycle.
Replaces reliance on repeated API calls for position state.
"""
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Any
from datetime import datetime, timezone
from typing import Optional
from config import PARTIAL_EXIT_TIERS, BARRIER_TP_BASE, BARRIER_TP_CONFIDENCE_SCALING, BARRIER_TP_CONFIDENCE_MAX

logger = logging.getLogger(__name__)


@dataclass
class Position:
    """
    Single position record. All monetary values in dollars.
    prices are in dollars (0.04 = 4 cents).
    """
    ticker: str
    event_id: str
    strategy: str
    side: str             # 'yes' or 'no'
    count: int            # number of contracts
    avg_fill_price: float  # average entry price per contract
    open_time: datetime
    last_updated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    current_price: float = 0.0
    signal_at_entry: float = 0.0
    # Default exit thresholds (can be overridden per-position)
    stop_loss_pct: float = 0.40   # exit if price drops 40% from entry
    take_profit_pct: float = 0.50  # exit if price rises 50% from entry

    # --- Step 1.4: Partial exit + advanced exits ---
    # Partial exit tracking
    exit_tiers: List[Dict[str, Any]] = field(default_factory=list)  # [{'threshold_mult': 1.20, 'qty_pct': 0.30, 'exited': False}, ...]
    remaining_count: int = 0         # tracks remaining contracts after partial exits
    initial_count: int = 0          # original count at entry

    # ATR trailing stop
    atr_trailing_stop: float = 0.0
    atr_multiplier: float = 3.0
    volatility: float = 0.0           # ATR value from market data (set each cycle)
    highest_price_since_entry: float = 0.0

    # Triple-barrier
    barrier_tp_multiplier: float = 1.50
    signal_confidence: float = 0.5

    # Volatility-time hybrid
    volatility_adjusted_tp_mult: float = 1.50

    # Barrier tracking
    barriers_triggered: List[str] = field(default_factory=list)
    barrier_hit_order: Optional[str] = None
    barrier_hit_time: Optional[datetime] = None

    @property
    def cost_basis(self) -> float:
        """Total cost to open this position."""
        return self.count * self.avg_fill_price

    @property
    def market_value(self) -> float:
        """Current market value if closed at current price."""
        return self.count * self.current_price

    @property
    def unrealized_pnl(self) -> float:
        """P&L if closed at current price (positive = profit)."""
        return self.market_value - self.cost_basis

    @property
    def unrealized_pnl_pct(self) -> float:
        """P&L as percentage of cost basis."""
        if self.cost_basis == 0:
            return 0.0
        return (self.unrealized_pnl / self.cost_basis) * 100

    @property
    def age_hours(self) -> float:
        """How long this position has been open."""
        return (datetime.now(timezone.utc) - self.open_time).total_seconds() / 3600

    @property
    def stop_loss_price(self) -> float:
        """Price at which stop loss triggers."""
        return self.avg_fill_price * (1 - self.stop_loss_pct)

    @property
    def take_profit_price(self) -> float:
        """Price at which take profit triggers."""
        return self.avg_fill_price * (1 + self.take_profit_pct)


class PositionTracker:
    """
    Tracks all open positions in memory. Updated every trading cycle.
    Syncs from API on startup to survive bot restarts.
    """

    def __init__(self):
        # ticker -> Position
        self._positions: dict[str, Position] = {}
        logger.info("PositionTracker initialized (empty)")

    # -------------------------------------------------------------------------
    # Read access
    # -------------------------------------------------------------------------

    def get_open_tickers(self) -> list[str]:
        """Tickers of all currently open positions."""
        return list(self._positions.keys())

    def get_position(self, ticker: str) -> Optional[Position]:
        """Get Position object for a ticker, or None."""
        return self._positions.get(ticker)

    def get_all_positions(self) -> list[Position]:
        """List of all open positions."""
        return list(self._positions.values())

    def total_exposure(self) -> float:
        """Total cost basis across all open positions."""
        return sum(p.cost_basis for p in self._positions.values())

    def total_unrealized_pnl(self) -> float:
        """Total unrealized P&L across all open positions."""
        return sum(p.unrealized_pnl for p in self._positions.values())

    # -------------------------------------------------------------------------
    # Write access
    # -------------------------------------------------------------------------

    def add_position(self, ticker: str, event_id: str, strategy: str,
                    side: str, count: int, avg_fill_price: float,
                    signal_confidence: float = 0.5,  # Step 5.1: for barrier_tp_multiplier
                    stop_loss_pct: float = 0.40,
                    take_profit_pct: float = 0.50) -> None:
        """
        Record a newly opened position. If already exists, averages into it.
        """
        now = datetime.now(timezone.utc)
        if ticker in self._positions:
            # Average into existing position
            pos = self._positions[ticker]
            total_cost = pos.avg_fill_price * pos.count + avg_fill_price * count
            new_count = pos.count + count
            pos.avg_fill_price = total_cost / new_count
            pos.count = new_count
            pos.last_updated = now
            pos.signal_at_entry = signal_confidence
            logger.info(f"Position increased: {ticker} -> {new_count} @ ${pos.avg_fill_price:.4f}")
        else:
            self._positions[ticker] = Position(
                ticker=ticker,
                event_id=event_id,
                strategy=strategy,
                side=side,
                count=count,
                avg_fill_price=avg_fill_price,
                open_time=now,
                last_updated=now,
                current_price=avg_fill_price,  # start at entry price
                # Step 6.4: Initialize peak to entry price (not 0.0)
                highest_price_since_entry=avg_fill_price,
                signal_at_entry=signal_confidence,
                stop_loss_pct=stop_loss_pct,
                take_profit_pct=take_profit_pct,
                # Step 3.1: Initialize partial exit tiers and counters
                exit_tiers=[
                    {'threshold_mult': mult, 'qty_pct': qty_pct, 'exited': False, 'exit_price': None}
                    for mult, qty_pct in PARTIAL_EXIT_TIERS
                ],
                remaining_count=count,
                initial_count=count,
            )
            pos = self._positions[ticker]
            # Step 5.1: Compute barrier_tp_multiplier from signal confidence
            # Linear interpolation: conf=0.0 → base_tp, conf=1.0 → confidence_max_tp
            pos.signal_confidence = max(0.0, min(1.0, signal_confidence))  # clamp to 0-1
            if BARRIER_TP_CONFIDENCE_SCALING:
                pos.barrier_tp_multiplier = min(
                    BARRIER_TP_CONFIDENCE_MAX,
                    BARRIER_TP_BASE + (BARRIER_TP_CONFIDENCE_MAX - BARRIER_TP_BASE) * pos.signal_confidence
                )
            else:
                pos.barrier_tp_multiplier = BARRIER_TP_BASE

            logger.info(f"Position opened: {ticker} — {count} @ ${avg_fill_price:.4f}")

    def update_price(self, ticker: str, current_price: float) -> None:
        """Update the current market price for a position."""
        if ticker in self._positions:
            self._positions[ticker].current_price = current_price
            self._positions[ticker].last_updated = datetime.now(timezone.utc)

    def update_highest_price(self, ticker: str, current_price: float) -> None:
        """
        Step 1.4 / 4.3: Update peak price tracked for ATR trailing stop.
        If current_price exceeds the tracked highest, update it and recompute the
        trailing stop level using the position's stored volatility.
        """
        if ticker not in self._positions:
            return
        pos = self._positions[ticker]
        if current_price > pos.highest_price_since_entry:
            pos.highest_price_since_entry = current_price
            # Recompute ATR trailing stop: stop = highest - (N × ATR)
            if pos.volatility and pos.volatility > 0:
                pos.atr_trailing_stop = pos.highest_price_since_entry - (pos.atr_multiplier * pos.volatility)

    def update_volatility_adjusted_tp(self, ticker: str, current_volatility: float,
                                       base_tp_mult: float = 1.50) -> None:
        """
        Step 4.3: Update the volatility-adjusted take-profit multiplier.
        Higher volatility → wider TP (more room to breathe).
        Formula: TP_mult = min(base_tp + (scalar × vol/price), max_cap)
        """
        if ticker not in self._positions:
            return
        pos = self._positions[ticker]
        vol_scalar = 0.3  # from config VOLATILITY_TP_SCALAR
        vol_tp_cap = 3.0  # from config VOLATILITY_TP_MAX
        price = pos.avg_fill_price
        vol_adjusted = base_tp_mult + (vol_scalar * current_volatility / price) if price > 0 else base_tp_mult
        pos.volatility_adjusted_tp_mult = min(vol_adjusted, vol_tp_cap)

    def reduce_position(self, ticker: str, count: int, reason: str = "") -> None:
        """Partially close a position."""
        if ticker not in self._positions:
            logger.warning(f"Tried to reduce non-existent position: {ticker}")
            return
        pos = self._positions[ticker]
        if count >= pos.count:
            self.close_position(ticker, reason=f"full_close: {reason}")
        elif hasattr(pos, 'remaining_count') and count >= pos.remaining_count:
            # If reducing by remaining_count or more, close the position
            self.close_position(ticker, reason=f"full_close: {reason}")
        else:
            # Partial exit: reduce remaining_count, but keep count (cost basis) unchanged
            # count = original position size for P&L
            # remaining_count = contracts still held (used for partial exit sizing)
            if hasattr(pos, 'remaining_count') and pos.remaining_count > 0:
                reduce_by = min(count, pos.remaining_count)
                pos.remaining_count = max(0, pos.remaining_count - reduce_by)
            pos.last_updated = datetime.now(timezone.utc)
            logger.info(f"Position reduced: {ticker} — sold {count}, "
                        f"{pos.remaining_count} remaining. Reason: {reason}")

    def close_position(self, ticker: str, reason: str = "") -> None:
        """Fully close a position."""
        if ticker in self._positions:
            pos = self._positions.pop(ticker)
            logger.info(
                f"Position closed: {ticker} — "
                f"{pos.count} @ ${pos.avg_fill_price:.4f} "
                f"-> ${pos.current_price:.4f} "
                f"pnl=${pos.unrealized_pnl:.2f} "
                f"reason={reason}"
            )
        else:
            logger.warning(f"Tried to close non-existent position: {ticker}")

    # -------------------------------------------------------------------------
    # API sync
    # -------------------------------------------------------------------------

    def sync_from_api(self, api_positions: list) -> int:
        """
        Rebuild local state from Kalshi API response on bot startup.
        Handles two formats:
          - market_positions: has 'ticker' + 'position_fp' (fixed-point count)
          - event_positions: has 'event_ticker' + 'total_cost_shares_fp'

        Returns number of positions restored.
        """
        self._positions.clear()
        restored = 0
        for p in api_positions:
            # Prefer market_positions (has proper ticker), fall back to event_positions
            ticker = p.get('ticker') or p.get('market_ticker') or p.get('event_ticker', '')
            if not ticker:
                continue

            # Count: try position_fp (market_positions) or total_cost_shares_fp (event_positions)
            count_raw = p.get('position_fp') or p.get('total_cost_shares_fp') or p.get('count', 0)
            try:
                # Accept int, str, or float for count_raw (Kalshi returns fixed-point ints)
                count = int(float(count_raw)) if isinstance(count_raw, (int, str, float)) else 0
            except (ValueError, TypeError):
                count = 0
            if count <= 0:
                continue

            # Avg price: total_traded_dollars / position_fp, or use avg_fill_price if available
            avg_price = 0.0
            total_traded = p.get('total_traded_dollars', 0)
            if total_traded and count:
                try:
                    avg_price = float(total_traded) / float(count)
                except (ValueError, TypeError):
                    pass
            if avg_price <= 0:
                avg_price = float(p.get('avg_fill_price', 0) or 0)
            if avg_price <= 0:
                continue

            self._positions[ticker] = Position(
                ticker=ticker,
                event_id=p.get('event_id', ticker),
                strategy=p.get('strategy', 'unknown'),
                side=p.get('side', 'yes'),
                count=count,
                avg_fill_price=avg_price,
                open_time=datetime.now(timezone.utc),
                last_updated=datetime.now(timezone.utc),
                current_price=avg_price,
            )
            restored += 1
        logger.info(f"PositionTracker synced from API: {restored} positions restored")
        return restored

    # -------------------------------------------------------------------------
    # Exit trigger helpers
    # -------------------------------------------------------------------------

    def check_stop_loss(self, ticker: str) -> tuple[bool, str]:
        """Returns (triggered, reason)."""
        pos = self._positions.get(ticker)
        if not pos:
            return False, ""
        if pos.current_price <= pos.stop_loss_price:
            return True, f"stop loss: ${pos.current_price:.4f} <= ${pos.stop_loss_price:.4f} (-{pos.stop_loss_pct:.0%})"
        return False, ""

    def check_take_profit(self, ticker: str) -> tuple[bool, str]:
        """Returns (triggered, reason)."""
        pos = self._positions.get(ticker)
        if not pos:
            return False, ""
        if pos.current_price >= pos.take_profit_price:
            return True, f"take profit: ${pos.current_price:.4f} >= ${pos.take_profit_price:.4f} (+{pos.take_profit_pct:.0%})"
        return False, ""

    def check_time_exit(self, ticker: str, max_hours: float = 24.0) -> tuple[bool, str]:
        """Returns (triggered, reason)."""
        pos = self._positions.get(ticker)
        if not pos:
            return False, ""
        if pos.age_hours >= max_hours:
            in_profit = "in profit" if pos.unrealized_pnl >= 0 else "not in profit"
            return True, f"time exit: {pos.age_hours:.1f}h old, {in_profit}"
        return False, ""
