import logging
import time
import os
import requests

from typing import Any, Dict, List, Optional
from config import (
    KALSHI_API_KEY,
    KALSHI_API_BASE_URL,
    MAX_RETRIES,
    RETRY_DELAY_SECONDS,
)

# Demo vs production URL per Kalshi docs: https://docs.kalshi.com/getting_started/demo_env
KALSHI_DEMO_URL = "https://demo-api.kalshi.co/trade-api/v2"
KALSHI_PROD_URL = "https://api.elections.kalshi.com/trade-api/v2"

class KalshiAPI:
    def __init__(
        self,
        api_key=None,
        base_url=None,
        max_retries=MAX_RETRIES,
        retry_delay=RETRY_DELAY_SECONDS,
    ):
        self.api_key = api_key or KALSHI_API_KEY
        
        # Check KALSHI_DEMO_MODE env var for demo vs production URL
        # Docs: https://docs.kalshi.com/getting_started/demo_env
        demo_mode = os.environ.get('KALSHI_DEMO_MODE', 'true').lower() == 'true'
        if base_url:
            self.base_url = base_url  # Explicit override
        elif demo_mode:
            self.base_url = KALSHI_DEMO_URL
            logging.info(f"KalshiAPI: Using DEMO mode ({KALSHI_DEMO_URL})")
        else:
            self.base_url = KALSHI_API_BASE_URL or KALSHI_PROD_URL
        
        self.logger = logging.getLogger(__name__)
        self.max_retries = max_retries
        self.retry_delay = retry_delay

    def _handle_request(self, method, endpoint, **kwargs):
        url = f"{self.base_url}{endpoint}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        attempt = 0
        backoff = self.retry_delay

        while attempt < self.max_retries:
            try:
                response = requests.request(method, url, headers=headers, **kwargs)
                response.raise_for_status()
                if response.content:
                    return response.json()
                return {}
            except requests.exceptions.HTTPError as http_err:
                status_code = getattr(http_err.response, "status_code", None)
                if status_code and 400 <= status_code < 500:
                    self.logger.error(
                        f"Non-retriable HTTP error ({status_code}) for {endpoint}: {http_err}"
                    )
                    break
                self.logger.warning(
                    f"HTTP error ({status_code}) on attempt {attempt + 1}/{self.max_retries} "
                    f"for {endpoint}: {http_err}. Retrying in {backoff}s."
                )
            except requests.exceptions.RequestException as req_err:
                self.logger.warning(
                    f"Request exception on attempt {attempt + 1}/{self.max_retries} "
                    f"for {endpoint}: {req_err}. Retrying in {backoff}s."
                )

            attempt += 1
            if attempt < self.max_retries:
                time.sleep(backoff)
                backoff *= 2

        self.logger.error(
            f"Failed to complete request to {endpoint} after {self.max_retries} attempts."
        )
        return None

    # ---- Exchange endpoints ----
    def get_exchange_status(self):
        return self._handle_request("GET", "/exchange/status")

    def get_exchange_announcements(self):
        return self._handle_request("GET", "/exchange/announcements")

    # ---- Market & event data ----
    def get_markets(self, params=None):
        return self._handle_request("GET", "/markets", params=params or {})

    def get_market(self, market_ticker, params=None):
        return self._handle_request(
            "GET", f"/markets/{market_ticker}", params=params or {}
        )

    def get_events(self, params=None):
        return self._handle_request("GET", "/events", params=params or {})

    def get_trades(self, params=None):
        """
        Fetch trades for a market (or all markets if no ticker filter).
        Paginated — call repeatedly with cursor until cursor is empty.

        Args:
            params: dict with optional keys:
                ticker (str)     — filter by market ticker
                min_ts (int)     — Unix timestamp, filter trades after this
                max_ts (int)     — Unix timestamp, filter trades before this
                limit (int)      — 1-1000, default 100
                cursor (str)     — pagination cursor from previous response

        Returns:
            {"trades": [...], "cursor": "..."} or None on failure.
        """
        return self._handle_request("GET", "/markets/trades", params=params or {})

    def get_orderbook(self, ticker: str, depth: int = 10) -> Optional[Dict[str, Any]]:
        """
        Fetch the current L2 order book for a Kalshi market.

        In Kalshi binary markets, the orderbook shows BIDS ONLY.
        A YES bid at $0.07 is equivalent to a NO ask at $0.93,
        so no_asks = mirror of yes_bids and vice versa.

        Response shape:
            {
              "orderbook_fp": {
                "yes_bids": [{"price_dollars": float, "quantity": float, "count": int}, ...],
                "no_bids":  [{"price_dollars": float, "quantity": float, "count": int}, ...],
                "yes_asks": [],
                "no_asks":  [],
                "last_yes_bid": float,
                "last_no_bid":  float,
              }
            }

        Args:
            ticker: Market ticker (e.g., "KXSECPRESSMENTION-25MAR20-PHONECALL")
            depth:  Number of price levels to return (1-100, 0=all). Default 10.

        Returns:
            Orderbook dict or None on failure.
        """
        return self._handle_request(
            "GET",
            f"/markets/{ticker}/orderbook",
            params={"depth": depth} if depth > 0 else {},
        )

    # ---- Portfolio endpoints ----
    def get_account_balance(self):
        return self._handle_request("GET", "/portfolio/balance")

    def get_positions(self, params=None):
        return self._handle_request("GET", "/portfolio/positions", params=params or {})

    def get_orders(self, params=None):
        return self._handle_request("GET", "/portfolio/orders", params=params or {})

    def create_order(self, order_payload):
        return self._handle_request("POST", "/portfolio/orders", json=order_payload)

    def cancel_order(self, order_id):
        return self._handle_request("DELETE", f"/portfolio/orders/{order_id}")

    # ---- Backwards-compatible helpers ----
    def fetch_market_data(self, params=None):
        """Legacy alias for get_markets used elsewhere in the bot."""
        return self.get_markets(params=params)

    def get_market_data(self, market_id):
        """Legacy alias for get_market to avoid breaking references."""
        return self.get_market(market_id)