import app.ingest.connect_binance as cb
from binance import Client
from app.config import config
import datetime
import app.db.timescaledb.crud as q


class BinanceDownloader(cb.BinanceData):
    """
    Downloads Binance market data and updates database
    Fetches symbols marked as "Active"
    """

    def __init__(self, session):
        super().__init__()
        self.exchange = 'binance' #change this to extend for other exchanges
        self.kline = Client.KLINE_INTERVAL_1MINUTE
        # start_time is considered as 01-01-2010 epoch equivalent  = 1262304000000
        self.BEGIN_OF_THE_TIME = 1262304000
        self.session = session

    def update_kline_tables(self, symbol, start_time_epoch, end_time_epoch):
        candle_sticks = self.get_kline_data(symbol, self.kline, start_time_epoch, end_time_epoch)
        if len(candle_sticks) > 0:
            q.insert_kline_rows(symbol, self.kline, candle_sticks, self.session)
        else:
            print("No New data available")

    def fetch_symbols(self):
        return q.get_active_symbols(self.session, True)

    def fetch_all_historical_data(self):
        symbols = self.fetch_symbols()
        for symbol in symbols:
            self.fetch_recent_historical_data(symbol)

    def fetch_all_gap_historical_data(self):
        symbols = self.fetch_symbols()
        for symbol in symbols:
            self.fetch_gap_hostorical_data(symbol)

    def fetch_recent_historical_data(self, symbol):
        q.create_table_if_not_exists(symbol, self.kline, self.session)
        start_time = self._get_most_recent_timestamp(symbol)
        end_time = datetime.datetime.now().timestamp()
        print("Fetching: " + symbol + " Since: " + str(start_time) + " Till: "+ str(end_time)) #info
        self.update_kline_tables(symbol, start_time, end_time)
        return "Completed Fetching: " + symbol


    def fetch_gap_hostorical_data(self, symbol):
        rs = q.find_gaps_in_kline_data(symbol, self.kline, self.session)
        for row in rs:
            print("Fetching: " + symbol + " Since: " + str(row.gap_start) + " Till: " + str(row.gap_end))  # info
            self.update_kline_tables(symbol, row.gap_start.timestamp(), row.gap_end.timestamp())

    def _get_most_recent_timestamp(self, symbol):
        table_name = q.get_table_name(symbol, self.kline)
        max_time = q.get_max_timestamp(table_name, self.session)
        if max_time is not None:
            return max_time.timestamp()
        return self.BEGIN_OF_THE_TIME
