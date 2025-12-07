"""
Zerodha/NSE Real-time Market Data Streaming

Streams live market data from Zerodha using KiteTicker WebSocket.
Handles active symbols and stores data to TimescaleDB.
"""

import logging
from typing import List, Dict, Any

from app.config import config
from app.exchanges import get_exchange
from app.exchanges.zerodha_adapter import ZerodhaAdapter
import app.db.timescaledb.crud as q
from app.ingest.zerodha_historical_data import ZerodhaDownloader
from app.config.config import STREAM_MARKET_DATA_KAFKA, KAFKA_MARKET_DATA_TOPIC
from app.kafka.kafka_utils import get_kafka_producer
from app.logger import setup_logging

logger = setup_logging()


class StreamZerodhaKLineData:
    """
    Streams real-time kline data from Zerodha/NSE using WebSocket.

    Handles:
    - Fetching active symbols from database
    - Pre-filling latest gaps before streaming
    - WebSocket connection to KiteTicker
    - Data normalization and storage
    - Optional Kafka publishing
    """

    def __init__(self, session, exchange_segment='NSE', interval='1m'):
        """
        Initialize Zerodha streaming.

        Args:
            session: Database session
            exchange_segment: Market segment (NSE, NFO, BSE)
            interval: Time interval for klines (default: 1m)
        """
        self.session = session
        self.exchange = 'zerodha'
        self.exchange_segment = exchange_segment
        self.interval = interval

        # Get active symbols from database
        self.symbols = self._get_active_symbols()
        logger.info(f"Initialized streaming for {len(self.symbols)} Zerodha symbols")

        # Initialize Kafka producer if enabled
        self.kafka_producer = get_kafka_producer() if STREAM_MARKET_DATA_KAFKA else None

        # Initialize Zerodha adapter
        try:
            self.zerodha: ZerodhaAdapter = get_exchange('zerodha')
            logger.info("Zerodha adapter initialized for streaming")
        except Exception as e:
            logger.error(f"Failed to initialize Zerodha adapter: {e}")
            raise

        # Pre-fill latest gaps before starting stream
        self._prefill_historical_data()

    def _get_active_symbols(self) -> List[str]:
        """
        Fetch active symbols from unified symbols table.

        Returns:
            List of symbol strings
        """
        try:
            symbols = q.get_active_symbols_unified(
                session=self.session,
                exchange=self.exchange,
                active=True
            )
            return list(symbols)
        except Exception as e:
            logger.error(f"Error fetching active symbols: {e}")
            return []

    def _prefill_historical_data(self):
        """
        Fill up the latest gaps before starting streaming.
        Ensures no missing data at the transition point.
        """
        logger.info("Pre-filling historical data before streaming...")

        try:
            downloader = ZerodhaDownloader(
                session=self.session,
                exchange_segment=self.exchange_segment,
                interval=self.interval
            )

            # Fetch recent data for all symbols
            downloader.fetch_all_historical_data()

            logger.info("Pre-fill completed successfully")

        except Exception as e:
            logger.error(f"Error during pre-fill: {e}")
            logger.warning("Continuing with streaming despite pre-fill error")

    def _handle_tick(self, tick_data: Dict[str, Any]):
        """
        Process incoming tick data from WebSocket.

        Args:
            tick_data: Normalized kline dictionary from adapter
        """
        try:
            symbol = tick_data['symbol']
            logger.debug(f"Received tick for {symbol}: {tick_data}")

            # Convert to database format
            candle_stick = [[
                tick_data['open_time'],
                tick_data['open'],
                tick_data['high'],
                tick_data['low'],
                tick_data['close'],
                tick_data['volume'],
                tick_data['close_time'],
                tick_data.get('quote_volume', 0),
                tick_data.get('trades', 0),
                tick_data.get('taker_buy_base_volume', 0),
                tick_data.get('taker_buy_quote_volume', 0),
                0  # ignore field
            ]]

            # Insert into database
            q.insert_kline_rows(
                symbol=symbol,
                interval=self.interval,
                kline_data=candle_stick,
                session=self.session,
                exchange=self.exchange
            )

            logger.info(f"Stored tick for {symbol}")

            # Publish to Kafka if enabled
            if STREAM_MARKET_DATA_KAFKA and self.kafka_producer:
                try:
                    self.kafka_producer.send(
                        KAFKA_MARKET_DATA_TOPIC,
                        bytes(str(tick_data).encode('utf-8'))
                    )
                except Exception as e:
                    logger.error(f"Failed to publish to Kafka: {e}")

        except Exception as e:
            logger.error(f"Error handling tick: {e}")
            import traceback
            traceback.print_exception(type(e), e, e.__traceback__)

    def main(self):
        """
        Start the WebSocket streaming connection.

        This method blocks and runs continuously until interrupted.
        """
        try:
            logger.info(f"Starting Zerodha WebSocket streaming for {len(self.symbols)} symbols")
            logger.info(f"Symbols: {', '.join(self.symbols[:5])}{'...' if len(self.symbols) > 5 else ''}")

            # Start streaming using the adapter
            self.zerodha.start_streaming(
                symbols=self.symbols,
                callback=self._handle_tick,
                exchange=self.exchange_segment
            )

            logger.info("Zerodha WebSocket streaming started successfully")

            # Keep the thread alive
            # The WebSocket runs in threaded mode, so this will block
            import time
            while True:
                time.sleep(1)

        except KeyboardInterrupt:
            logger.info("Received interrupt signal, stopping streaming...")
            self.stop()
        except Exception as e:
            logger.error(f"Error in streaming: {e}")
            import traceback
            traceback.print_exception(type(e), e, e.__traceback__)
            raise

    def stop(self):
        """
        Stop the WebSocket streaming connection.
        """
        try:
            logger.info("Stopping Zerodha WebSocket streaming...")
            self.zerodha.stop_streaming()
            logger.info("Zerodha WebSocket streaming stopped")
        except Exception as e:
            logger.error(f"Error stopping streaming: {e}")


if __name__ == "__main__":
    # Test the streaming
    from app.db.timescaledb import timescaledb_connect as c

    session_pool = c.get_session_pool()
    session = session_pool()

    try:
        stream = StreamZerodhaKLineData(
            session=session,
            exchange_segment='NSE',
            interval='1m'
        )
        stream.main()
    except KeyboardInterrupt:
        logger.info("Streaming stopped by user")
    finally:
        session.close()
