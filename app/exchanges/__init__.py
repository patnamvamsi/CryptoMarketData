"""
Exchange Adapters Module

This module provides exchange adapters for different cryptocurrency and stock exchanges.
All adapters implement the BaseExchange interface for consistent access to market data.

Available Adapters:
- BinanceAdapter: Binance cryptocurrency exchange
- ZerodhaAdapter: Zerodha/NSE stock exchange (coming soon)

Usage:
    from app.exchanges import get_exchange

    # Get Binance adapter
    binance = get_exchange('binance')

    # Get Zerodha adapter
    zerodha = get_exchange('zerodha', access_token='your_token')
"""

from .base_exchange import BaseExchange
from .exchange_factory import ExchangeFactory, get_exchange

# Import and register available adapters
try:
    from .binance_adapter import BinanceAdapter
    ExchangeFactory.register_exchange('binance', BinanceAdapter)
except ImportError as e:
    import logging
    logging.warning(f"BinanceAdapter not available: {e}")

# Zerodha adapter for NSE/BSE markets
try:
    from .zerodha_adapter import ZerodhaAdapter
    ExchangeFactory.register_exchange('zerodha', ZerodhaAdapter)
except ImportError as e:
    import logging
    logging.warning(f"ZerodhaAdapter not available: {e}")

__all__ = [
    'BaseExchange',
    'ExchangeFactory',
    'get_exchange',
    'BinanceAdapter',
    'ZerodhaAdapter'
]
