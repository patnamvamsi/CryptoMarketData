import datetime
import traceback

import sqlalchemy
from sqlalchemy import create_engine, text
from app.config import config as cfg
import pandas as pd
import logging
from app.logger import setup_logging
logger = logging.getLogger(__name__)

# Redis cache imports
from app.cache.decorators import cache_result
from app.config import config

def create_kline_temp_table():
    """
    Creates a temporary table to store kline data.
    """
    query = f"""
        CREATE TABLE temp_kline_binance(
        open_time NUMERIC,
        open NUMERIC,
        high NUMERIC,
        low NUMERIC,
        close NUMERIC,
        volume NUMERIC ,
        close_time NUMERIC,
        quote_asset_volume NUMERIC,
        trades NUMERIC,
        taker_buy_base_asset_volume NUMERIC,
        taker_buy_quote_asset_volume NUMERIC,
        ignore NUMERIC
    );
    """
    return query


def truncate_temp_kline_table(exchange='binance'):
    """
    Generate query to truncate temporary kline table.

    Args:
        exchange: Exchange name (default: 'binance' for backward compatibility)

    Returns:
        SQL query string
    """
    query = f"TRUNCATE TABLE temp_kline_{exchange}"
    return query


def create_kline_binance_table(symbol, kline_interval, exchange='binance'):
    """
    Creates a table to store kline data.

    Args:
        symbol: Trading symbol
        kline_interval: Time interval
        exchange: Exchange name (default: 'binance' for backward compatibility)

    Returns:
        Tuple of (query, table_name)
    """
    table_name = get_table_name(symbol, kline_interval, exchange)
    query = f"""create table {table_name}
 (
        open_time TIMESTAMPTZ,
        open NUMERIC,
        high NUMERIC,
        low NUMERIC,
        close NUMERIC,
        volume NUMERIC ,
        close_time TIMESTAMPTZ,
        quote_asset_volume NUMERIC,
        trades NUMERIC,
        taker_buy_base_asset_volume NUMERIC,
        taker_buy_quote_asset_volume NUMERIC,
        ignore NUMERIC
    );

    SELECT create_hypertable('{table_name}', 'open_time');

    CREATE UNIQUE INDEX idx_{table_name} ON {table_name}(open_time);
    """
    return query, table_name


def sanitize_symbol_for_table(symbol):
    """
    Sanitize symbol name for use in PostgreSQL table names.
    Replaces all special characters (non-alphanumeric) with underscores.

    Args:
        symbol: Trading symbol (e.g., 'BAJAJ-AUTO', 'M&M')

    Returns:
        Sanitized symbol (e.g., 'bajaj_auto', 'm_m')
    """
    import re
    # Replace any non-alphanumeric character with underscore
    sanitized = re.sub(r'[^a-zA-Z0-9]', '_', symbol.lower())
    # Remove consecutive underscores
    sanitized = re.sub(r'_+', '_', sanitized)
    # Remove leading/trailing underscores
    sanitized = sanitized.strip('_')
    return sanitized


def get_table_name(symbol, kline_interval, exchange='binance'):
    """
    Generate table name for kline data.

    Args:
        symbol: Trading symbol
        kline_interval: Time interval
        exchange: Exchange name (default: 'binance' for backward compatibility)

    Returns:
        Table name in format: {exchange}_{symbol}_kline_{interval}
    """
    sanitized_symbol = sanitize_symbol_for_table(symbol)
    table_name = f"{exchange}_{sanitized_symbol}_kline_{kline_interval}"
    return table_name


@cache_result(
    key_pattern="cryptomarket:table_exists:{exchange}:{symbol}:{kline_interval}",
    ttl=None,  # No expiry - tables rarely deleted
    enabled_check=lambda: config.ENABLE_REDIS_CACHE
)
def check_if_table_exists(symbol, kline_interval, exchange='binance'):
    """
    Checks if a table exists.

    Args:
        symbol: Trading symbol
        kline_interval: Time interval
        exchange: Exchange name (default: 'binance' for backward compatibility)

    Returns:
        Tuple of (query, table_name)
    """
    table_name = get_table_name(symbol, kline_interval, exchange)
    query = f"""
        SELECT EXISTS(
            SELECT 1
            FROM   information_schema.tables
            WHERE  table_schema = 'public'
            AND    table_name = '{table_name}'
        )
    """
    return query, table_name


def load_kline_temp_to_main(symbol, kline_interval, exchange='binance'):
    """
    Loads kline data from temp table to main table.

    Args:
        symbol: Trading symbol
        kline_interval: Time interval
        exchange: Exchange name (default: 'binance' for backward compatibility)

    Returns:
        Tuple of (query, table_name)
    """
    table_name = get_table_name(symbol, kline_interval, exchange)
    temp_table = f"temp_kline_{exchange}"
    query = f"""
        INSERT INTO {table_name}
        SELECT to_timestamp(open_time) ,
        open,
        high,
        low,
        close,
        volume ,
        to_timestamp(close_time),
        quote_asset_volume,
        trades,
        taker_buy_base_asset_volume,
        taker_buy_quote_asset_volume,
        ignore
        FROM {temp_table}
        ON CONFLICT (open_time)
        DO NOTHING;
    """
    return query, table_name


def create_sqlalchemy_engine_conn():
    ts_engine = create_engine('postgresql://' + cfg.TIMESCALE_USERNAME + ':' +
                              cfg.TIMESCALE_PASSWORD + '@' +
                              cfg.TIMESCALE_HOST + ':' +
                              cfg.TIMESCALE_PORT + '/' +
                              cfg.TIMESCALE_MARKET_DATA_DB
                              )
    return ts_engine


def insert_kline_rows(symbol, kline, candle_sticks, session, exchange='binance'): # handle duplicate values
    """
    Insert kline data into database using temporary table staging.

    Args:
        symbol: Trading symbol
        kline: Kline interval
        candle_sticks: List of candle data
        session: Database session
        exchange: Exchange name (default: 'binance' for backward compatibility)

    Returns:
        None
    """
    # Rollback any previous failed transaction to ensure clean state
    try:
        session.rollback()
    except Exception:
        pass  # Ignore if no transaction to rollback

    temp_table = f'temp_kline_{exchange}'
    meta = sqlalchemy.MetaData()
    session.execute(text(truncate_temp_kline_table(exchange)))
    kline_table = sqlalchemy.Table(temp_table, meta, autoload_with=session.bind)
    kline_table_ins = kline_table.insert()

    fields = ["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_asset_volume",
              "trades", "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume", "ignore"]

    candle_sticks = [[row[0] / 1000,
                      float(row[1]),
                      float(row[2]),
                      float(row[3]),
                      float(row[4]),
                      float(row[5]),
                      row[6] / 1000,
                      float(row[7]),
                      float(row[8]),
                      float(row[9]),
                      float(row[10]),
                      float(row[11])
                      ] for row in candle_sticks]
    xs = [{k: v for k, v in zip(fields, row)} for row in candle_sticks]
    try:
        session.execute(kline_table_ins, xs)
        session.commit()
        query, table_name = load_kline_temp_to_main(symbol, kline, exchange)
        session.execute(text(query))
        session.commit()

        # Update max_timestamp cache with latest value
        from app.cache.redis_client import get_redis_cache
        cache = get_redis_cache()
        if cache and candle_sticks:
            # Get the latest close_time from inserted data
            latest_close_time = max(row[6] for row in candle_sticks)
            cache_key = f"cryptomarket:max_timestamp:{table_name}"
            from datetime import datetime
            cache.set(cache_key, datetime.fromtimestamp(latest_close_time).isoformat(), ttl=120)

    except Exception as ex:
        logger.error("Error " + symbol + str(ex))
        traceback.print_exception(type(ex), ex, ex.__traceback__)
        session.rollback()  # Rollback failed transaction to allow future operations

    return

def update_binance_symbols(df, session):
    ts_engine = create_sqlalchemy_engine_conn()

    df.to_sql('temp_binance_symbols', ts_engine, if_exists='replace', dtype=
    {'filters': sqlalchemy.types.JSON, 'ordertypes': sqlalchemy.types.JSON, 'permissions': sqlalchemy.types.JSON})

    update_sql = '''
    UPDATE binance_symbols  p
        SET
        status= T.status,
        baseAsset = T.baseAsset,
        baseAssetPrecision  = T.baseAssetPrecision,
        quoteAsset = T.quoteAsset,
        quotePrecision  = T.quotePrecision ,
        quoteAssetPrecision  = T.quoteAssetPrecision ,
        baseCommissionPrecision  = T.baseCommissionPrecision ,
        quoteCommissionPrecision  = T.quoteCommissionPrecision ,
        orderTypes  = T.orderTypes ,
        icebergAllowed   = T.icebergAllowed  ,
        ocoAllowed = T.ocoAllowed ,
        quoteOrderQtyMarketAllowed  = T.quoteOrderQtyMarketAllowed ,
        allowTrailingStop = T.allowTrailingStop ,
        isSpotTradingAllowed  = T. isSpotTradingAllowed ,
        isMarginTradingAllowed  = T.isMarginTradingAllowed  ,
        filters  = T.filters ,
        permissions  = T.permissions ,
        version = P.version + 1,
        last_updated = T.last_updated
        FROM temp_binance_symbols  T WHERE p.symbol = T.symbol
        '''

    insert_sql = '''INSERT INTO binance_symbols 
    SELECT symbol,
status,
baseasset,
baseassetprecision,
quoteasset,
quoteprecision,
quoteassetprecision,
basecommissionprecision,
quotecommissionprecision,
ordertypes,
icebergallowed,
ocoallowed,
quoteorderqtymarketallowed,
allowtrailingstop,
isspottradingallowed,
ismargintradingallowed,
filters,
permissions,
priority,
active,
version,
last_updated FROM temp_binance_symbols WHERE
    symbol NOT IN (SELECT symbol from binance_symbols)'''

    drop_temp_table = 'DROP TABLE temp_binance_symbols'

    try:
        logger.info("updating binance_symbols")
        with ts_engine.begin() as conn:  # TRANSACTION
            conn.execute(update_sql)
            conn.execute(insert_sql)
            conn.execute("commit")
            conn.execute(drop_temp_table)
            conn.close()
    except Exception as ex:
        logger.error("Error updating binance_symbols table" + str(ex))
        traceback.print_exception(type(ex), ex, ex.__traceback__)

    return 0  # return row count


def update_symbol_config(symbol, priority, activate, session):

    sql = f"""
    UPDATE binance_symbols
    SET
    priority = {priority},
    active = {activate},
    version = version + 1,
    last_updated = CURRENT_TIMESTAMP
    WHERE symbol = '{symbol}'"""

    try:
        session.execute(text(sql))
        session.commit()  # Commit the transaction to persist changes
    except Exception as ex:
        logger.error("Error updating symbol:" + symbol + str(ex))
        traceback.print_exception(type(ex), ex, ex.__traceback__)
        session.rollback()  # Rollback on error
    return


@cache_result(
    key_pattern="cryptomarket:max_timestamp:{table}",
    ttl=120,  # 2 minutes
    enabled_check=lambda: config.ENABLE_REDIS_CACHE
)
def get_max_timestamp(table, session):

    sql = f"""
    SELECT max(close_time)
    FROM {table}"""

    rs = session.execute(text(sql))
    if rs.returns_rows:
        for row in rs:
            return row[0]
    return None


def get_active_symbols(session, active=True):
    sql = f"""
    SELECT symbol from binance_symbols
    WHERE active = {active} order by  priority"""

    result_proxy = session.execute(text(sql))
    results = result_proxy.fetchall()

    if not results:
        return []

    symbol_list = pd.DataFrame(results)[0].values
    return symbol_list


def create_table_if_not_exists(symbol, kline_interval, session, exchange='binance'):
    """
    Creates a table in the TimescaleDB database if it does not exist.

    Args:
        symbol: Trading symbol
        kline_interval: Time interval
        session: Database session
        exchange: Exchange name (default: 'binance' for backward compatibility)

    Returns:
        table_name: Name of the created or existing table
    """

    #check if table exists
    query, table_name = check_if_table_exists(symbol, kline_interval, exchange)
    rs = session.execute(text(query))

    # create table if it does not exist
    if rs.fetchone()[0] == False:
        query,table_name = create_kline_binance_table(symbol, kline_interval, exchange)
        session.execute(text(query))
        session.commit()
        logger.info("Created table {}".format(table_name))

        # Update cache to reflect table now exists
        from app.cache.redis_client import get_redis_cache
        cache = get_redis_cache()
        if cache:
            cache_key = f"cryptomarket:table_exists:{exchange}:{symbol}:{kline_interval}"
            cache.set(cache_key, True, ttl=None)

        return table_name
    else:
        logger.info("Table {} already exists".format(table_name))
        return table_name


def find_gaps_in_kline_data(symbol, kline_interval, session, exchange='binance'):
    """
    Find gaps in historical kline data.

    Args:
        symbol: Trading symbol
        kline_interval: Time interval
        session: Database session
        exchange: Exchange name (default: 'binance' for backward compatibility)

    Returns:
        ResultSet with gap information
    """
    table_name = get_table_name(symbol, kline_interval, exchange)
    query = f"""
            WITH gaps AS (
              SELECT
                open_time AS gap_start,
                LEAD(open_time) OVER (ORDER BY open_time) AS gap_end
              FROM
                {table_name}
            )
            SELECT
              gap_start,
              gap_end,
              EXTRACT(EPOCH FROM (gap_end - gap_start)) AS gap_duration
            FROM
              gaps
            WHERE
              EXTRACT(EPOCH FROM (gap_end - gap_start)) > 120 -- 120 seconds = 2 minutes
            ORDER BY 1 DESC;
            """
    rs = session.execute(text(query))
    return rs

# ============================================================================
# UNIFIED SYMBOLS TABLE FUNCTIONS (EXCHANGE-AGNOSTIC)
# ============================================================================

@cache_result(
    key_pattern="cryptomarket:active_symbols:{exchange}:{active}",
    ttl=300,  # 5 minutes
    enabled_check=lambda: config.ENABLE_REDIS_CACHE
)
def get_active_symbols_unified(session, exchange=None, active=True):
    """
    Get active symbols from unified symbols table.

    Args:
        session: Database session
        exchange: Exchange name (optional, None = all exchanges)
        active: Filter by active status

    Returns:
        List of symbol strings
    """
    if exchange:
        sql = f"""
        SELECT symbol FROM symbols
        WHERE exchange = '{exchange}' AND active = {active}
        ORDER BY priority"""
    else:
        sql = f"""
        SELECT exchange, symbol FROM symbols
        WHERE active = {active}
        ORDER BY exchange, priority"""

    result_proxy = session.execute(text(sql))
    rows = result_proxy.fetchall()

    if exchange:
        return [row[0] for row in rows]
    else:
        return [(row[0], row[1]) for row in rows]  # (exchange, symbol) tuples


def upsert_symbol(symbol_dict, session):
    """
    Insert or update a symbol in the unified symbols table.

    Args:
        symbol_dict: Dict with keys: exchange, symbol, base_asset, quote_asset,
                    status, priority, active, metadata
        session: Database session

    Returns:
        None
    """
    import json
    from datetime import date, datetime

    # Convert any date/datetime objects in metadata to ISO strings
    metadata = symbol_dict.get('metadata', {})
    serializable_metadata = {}
    for key, value in metadata.items():
        if isinstance(value, (date, datetime)):
            serializable_metadata[key] = value.isoformat()
        else:
            serializable_metadata[key] = value

    try:
        metadata_json = json.dumps(serializable_metadata)
    except TypeError as e:
        logger.error(f"Failed to serialize metadata for {symbol_dict.get('symbol')}: {e}")
        logger.error(f"Metadata content: {metadata}")
        raise

    sql = f"""
    INSERT INTO symbols (exchange, symbol, base_asset, quote_asset, status,
                        priority, active, metadata, last_updated)
    VALUES ('{symbol_dict['exchange']}', '{symbol_dict['symbol']}',
            '{symbol_dict.get('base_asset', '')}', '{symbol_dict.get('quote_asset', '')}',
            '{symbol_dict.get('status', 'UNKNOWN')}', {symbol_dict.get('priority', 9999)},
            {symbol_dict.get('active', False)}, '{metadata_json}'::jsonb, CURRENT_TIMESTAMP)
    ON CONFLICT (exchange, symbol)
    DO UPDATE SET
        base_asset = EXCLUDED.base_asset,
        quote_asset = EXCLUDED.quote_asset,
        status = EXCLUDED.status,
        metadata = EXCLUDED.metadata,
        last_updated = CURRENT_TIMESTAMP
    """

    try:
        session.execute(text(sql))
        session.commit()
    except Exception as ex:
        logger.error(f"Error upserting symbol {symbol_dict.get('symbol')}: {ex}")
        traceback.print_exception(type(ex), ex, ex.__traceback__)
        session.rollback()


def update_symbol_status(exchange, symbol, priority, active, session):
    """
    Update symbol priority and active status in unified table.

    Args:
        exchange: Exchange name
        symbol: Trading symbol
        priority: Priority level (0-10)
        active: Active status
        session: Database session

    Returns:
        None
    """
    sql = f"""
    UPDATE symbols
    SET
    priority = {priority},
    active = {active},
    last_updated = CURRENT_TIMESTAMP
    WHERE exchange = '{exchange}' AND symbol = '{symbol}'"""

    try:
        session.execute(text(sql))
        session.commit()

        # Invalidate active_symbols cache
        from app.cache.redis_client import get_redis_cache
        cache = get_redis_cache()
        if cache:
            cache.delete_pattern(f"cryptomarket:active_symbols:{exchange}:*")
            logger.info(f"Invalidated active_symbols cache for {exchange}")

    except Exception as ex:
        logger.error(f"Error updating symbol status for {exchange}:{symbol}: {ex}")
        traceback.print_exception(type(ex), ex, ex.__traceback__)
        session.rollback()


def get_symbol_by_exchange_and_name(exchange, symbol, session):
    """
    Get symbol details from unified table.

    Args:
        exchange: Exchange name
        symbol: Trading symbol
        session: Database session

    Returns:
        Dict with symbol details or None if not found
    """
    sql = f"""
    SELECT exchange, symbol, base_asset, quote_asset, status, priority, active, metadata
    FROM symbols
    WHERE exchange = '{exchange}' AND symbol = '{symbol}'
    """

    result = session.execute(text(sql))
    row = result.fetchone()

    if row:
        return {
            'exchange': row[0],
            'symbol': row[1],
            'base_asset': row[2],
            'quote_asset': row[3],
            'status': row[4],
            'priority': row[5],
            'active': row[6],
            'metadata': row[7]
        }
    return None
