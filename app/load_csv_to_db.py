from db.timescaledb import connect_postgres as tscdb
from db.timescaledb import queries as qry
from config import config as cfg
from binance.client import Client


def load_csv_to_db(csv_file, db_name, table_name, delimiter=','):
    """
    Loads a CSV file into a TimescaleDB database.

    :param csv_file: The CSV file to load.
    :param db_name: The name of the database to load the CSV file into.
    :param table_name: The name of the table to load the CSV file into.
    :param delimiter: The delimiter to use in the CSV file.
    :return: None
    """
    #intiialise db connection
    #cur = get_market_data_conn()

    table = create_table_if_not_exists("XRPUSDT", Client.KLINE_INTERVAL_1MINUTE)
    print(table)



def create_table_if_not_exists(symbol, kline_interval):
    """
    Creates a table in the TimescaleDB database if it does not exist.

    :param table_name: The name of the table to create.
    :param column_names: The names of the columns to create in the table.
    :return: None
    """
    #initialise db connection
    initiate_market_data_conn()

    #check if table exists
    query,table_name = qry.check_if_table_exists (symbol, kline_interval)
    with tscdb.CursorFromConnectionPool() as cursor:
        cursor.execute(query)
        if (cursor.fetchone()[0] == False):

            #create table if it does not exist
            query,table_name = qry.create_kline_binance_table(symbol, kline_interval)
            with tscdb.CursorFromConnectionPool() as cursor:
                cursor.execute(query)
                print("Created table {}".format(table_name))
                cursor.execute('commit')
                return table_name
        else:
            print("Table {} already exists".format(table_name))
            return table_name

def initiate_market_data_conn(  ):
    tscdb.Database.initialise(database=cfg.TIMESCALE_MARKET_DATA_DB, user=cfg.TIMESCALE_USERNAME,
                        password=cfg.TIMESCALE_PASSWORD, host=cfg.TIMESCALE_HOST)




load_csv_to_db('/home/ubuntu/data/market_data/market_data_2018-01-01_2018-12-31.csv', 'market_data', 'market_data')

