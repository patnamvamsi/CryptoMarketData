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
from app.ingest import manage_zerodha_symbols as zerodha_sym
from app.ingest.zerodha_historical_data import ZerodhaDownloader
from app.stream.get_streaming_kline import StreamKLineData
from app.stream.stream_zerodha_kline import StreamZerodhaKLineData
import csv
import os, sys
from app.db.timescaledb import timescaledb_connect  as c
from app.kafka.kafka_utils import initilaise_topics
from app.logger import setup_logging

# Setup logging at the very start
logger = setup_logging()

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
    logger.info("Started thread for streaming Binance kline data")
    stream_market_data.main()

def stream_zerodha_kline_data():
    session = session_pool()
    stream_market_data = StreamZerodhaKLineData(session, exchange_segment='NSE', interval='1m')
    logger.info("Started thread for streaming Zerodha/NSE kline data")
    stream_market_data.main()

@app.get("/historicaldata")
def fetch_historical_data():
    logger.info("Fetching historical data")
    hist_session = session_pool()
    h.BinanceDownloader(hist_session).fetch_all_historical_data()
    hist_session.close()
    logger.info("Finished Fetching historical data")


@app.get("/historicalgapdata")
def fetch_historical_gap_data():
    logger.info("Fetching historical kline gap data")
    gap_session = session_pool()
    h.BinanceDownloader(gap_session).fetch_all_gap_historical_data()
    gap_session.close()
    logger.info("Finished Fetching historical kline gap data")


def fetch_zerodha_gap_data():
    logger.info("Fetching Zerodha historical kline gap data")
    gap_session = session_pool()
    ZerodhaDownloader(gap_session, exchange_segment='NSE', interval='1m').fetch_all_gap_historical_data()
    gap_session.close()
    logger.info("Finished Fetching Zerodha historical kline gap data")


@app.on_event('startup')
def app_startup():
    scheduler = BackgroundScheduler()

    # Initialize default NSE symbols if configured
    if config.ENABLE_ZERODHA_STREAMING or config.ENABLE_ZERODHA_GAP_FILL:
        try:
            init_session = session_pool()
            # Check if there are any active Zerodha symbols
            active_symbols = zerodha_sym.get_active_symbols(init_session, exchange='zerodha')
            if len(active_symbols) == 0:
                logger.info("No active Zerodha symbols found, initializing defaults...")
                zerodha_sym.initialize_default_nse_symbols(init_session)
            else:
                logger.info(f"Found {len(active_symbols)} active Zerodha symbols")
            init_session.close()
        except Exception as e:
            logger.error(f"Error initializing Zerodha symbols: {e}")

    # Binance services (existing)
    scheduler.add_job(stream_kline_data)
    #scheduler.add_job(fetch_historical_data)
    scheduler.add_job(fetch_historical_gap_data)

    # Zerodha/NSE services (conditional)
    if config.ENABLE_ZERODHA_STREAMING:
        logger.info("Zerodha streaming enabled, adding to scheduler")
        scheduler.add_job(stream_zerodha_kline_data)

    if config.ENABLE_ZERODHA_GAP_FILL:
        logger.info("Zerodha gap fill enabled, adding to scheduler")
        scheduler.add_job(fetch_zerodha_gap_data)

    scheduler.start()
    logger.info("Background scheduler started successfully")


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


# ============================================================================
# ZERODHA/NSE API ENDPOINTS
# ============================================================================

@app.get("/zerodha/update/symbols")
def refresh_zerodha_symbols(exchange_segment: str = 'NSE'):
    """Refresh Zerodha symbols from API"""
    session = session_pool()
    try:
        count = zerodha_sym.refresh_zerodha_symbols(session, exchange_segment=exchange_segment)
        return {"message": f"Refreshed {count} Zerodha {exchange_segment} symbols", "count": count}
    except Exception as e:
        logger.error(f"Error refreshing Zerodha symbols: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@app.post("/zerodha/activatesymbol/{symbol}/{priority}/{state}")
def update_zerodha_symbol_status(symbol: str, priority: str, state: str):
    """Activate/deactivate a Zerodha symbol"""
    bool_state = True if state.upper() == "TRUE" else False
    session = session_pool()
    try:
        zerodha_sym.set_symbol_priority(
            symbol=symbol,
            priority=int(priority),
            session=session,
            active=bool_state,
            exchange='zerodha'
        )
        return {"message": "Successful", "symbol": symbol, "priority": priority, "active": bool_state}
    except Exception as e:
        logger.error(f"Error updating Zerodha symbol {symbol}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@app.get("/zerodha/update/marketdata/{symbol}")
def update_zerodha_market_data(symbol: str, exchange_segment: str = 'NSE', interval: str = '1m'):
    """Update market data for a specific Zerodha symbol"""
    mkt_data_session = session_pool()
    try:
        downloader = ZerodhaDownloader(
            session=mkt_data_session,
            exchange_segment=exchange_segment,
            interval=interval
        )
        msg = downloader.fetch_recent_historical_data(symbol)
        return {"message": msg, "symbol": symbol}
    except Exception as e:
        logger.error(f"Error updating Zerodha market data for {symbol}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        mkt_data_session.close()


@app.get("/zerodha/active-symbols")
def get_active_zerodha_symbols():
    """Get list of active Zerodha symbols"""
    session = session_pool()
    try:
        symbols = zerodha_sym.get_active_symbols(session, exchange='zerodha')
        return {"symbols": symbols, "count": len(symbols)}
    except Exception as e:
        logger.error(f"Error fetching active Zerodha symbols: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@app.post("/zerodha/initialize-defaults")
def initialize_default_nse_symbols():
    """Initialize default NSE symbols for tracking"""
    session = session_pool()
    try:
        zerodha_sym.initialize_default_nse_symbols(session)
        active_symbols = zerodha_sym.get_active_symbols(session, exchange='zerodha')
        return {
            "message": "Default NSE symbols initialized",
            "active_symbols": active_symbols,
            "count": len(active_symbols)
        }
    except Exception as e:
        logger.error(f"Error initializing default NSE symbols: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


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