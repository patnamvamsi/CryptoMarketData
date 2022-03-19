

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