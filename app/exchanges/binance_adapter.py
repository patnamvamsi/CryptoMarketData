"""
Binance Exchange Adapter

This module implements the BaseExchange interface for Binance cryptocurrency exchange.
It wraps the Binance API client and provides a unified interface for market data operations.
"""

from typing import List, Dict, Any, Callable
from datetime import datetime
import logging
import traceback
import pytz

from binance.client import Client
from binance import ThreadedWebsocketManager

from .base_exchange import BaseExchange
from app.config import config
from app.models.market_data import Symbol, Kline

logger = logging.getLogger(__name__)


class BinanceAdapter(BaseExchange):
    """
    Binance exchange adapter implementation.

    Provides access to Binance cryptocurrency market data including:
    - Symbol/instrument information
    - Historical candlestick (kline) data
    - Real-time streaming via WebSocket
    """

    def __init__(self):
        """Initialize Binance adapter with API credentials."""
        super().__init__()
        self._exchange_name = 'binance'

        try:
            self.client = Client(config.API_KEY, config.API_SECRET)
            logger.info("Binance client initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Binance client: {e}")
            raise

        # WebSocket manager (lazy initialized)
        self.twm: ThreadedWebsocketManager = None
        self._stream_callback: Callable = None

    @property
    def exchange_name(self) -> str:
        """Return 'binance' as the exchange identifier."""
        return self._exchange_name

    def get_symbols(self) -> List[Dict[str, Any]]:
        """
        Fetch all available trading symbols from Binance.

        Returns:
            List of normalized symbol dictionaries

        Raises:
            Exception: If API call fails
        """
        try:
            exchange_info = self.client.get_exchange_info()
            symbols_raw = exchange_info.get('symbols', [])

            logger.info(f"Fetched {len(symbols_raw)} symbols from Binance")

            # Normalize each symbol
            normalized_symbols = [
                self.normalize_symbol(symbol_data)
                for symbol_data in symbols_raw
            ]

            return normalized_symbols

        except Exception as e:
            logger.error(f"Error fetching symbols from Binance: {e}")
            traceback.print_exception(type(e), e, e.__traceback__)
            raise

    def get_historical_data(
        self,
        symbol: str,
        interval: str,
        start_time: datetime,
        end_time: datetime
    ) -> List[Dict[str, Any]]:
        """
        Fetch historical candlestick data for a symbol.

        Args:
            symbol: Trading pair symbol (e.g., 'BTCUSDT')
            interval: Binance kline interval (e.g., '1m', '5m', '1h', '1d')
            start_time: Start datetime (UTC)
            end_time: End datetime (UTC)

        Returns:
            List of normalized kline dictionaries

        Raises:
            Exception: If API call fails
        """
        try:
            # Ensure timezone-aware datetimes
            if start_time.tzinfo is None:
                start_time = pytz.UTC.localize(start_time)
            if end_time.tzinfo is None:
                end_time = pytz.UTC.localize(end_time)

            # Convert interval to Binance format
            binance_interval = self._convert_interval(interval)

            # Fetch klines from Binance
            candlesticks = self.client.get_historical_klines(
                symbol.upper(),
                binance_interval,
                str(start_time),
                str(end_time)
            )

            logger.info(
                f"Fetched {len(candlesticks)} klines for {symbol} "
                f"from {start_time} to {end_time}"
            )

            # Normalize each kline
            normalized_klines = [
                self.normalize_kline(kline_raw, symbol, interval)
                for kline_raw in candlesticks
            ]

            return normalized_klines

        except Exception as e:
            logger.error(
                f"Error getting historical klines for {symbol}: {e}"
            )
            traceback.print_exception(type(e), e, e.__traceback__)
            raise

    def start_streaming(
        self,
        symbols: List[str],
        callback: Callable[[Dict[str, Any]], None]
    ) -> None:
        """
        Start real-time WebSocket streaming for given symbols.

        Args:
            symbols: List of trading pair symbols
            callback: Function to call with each kline update

        Raises:
            Exception: If streaming fails to start
        """
        try:
            # Build stream names in Binance format
            streams = [
                f"{symbol.lower()}@kline_{Client.KLINE_INTERVAL_1MINUTE}"
                for symbol in symbols
            ]

            logger.info(f"Starting Binance WebSocket for {len(symbols)} symbols")
            logger.debug(f"Streams: {streams}")

            # Store callback for use in message handler
            self._stream_callback = callback

            # Initialize WebSocket manager
            self.twm = ThreadedWebsocketManager(
                api_key=config.API_KEY,
                api_secret=config.API_SECRET
            )
            self.twm.start()

            # Define message handler
            def handle_socket_message(msg):
                """Process incoming WebSocket messages."""
                try:
                    data = msg.get('data', msg)

                    if data.get('e') == 'kline':
                        # Extract and normalize kline data
                        symbol = data['s']
                        normalized_kline = self._normalize_stream_message(data)

                        # Call user callback with normalized data
                        self._stream_callback(normalized_kline)

                except Exception as e:
                    logger.error(f"Error processing stream message: {e}")
                    traceback.print_exception(type(e), e, e.__traceback__)

            # Start multiplex socket
            self.twm.start_multiplex_socket(
                callback=handle_socket_message,
                streams=streams
            )

            self._streaming_active = True
            logger.info("Binance WebSocket streaming started successfully")

        except Exception as e:
            logger.error(f"Failed to start Binance streaming: {e}")
            self._streaming_active = False
            raise

    def stop_streaming(self) -> None:
        """Stop active WebSocket streaming connection."""
        try:
            if self.twm:
                logger.info("Stopping Binance WebSocket streaming")
                self.twm.stop()
                self.twm = None
                self._streaming_active = False
                self._stream_callback = None
                logger.info("Binance WebSocket streaming stopped")
            else:
                logger.warning("No active streaming to stop")

        except Exception as e:
            logger.error(f"Error stopping Binance streaming: {e}")
            raise

    def normalize_symbol(self, raw_symbol: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convert Binance symbol format to unified format.

        Args:
            raw_symbol: Raw symbol data from Binance API

        Returns:
            Normalized symbol dictionary

        Example Binance symbol:
            {
                'symbol': 'BTCUSDT',
                'status': 'TRADING',
                'baseAsset': 'BTC',
                'quoteAsset': 'USDT',
                'baseAssetPrecision': 8,
                'quotePrecision': 8,
                ...
            }
        """
        return {
            'exchange': 'binance',
            'symbol': raw_symbol['symbol'],
            'base_asset': raw_symbol.get('baseAsset', ''),
            'quote_asset': raw_symbol.get('quoteAsset', ''),
            'status': raw_symbol.get('status', 'UNKNOWN'),
            'priority': 9999,  # Default low priority (not active)
            'active': False,   # Default inactive
            'metadata': {
                'baseAssetPrecision': raw_symbol.get('baseAssetPrecision'),
                'quotePrecision': raw_symbol.get('quotePrecision'),
                'quoteAssetPrecision': raw_symbol.get('quoteAssetPrecision'),
                'baseCommissionPrecision': raw_symbol.get('baseCommissionPrecision'),
                'quoteCommissionPrecision': raw_symbol.get('quoteCommissionPrecision'),
                'orderTypes': raw_symbol.get('orderTypes'),
                'icebergAllowed': raw_symbol.get('icebergAllowed'),
                'ocoAllowed': raw_symbol.get('ocoAllowed'),
                'isSpotTradingAllowed': raw_symbol.get('isSpotTradingAllowed'),
                'isMarginTradingAllowed': raw_symbol.get('isMarginTradingAllowed'),
                'filters': raw_symbol.get('filters'),
                'permissions': raw_symbol.get('permissions')
            }
        }

    def normalize_kline(
        self,
        raw_kline: Any,
        symbol: str,
        interval: str
    ) -> Dict[str, Any]:
        """
        Convert Binance kline format to unified format.

        Args:
            raw_kline: Raw kline data from Binance (list format)
            symbol: Trading symbol
            interval: Time interval

        Returns:
            Normalized kline dictionary

        Binance kline format:
            [
                1499040000000,      # 0: Open time (ms)
                "0.01634790",       # 1: Open
                "0.80000000",       # 2: High
                "0.01575800",       # 3: Low
                "0.01577100",       # 4: Close
                "148976.11427815",  # 5: Volume
                1499644799999,      # 6: Close time (ms)
                "2434.19055334",    # 7: Quote asset volume
                308,                # 8: Number of trades
                "1756.87402397",    # 9: Taker buy base asset volume
                "28.46694368",      # 10: Taker buy quote asset volume
                "17928899.62484339" # 11: Ignored
            ]
        """
        return {
            'exchange': 'binance',
            'symbol': symbol.upper(),
            'interval': interval,
            'open_time': datetime.fromtimestamp(int(raw_kline[0]) / 1000, tz=pytz.UTC),
            'close_time': datetime.fromtimestamp(int(raw_kline[6]) / 1000, tz=pytz.UTC),
            'open': float(raw_kline[1]),
            'high': float(raw_kline[2]),
            'low': float(raw_kline[3]),
            'close': float(raw_kline[4]),
            'volume': float(raw_kline[5]),
            'quote_volume': float(raw_kline[7]),
            'trades': int(raw_kline[8]),
            'taker_buy_base_volume': float(raw_kline[9]),
            'taker_buy_quote_volume': float(raw_kline[10]),
            'extra_data': {
                'ignored_field': raw_kline[11]
            }
        }

    def _normalize_stream_message(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalize WebSocket stream message to unified format.

        Args:
            msg: WebSocket message from Binance

        Returns:
            Normalized kline dictionary

        Stream message format:
            {
                'e': 'kline',
                's': 'BTCUSDT',
                'k': {
                    't': 1499040000000,  # Open time
                    'T': 1499644799999,  # Close time
                    'o': '0.01634790',   # Open
                    'h': '0.80000000',   # High
                    'l': '0.01575800',   # Low
                    'c': '0.01577100',   # Close
                    'v': '148976.11427815',  # Volume
                    'q': '2434.19055334',    # Quote volume
                    'n': 308,                 # Trades
                    'V': '1756.87402397',    # Taker buy base volume
                    'Q': '28.46694368',      # Taker buy quote volume
                    'B': '17928899.62484339' # Ignored
                }
            }
        """
        k = msg['k']

        return {
            'exchange': 'binance',
            'symbol': msg['s'],
            'interval': '1m',  # Stream is 1-minute
            'open_time': datetime.fromtimestamp(int(k['t']) / 1000, tz=pytz.UTC),
            'close_time': datetime.fromtimestamp(int(k['T']) / 1000, tz=pytz.UTC),
            'open': float(k['o']),
            'high': float(k['h']),
            'low': float(k['l']),
            'close': float(k['c']),
            'volume': float(k['v']),
            'quote_volume': float(k['q']),
            'trades': int(k['n']),
            'taker_buy_base_volume': float(k['V']),
            'taker_buy_quote_volume': float(k['Q']),
            'extra_data': {
                'is_closed': k.get('x', False),
                'ignored_field': k.get('B', '')
            }
        }

    def _convert_interval(self, interval: str) -> str:
        """
        Convert unified interval format to Binance format.

        Args:
            interval: Unified interval (e.g., '1m', '5m', '1h')

        Returns:
            Binance interval constant
        """
        # Map unified intervals to Binance constants
        interval_map = {
            '1m': Client.KLINE_INTERVAL_1MINUTE,
            '3m': Client.KLINE_INTERVAL_3MINUTE,
            '5m': Client.KLINE_INTERVAL_5MINUTE,
            '15m': Client.KLINE_INTERVAL_15MINUTE,
            '30m': Client.KLINE_INTERVAL_30MINUTE,
            '1h': Client.KLINE_INTERVAL_1HOUR,
            '2h': Client.KLINE_INTERVAL_2HOUR,
            '4h': Client.KLINE_INTERVAL_4HOUR,
            '6h': Client.KLINE_INTERVAL_6HOUR,
            '8h': Client.KLINE_INTERVAL_8HOUR,
            '12h': Client.KLINE_INTERVAL_12HOUR,
            '1d': Client.KLINE_INTERVAL_1DAY,
            '3d': Client.KLINE_INTERVAL_3DAY,
            '1w': Client.KLINE_INTERVAL_1WEEK,
            '1M': Client.KLINE_INTERVAL_1MONTH
        }

        return interval_map.get(interval, Client.KLINE_INTERVAL_1MINUTE)

    def validate_interval(self, interval: str) -> bool:
        """
        Validate if interval is supported by Binance.

        Args:
            interval: Time interval to validate

        Returns:
            True if supported, False otherwise
        """
        supported = [
            '1m', '3m', '5m', '15m', '30m',
            '1h', '2h', '4h', '6h', '8h', '12h',
            '1d', '3d', '1w', '1M'
        ]
        return interval in supported
