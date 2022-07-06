import os
from os import path
from config import config
import  csv
from binance.client import Client
import datetime

client = Client(config.API_KEY, config.API_SECRET)
data_root_dir = config.DATA_ROOT_DIR


def _write_ticker_to_file(symbol, start_ticker_time, end_date):
    start_date = datetime.datetime.fromtimestamp(start_ticker_time)

    file_name = symbol + "_" + str(start_date.strftime('%d-%b-%Y')) + "_" + str(end_date.strftime('%d-%b-%Y'))
    end_date = end_date.strftime('%d %b %Y')

    dir = data_root_dir + symbol
    if not path.exists(dir):
        os.mkdir(dir)

    try:
        candlesticks = client.get_historical_klines(symbol, Client.KLINE_INTERVAL_1DAY, str(start_ticker_time),
                                                    end_date)  # KLINE_INTERVAL_1MINUTE
    except Exception as ex:
        print("Error getting historical klines for symbol:" + symbol + str(ex))

    try:
        csvfile = open(dir + "/" + file_name, 'w', newline='')
        candlestick_writer = csv.writer(csvfile, delimiter=',')

        for candlestick in candlesticks:
            candlestick[0] = candlestick[0] / 1000
            candlestick_writer.writerow(candlestick)
        csvfile.close()
    except:
        print("error writing to a file")


def _get_symbols():
    try:
        tickers = client.get_all_tickers()
        symbols = []
        for tick in tickers:
            symbols.append(tick['symbol'])
    except:
        print("_get_symbols:" + "Error fetching ticker symbols")
    return (symbols)


def fetch_symbols():  #get from DB
    with open('Symbols.txt', 'r') as reader:
        sym = reader.read().splitlines()
        return sym


def _find_latest_timestamp(symbol):
    # implement logic to fetch the latest from DB\storage
    return 0


def _fetch_all_historical_data():
    end_date = datetime.date.today()
    symbols = fetch_symbols()

    for symbol in symbols:
        print("Fetching: " + symbol)
        max_ticker_time = _get_most_recent_timestamp(data_root_dir, symbol) / 1000
        start_date = datetime.datetime.fromtimestamp(max_ticker_time).strftime('%d %b %Y')

        _write_ticker_to_file(symbol, max_ticker_time, end_date)


def _fetch_historical_data(symbol, start_date, end_date):
    print("Fetching: " + symbol)
    _write_ticker_to_file(symbol, start_date, end_date)
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
                        #last_line = f.read().splitlines()[-1]
                        if max_time < int(last_line.split(",")[6]):
                            max_time = int(last_line.split(",")[6])
            except Exception as file_ex:
                print("Error Reading file:" + filename + str(file_ex))
    except Exception as ex:
        print("Coudn't fetch latest ticker time, using 01-01-2010 00:00" + ex)

    return max_time

_fetch_all_historical_data()

#to do softcode kline 1day , 1 min etc and also cater for directory.

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