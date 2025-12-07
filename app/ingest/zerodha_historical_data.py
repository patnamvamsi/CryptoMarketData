"""
Zerodha Historical Data Downloader

Downloads NSE/BSE market data using Zerodha adapter and updates database.
Fetches symbols marked as "Active" in the unified symbols table.
"""

import datetime
import logging
from typing import List, Tuple

from app.exchanges import get_exchange
from app.exchanges.zerodha_adapter import ZerodhaAdapter
from app.config import config
import app.db.timescaledb.crud as q
from app.logger import setup_logging

logger = setup_logging()


class ZerodhaDownloader:
    """
    Downloads Zerodha/NSE market data and updates database.
    Fetches symbols marked as "Active" for the 'zerodha' exchange.
    """

    def __init__(self, session, exchange_segment='NSE', interval='1m'):
        """
        Initialize Zerodha downloader.

        Args:
            session: Database session
            exchange_segment: Market segment (NSE, NFO, BSE)
            interval: Default interval for data (1m, 5m, 1h, 1d)
        """
        self.session = session
        self.exchange = 'zerodha'
        self.exchange_segment = exchange_segment  # NSE, NFO, BSE
        self.interval = interval
        # start_time is considered as 01-01-2010 epoch equivalent
        self.BEGIN_OF_THE_TIME = config.START_OF_TIME

        # Initialize Zerodha adapter (loads access token from file)
        try:
            self.zerodha: ZerodhaAdapter = get_exchange('zerodha')
            logger.info(f"Zerodha adapter initialized for {exchange_segment}")
        except Exception as e:
            logger.error(f"Failed to initialize Zerodha adapter: {e}")
            logger.error("Make sure you have generated an access token using zerodha_auth.py")
            raise

    def update_kline_tables(self, symbol: str, start_time_epoch: float, end_time_epoch: float):
        """
        Fetch kline data and update database tables.

        Args:
            symbol: Trading symbol (e.g., 'RELIANCE', 'INFY')
            start_time_epoch: Start time as epoch timestamp
            end_time_epoch: End time as epoch timestamp
        """
        try:
            # Convert epoch to datetime
            start_time = datetime.datetime.fromtimestamp(start_time_epoch)
            end_time = datetime.datetime.fromtimestamp(end_time_epoch)

            # Fetch historical data using Zerodha adapter
            klines = self.zerodha.get_historical_data(
                symbol=symbol,
                interval=self.interval,
                start_time=start_time,
                end_time=end_time,
                exchange=self.exchange_segment
            )

            if len(klines) > 0:
                # Convert normalized klines to database format
                candle_sticks = self._convert_klines_to_db_format(klines)

                # Insert into database
                q.insert_kline_rows(
                    symbol=symbol,
                    interval=self.interval,
                    kline_data=candle_sticks,
                    session=self.session,
                    exchange=self.exchange
                )
                logger.info(f"Inserted {len(candle_sticks)} candles for {symbol}")
            else:
                logger.info(f"No new data available for {symbol}")

        except Exception as e:
            logger.error(f"Error updating kline tables for {symbol}: {e}")
            import traceback
            traceback.print_exception(type(e), e, e.__traceback__)

    def _convert_klines_to_db_format(self, klines: List[dict]) -> List[List]:
        """
        Convert normalized kline dictionaries to database format.

        Args:
            klines: List of normalized kline dictionaries

        Returns:
            List of lists matching database schema
        """
        candle_sticks = []
        for kline in klines:
            # Format: [open_time, open, high, low, close, volume, close_time,
            #          quote_volume, trades, taker_buy_base, taker_buy_quote, extra]
            candle_stick = [
                kline['open_time'],
                kline['open'],
                kline['high'],
                kline['low'],
                kline['close'],
                kline['volume'],
                kline['close_time'],
                kline.get('quote_volume', 0),
                kline.get('trades', 0),
                kline.get('taker_buy_base_volume', 0),
                kline.get('taker_buy_quote_volume', 0),
                0  # ignore field
            ]
            candle_sticks.append(candle_stick)

        return candle_sticks

    def fetch_symbols(self) -> List[str]:
        """
        Get list of active symbols from database.

        Returns:
            List of symbol strings marked as active for zerodha exchange
        """
        try:
            symbols = q.get_active_symbols_unified(
                session=self.session,
                exchange=self.exchange,
                active=True
            )
            logger.info(f"Found {len(symbols)} active Zerodha symbols")
            return symbols
        except Exception as e:
            logger.error(f"Error fetching active symbols: {e}")
            # Fallback to empty list if unified table doesn't exist yet
            return []

    def fetch_all_historical_data(self):
        """
        Fetch recent historical data for all active symbols.
        Updates from most recent timestamp to current time.
        """
        symbols = self.fetch_symbols()
        logger.info(f"Starting historical data fetch for {len(symbols)} symbols")

        for symbol in symbols:
            try:
                self.fetch_recent_historical_data(symbol)
            except Exception as e:
                logger.error(f"Failed to fetch historical data for {symbol}: {e}")
                continue

    def fetch_all_gap_historical_data(self):
        """
        Fill gaps in historical data for all active symbols.
        Identifies and fills missing time periods.
        """
        symbols = self.fetch_symbols()
        logger.info(f"Starting gap fill for {len(symbols)} symbols")

        for symbol in symbols:
            try:
                self.fetch_gap_historical_data(symbol)
            except Exception as e:
                logger.error(f"Failed to fill gaps for {symbol}: {e}")
                continue

    def fetch_recent_historical_data(self, symbol: str) -> str:
        """
        Fetch recent historical data for a specific symbol.

        Args:
            symbol: Trading symbol

        Returns:
            Status message
        """
        # Ensure table exists
        q.create_table_if_not_exists(
            symbol=symbol,
            kline_interval=self.interval,
            session=self.session,
            exchange=self.exchange
        )

        # Get most recent timestamp from database
        start_time = self._get_most_recent_timestamp(symbol)
        end_time = datetime.datetime.now().timestamp()

        logger.info(
            f"Fetching {symbol} since {datetime.datetime.fromtimestamp(start_time)} "
            f"till {datetime.datetime.fromtimestamp(end_time)}"
        )

        self.update_kline_tables(symbol, start_time, end_time)
        return f"Completed fetching {symbol}"

    def fetch_gap_historical_data(self, symbol: str):
        """
        Identify and fill gaps in historical data for a symbol.

        Args:
            symbol: Trading symbol
        """
        # Ensure table exists
        q.create_table_if_not_exists(
            symbol=symbol,
            kline_interval=self.interval,
            session=self.session,
            exchange=self.exchange
        )

        # Find gaps in data
        rs = q.find_gaps_in_kline_data(
            symbol=symbol,
            kline_interval=self.interval,
            session=self.session,
            exchange=self.exchange
        )

        for row in rs:
            logger.info(
                f"Filling gap for {symbol}: {row.gap_start} to {row.gap_end}"
            )
            self.update_kline_tables(
                symbol,
                row.gap_start.timestamp(),
                row.gap_end.timestamp()
            )

    def _get_most_recent_timestamp(self, symbol: str) -> float:
        """
        Get the most recent timestamp from database for a symbol.

        Args:
            symbol: Trading symbol

        Returns:
            Epoch timestamp (float)
        """
        table_name = q.get_table_name(symbol, self.interval, self.exchange)
        max_time = q.get_max_timestamp(table_name, self.session)

        if max_time is not None:
            return max_time.timestamp()

        # If no data exists, start from configured begin time
        return self.BEGIN_OF_THE_TIME


if __name__ == "__main__":
    # Test the downloader
    from app.db.timescaledb import timescaledb_connect as c

    session_pool = c.get_session_pool()
    session = session_pool()

    try:
        downloader = ZerodhaDownloader(session, exchange_segment='NSE', interval='5m')

        # Test fetching data for a single symbol
        downloader.fetch_recent_historical_data('RELIANCE')

    finally:
        session.close()
