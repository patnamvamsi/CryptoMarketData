"""
Real-time ticker price streaming from Binance WebSocket API.

This module subscribes to Binance ticker streams (24hr price updates) and:
1. Publishes individual ticker updates to Kafka topics (binance.ticks.{SYMBOL})
2. Updates Redis with latest price for instant lookup
3. Provides real-time price data for UI and other consumers

Unlike kline streams (1-minute candles), ticker streams provide:
- Every price update (tick-by-tick)
- 24h statistics (high, low, volume, price change %)
- Sub-second latency

Architecture:
    Binance WebSocket → StreamBinanceTicker → Kafka Topics (per symbol)
                                            ↘ Redis (latest price)
"""

import json
import time
import threading
from datetime import datetime
from typing import List, Dict, Any, Optional
from binance import ThreadedWebsocketManager, Client

from app.config import config
from app.config.config import (
    STREAM_MARKET_DATA_KAFKA,
    KAFKA_BOOTSTRAP_SERVERS
)
from app.kafka.kafka_utils import get_kafka_producer
from app.cache.redis_client import get_redis_cache
from app.logger import setup_logging
import app.db.timescaledb.crud as q

logger = setup_logging()


class StreamBinanceTicker:
    """
    Stream real-time ticker data from Binance WebSocket API.

    Features:
    - Subscribe to multiple symbol ticker streams
    - Publish to symbol-specific Kafka topics (binance.ticks.BTCUSDT)
    - Update Redis with latest price (TTL: 1 hour)
    - Automatic reconnection on failures
    """

    def __init__(self, session, enable_kafka: bool = True, enable_redis: bool = True):
        """
        Initialize Binance ticker stream.

        Args:
            session: SQLAlchemy session for database access
            enable_kafka: Enable Kafka publishing
            enable_redis: Enable Redis price updates
        """
        self.session = session
        self.enable_kafka = enable_kafka and STREAM_MARKET_DATA_KAFKA
        self.enable_redis = enable_redis

        # Get active symbols from database (convert to list for mutability)
        self.symbols = list(q.get_active_symbols(self.session, active=True))
        self.stream_names = []

        # Track individual symbol sockets for dynamic add/remove
        self.symbol_sockets = {}  # {symbol: socket_name}
        self._socket_lock = threading.Lock()  # guards symbol_sockets/symbols mutation

        # Initialize Kafka producer if enabled
        self.kafka_producer = None
        if self.enable_kafka:
            try:
                self.kafka_producer = get_kafka_producer()
                logger.info("Kafka producer initialized for ticker streaming")
            except Exception as e:
                logger.error(f"Failed to initialize Kafka producer: {e}")
                self.enable_kafka = False

        # Initialize Redis cache if enabled
        self.redis_cache = None
        if self.enable_redis:
            self.redis_cache = get_redis_cache()
            if self.redis_cache and self.redis_cache.health_check():
                logger.info("Redis cache initialized for ticker streaming")
            else:
                logger.warning("Redis cache not available for ticker streaming")
                self.enable_redis = False

        # Statistics
        self.ticks_received = 0
        self.kafka_published = 0
        self.redis_updated = 0
        self.errors = 0
        self.start_time = datetime.now()

        # Build stream names
        self._build_stream_names()

        # WebSocket manager (initialized in main())
        self.twm = None

        logger.info(f"StreamBinanceTicker initialized for {len(self.symbols)} symbols")
        logger.info(f"Kafka enabled: {self.enable_kafka}, Redis enabled: {self.enable_redis}")

    def _build_stream_names(self):
        """Build WebSocket stream names for all active symbols."""
        # Binance ticker stream format: btcusdt@ticker
        # See: https://binance-docs.github.io/apidocs/spot/en/#individual-symbol-ticker-streams
        for symbol in self.symbols:
            self.stream_names.append(f"{symbol.lower()}@ticker")

        logger.info(f"Subscribed to {len(self.stream_names)} ticker streams")
        logger.debug(f"Streams: {self.stream_names[:5]}..." if len(self.stream_names) > 5 else f"Streams: {self.stream_names}")

    def _get_kafka_topic(self, symbol: str) -> str:
        """
        Get Kafka topic name for a specific symbol.

        Format: binance.ticks.BTCUSDT

        Args:
            symbol: Trading pair symbol (e.g., 'BTCUSDT')

        Returns:
            Kafka topic name
        """
        return f"binance.ticks.{symbol.upper()}"

    def _get_redis_key(self, symbol: str) -> str:
        """
        Get Redis key for latest price of a symbol.

        Format: price:binance:BTCUSDT

        Args:
            symbol: Trading pair symbol (e.g., 'BTCUSDT')

        Returns:
            Redis key
        """
        return f"price:binance:{symbol.upper()}"

    def _publish_to_kafka(self, symbol: str, ticker_data: Dict[str, Any]):
        """
        Publish ticker data to symbol-specific Kafka topic.

        Args:
            symbol: Trading pair symbol
            ticker_data: Formatted ticker data dictionary
        """
        if not self.enable_kafka or not self.kafka_producer:
            return

        try:
            topic = self._get_kafka_topic(symbol)
            message = json.dumps(ticker_data).encode('utf-8')

            self.kafka_producer.send(topic, message)
            self.kafka_published += 1

            # Log first few messages for debugging
            if self.kafka_published <= 5:
                logger.debug(f"Published to Kafka topic '{topic}': {ticker_data}")

        except Exception as e:
            self.errors += 1
            logger.error(f"Failed to publish to Kafka for {symbol}: {e}")

    def _update_redis(self, symbol: str, ticker_data: Dict[str, Any]):
        """
        Update Redis with latest price data.

        Args:
            symbol: Trading pair symbol
            ticker_data: Formatted ticker data dictionary
        """
        if not self.enable_redis or not self.redis_cache:
            return

        try:
            key = self._get_redis_key(symbol)

            # Store latest price with a short TTL: if the stream dies, we want stale
            # "live" prices to expire into an honest 404 within ~2 minutes rather than
            # silently serving up to an hour of stale data as if it were live.
            self.redis_cache.set(key, ticker_data, ttl=120)
            self.redis_updated += 1

            # Log first few updates for debugging
            if self.redis_updated <= 5:
                logger.debug(f"Updated Redis key '{key}': ${ticker_data['price']}")

        except Exception as e:
            self.errors += 1
            logger.error(f"Failed to update Redis for {symbol}: {e}")

    def _format_ticker_data(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        """
        Format raw Binance ticker message to standardized structure.

        Args:
            msg: Raw ticker message from Binance WebSocket

        Returns:
            Formatted ticker data dictionary
        """
        return {
            'exchange': 'binance',
            'symbol': msg['s'],  # Symbol (e.g., 'BTCUSDT')
            'price': float(msg['c']),  # Last price
            'price_change': float(msg['p']),  # Price change (absolute)
            'price_change_percent': float(msg['P']),  # Price change (percent)
            'high_24h': float(msg['h']),  # 24h high
            'low_24h': float(msg['l']),  # 24h low
            'volume_24h': float(msg['v']),  # 24h volume (base asset)
            'quote_volume_24h': float(msg['q']),  # 24h volume (quote asset)
            'open_price': float(msg['o']),  # Open price
            'timestamp': msg['E'],  # Event time (milliseconds)
            'datetime': datetime.fromtimestamp(msg['E'] / 1000).isoformat(),
            'number_of_trades': int(msg['n'])  # Number of trades in 24h
        }

    def _handle_ticker_message(self, msg: Dict[str, Any]):
        """
        Process incoming ticker message from Binance WebSocket.

        Args:
            msg: Raw WebSocket message
        """
        try:
            # Extract data from multiplex stream wrapper
            if 'data' in msg:
                msg = msg['data']

            # Validate message type
            if msg.get('e') != '24hrTicker':
                return

            self.ticks_received += 1
            symbol = msg['s']

            # Format ticker data
            ticker_data = self._format_ticker_data(msg)

            # Publish to Kafka
            if self.enable_kafka:
                self._publish_to_kafka(symbol, ticker_data)

            # Update Redis
            if self.enable_redis:
                self._update_redis(symbol, ticker_data)

            # Log progress every 100 ticks
            if self.ticks_received % 100 == 0:
                elapsed = (datetime.now() - self.start_time).total_seconds()
                rate = self.ticks_received / elapsed if elapsed > 0 else 0
                logger.info(
                    f"Ticker stats: {self.ticks_received} ticks received, "
                    f"{self.kafka_published} published to Kafka, "
                    f"{self.redis_updated} updated in Redis, "
                    f"{self.errors} errors, "
                    f"{rate:.1f} ticks/sec"
                )

        except Exception as e:
            self.errors += 1
            logger.error(f"Error handling ticker message: {e}")
            logger.debug(f"Problematic message: {msg}")

    def main(self):
        """
        Start the ticker WebSocket stream.

        This method blocks and runs the WebSocket connection loop.
        It will automatically reconnect on failures.
        """
        try:
            # Initialize WebSocket manager
            self.twm = ThreadedWebsocketManager(
                api_key=config.API_KEY,
                api_secret=config.API_SECRET
            )

            # Start the internal event loop
            self.twm.start()

            logger.info(f"Starting Binance ticker stream for {len(self.stream_names)} symbols")

            # Start multiplex stream (multiple symbols in one connection)
            multiplex_socket = self.twm.start_multiplex_socket(
                callback=self._handle_ticker_message,
                streams=self.stream_names
            )

            logger.info("Binance ticker WebSocket stream started successfully")
            logger.info(f"Publishing to Kafka: {self.enable_kafka}")
            logger.info(f"Updating Redis: {self.enable_redis}")

            # Block and wait for messages
            self.twm.join()

        except KeyboardInterrupt:
            logger.info("Ticker stream interrupted by user")
            self.stop()

        except Exception as e:
            logger.error(f"Fatal error in ticker stream: {e}")
            self.stop()
            raise

    def add_symbol(self, symbol: str):
        """
        Dynamically add a symbol to the ticker stream.

        Args:
            symbol: Trading pair symbol (e.g., 'BTCUSDT')

        Returns:
            bool: True if successfully added, False otherwise
        """
        try:
            symbol = symbol.upper()

            with self._socket_lock:
                # Check if already subscribed
                if symbol in self.symbol_sockets:
                    logger.info(f"Symbol {symbol} already subscribed to ticker stream")
                    return True

                # Check if WebSocket manager is running
                if not self.twm:
                    logger.error("WebSocket manager not initialized, cannot add symbol dynamically")
                    return False

                # Create individual ticker socket for this symbol
                stream_name = f"{symbol.lower()}@ticker"
                socket_name = self.twm.start_symbol_ticker_socket(
                    callback=self._handle_ticker_message,
                    symbol=symbol
                )

                # Track the socket
                self.symbol_sockets[symbol] = socket_name
                self.symbols.append(symbol)

            logger.info(f"Dynamically added {symbol} to ticker stream (socket: {socket_name})")
            return True

        except Exception as e:
            logger.error(f"Failed to add symbol {symbol} to ticker stream: {e}")
            return False

    def remove_symbol(self, symbol: str):
        """
        Dynamically remove a symbol from the ticker stream.

        Args:
            symbol: Trading pair symbol (e.g., 'BTCUSDT')

        Returns:
            bool: True if successfully removed, False otherwise
        """
        try:
            symbol = symbol.upper()

            with self._socket_lock:
                # Check if subscribed
                if symbol not in self.symbol_sockets:
                    logger.info(f"Symbol {symbol} not subscribed to ticker stream")
                    return True

                # Check if WebSocket manager is running
                if not self.twm:
                    logger.error("WebSocket manager not initialized")
                    return False

                # Stop the individual socket
                socket_name = self.symbol_sockets[symbol]
                self.twm.stop_socket(socket_name)

                # Remove tracking
                del self.symbol_sockets[symbol]
                if symbol in self.symbols:
                    self.symbols.remove(symbol)

            logger.info(f"Dynamically removed {symbol} from ticker stream")
            return True

        except Exception as e:
            logger.error(f"Failed to remove symbol {symbol} from ticker stream: {e}")
            return False

    def start(self):
        """
        No-op provided for API parity with the rest of this class's public surface.

        Nothing in the current codebase calls this — app/main.py's background thread calls
        .main() directly, which blocks and does the real work. Kept only so this class
        doesn't surprise a future caller that reasonably expects a start()/stop() pair.
        """
        pass

    def stop(self):
        """Stop the WebSocket stream and clean up resources."""
        try:
            if hasattr(self, 'twm'):
                self.twm.stop()
                logger.info("Ticker WebSocket stream stopped")

            # Final statistics
            elapsed = (datetime.now() - self.start_time).total_seconds()
            logger.info(
                f"Final ticker stats: {self.ticks_received} ticks in {elapsed:.1f}s, "
                f"{self.kafka_published} published, {self.redis_updated} cached, "
                f"{self.errors} errors"
            )

        except Exception as e:
            logger.error(f"Error stopping ticker stream: {e}")


if __name__ == "__main__":
    """
    Standalone execution for testing.

    Usage:
        python -m app.stream.stream_binance_ticker
    """
    from app.db.timescaledb import timescaledb_connect as c
    from app.logger import setup_logging

    # Setup logging
    logger = setup_logging()

    # Create database session
    session_pool = c.get_session_pool()
    session = session_pool()

    try:
        # Create and start ticker stream
        ticker_stream = StreamBinanceTicker(session)
        ticker_stream.main()

    except KeyboardInterrupt:
        logger.info("Ticker stream stopped by user")

    finally:
        session.close()
