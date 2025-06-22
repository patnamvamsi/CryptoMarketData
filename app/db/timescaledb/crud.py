import datetime
import traceback

import sqlalchemy
from sqlalchemy import create_engine
from app.config import config as cfg
import pandas as pd
import logging
from app.logger import setup_logging
logger = logging.getLogger(__name__)

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


def truncate_temp_kline_table():
    query = "TRUNCATE TABLE temp_kline_binance"
    return query


def create_kline_binance_table(symbol, kline_interval):
    """
    Creates a table to store kline data.
    """
    table_name = get_table_name(symbol, kline_interval)
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


def get_table_name(symbol, kline_interval):
    table_name = "binance_" + f"{symbol.lower()}_kline_{kline_interval}"
    return table_name


def check_if_table_exists(symbol, kline_interval):
    """
    Checks if a table exists.
    """
    table_name = get_table_name(symbol, kline_interval)
    query = f"""
        SELECT EXISTS(
            SELECT 1
            FROM   information_schema.tables
            WHERE  table_schema = 'public'
            AND    table_name = '{table_name}'
        )
    """
    return query, table_name


def load_kline_temp_to_main(symbol, kline_interval):
    """
    Loads kline data from temp table to main table.
    """
    table_name = get_table_name(symbol, kline_interval)
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
        FROM temp_kline_binance	
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


def insert_kline_rows(symbol, kline, candle_sticks, session): # handle duplicate values

    temp_table = 'temp_kline_binance'
    meta = sqlalchemy.MetaData(bind=session.bind)
    session.execute(truncate_temp_kline_table())
    kline_table = sqlalchemy.Table(temp_table, meta, autoload=True)
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
        query, table_name = load_kline_temp_to_main(symbol, kline)
        session.execute(query)
        session.commit()
    except Exception as ex:
        logger.error("Error " + symbol + str(ex))
        traceback.print_exception(type(ex), ex, ex.__traceback__)


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
        session.execute(sql)
    except Exception as ex:
        logger.error("Error updating symbol:" + symbol + str(ex))
        traceback.print_exception(type(ex), ex, ex.__traceback__)
    return


def get_max_timestamp(table, session):

    sql = f"""
    SELECT max(close_time)
    FROM {table}"""

    rs = session.execute(sql)
    if rs.returns_rows:
        for row in rs:
            return row[0]
    return None


def get_active_symbols(session, active=True):
    sql = f"""
    SELECT symbol from binance_symbols
    WHERE active = {active} order by  priority"""

    result_proxy = session.execute(sql)
    symbol_list = pd.DataFrame(result_proxy.fetchall())[0].values

    return symbol_list


def create_table_if_not_exists(symbol, kline_interval, session):
    """
    Creates a table in the TimescaleDB database if it does not exist.

    :param table_name: The name of the table to create.
    :param column_names: The names of the columns to create in the table.
    :return: None
    """

    #check if table exists
    query, table_name = check_if_table_exists (symbol, kline_interval)
    rs = session.execute(query)

    # create table if it does not exist
    if rs.fetchone()[0] == False:
        query,table_name = create_kline_binance_table(symbol, kline_interval)
        session.execute(query)
        logger.info("Created table {}".format(table_name))
        return table_name
    else:
        logger.info("Table {} already exists".format(table_name))
        return table_name


def find_gaps_in_kline_data(symbol, kline_interval, session):
    table_name = get_table_name(symbol, kline_interval)
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
    rs = session.execute(query)
    return rs
