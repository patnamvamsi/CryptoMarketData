import sqlalchemy
from sqlalchemy import create_engine
from app.config import config as cfg
import pandas as pd

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
    Creates a temporary table to store kline data.
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
        to_timestamp(close_time/1000),
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
                              cfg.TIMESCALE_MARKET_DATA_DB)
    return ts_engine

def update_binance_symbols(df):

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
    SELECT * FROM temp_binance_symbols WHERE
    symbol NOT IN (SELECT symbol from binance_symbols)'''

    drop_temp_table = 'DROP TABLE temp_binance_symbols'

    with ts_engine.begin() as conn:  # TRANSACTION
        conn.execute(update_sql)
        conn.execute(insert_sql)
        conn.execute(drop_temp_table)


def update_symbol_config(symbol,priority,activate):

    ts_engine  = create_sqlalchemy_engine_conn()

    sql = f"""
    UPDATE binance_symbols
    SET
    priority = {priority},
    active = {activate},
    version = version + 1,
    last_updated = CURRENT_TIMESTAMP
    WHERE symbol = '{symbol}'"""

    with ts_engine.begin() as conn:
        conn.execute(sql)



def get_active_symbols(active=True):

    ts_engine  = create_sqlalchemy_engine_conn()
    sql = f"""
    SELECT symbol from binance_symbols
    WHERE active = {active}"""

    with ts_engine.begin() as conn:
        ResultProxy = conn.execute(sql)
        symbol_list = pd.DataFrame(ResultProxy.fetchall())[0].values

    return symbol_list