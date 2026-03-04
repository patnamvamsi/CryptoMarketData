"""
Base Exchange Adapter

This module defines the abstract base class that all exchange adapters must implement.
It provides a unified interface for interacting with different exchanges (Binance, Zerodha, etc.)
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Callable
from datetime import datetime


class BaseExchange(ABC):
    """
    Abstract base class for exchange adapters.

    All exchange-specific implementations (Binance, Zerodha, etc.) must inherit from
    this class and implement all abstract methods to ensure a consistent interface.
    """

    def __init__(self):
        """Initialize the exchange adapter."""
        self._streaming_active = False

    @property
    @abstractmethod
    def exchange_name(self) -> str:
        """
        Return the exchange identifier (e.g., 'binance', 'zerodha').

        Returns:
            str: Lowercase exchange name used for database tables and routing
        """
        pass

    @abstractmethod
    def get_symbols(self) -> List[Dict[str, Any]]:
        """
        Fetch all available trading symbols/instruments from the exchange.

        Returns:
            List[Dict]: List of symbols in normalized format:
                {
                    'exchange': str,
                    'symbol': str,
                    'base_asset': str,
                    'quote_asset': str,
                    'status': str,
                    'metadata': dict  # Exchange-specific data
                }
        """
        pass

    @abstractmethod
    def get_historical_data(
        self,
        symbol: str,
        interval: str,
        start_time: datetime,
        end_time: datetime
    ) -> List[Dict[str, Any]]:
        """
        Fetch historical candlestick/kline data for a symbol.

        Args:
            symbol: Trading symbol (e.g., 'BTCUSDT' for Binance, 'RELIANCE' for Zerodha)
            interval: Time interval (e.g., '1m', '5m', '1h', '1d')
            start_time: Start datetime for historical data
            end_time: End datetime for historical data

        Returns:
            List[Dict]: List of klines in normalized format:
                {
                    'exchange': str,
                    'symbol': str,
                    'interval': str,
                    'open_time': datetime,
                    'close_time': datetime,
                    'open': float,
                    'high': float,
                    'low': float,
                    'close': float,
                    'volume': float,
                    'trades': Optional[int],
                    'extra_data': dict  # Exchange-specific fields
                }
        """
        pass

    @abstractmethod
    def start_streaming(
        self,
        symbols: List[str],
        callback: Callable[[Dict[str, Any]], None]
    ) -> None:
        """
        Start real-time data streaming via WebSocket for the given symbols.

        Args:
            symbols: List of trading symbols to stream
            callback: Function to call with each received data point.
                     Should accept a normalized kline dict as parameter.

        Raises:
            Exception: If streaming fails to start
        """
        pass

    @abstractmethod
    def stop_streaming(self) -> None:
        """
        Stop the active WebSocket streaming connection.

        This should gracefully close the connection and clean up resources.
        """
        pass

    @abstractmethod
    def normalize_symbol(self, raw_symbol: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convert exchange-specific symbol format to unified format.

        Args:
            raw_symbol: Raw symbol data from exchange API

        Returns:
            Dict: Normalized symbol dictionary
        """
        pass

    @abstractmethod
    def normalize_kline(
        self,
        raw_kline: Any,
        symbol: str,
        interval: str
    ) -> Dict[str, Any]:
        """
        Convert exchange-specific kline/candlestick format to unified format.

        Args:
            raw_kline: Raw kline data from exchange (format varies by exchange)
            symbol: Trading symbol this kline belongs to
            interval: Time interval of this kline

        Returns:
            Dict: Normalized kline dictionary
        """
        pass

    def is_streaming_active(self) -> bool:
        """
        Check if streaming is currently active.

        Returns:
            bool: True if streaming, False otherwise
        """
        return self._streaming_active

    def validate_interval(self, interval: str) -> bool:
        """
        Validate if the given interval is supported by this exchange.

        Args:
            interval: Time interval string (e.g., '1m', '5m')

        Returns:
            bool: True if supported, False otherwise

        Note:
            Override this method if exchange has specific interval restrictions
        """
        # Common intervals supported by most exchanges
        supported = ['1m', '3m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '12h', '1d', '1w']
        return interval in supported

    def __repr__(self) -> str:
        """String representation of the exchange adapter."""
        return f"<{self.__class__.__name__}(exchange='{self.exchange_name}')>"
