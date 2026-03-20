#!/usr/bin/env python3
"""
Order Tracker — Tracks open orders, fills, and requoting for K-ATA market making.

Tracks the lifecycle of every bid/ask quote posted:
  1. OPEN       → quote posted, waiting for fill
  2. FILLED     → counterparty hit our quote
  3. PARTIAL    → partially filled
  4. CANCELLED  → we cancelled (stale, or VPIN spike)
  5. EXPIRED    → TTL exceeded without fill

Key metrics computed:
  - Fill rate per ticker
  - Average fill price vs mid-price (execution quality)
  - Spread captured (bid-ask midpoint - our fill price)
  - Time-to-fill distribution

Usage:
    tracker = OrderTracker()
    tracker.track_bid(ticker="KXSEC...", order_id="abc123", price=0.54, qty=100)
    tracker.track_ask(ticker="KXSEC...", order_id="def456", price=0.56, qty=100)
    # ... later ...
    fills = tracker.check_fills(market_maker)  # updates from live API
    tracker.compute_metrics()  # → P&L, fill rate, etc.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class TrackedOrder:
    """A single order being tracked through its lifecycle."""
    order_id:    str
    ticker:      str
    side:       str          # "bid" or "ask"
    price:       float        # our quoted price
    quantity:    float        # original quantity
    filled:     float = 0.0 # filled quantity so far
    status:      str  = "open"  # open | filled | cancelled | expired
    opened_at:   float = field(default_factory=time.time)
    filled_at:   Optional[float] = None
    last_check:  float = field(default_factory=time.time)

    def fill(self, qty: float, price: float) -> None:
        """Record a fill for this order."""
        self.filled += qty
        self.last_check = time.time()
        if self.filled >= self.quantity:
            self.status = "filled"
            self.filled_at = time.time()
            logger.info(
                f"OrderTracker: {self.side.upper()} {self.ticker} "
                f"FILLED {qty}@{price} (total={self.filled}/{self.quantity})"
            )
        else:
            self.status = "partial"
            logger.info(
                f"OrderTracker: {self.side.upper()} {self.ticker} "
                f"PARTIAL fill {qty}@{price} ({self.filled}/{self.quantity})"
            )

    def cancel(self) -> None:
        """Cancel this order."""
        self.status = "cancelled"
        self.last_check = time.time()
        logger.info(f"OrderTracker: {self.side.upper()} {self.ticker} CANCELLED")

    def is_stale(self, max_age_seconds: float = 60.0) -> bool:
        """Check if order has been open too long without fill."""
        return (time.time() - self.opened_at) > max_age_seconds


# ---------------------------------------------------------------------------
# Order Tracker
# ---------------------------------------------------------------------------

class OrderTracker:
    """
    Tracks open orders per ticker and computes fill/P&L metrics.

    Integrates with KalshiMarketMaker's `open_market_making_position()`:
      1. Call market_maker.open_market_making_position(bid_qty, ask_qty, ...)
      2. tracker.track_bid(ticker, order_id, bid_price, qty)
      3. tracker.track_ask(ticker, order_id, ask_price, qty)
      4. On each cycle: tracker.check_fills(market_maker) → updates fills

    Usage in trading loop:
        tracker = OrderTracker()
        tracker.track_bid(ticker, order_id, price, qty)
        tracker.track_ask(ticker, order_id, price, qty)
        # In next cycle:
        tracker.check_fills(market_maker)   # updates from API
        tracker.expire_stale_orders(max_age=60)  # cancel old orders
        metrics = tracker.get_metrics(ticker)   # P&L, fill rate, etc.
    """

    def __init__(self, stale_threshold_seconds: float = 60.0):
        """
        Args:
            stale_threshold_seconds: Age after which an open order is considered stale (default 60s)
        """
        self.stale_threshold = stale_threshold_seconds
        # ticker -> {"bid": TrackedOrder or None, "ask": TrackedOrder or None}
        self._open_orders: Dict[str, Dict[str, Optional[TrackedOrder]]] = {}
        # All historical fills for metrics computation
        self._fills: List[Dict[str, Any]] = []

    # -------------------------------------------------------------------------
    # Tracking
    # -------------------------------------------------------------------------

    def track_bid(
        self,
        ticker: str,
        order_id: str,
        price: float,
        quantity: float,
    ) -> TrackedOrder:
        """
        Track a newly placed bid (buy YES) order.

        Cancels any existing open bid for this ticker first (one bid per ticker).
        """
        self._ensure_ticker(ticker)
        # Cancel existing open bid
        if self._open_orders[ticker]["bid"] is not None:
            old = self._open_orders[ticker]["bid"]
            if old.status == "open":
                old.cancel()
        order = TrackedOrder(
            order_id=order_id,
            ticker=ticker,
            side="bid",
            price=price,
            quantity=quantity,
        )
        self._open_orders[ticker]["bid"] = order
        logger.debug(f"OrderTracker: tracking BID {ticker} {order_id} {quantity}@{price}")
        return order

    def track_ask(
        self,
        ticker: str,
        order_id: str,
        price: float,
        quantity: float,
    ) -> TrackedOrder:
        """
        Track a newly placed ask (sell YES) order.

        Cancels any existing open ask for this ticker first.
        """
        self._ensure_ticker(ticker)
        if self._open_orders[ticker]["ask"] is not None:
            old = self._open_orders[ticker]["ask"]
            if old.status == "open":
                old.cancel()
        order = TrackedOrder(
            order_id=order_id,
            ticker=ticker,
            side="ask",
            price=price,
            quantity=quantity,
        )
        self._open_orders[ticker]["ask"] = order
        logger.debug(f"OrderTracker: tracking ASK {ticker} {order_id} {quantity}@{price}")
        return order

    def get_open_orders(self, ticker: str) -> Dict[str, Optional[TrackedOrder]]:
        """Return open bid/ask for a ticker."""
        self._ensure_ticker(ticker)
        return self._open_orders[ticker]

    # -------------------------------------------------------------------------
    # Fill checking
    # -------------------------------------------------------------------------

    def check_fills(self, market_maker) -> List[Dict[str, Any]]:
        """
        Check open orders against the market maker's fill status and update.

        Calls market_maker.get_order_status(order_id) for each open order
        to detect fills. Updates order status and records fill events.

        Args:
            market_maker: Object with get_order_status(order_id) method.
                          Expected to return: {"filled": float, "status": str}

        Returns:
            List of fill events [{"ticker", "side", "qty", "price", "filled_at"}, ...]
        """
        new_fills = []

        for ticker, sides in self._open_orders.items():
            for side, order in sides.items():
                if order is None or order.status != "open":
                    continue

                try:
                    status = market_maker.get_order_status(order.order_id)
                except Exception as e:
                    logger.warning(f"OrderTracker: get_order_status failed for {order.order_id}: {e}")
                    continue

                order.last_check = time.time()
                filled_qty = status.get("filled", 0.0)
                order_status = status.get("status", "").lower()

                if filled_qty > 0:
                    fill_price = status.get("price", order.price)
                    order.fill(filled_qty, fill_price)
                    if order.status == "filled":
                        self._fills.append({
                            "ticker": ticker,
                            "side": side,
                            "order_id": order.order_id,
                            "qty": filled_qty,
                            "price": fill_price,
                            "our_price": order.price,
                            "filled_at": order.filled_at,
                        })
                        new_fills.append(self._fills[-1])

                # Check for external cancellations
                if order_status in ("cancelled", "expired", "failed"):
                    order.cancel()

        return new_fills

    def expire_stale_orders(self, max_age: Optional[float] = None) -> List[TrackedOrder]:
        """
        Mark open orders older than max_age as cancelled.

        Returns list of expired orders.
        """
        max_age = max_age or self.stale_threshold
        expired = []
        for ticker, sides in self._open_orders.items():
            for side, order in sides.items():
                if order is None:
                    continue
                if order.status == "open" and order.is_stale(max_age):
                    order.cancel()
                    expired.append(order)
                    logger.info(
                        f"OrderTracker: EXPIRED {order.side.upper()} "
                        f"{ticker} after {time.time() - order.opened_at:.0f}s without fill"
                    )
        return expired

    # -------------------------------------------------------------------------
    # Metrics
    # -------------------------------------------------------------------------

    def get_metrics(self, ticker: str) -> Dict[str, Any]:
        """
        Compute fill/P&L metrics for a ticker.

        Returns:
            {
                "n_bids": int,       # total bid orders
                "n_asks": int,      # total ask orders
                "n_fills": int,     # total fills
                "fill_rate": float, # fills / (bids + asks)
                "avg_bid_fill_price": float,
                "avg_ask_fill_price": float,
                "spread_captured_pct": float,  # vs mid at fill time
                "open_bids": int,
                "open_asks": int,
            }
        """
        self._ensure_ticker(ticker)
        ticker_fills = [f for f in self._fills if f["ticker"] == ticker]
        bids = [o for o in self._get_all_orders(ticker) if o and o.side == "bid"]
        asks = [o for o in self._get_all_orders(ticker) if o and o.side == "ask"]
        open_bids = [o for o in bids if o and o.status == "open"]
        open_asks = [o for o in asks if o and o.status == "open"]

        n_fills = len(ticker_fills)
        total_orders = len(bids) + len(asks)
        fill_rate = n_fills / total_orders if total_orders > 0 else 0.0

        bid_fills = [f for f in ticker_fills if f["side"] == "bid"]
        ask_fills = [f for f in ticker_fills if f["side"] == "ask"]

        avg_bid_fill = (
            sum(f["price"] for f in bid_fills) / len(bid_fills)
            if bid_fills else 0.0
        )
        avg_ask_fill = (
            sum(f["price"] for f in ask_fills) / len(ask_fills)
            if ask_fills else 0.0
        )

        # Spread captured: for a filled bid at $0.54 on a market with mid=$0.55,
        # we bought below mid = good execution
        spread_captured = 0.0
        if avg_bid_fill > 0 and avg_ask_fill > 0:
            spread_captured = (avg_ask_fill - avg_bid_fill) * 100

        return {
            "ticker": ticker,
            "n_bids": len(bids),
            "n_asks": len(asks),
            "n_fills": n_fills,
            "fill_rate": round(fill_rate, 4),
            "avg_bid_fill_price": round(avg_bid_fill, 4),
            "avg_ask_fill_price": round(avg_ask_fill, 4),
            "spread_captured_pct": round(spread_captured, 4),
            "open_bids": len(open_bids),
            "open_asks": len(open_asks),
        }

    def get_all_metrics(self) -> Dict[str, Dict[str, Any]]:
        """Compute metrics for all tickers."""
        tickers = set(self._open_orders.keys())
        return {t: self.get_metrics(t) for t in tickers}

    # -------------------------------------------------------------------------
    # Internal
    # -------------------------------------------------------------------------

    def _ensure_ticker(self, ticker: str) -> None:
        if ticker not in self._open_orders:
            self._open_orders[ticker] = {"bid": None, "ask": None}

    def _get_all_orders(self, ticker: str) -> List[Optional[TrackedOrder]]:
        self._ensure_ticker(ticker)
        return [self._open_orders[ticker]["bid"], self._open_orders[ticker]["ask"]]
