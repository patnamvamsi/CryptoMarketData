from app.config import config
from binance.client import Client
import datetime
import traceback
import pytz
import logging
from app.logger import setup_logging

logger = logging.getLogger(__name__)

class BinanceData:

    def __init__(self):
        self.client = Client(config.API_KEY, config.API_SECRET)

    def get_kline_data(self, symbol, kline, start_ticker_time, end_ticker_time):
        start_date = datetime.datetime.fromtimestamp(start_ticker_time, pytz.timezone("UTC"))
        end_date = datetime.datetime.fromtimestamp(end_ticker_time, pytz.timezone("UTC"))
        try:
            #candlesticks = self.client.get_historical_klines(symbol.upper(), kline, str(start_ticker_time), str(end_ticker_time))
            candlesticks = self.client.get_historical_klines(symbol.upper(), kline, str(start_date),
                                                             str(end_date))
            logger.info("Received {} kline candlesticks".format(len(candlesticks)))
        except Exception as ex:
            logger.error("Error getting historical klines for symbol:" + symbol + str(ex))
            traceback.print_exception(type(ex), ex, ex.__traceback__)

        return candlesticks

    def _get_symbols(self):
        try:
            tickers = self.client.get_all_tickers()
            symbols = []
            for tick in tickers:
                symbols.append(tick['symbol'])
        except:
            logger.error("_get_symbols:" + "Error fetching ticker symbols")
        return symbols


# to do softcode kline 1day , 1 min etc and also cater for directory.

"""
output: 
[
    [
        1499040000000,      # Open time
        "0.01634790",       # Open
        "0.80000000",       # High
        "0.01575800",       # Low
        "0.01577100",       # Close
        "148976.11427815",  # Volume
        1499644799999,      # Close time
        "2434.19055334",    # Quote asset volume
        308,                # Number of trades
        "1756.87402397",    # Taker buy base asset volume
        "28.46694368",      # Taker buy quote asset volume
        "17928899.62484339" # Can be ignored
    ]
]
"""
