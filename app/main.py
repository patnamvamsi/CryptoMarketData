import asyncio
import concurrent
import time
from multiprocessing import Process

from fastapi import FastAPI, status, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import text

from app import config
from pydantic import BaseModel
from app.ingest import manage_binance_symbols as sym
from app.ingest import historical_data_to_db as h
from app.ingest import manage_zerodha_symbols as zerodha_sym
from app.ingest.zerodha_historical_data import ZerodhaDownloader
from app.stream.get_streaming_kline import StreamKLineData
from app.stream.stream_zerodha_kline import StreamZerodhaKLineData
from app.stream.stream_binance_ticker import StreamBinanceTicker
import csv
import os, sys
from app.db.timescaledb import timescaledb_connect  as c
from app.kafka.kafka_utils import initilaise_topics
from app.logger import setup_logging

# Setup logging at the very start
logger = setup_logging()

sys.path.insert(1, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
app = FastAPI()

# Configure CORS to allow requests from the Django frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ],
    allow_credentials=True,
    allow_methods=["*"],  # Allow all methods (GET, POST, OPTIONS, etc.)
    allow_headers=["*"],  # Allow all headers
)

session_pool = c.get_session_pool()
initilaise_topics()

# Initialize Redis cache
redis_cache = None
if config.ENABLE_REDIS_CACHE:
    try:
        from app.cache.redis_client import RedisCache, set_redis_cache
        redis_cache = RedisCache(
            host=config.REDIS_HOST,
            port=config.REDIS_PORT,
            db=config.REDIS_DB,
            password=config.REDIS_PASSWORD,
            enabled=config.ENABLE_REDIS_CACHE
        )
        set_redis_cache(redis_cache)
        if redis_cache.health_check():
            logger.info(f"Redis cache initialized at {config.REDIS_HOST}:{config.REDIS_PORT}")
        else:
            logger.warning("Redis health check failed")
    except Exception as e:
        logger.error(f"Failed to initialize Redis: {e}. Running without cache.")
        redis_cache = None
else:
    logger.info("Redis cache disabled by configuration")

# Global ticker stream instance for dynamic symbol management
global_ticker_stream = None

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

def stream_binance_ticker_data():
    """Stream real-time Binance ticker data to Kafka and Redis."""
    global global_ticker_stream
    session = session_pool()
    stream_ticker = StreamBinanceTicker(session, enable_kafka=True, enable_redis=True)
    global_ticker_stream = stream_ticker  # Store globally for dynamic symbol management
    logger.info("Started thread for streaming Binance ticker data (live prices)")
    stream_ticker.main()

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

    # Initialize Zerodha services (with graceful degradation on auth failure)
    zerodha_available = False
    if config.ENABLE_ZERODHA_STREAMING or config.ENABLE_ZERODHA_GAP_FILL:
        try:
            logger.info("Initializing Zerodha services...")

            # Test Zerodha authentication by creating adapter
            from app.exchanges import get_exchange
            try:
                zerodha_adapter = get_exchange('zerodha')
                logger.info("Zerodha authentication successful")
                zerodha_available = True

                # Initialize default NSE symbols if needed
                init_session = session_pool()
                active_symbols = zerodha_sym.get_active_symbols(init_session, exchange='zerodha')
                if len(active_symbols) == 0:
                    logger.info("No active Zerodha symbols found, initializing defaults...")
                    zerodha_sym.initialize_default_nse_symbols(init_session)
                else:
                    logger.info(f"Found {len(active_symbols)} active Zerodha symbols")
                init_session.close()

            except Exception as auth_error:
                logger.error(f"Zerodha authentication failed: {auth_error}")
                logger.warning("Zerodha services will be DISABLED for this session")
                logger.info("Binance services will continue normally")
                zerodha_available = False

        except Exception as e:
            logger.error(f"Error initializing Zerodha services: {e}")
            zerodha_available = False

    # Binance services (existing) - Start in background thread
    import threading
    threading.Thread(target=stream_kline_data, daemon=True, name="BinanceKlineStream").start()
    logger.info("Started Binance kline data stream in background thread")

    # Binance real-time ticker streaming (live prices for UI)
    if config.STREAM_TICKER_KAFKA:
        logger.info("Binance ticker streaming enabled, starting in background thread")
        threading.Thread(target=stream_binance_ticker_data, daemon=True, name="BinanceTickerStream").start()

        # Create Kafka topics for active symbols
        from app.kafka.kafka_utils import create_ticker_topics_for_symbols
        from app.db.timescaledb import crud
        init_session = session_pool()
        active_symbols = crud.get_active_symbols(init_session, active=True)
        create_ticker_topics_for_symbols(active_symbols)
        init_session.close()
    else:
        logger.info("Binance ticker streaming disabled")

    #scheduler.add_job(fetch_historical_data)
    # DISABLED: Gap filling blocks the API for long periods
    # Run manually via endpoint /historicalgapdata when needed
    # scheduler.add_job(fetch_historical_gap_data)

    # Zerodha/NSE services (conditional on availability)
    if zerodha_available:
        if config.ENABLE_ZERODHA_STREAMING:
            logger.info("Zerodha streaming enabled, starting in background thread")
            threading.Thread(target=stream_zerodha_kline_data, daemon=True, name="ZerodhaKlineStream").start()

        # DISABLED: Zerodha gap filling blocks the API for long periods
        # Can be run manually when needed
        # if config.ENABLE_ZERODHA_GAP_FILL:
        #     logger.info("Zerodha gap fill enabled, adding to scheduler")
        #     scheduler.add_job(fetch_zerodha_gap_data)
    else:
        logger.warning("Zerodha services NOT started (authentication unavailable)")

    # Warm up cache with active symbols
    if config.ENABLE_REDIS_CACHE and redis_cache:
        try:
            logger.info("Warming up cache with active symbols...")
            warm_session = session_pool()
            from app.db.timescaledb import crud

            # Warm Binance symbols
            binance_symbols = crud.get_active_symbols_unified(warm_session, exchange='binance', active=True)
            logger.info(f"Cached {len(binance_symbols)} active Binance symbols")

            # Warm Zerodha symbols if available
            if zerodha_available:
                zerodha_symbols = crud.get_active_symbols_unified(warm_session, exchange='zerodha', active=True)
                logger.info(f"Cached {len(zerodha_symbols)} active Zerodha symbols")

            warm_session.close()
            logger.info("Cache warming completed")
        except Exception as e:
            logger.error(f"Error during cache warming: {e}")

    scheduler.start()
    logger.info("Background scheduler started successfully")


@app.get("/symbol/{sym}/from/{start_date}/to/{end_date}")
def get_ranged_historical_data(sym: str, start_date: str, end_date: str):
    # Yet to be implemented
    return sym + start_date + end_date


@app.post("/activatesymbol/{symbol}/{priority}/{state}")
def update_symbol_status(symbol: str, priority: str, state: str,):
    global global_ticker_stream
    bool_state = True if state.upper() == "TRUE" else False
    session = session_pool()

    # Update database
    sym.set_symbol_priority(symbol, int(priority), session, bool_state)
    session.close()

    # Dynamically add/remove symbol from ticker stream
    if global_ticker_stream:
        if bool_state:
            # Activating symbol - add to ticker stream
            from app.kafka.kafka_utils import create_topic, KAFKA_BOOTSTRAP_SERVERS

            # Create Kafka topic for this symbol
            topic_name = f"binance.ticks.{symbol.upper()}"
            create_topic(KAFKA_BOOTSTRAP_SERVERS, topic_name, num_partitions=1, replication_factor=1)

            # Add to ticker stream
            success = global_ticker_stream.add_symbol(symbol)
            logger.info(f"Activated {symbol}: DB updated, Kafka topic created, Ticker stream {'added' if success else 'add failed'}")
        else:
            # Deactivating symbol - remove from ticker stream
            success = global_ticker_stream.remove_symbol(symbol)
            logger.info(f"Deactivated {symbol}: DB updated, Ticker stream {'removed' if success else 'remove failed'}")
    else:
        logger.warning(f"Symbol {symbol} database updated but ticker stream not available for dynamic update")

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


@app.get("/binance/active-symbols")
def get_active_binance_symbols():
    """Get list of active Binance symbols"""
    session = session_pool()
    try:
        from app.db.timescaledb import crud
        symbols = crud.get_active_symbols(session, active=True)
        return {"symbols": list(symbols), "count": len(symbols)}
    except Exception as e:
        logger.error(f"Error fetching active Binance symbols: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@app.get("/binance/symbols")
def get_all_binance_symbols():
    """Get list of all Binance symbols (for autocomplete)"""
    session = session_pool()
    try:
        # Query all symbols from binance_symbols table
        sql = "SELECT symbol FROM binance_symbols ORDER BY symbol"
        result_proxy = session.execute(text(sql))
        symbols = [row[0] for row in result_proxy.fetchall()]
        return {"symbols": symbols, "count": len(symbols)}
    except Exception as e:
        logger.error(f"Error fetching Binance symbols: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


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


@app.get("/zerodha/symbols")
def get_all_zerodha_symbols(exchange_segment: str = 'NSE'):
    """Get list of all Zerodha symbols for specified exchange (for autocomplete)"""
    session = session_pool()
    try:
        # Query all symbols from unified symbols table for zerodha
        # Filter by exchange_raw in metadata JSON field
        sql = f"SELECT symbol FROM symbols WHERE exchange = 'zerodha' AND metadata->>'exchange_raw' = '{exchange_segment}' ORDER BY symbol"
        result_proxy = session.execute(text(sql))
        symbols = [row[0] for row in result_proxy.fetchall()]
        return {"symbols": symbols, "count": len(symbols), "exchange_segment": exchange_segment}
    except Exception as e:
        logger.error(f"Error fetching Zerodha symbols: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


# ============================================================================
# REAL-TIME PRICE API ENDPOINTS (Redis-backed)
# ============================================================================

@app.get("/binance/live-prices")
def get_binance_live_prices(symbols: str = None):
    """
    Get latest live prices from Redis for Binance symbols.

    Query parameters:
        symbols (optional): Comma-separated list of symbols (e.g., "BTCUSDT,ETHUSDT")
                           If not provided, returns all active symbols with cached prices

    Returns:
        {
            "prices": {
                "BTCUSDT": {
                    "price": 43250.50,
                    "price_change_percent": 0.25,
                    "high_24h": 43500.00,
                    ...
                },
                ...
            },
            "count": 10,
            "cached_at": "2025-12-29T12:34:56"
        }
    """
    try:
        if not config.ENABLE_REDIS_CACHE or not redis_cache:
            raise HTTPException(
                status_code=503,
                detail="Redis cache not available. Live prices require Redis to be enabled."
            )

        prices = {}

        # Get symbols to query
        if symbols:
            symbol_list = [s.strip().upper() for s in symbols.split(',')]
        else:
            # Get all active symbols
            from app.db.timescaledb import crud
            session = session_pool()
            try:
                symbol_list = crud.get_active_symbols(session, active=True)
            finally:
                session.close()

        # Fetch prices from Redis
        for symbol in symbol_list:
            redis_key = f"price:binance:{symbol.upper()}"
            price_data = redis_cache.get(redis_key)

            if price_data:
                prices[symbol.upper()] = price_data

        from datetime import datetime
        return {
            "prices": prices,
            "count": len(prices),
            "cached_at": datetime.now().isoformat()
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching live prices from Redis: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/binance/live-price/{symbol}")
def get_binance_live_price(symbol: str):
    """
    Get latest live price from Redis for a specific Binance symbol.

    Path parameters:
        symbol: Trading pair symbol (e.g., "BTCUSDT")

    Returns:
        {
            "symbol": "BTCUSDT",
            "price": 43250.50,
            "price_change_percent": 0.25,
            "high_24h": 43500.00,
            "low_24h": 42800.00,
            "volume_24h": 12345.67,
            "timestamp": 1640000000000,
            "datetime": "2025-12-29T12:34:56"
        }
    """
    try:
        if not config.ENABLE_REDIS_CACHE or not redis_cache:
            raise HTTPException(
                status_code=503,
                detail="Redis cache not available. Live prices require Redis to be enabled."
            )

        redis_key = f"price:binance:{symbol.upper()}"
        price_data = redis_cache.get(redis_key)

        if not price_data:
            raise HTTPException(
                status_code=404,
                detail=f"No live price data found for {symbol.upper()}. "
                       "Symbol may not be active or ticker stream may not be running."
            )

        return price_data

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching live price for {symbol}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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


@app.get("/zerodha/historicalgapdata")
def fetch_zerodha_gap_data_endpoint(exchange_segment: str = 'NSE', interval: str = '1m'):
    """
    Fetch historical gap data for all active Zerodha symbols.

    This endpoint fills missing data gaps in the historical records.
    May take several minutes to complete depending on gaps found.
    """
    logger.info(f"Fetching Zerodha historical kline gap data for {exchange_segment}")
    gap_session = session_pool()
    try:
        downloader = ZerodhaDownloader(gap_session, exchange_segment=exchange_segment, interval=interval)
        downloader.fetch_all_gap_historical_data()
        return {
            "message": f"Zerodha {exchange_segment} gap fill completed",
            "exchange_segment": exchange_segment,
            "interval": interval
        }
    except Exception as e:
        logger.error(f"Error fetching Zerodha gap data: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        gap_session.close()
        logger.info("Finished Fetching Zerodha historical kline gap data")


# ============================================================================
# ZERODHA TOKEN MANAGEMENT ENDPOINTS
# ============================================================================

@app.get("/zerodha/token/status")
def get_zerodha_token_status():
    """Get Zerodha token status from Redis"""
    try:
        if not config.ENABLE_REDIS_CACHE or not redis_cache:
            return {
                "available": False,
                "error": "Redis cache not available"
            }

        from app.auth.token_manager import get_token_manager
        token_manager = get_token_manager(redis_cache)
        token_info = token_manager.get_token_info()

        if not token_info:
            return {
                "available": False,
                "message": "No Zerodha token found in Redis"
            }

        # Get time until expiry
        from datetime import datetime
        expiry = token_manager.get_token_expiry()
        time_left = None
        if expiry:
            time_left_seconds = (expiry - datetime.utcnow()).total_seconds()
            time_left = {
                "seconds": int(time_left_seconds),
                "hours": round(time_left_seconds / 3600, 1)
            }

        return {
            "available": True,
            "user_id": token_info.get("user_id"),
            "user_name": token_info.get("profile", {}).get("user_name"),
            "generated_at": token_info.get("generated_at"),
            "expires_at": token_info.get("expires_at"),
            "time_remaining": time_left
        }

    except Exception as e:
        logger.error(f"Error checking Zerodha token status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/zerodha/token/refresh")
def refresh_zerodha_token():
    """
    Manually refresh Zerodha access token and store in Redis.

    This forces re-authentication and updates the token in Redis.
    Useful for manual token refresh without restarting the service.
    """
    try:
        logger.info("Manual Zerodha token refresh requested")

        if not config.ENABLE_REDIS_CACHE or not redis_cache:
            raise HTTPException(
                status_code=400,
                detail="Redis cache not available - token refresh requires Redis"
            )

        # Force creation of new ZerodhaAdapter (which will re-authenticate)
        from app.exchanges.exchange_factory import ExchangeFactory
        factory = ExchangeFactory()

        # Clear cached adapter to force re-initialization
        factory._adapters.pop('zerodha', None)

        # Create new adapter (triggers authentication and Redis storage)
        zerodha_adapter = factory.get_exchange('zerodha')

        # Get updated token info from Redis
        from app.auth.token_manager import get_token_manager
        token_manager = get_token_manager(redis_cache)
        token_info = token_manager.get_token_info()

        if token_info:
            logger.info(f"Zerodha token refreshed for user {token_info.get('user_id')}")
            return {
                "success": True,
                "message": "Zerodha token refreshed successfully",
                "user_id": token_info.get("user_id"),
                "expires_at": token_info.get("expires_at")
            }
        else:
            raise HTTPException(status_code=500, detail="Token refreshed but not found in Redis")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error refreshing Zerodha token: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to refresh token: {str(e)}")


# ============================================================================
# CACHE MANAGEMENT ENDPOINTS
# ============================================================================

@app.get("/cache/stats")
def get_cache_stats():
    """Get cache statistics (hit rate, memory usage, etc.)"""
    try:
        from app.cache.redis_client import get_redis_cache
        from app.config import config as cfg
        cache = get_redis_cache()

        if not cache or not cfg.ENABLE_REDIS_CACHE:
            return {
                "enabled": False,
                "message": "Redis cache is disabled"
            }

        stats = cache.get_stats()
        health = cache.health_check()

        return {
            "enabled": True,
            "healthy": health,
            "stats": stats,
            "redis_config": {
                "host": cfg.REDIS_HOST,
                "port": cfg.REDIS_PORT,
                "db": cfg.REDIS_DB
            }
        }
    except Exception as e:
        logger.error(f"Error fetching cache stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/cache/clear")
def clear_all_cache():
    """Clear all cached data"""
    try:
        from app.cache.redis_client import get_redis_cache
        cache = get_redis_cache()

        if not cache:
            raise HTTPException(status_code=400, detail="Redis cache not available")

        cache.clear_all()
        logger.info("Cleared all cache data")
        return {"message": "All cache cleared successfully"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error clearing cache: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/cache/clear/{pattern}")
def clear_cache_pattern(pattern: str):
    """Clear cache entries matching a pattern (e.g., 'active_symbols:*')"""
    try:
        from app.cache.redis_client import get_redis_cache
        cache = get_redis_cache()

        if not cache:
            raise HTTPException(status_code=400, detail="Redis cache not available")

        # Add service prefix if not present
        if not pattern.startswith("cryptomarket:"):
            pattern = f"cryptomarket:{pattern}"

        deleted_count = cache.delete_pattern(pattern)
        logger.info(f"Cleared {deleted_count} cache entries matching pattern: {pattern}")

        return {
            "message": f"Cleared cache pattern: {pattern}",
            "deleted_count": deleted_count
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error clearing cache pattern {pattern}: {e}")
        raise HTTPException(status_code=500, detail=str(e))



# ============================================================================
# NSE DATA INGESTION ENDPOINTS & SCHEDULER
# ============================================================================

from datetime import date as _date, datetime as _datetime

@app.post("/ingest/start/{pipeline}")
def start_nse_ingest(
    pipeline: str,
    start_date: str = None,
    end_date: str = None,
    skip_existing: bool = True,
):
    """
    Start an NSE ingestion pipeline as a background task.

    pipeline: equity | fo | index | all
    Runs detached in a daemon thread so FastAPI doesn't block.
    """
    from app.ingest.pipeline_runner import start_ingest, PIPELINE_CLASSES

    valid = list(PIPELINE_CLASSES) + ["all"]
    if pipeline not in valid:
        raise HTTPException(status_code=400, detail=f"Unknown pipeline '{pipeline}'. Choose from {valid}")

    sd = _datetime.strptime(start_date, "%Y-%m-%d").date() if start_date else None
    ed = _datetime.strptime(end_date, "%Y-%m-%d").date() if end_date else None

    if pipeline == "all":
        results = {}
        for name in PIPELINE_CLASSES:
            results[name] = start_ingest(name, start_date=sd, end_date=ed,
                                         skip_existing=skip_existing, background=True)
        return results
    else:
        return start_ingest(pipeline, start_date=sd, end_date=ed,
                            skip_existing=skip_existing, background=True)


@app.get("/ingest/status")
def get_nse_ingest_status():
    """Return progress JSON for all NSE ingestion pipelines."""
    from app.ingest.pipeline_runner import get_all_status
    return get_all_status()


@app.post("/ingest/stop/{pipeline}")
def stop_nse_ingest(pipeline: str):
    """Gracefully stop a running ingestion pipeline."""
    from app.ingest.pipeline_runner import stop_ingest, PIPELINE_CLASSES

    if pipeline == "all":
        results = {}
        for name in PIPELINE_CLASSES:
            results[name] = stop_ingest(name)
        return results
    else:
        result = stop_ingest(pipeline)
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        return result


# Register NSE daily ingest scheduler jobs inside app_startup
_original_app_startup = app_startup

def _patched_app_startup():
    _original_app_startup()

    # NSE daily ingestion scheduler
    from app.config import config_template as cfg
    if getattr(cfg, 'ENABLE_NSE_DAILY_INGEST', False):
        from apscheduler.triggers.cron import CronTrigger
        from app.ingest.pipeline_runner import daily_catchup_all
        import pytz

        ist = pytz.timezone('Asia/Kolkata')
        hour = getattr(cfg, 'NSE_INGEST_SCHEDULE_HOUR', 16)
        minute = getattr(cfg, 'NSE_INGEST_SCHEDULE_MINUTE', 30)

        # We need the scheduler instance — create one that lives with the app
        nse_scheduler = BackgroundScheduler(timezone=ist)
        nse_scheduler.add_job(
            daily_catchup_all,
            CronTrigger(hour=hour, minute=minute, day_of_week='mon-fri', timezone=ist),
            id='nse_daily_ingest',
            name='NSE Daily Catch-up (equity+fo+index)',
            replace_existing=True,
        )
        nse_scheduler.start()
        logger.info(
            f"NSE daily ingestion scheduled at {hour:02d}:{minute:02d} IST, Mon-Fri"
        )
    else:
        logger.info("NSE daily ingestion scheduler disabled (ENABLE_NSE_DAILY_INGEST=false)")

# Replace the startup event
app.on_event('startup')(lambda: None)  # clear original
app.router.on_startup.clear()
app.router.on_startup.append(_patched_app_startup)


'''
future apis
accept the source binance or coinbase etc, default to binance
'''