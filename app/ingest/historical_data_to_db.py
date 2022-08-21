import app.ingest.connect_binance as cb
from binance import Client
from app.config import config
import datetime
import app.db.timescaledb.queries as q

class BinanceDownloader(cb.BinanceData):

    def __init__(self):
        super().__init__()
        self.data_root_dir = config.DATA_ROOT_DIR
        self.exchange = 'binance' #change this to extend for other exchanges
        self.kline = Client.KLINE_INTERVAL_1MINUTE
        # start_time is considered as 01-01-2010 epoch equivalent  = 1262304000000
        self.BEGIN_OF_THE_TIME = 1262304000

    def update_kline_tables(self, symbol, start_time_epoch, end_time_epoch):

        candle_sticks = self.get_kline_data(symbol,self.kline,start_time_epoch, end_time_epoch)
        if candle_sticks is not None:
            q.insert_kline_rows(symbol, self.kline, candle_sticks)
        else:
            print("No New data available")


    def fetch_symbols(self):  # get from DB
        return q.get_active_symbols(True)

    def _fetch_all_historical_data(self):
        end_date = datetime.datetime.now().timestamp()
        symbols = self.fetch_symbols()

        for symbol in symbols:
            print("Fetching: " + symbol)
            max_ticker_time = self._get_most_recent_timestamp(symbol)
            self.update_kline_tables(symbol, max_ticker_time, end_date)

    def _fetch_historical_data(self, symbol, start_date_epoch, end_date_epoch):
        print("Fetching: " + symbol)
        self.update_kline_tables(symbol, start_date_epoch, end_date_epoch)
        print("Completed Fetching: " + symbol)

    def _get_most_recent_timestamp(self, symbol):

        table_name = q.get_table_name(symbol, self.kline)
        max_time = q.get_max_timestamp(table_name)
        if max_time is not None:
            return max_time.timestamp()
        return self.BEGIN_OF_THE_TIME


    #_fetch_all_historical_data()

# to do softcode kline 1day , 1 min etc and also cater for directory.

