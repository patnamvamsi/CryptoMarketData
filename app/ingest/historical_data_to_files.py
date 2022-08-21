import os
from os import path
import connect_binance as cb
from binance import Client
from app.config import config
import csv
import datetime
import connect_binance


class BinanceDownloader(cb.BinanceData):

    def __init__(self):
        self.data_root_dir = config.DATA_ROOT_DIR

    def _write_ticker_to_file(self, symbol, start_ticker_time, end_date):
        start_date = datetime.datetime.fromtimestamp(start_ticker_time)
        file_name = symbol + "_" + str(start_date.strftime('%d-%b-%Y')) + "_" + str(end_date.strftime('%d-%b-%Y'))
        end_date = end_date.strftime('%d %b %Y')

        dir = self.data_root_dir + symbol
        if not path.exists(dir):
            os.mkdir(dir)
        candle_sticks = connect_binance.BinanceData().get_kline_data(symbol, Client.KLINE_INTERVAL_1DAY,
                                                                     str(start_ticker_time), end_date)

        try:
            csv_file = open(dir + "/" + file_name, 'w', newline='')
            candlestick_writer = csv.writer(csv_file, delimiter=',')

            for candlestick in candle_sticks:
                candlestick[0] = candlestick[0] / 1000
                candlestick_writer.writerow(candlestick)
            csv_file.close()
        except BaseException as err:
            print("Error writing to a file" + err)

    def fetch_symbols(self):  # get from DB
        with open('Symbols.txt', 'r') as reader:
            sym = reader.read().splitlines()
            return sym

    def _fetch_all_historical_data(self):
        end_date = datetime.date.today()
        symbols = self.fetch_symbols()

        for symbol in symbols:
            print("Fetching: " + symbol)
            max_ticker_time = self._get_most_recent_timestamp(self.data_root_dir, symbol) / 1000
            start_date = datetime.datetime.fromtimestamp(max_ticker_time).strftime('%d %b %Y')

            self._write_ticker_to_file(symbol, max_ticker_time, end_date)

    def _fetch_historical_data(self, symbol, start_date, end_date):
        print("Fetching: " + symbol)
        self._write_ticker_to_file(symbol, start_date, end_date)
        print("Completed Fetching: " + symbol)

    def _get_most_recent_timestamp(data_root_dir, symbol):
        # start_time is considered as 01-01-2010 epoch equivalent  = 1262304000000
        max_time = 1262304000000

        directory = data_root_dir + symbol
        if not path.exists(directory):
            os.mkdir(directory)

        try:
            for filename in os.listdir(directory):
                try:
                    with open(os.path.join(directory, filename), 'r') as f:
                        if os.fstat(f.fileno()).st_size > 0:  # checking if file is empty
                            last_line = f.read().splitlines()[-1]
                            reader = csv.reader(f)
                            for row in reader:
                                if int(float(row[0])) > max_time:
                                    max_time = int(float(row[0]))
                            # last_line = f.read().splitlines()[-1]
                            if max_time < int(last_line.split(",")[6]):
                                max_time = int(last_line.split(",")[6])
                except Exception as file_ex:
                    print("Error Reading file:" + filename + str(file_ex))
        except Exception as ex:
            print("Coudn't fetch latest ticker time, using 01-01-2010 00:00" + ex)

        return max_time


    #_fetch_all_historical_data()

# to do softcode kline 1day , 1 min etc and also cater for directory.

