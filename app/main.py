import asyncio
import concurrent
import time
from multiprocessing import Process

from fastapi import FastAPI, status, HTTPException, BackgroundTasks
from apscheduler.schedulers.background import BackgroundScheduler

from app import config
from pydantic import BaseModel
from app.ingest import manage_binance_symbols as sym
from app.ingest import historical_data_to_db as h
from app.stream.get_streaming_kline import StreamKLineData
import csv
import os, sys
from app.db.timescaledb import timescaledb_connect  as c
from app.kafka.kafka_utils import initilaise_topics

sys.path.insert(1, os.path)
app = FastAPI()
session_pool = c.get_session_pool()
initilaise_topics()

# pydantic semantic checks for the historical model
class historicaldata_post(BaseModel):
    sym: str
    start_date: str
    end_date: str
    service_name: str = "unknown"


@app.get("/")
def landing():
    return "welcome to the crypto market data module"

def stream_kline_data():
    session = session_pool()
    stream_market_data = StreamKLineData(session)
    print ("Started thread")
    stream_market_data.main()

@app.get("/historicaldata")
def fetch_historical_data():
    print("Fetching historical data")
    hist_session = session_pool()
    h.BinanceDownloader(hist_session).fetch_all_historical_data()
    hist_session.close()
    print("Finished Fetching historical data")


@app.get("/historicalgapdata")
def fetch_historical_gap_data():
    print("Fetching historical kline gap data")
    gap_session = session_pool()
    h.BinanceDownloader(gap_session).fetch_all_gap_historical_data()
    gap_session.close()
    print("Finished Fetching historical kline gap data")


@app.on_event('startup')
def app_startup():
    scheduler = BackgroundScheduler()
    scheduler.add_job(stream_kline_data)
    #scheduler.add_job(fetch_historical_data)
    scheduler.add_job(fetch_historical_gap_data)
    scheduler.start()


@app.get("/symbol/{sym}/from/{start_date}/to/{end_date}")
def get_ranged_historical_data(sym: str, start_date: str, end_date: str):
    # Yet to be implemented
    return sym + start_date + end_date


@app.post("/activatesymbol/{symbol}/{priority}/{state}")
def update_symbol_status(symbol: str, priority: str, state: str,):
    bool = True if state.upper() == "TRUE" else False
    session = session_pool()
    sym.set_symbol_priority(symbol, int(priority), session, bool)
    session.close()
    return "Successful"


@app.get("/update/symbols")
def refresh_symbols():
    session = session_pool()
    sym.refresh_binance_symbols(session)
    session.close()
    return "Symbols refreshed"  # return number of new symbols


@app.get("/update/marketdata/{symbol}")
def update_market_data(symbol: str):
    mkt_data_session = session_pool()
    c = h.BinanceDownloader(mkt_data_session)
    msg = c.fetch_recent_historical_data(symbol)
    mkt_data_session.close()
    return msg


'''
future apis
accept the source binance or coinbase etc, default to binance
1. Design  decision -- how to stream data with min lag -- required for live trading, not now

'''
'''
uses these status codes to return correct ones:"
HTTP_200_OK = 200
HTTP_201_CREATED = 201
HTTP_202_ACCEPTED = 202  -- for batch processing
https://developer.mozilla.org/en-US/docs/Web/HTTP/Status
'''

''' iki '''