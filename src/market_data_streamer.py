#!/usr/bin/env python3
"""Enhanced market data module for Phase 3 - Kalshi trading bot."""

import logging
import time
import threading
import numpy as np
from typing import Dict, List, Optional, Any, Callable, Union
from dataclasses import dataclass
from datetime import datetime, timedelta
import requests

logger = logging.getLogger(__name__)


@dataclass
class MarketData:
    """Structured market data container."""
    market_id: str
    title: str
    current_price: float
    previous_price: Optional[float] = None
    volume: Optional[int] = None
    open_interest: Optional[int] = None
    last_updated: Optional[datetime] = None
    price_history: Optional[List[float]] = None
    volatility: Optional[float] = None
    close_date: Optional[str] = None  # ISO8601 timestamp from Kalshi API

    def __post_init__(self):
        if self.last_updated is None:
            self.last_updated = datetime.now()
        if self.price_history is None:
            self.price_history = []

    @property
    def price_change(self) -> Optional[float]:
        """Price change from previous price."""
        if self.previous_price is None:
            return None
        return self.current_price - self.previous_price

    @property
    def price_change_pct(self) -> Optional[float]:
        """Percentage price change."""
        if self.previous_price is None or self.previous_price == 0:
            return None
        return (self.price_change / self.previous_price) * 100


class MarketDataStreamer:
    """
    Enhanced market data streaming and management for Kalshi.
    Handles both raw dict responses (from kalshi_api) and structured MarketData objects.
    Phase 1 adds: get_current_price(), is_market_liquid()
    """

    def __init__(self, api_client, update_interval: int = 30):
        self.api_client = api_client
        self.update_interval = update_interval
        self.markets_data: Dict[str, MarketData] = {}
        self.subscribers: List[Callable] = []
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.last_update = datetime.now()

    # -------------------------------------------------------------------------
    # Phase 1 helpers: price extraction + liquidity check
    # -------------------------------------------------------------------------

    def get_current_price(self, market_or_ticker) -> float:
        """
        Extract the best available price from a MarketData object or raw market dict.

        Args:
            market_or_ticker: MarketData object, dict, or ticker string.
                              If string, looks up in self.markets_data.

        Returns:
            float price in dollars, or 0.0 if unavailable.
        """
        # Support ticker lookup
        if isinstance(market_or_ticker, str):
            md = self.markets_data.get(market_or_ticker)
            if md:
                return md.current_price or 0.0
            return 0.0

        # MarketData dataclass object
        if hasattr(market_or_ticker, 'current_price'):
            return market_or_ticker.current_price or 0.0

        # Raw dict from Kalshi API
        if isinstance(market_or_ticker, dict):
            # Kalshi API field names (confirmed from live API):
            # yes_bid_dollars, yes_ask_dollars, last_price_dollars (strings like "0.4500")
            bid = market_or_ticker.get('yes_bid_dollars') or market_or_ticker.get('yes_bid')
            ask = market_or_ticker.get('yes_ask_dollars') or market_or_ticker.get('yes_ask')
            last = market_or_ticker.get('last_price_dollars') or market_or_ticker.get('last_price')

            if bid:
                try:
                    bid_f = float(bid) if isinstance(bid, str) else bid
                    ask_f = float(ask) if isinstance(ask, str) and ask else None
                    if ask_f and ask_f > 0:
                        return (bid_f + ask_f) / 2.0  # mid price
                    return bid_f  # bid-only fallback
                except (ValueError, TypeError):
                    pass

            if last:
                try:
                    return float(last) if isinstance(last, str) else last
                except (ValueError, TypeError):
                    pass

            return 0.0

        return 0.0

    def is_market_liquid(self, market_or_ticker, min_spread_pct: float = 0.15) -> tuple[bool, dict]:
        """
        Returns (is_liquid: bool, details: dict).
        Rejects markets with zero/no bid, or spread wider than min_spread_pct (default 15%%).

        Works with MarketData objects (assumes liquid), raw dicts, or ticker strings.
        """
        details = {'bid': None, 'ask': None, 'spread_pct': None, 'mid': None}

        # Resolve ticker string to actual data
        if isinstance(market_or_ticker, str):
            md = self.markets_data.get(market_or_ticker)
            if md is None:
                return False, {'reason': 'ticker_not_found', **details}
            # MarketData dataclass doesn't have bid/ask — assume liquid based on price
            return True, {'bid': md.current_price, 'ask': None, 'spread_pct': 0.0,
                          'mid': md.current_price, 'source': 'dataclass'}

        # MarketData dataclass — assume liquid
        if hasattr(market_or_ticker, 'current_price'):
            p = market_or_ticker.current_price or 0.0
            return True, {'bid': p, 'ask': None, 'spread_pct': 0.0,
                          'mid': p, 'source': 'dataclass'}

        # Raw dict from Kalshi API
        if isinstance(market_or_ticker, dict):
            bid_raw = market_or_ticker.get('yes_bid_dollars') or market_or_ticker.get('yes_bid')
            ask_raw = market_or_ticker.get('yes_ask_dollars') or market_or_ticker.get('yes_ask')

            if not bid_raw:
                return False, {'reason': 'no_bid', **details}

            try:
                bid_f = float(bid_raw) if isinstance(bid_raw, str) else bid_raw
                ask_f = float(ask_raw) if isinstance(ask_raw, str) and ask_raw else None
            except (ValueError, TypeError):
                return False, {'reason': 'invalid_price', 'bid': bid_raw, **details}

            if bid_f <= 0:
                return False, {'reason': 'bid_zero_or_negative', 'bid': bid_f, **details}

            if ask_f and ask_f > 0:
                spread_pct = (ask_f - bid_f) / ask_f
                mid = (bid_f + ask_f) / 2.0
            else:
                spread_pct = 0.0
                mid = bid_f

            details = {'bid': bid_f, 'ask': ask_f, 'spread_pct': spread_pct, 'mid': mid}

            if spread_pct > min_spread_pct:
                return False, {'reason': f'spread_too_wide_{spread_pct:.1%}', **details}

            return True, details

        return False, {'reason': 'unknown_format', **details}

    # -------------------------------------------------------------------------
    # Subscriber pattern
    # -------------------------------------------------------------------------

    def add_subscriber(self, callback: Callable):
        """Register a callback to receive market data updates."""
        self.subscribers.append(callback)

    def remove_subscriber(self, callback: Callable):
        """Remove a registered callback."""
        if callback in self.subscribers:
            self.subscribers.remove(callback)

    def _notify_subscribers(self, updated_ids: List[str]):
        """Send update notification to all subscribers."""
        for cb in self.subscribers:
            try:
                cb(updated_ids, self.markets_data)
            except Exception as e:
                logger.error("Subscriber callback error: %s", e)

    # -------------------------------------------------------------------------
    # Streaming lifecycle
    # -------------------------------------------------------------------------

    def start_streaming(self):
        """Start background refresh thread."""
        if self.running:
            logger.warning("Streaming already running")
            return
        self.running = True
        # Initial synchronous fetch so first cycle has data
        try:
            self._update_market_data()
            logger.info("Initial fetch complete: %d markets", len(self.markets_data))
        except Exception as e:
            logger.error("Initial fetch failed: %s", e)
        self.thread = threading.Thread(target=self._streaming_loop, daemon=True)
        self.thread.start()
        logger.info("Streaming started (interval=%ds)", self.update_interval)

    def stop_streaming(self):
        """Stop background refresh thread."""
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5)
        logger.info("Streaming stopped")

    def _streaming_loop(self):
        """Background thread: refresh data at update_interval seconds."""
        while self.running:
            try:
                self._update_market_data()
            except Exception as e:
                logger.error("Streaming loop error: %s", e)
            time.sleep(self.update_interval)

    # -------------------------------------------------------------------------
    # Data fetching
    # -------------------------------------------------------------------------

    def _update_market_data(self):
        """
        Fetch latest market list from Kalshi API and update internal state.
        Handles two response formats:
          - dict: {'markets': [...]}  (kalshi_api.py)
          - list: [...]               (future SDK)
        """
        try:
            raw = self.api_client.get_markets()
            markets_list = []
            if isinstance(raw, dict):
                markets_list = raw.get('markets', [])
            elif isinstance(raw, list):
                markets_list = raw
            else:
                logger.warning("Unknown markets response type: %s", type(raw).__name__)
                return

            if not markets_list:
                logger.warning("Empty markets response")
                return

            updated = []

            for mkt in markets_list[:20]:  # Rate-limit to first 20
                ticker = mkt.get('ticker')
                if not ticker:
                    continue

                # Compute mid price from bid-ask, fall back to last_price
                # Same logic as get_current_price() — ensures we don't skip last_price-only markets
                bid_r = mkt.get('yes_bid_dollars') or mkt.get('yes_bid')
                ask_r = mkt.get('yes_ask_dollars') or mkt.get('yes_ask')
                last_r = mkt.get('last_price_dollars') or mkt.get('last_price')
                price = None
                if bid_r:
                    try:
                        bid_f = float(bid_r) if isinstance(bid_r, str) else bid_r
                        ask_f = float(ask_r) if isinstance(ask_r, str) and ask_r else None
                        if ask_f and ask_f > 0:
                            price = (bid_f + ask_f) / 2.0
                        else:
                            price = bid_f
                    except (ValueError, TypeError):
                        pass
                elif last_r:
                    # No bid — fall back to last_price so we don't skip last_price-only markets
                    try:
                        price = float(last_r) if isinstance(last_r, str) else last_r
                    except (ValueError, TypeError):
                        pass

                if not price or price == 0:
                    continue

                if ticker in self.markets_data:
                    md = self.markets_data[ticker]
                    md.previous_price = md.current_price
                    md.current_price = price
                    md.last_updated = datetime.now()
                    md.price_history.append(price)
                    if len(md.price_history) > 100:
                        md.price_history.pop(0)
                else:
                    md = MarketData(
                        market_id=ticker,
                        title=mkt.get('title', ''),
                        current_price=price,
                        volume=mkt.get('volume'),
                        open_interest=None,
                        price_history=[price],
                        close_date=mkt.get('close_date') or mkt.get('market_close'),
                    )
                    self.markets_data[ticker] = md

                # Annualized volatility from rolling returns
                if len(md.price_history) > 10:
                    recent = md.price_history[-20:]
                    if len(recent) > 1:
                        returns = [recent[i+1]/recent[i] - 1 for i in range(len(recent)-1)]
                        md.volatility = float(np.std(returns) * np.sqrt(252))

                updated.append(ticker)

            self.last_update = datetime.now()
            if updated:
                self._notify_subscribers(updated)
                logger.debug("Updated %d markets", len(updated))

        except Exception as e:
            logger.error("Failed to update market data: %s", e)

    # -------------------------------------------------------------------------
    # Data access
    # -------------------------------------------------------------------------

    def get_market_data(self, market_id: str) -> Optional[MarketData]:
        """Get MarketData object for a specific ticker."""
        return self.markets_data.get(market_id)

    def get_all_markets_data(self) -> Dict[str, MarketData]:
        """Get snapshot of all tracked markets."""
        return self.markets_data.copy()

    def get_top_movers(self, limit: int = 5) -> List[MarketData]:
        """Markets with largest absolute price change %%."""
        movers = [m for m in self.markets_data.values() if m.price_change_pct is not None]
        movers.sort(key=lambda m: abs(m.price_change_pct), reverse=True)
        return movers[:limit]

    def get_high_volatility_markets(self, limit: int = 5) -> List[MarketData]:
        """Markets with highest annualized volatility."""
        vol = [m for m in self.markets_data.values() if m.volatility is not None]
        vol.sort(key=lambda m: m.volatility, reverse=True)
        return vol[:limit]

    def get_market_summary(self) -> Dict[str, Any]:
        """Summary statistics across all tracked markets."""
        if not self.markets_data:
            return {'total_markets': 0}
        markets = list(self.markets_data.values())
        avg_price = np.mean([m.current_price for m in markets])
        avg_vol = [m.volatility for m in markets if m.volatility]
        gainers = sum(1 for m in markets if m.price_change_pct and m.price_change_pct > 0)
        losers = sum(1 for m in markets if m.price_change_pct and m.price_change_pct < 0)
        return {
            'total_markets': len(markets),
            'average_price': float(avg_price),
            'average_volatility': float(np.mean(avg_vol)) if avg_vol else None,
            'gainers': gainers,
            'losers': losers,
            'unchanged': len(markets) - gainers - losers,
            'last_update': self.last_update.isoformat(),
            'update_interval': self.update_interval,
        }
