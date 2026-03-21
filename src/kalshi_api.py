import logging
import time
import os
import base64
import datetime
import requests

from typing import Any, Dict, List, Optional
from config import (
    KALSHI_API_KEY,
    KALSHI_PRIVATE_KEY_PATH,
    KALSHI_API_BASE_URL,
    MAX_RETRIES,
    RETRY_DELAY_SECONDS,
)

# Per Kalshi docs: https://docs.kalshi.com/getting_started/api_keys
KALSHI_DEMO_URL = "https://demo-api.kalshi.co/trade-api/v2"
KALSHI_PROD_URL = "https://api.elections.kalshi.com/trade-api/v2"


def load_private_key(file_path: str):
    """Load RSA private key from PEM file for signing."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.backends import default_backend

    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Private key file not found: {file_path}")

    with open(file_path, "rb") as f:
        private_key = serialization.load_pem_private_key(
            f.read(),
            password=None,
            backend=default_backend(),
        )
    if not isinstance(private_key, rsa.RSAPrivateKey):
        raise ValueError("Key file does not contain an RSA private key")
    return private_key


def sign_pss_text(private_key, text: str) -> str:
    """Sign text with RSA-PSS/SHA256 (Kalshi's required signature scheme)."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.exceptions import InvalidSignature

    message = text.encode("utf-8")
    try:
        signature = private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        return base64.b64encode(signature).decode("utf-8")
    except InvalidSignature as e:
        raise ValueError("RSA-PSS sign failed") from e


class KalshiAPI:
    def __init__(
        self,
        api_key=None,
        base_url=None,
        private_key_path=None,
        max_retries=MAX_RETRIES,
        retry_delay=RETRY_DELAY_SECONDS,
    ):
        self.api_key_id = api_key or KALSHI_API_KEY

        # Resolve base URL
        demo_mode = os.environ.get("KALSHI_DEMO_MODE", "true").lower() == "true"
        if base_url:
            self.base_url = base_url
        elif demo_mode:
            self.base_url = KALSHI_DEMO_URL
            logging.info(f"KalshiAPI: Using DEMO mode ({self.base_url})")
        else:
            self.base_url = KALSHI_API_BASE_URL or KALSHI_PROD_URL
            logging.info(f"KalshiAPI: Using PRODUCTION mode ({self.base_url})")

        # Load private key for RSA signing
        key_path = private_key_path or KALSHI_PRIVATE_KEY_PATH
        if os.path.exists(key_path):
            self.private_key = load_private_key(key_path)
            logging.info(f"KalshiAPI: Private key loaded from {key_path}")
        else:
            self.private_key = None
            logging.warning(f"KalshiAPI: Private key file not found at {key_path} — requests will fail")

        self.logger = logging.getLogger(__name__)
        self.max_retries = max_retries
        self.retry_delay = retry_delay

    def _build_auth_headers(self, method: str, path: str) -> Dict[str, str]:
        """
        Build Kalshi RSA-PSS authentication headers.
        Per docs: sign timestamp + HTTP method + path (no query params).
        Path must include /trade-api/v2 prefix for signature.
        """
        timestamp_ms = int(datetime.datetime.now().timestamp() * 1000)
        # Strip query parameters from path before signing
        path_clean = path.split("?")[0]
        
        # CRITICAL: The path for signing must include /trade-api/v2 prefix
        # even if base_url already contains it
        api_path_prefix = "/trade-api/v2"
        if not path_clean.startswith(api_path_prefix):
            path_for_signature = api_path_prefix + path_clean
        else:
            path_for_signature = path_clean
        
        msg_string = f"{timestamp_ms}{method.upper()}{path_for_signature}"

        if self.private_key is None:
            raise ValueError("Private key not loaded — cannot sign request")

        signature = sign_pss_text(self.private_key, msg_string)

        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-TIMESTAMP": str(timestamp_ms),
            "KALSHI-ACCESS-SIGNATURE": signature,
            "Content-Type": "application/json",
        }

    def _handle_request(self, method, endpoint, **kwargs):
        url = f"{self.base_url}{endpoint}"
        headers = self._build_auth_headers(method, endpoint)
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
        return self._handle_request("GET", "/markets/trades", params=params or {})

    def get_orderbook(self, ticker: str, depth: int = 10) -> Optional[Dict[str, Any]]:
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
        return self.get_markets(params=params)

    def get_market_data(self, market_id):
        return self.get_market(market_id)
