#!/usr/bin/env python3
"""Enhanced market data module for Phase 3 - Kalshi trading bot."""

import logging
import time
import threading
import numpy as np
from typing import Dict, List, Optional, Any, Callable, Union
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import requests
from volatility_analyzer import VolatilityAnalyzer
from config import LIQUIDITY_MIN_BID_DOLLARS, LIQUIDITY_MIN_BID_QTY, LIQUIDITY_SPREAD_MAX

# Module constants
MAX_PRICE_HISTORY = 200  # Max entries in rolling price history
SERIES_DISCOVERY_INTERVAL = 1800  # seconds between full series rescans (30 min)
SERIES_MAX_PER_CYCLE = 15        # max series to fetch per market update cycle
SERIES_MARKET_LIMIT = 50         # markets to fetch per series per cycle

logger = logging.getLogger(__name__)


class SeriesDiscovery:
    """
    Discovers and caches sports/game series tickers from Kalshi.

    The general /markets endpoint returns illiquid multivolume products.
    Real game winner markets (NBA, MLB, NFL, etc.) are only accessible
    via series-scoped queries: /markets?series_ticker=KXNBAGAME.

    This class maintains a live list of sports-series tickers by scanning
    the full series catalog and filtering on ticker/title keywords.
    Series don't change often — we cache for SERIES_DISCOVERY_INTERVAL seconds.
    """

    SPORTS_TICKER_MARKERS = [
        'NBAGAME', 'MLBGAME', 'NHLGAME', 'NFL', 'SOCCER', 'TENNIS',
        'UFC', 'MMA', 'BOXING', 'GOLF', 'NCAAB', 'NCAAF',
        'WNBA', 'MLS', 'EUROLEAGUE', 'FIBA', 'Wimbledon',
    ]
    SPORTS_TITLE_MARKERS = [
        'nba', 'nfl', 'nhl', 'mlb', 'soccer', 'tennis',
        'ufc', 'mma', 'boxing', 'golf', 'college basketball',
        'college football', 'wnba', 'major league soccer',
    ]

    def __init__(self, api_client):
        self.api_client = api_client
        self._series_cache: Dict[str, dict] = {}
        self._last_discovery_ts: Optional[datetime] = None

    def discover(self, force: bool = False) -> List[dict]:
        """Return cached sports series, refreshing if stale."""
        now = datetime.now()
        stale = (
            self._last_discovery_ts is None
            or (now - self._last_discovery_ts).total_seconds() > SERIES_DISCOVERY_INTERVAL
        )
        if not force and not stale:
            return list(self._series_cache.values())
        all_series = self._fetch_all_series()
        sports = [s for s in all_series if self._is_sports_series(s)]
        self._series_cache = {s['ticker']: s for s in sports}
        self._last_discovery_ts = now
        logger.info("SeriesDiscovery: refreshed — %d total, %d sports",
                    len(all_series), len(sports))
        return sports

    def _fetch_all_series(self) -> List[dict]:
        all_series = []
        cursor = None
        while True:
            params = {'limit': 100}
            if cursor:
                params['cursor'] = cursor
            resp = self.api_client.get_series(params)
            batch = (resp or {}).get('series', [])
            all_series.extend(batch)
            cursor = (resp or {}).get('cursor')
            if not cursor:
                break
            time.sleep(0.2)
        return all_series

    def _is_sports_series(self, series: dict) -> bool:
        ticker = series.get('ticker', '').upper()
        title = series.get('title', '').lower()
        return (
            any(m in ticker for m in self.SPORTS_TICKER_MARKERS)
            or any(m in title for m in self.SPORTS_TITLE_MARKERS)
        )


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

    # --- Step 1.5: Liquidity fields for order book monitoring ---
    yes_bid: Optional[float] = None       # Best bid price (dollars)
    yes_ask: Optional[float] = None       # Best ask price (dollars)
    yes_bid_qty: Optional[int] = None   # Contracts available at best bid
    yes_ask_qty: Optional[int] = None   # Contracts available at best ask
    spread_pct: Optional[float] = None   # (ask - bid) / ask, 0 to 1

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
        self.volatility_analyzer = VolatilityAnalyzer()
        self.series_discovery = SeriesDiscovery(api_client)
        self._fetch_lock = threading.Lock()  # guard concurrent _update_market_data calls

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

    def _fetch_markets_from_series(self) -> List[dict]:
        """
        Fetch liquid game markets from sports series.

        The general /markets endpoint returns illiquid multivolume products.
        Real NBA/MLB/NFL game markets only appear via series-scoped queries.

        We sort series to prioritize GAME-series first (most trading activity).
        This matters because SERIES_MAX_PER_CYCLE limits how many we can fetch per cycle.

        Returns:
            List of raw market dicts (Kalshi API format).
        """
        sports_series = self.series_discovery.discover()
        if not sports_series:
            return []

        def series_priority(s: dict) -> tuple:
            ticker = s.get('ticker', '').upper()
            return (0 if 'GAME' in ticker else 1, ticker)

        sorted_series = sorted(sports_series, key=series_priority)
        all_markets = []
        for series in sorted_series[:SERIES_MAX_PER_CYCLE]:
            try:
                resp = self.api_client.get_markets_by_series(
                    series['ticker'],
                    params={'status': 'open', 'limit': SERIES_MARKET_LIMIT}
                )
                markets = (resp or {}).get('markets', [])
                all_markets.extend(markets)
                time.sleep(0.15)
            except Exception as e:
                logger.warning("Failed to fetch markets for series %s: %s",
                              series['ticker'], e)
        return all_markets

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
                # Always parse bid/ask floats so they're available for liquidity fields
                bid_f = float(bid_r) if bid_r else None
                ask_f = float(ask_r) if ask_r else None
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
                    if len(md.price_history) > MAX_PRICE_HISTORY:
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

                # Step 1.5: Populate liquidity fields from the raw market dict
                md.yes_bid = bid_f
                md.yes_ask = ask_f
                md.yes_bid_qty = self._parse_int(mkt.get('yes_bid_qty'))
                md.yes_ask_qty = self._parse_int(mkt.get('yes_ask_qty'))
                # Compute spread_pct: (ask - bid) / ask
                if bid_f is not None and ask_f is not None and ask_f > 0:
                    md.spread_pct = (ask_f - bid_f) / ask_f
                else:
                    md.spread_pct = None

                # ATR-based volatility via VolatilityAnalyzer (Step 1.3)
                # Requires 14+ price points for ATR(period=14)
                if len(md.price_history) > 13:
                    md.volatility = self.volatility_analyzer.calculate_atr(md.price_history, period=14)

                updated.append(ticker)

            self.last_update = datetime.now()
            if updated:
                self._notify_subscribers(updated)
                logger.debug("Updated %d markets", len(updated))

        except Exception as e:
            logger.error("Failed to update market data: %s", e)

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _parse_int(self, value) -> Optional[int]:
        """
        Step 1.5: Safely parse an int from various input types.
        Handles None, int, float, and string.
        """
        if value is None:
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value) if not (value != value) else None  # NaN check
        if isinstance(value, str):
            try:
                return int(float(value))
            except (ValueError, TypeError):
                return None
        return None

    def is_market_liquid(self, market_md: 'MarketData') -> tuple[bool, str]:
        """
        Step 2.1: Check if a market has sufficient liquidity for trading.
        Returns (True, '') if liquid — returns (False, 'reason') if not.
        Uses config thresholds: min bid, min bid qty, max spread, min ask.
        """
        if market_md.yes_bid is None or market_md.yes_bid < LIQUIDITY_MIN_BID_DOLLARS:
            return False, f"bid_too_low_{market_md.yes_bid}"

        if market_md.yes_bid_qty is not None and market_md.yes_bid_qty < LIQUIDITY_MIN_BID_QTY:
            return False, f"insufficient_bid_qty_{market_md.yes_bid_qty}"

        if market_md.spread_pct is not None and market_md.spread_pct > LIQUIDITY_SPREAD_MAX:
            return False, f"spread_too_wide_{market_md.spread_pct:.1%}"

        if market_md.yes_ask is None or market_md.yes_ask == 0:
            return False, "no_ask"

        return True, ""

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
