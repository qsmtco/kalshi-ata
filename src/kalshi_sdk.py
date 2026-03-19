#!/usr/bin/env python3
"""
Kalshi SDK Wrapper - Official kalshi-python SDK integration.

Phase 4 of Improvement Plan:
- Replace custom API wrapper with official SDK
- Provides clean interface for all trading operations
"""

import os
from typing import Optional, List, Dict, Any


class KalshiSDK:
    """
    Wrapper around official kalshi-python SDK.
    Provides clean interface for K-ATA trading operations.
    """
    
    def __init__(self, 
                 api_key_id: Optional[str] = None,
                 private_key_path: Optional[str] = None,
                 demo_mode: bool = True):
        """
        Initialize SDK client.
        
        Args:
            api_key_id: Kalshi API key ID (from environment if not provided)
            private_key_path: Path to private key file
            demo_mode: Use demo API if True (for testing)
        """
        # Get from environment if not provided
        self.api_key_id = api_key_id or os.environ.get('KALSHI_API_KEY_ID')
        self.private_key_path = private_key_path or os.environ.get(
            'KALSHI_PRIVATE_KEY_PATH', 'kalshi_private_key.pem'
        )
        self.demo_mode = demo_mode
        
        # Determine host URL
        if demo_mode:
            self.host = "https://demo-api.kalshi.co/trade-api/v2"
        else:
            self.host = "https://api.elections.kalshi.com/trade-api/v2"
        
        self._client = None
        self._initialized = False
    
    def _ensure_initialized(self):
        """Lazy initialization of SDK client."""
        if self._initialized:
            return
        
        if not self.api_key_id:
            raise ValueError("API key ID not provided. Set KALSHI_API_KEY_ID env var.")
        
        if not os.path.exists(self.private_key_path):
            raise ValueError(
                f"Private key not found at {self.private_key_path}. "
                "Download from kalshi.com/account/settings"
            )
        
        # Import official SDK
        try:
            from kalshi_python import Configuration, KalshiClient
        except ImportError:
            raise ImportError(
                "kalshi-python not installed. Run: pip install kalshi-python"
            )
        
        # Read private key
        with open(self.private_key_path, 'r') as f:
            private_key = f.read()
        
        # Configure and create client
        config = Configuration(host=self.host)
        config.api_key_id = self.api_key_id
        config.private_key_pem = private_key
        
        self._client = KalshiClient(config)
        self._initialized = True
    
    # =========================================================================
    # ACCOUNT
    # =========================================================================
    
    def get_balance(self) -> Dict[str, Any]:
        """Get account balance."""
        self._ensure_initialized()
        result = self._client.get_balance()
        return {
            'balance': result.balance / 100 if result.balance else 0,  # Convert from cents
            'currency': 'USD'
        }
    
    # =========================================================================
    # MARKETS
    # =========================================================================
    
    def get_markets(self, 
                   status: Optional[str] = None,
                   tickers: Optional[List[str]] = None,
                   limit: int = 20) -> List[Dict[str, Any]]:
        """Get list of markets."""
        self._ensure_initialized()
        
        params = {'limit': limit}
        if status:
            params['status'] = status
        if tickers:
            params['tickers'] = ','.join(tickers)
        
        result = self._client.get_markets(**params)
        markets = result.markets if hasattr(result, 'markets') else []
        
        return [
            {
                'ticker': m.ticker,
                'title': m.title,
                'status': m.status,
                'yes_bid': m.yes_bid / 100 if m.yes_bid else 0.5,
                'yes_ask': m.yes_ask / 100 if m.yes_ask else 0.5,
                'no_bid': m.no_bid / 100 if m.no_bid else 0.5,
                'no_ask': m.no_ask / 100 if m.no_ask else 0.5,
                'last_price': m.last_price / 100 if m.last_price else 0.5,
                'volume': m.volume or 0,
                'close_date': getattr(m, 'close_time', None),
            }
            for m in markets
        ]
    
    def get_market(self, market_id: str) -> Dict[str, Any]:
        """Get specific market details."""
        self._ensure_initialized()
        
        result = self._client.get_market(market_id=market_id)
        m = result.market
        
        return {
            'ticker': m.ticker,
            'title': m.title,
            'status': m.status,
            'yes_bid': m.yes_bid / 100 if m.yes_bid else 0.5,
            'yes_ask': m.yes_ask / 100 if m.yes_ask else 0.5,
            'no_bid': m.no_bid / 100 if m.no_bid else 0.5,
            'no_ask': m.no_ask / 100 if m.no_ask else 0.5,
            'last_price': m.last_price / 100 if m.last_price else 0.5,
            'volume': m.volume or 0,
            'close_date': getattr(m, 'close_time', None),
        }
    
    # =========================================================================
    # ORDERS
    # =========================================================================
    
    def create_order(self,
                    market_id: str,
                    side: str,  # 'yes' or 'no'
                    order_type: str,  # 'limit' or 'market'
                    price: float,
                    quantity: int) -> Dict[str, Any]:
        """
        Place an order.
        
        Args:
            market_id: Market ticker (e.g., 'KALSHI_EVENT')
            side: 'yes' (buy) or 'no' (sell)
            order_type: 'limit' or 'market'
            price: Price (0-1, will be converted to cents)
            quantity: Number of contracts
            
        Returns:
            Order details
        """
        self._ensure_initialized()
        
        # Convert price to cents
        price_cents = int(price * 100)
        
        result = self._client.create_order(
            market_id=market_id,
            side=side,
            order_type=order_type,
            price=price_cents,
            count=quantity
        )
        
        order = result.order
        return {
            'order_id': order.order_id,
            'market_id': order.market_id,
            'side': order.side,
            'status': order.status,
            'price': order.price / 100 if order.price else 0,
            'count': order.count,
        }
    
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order."""
        self._ensure_initialized()
        
        try:
            self._client.cancel_order(order_id=order_id)
            return True
        except Exception as e:
            print(f"Failed to cancel order {order_id}: {e}")
            return False
    
    def get_orders(self, 
                  status: Optional[str] = None,
                  market_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get orders."""
        self._ensure_initialized()
        
        params = {}
        if status:
            params['status'] = status
        if market_id:
            params['market_id'] = market_id
        
        result = self._client.get_orders(**params)
        orders = result.orders if hasattr(result, 'orders') else []
        
        return [
            {
                'order_id': o.order_id,
                'market_id': o.market_id,
                'side': o.side,
                'status': o.status,
                'price': o.price / 100 if o.price else 0,
                'count': o.count,
            }
            for o in orders
        ]
    
    # =========================================================================
    # POSITIONS
    # =========================================================================
    
    def get_positions(self) -> List[Dict[str, Any]]:
        """Get open positions."""
        self._ensure_initialized()
        
        result = self._client.get_positions()
        positions = result.positions if hasattr(result, 'positions') else []
        
        return [
            {
                'market_id': p.market_id,
                'side': p.side,
                'size': p.size,
                'entry_price': p.entry_price / 100 if p.entry_price else 0,
                'market_value': p.market_value / 100 if p.market_value else 0,
                'cost_basis': p.cost_basis / 100 if p.cost_basis else 0,
            }
            for p in positions
        ]


# =============================================================================
# BACKWARDS COMPATIBILITY - Keep old kalshi_api.py interface
# =============================================================================

class KalshiAPI:
    """
    Backwards-compatible wrapper around KalshiSDK.
    Provides same interface as original kalshi_api.py
    """
    
    def __init__(self, api_key: Optional[str] = None):
        """Initialize with optional API key (ignored, uses env vars)."""
        # Check for API key in environment
        api_key_id = os.environ.get('KALSHI_API_KEY_ID') or api_key
        private_key_path = os.environ.get('KALSHI_PRIVATE_KEY_PATH')
        
        demo = os.environ.get('KALSHI_DEMO_MODE', 'true').lower() == 'true'
        
        self.sdk = KalshiSDK(
            api_key_id=api_key_id,
            private_key_path=private_key_path,
            demo_mode=demo
        )
    
    def get_balance(self):
        """Get balance (legacy interface)."""
        return self.sdk.get_balance()
    
    def get_markets(self, **kwargs):
        """Get markets (legacy interface)."""
        return self.sdk.get_markets(**kwargs)
    
    def get_market(self, market_id):
        """Get market (legacy interface)."""
        return self.sdk.get_market(market_id)
    
    def create_order(self, market_id, side, order_type, price, quantity):
        """Create order (legacy interface)."""
        return self.sdk.create_order(market_id, side, order_type, price, quantity)
    
    def cancel_order(self, order_id):
        """Cancel order (legacy interface)."""
        return self.sdk.cancel_order(order_id)
    
    def get_positions(self):
        """Get positions (legacy interface)."""
        return self.sdk.get_positions()
    
    def get_orders(self, status=None, market_id=None):
        """Get orders (legacy interface)."""
        return self.sdk.get_orders(status=status, market_id=market_id)


if __name__ == "__main__":
    # Quick test - will fail without proper credentials
    print("KalshiSDK loaded successfully")
    print("To use: Set KALSHI_API_KEY_ID and place private key file")
