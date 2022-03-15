

def create_kline_temp_table():
    """
    Creates a temporary table to store kline data.
    """
    query = f"""
        CREATE TEMP TABLE TEMP_KLINE(
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
    """
    return query


def create_kline_binance_table(symbol, kline_interval):
    """
    Creates a temporary table to store kline data.
    """
    table_name  = f"{symbol.lower()}_kline_{kline_interval}_binance"
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

def check_if_table_exists(symbol, kline_interval):
    """
    Checks if a table exists.
    """
    table_name = f"{symbol.lower()}_kline_{kline_interval}_binance"
    query = f"""
        SELECT EXISTS(
            SELECT 1
            FROM   information_schema.tables
            WHERE  table_schema = 'public'
            AND    table_name = '{table_name}'
        )
    """
    return query, table_name