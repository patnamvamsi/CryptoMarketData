import os
from app.db.timescaledb import connect_postgres as tscdb
from app.db.timescaledb import queries as qry
from app.config import config as cfg
from binance.client import Client


def load_csv_to_db(root_dir, kline_intervel,delimiter=','):
    """
    Loads a CSV file into a TimescaleDB database.
    :param root_dir:
    :param delimiter: The delimiter to use in the CSV file.
    :return: None
    """
    PATH = root_dir
    kline_interval = kline_intervel

    #initialise db connection
    initiate_market_data_conn()

    #loop through each directory in the main directory
    for sub_dir in os.scandir(PATH):
        if sub_dir.is_dir():
            print("Processing Directory: " + sub_dir.name)
            for filename in os.scandir(PATH + sub_dir.name):
                if filename.is_file():
                    print("Processing: " + filename.path)

                    # identify the symbol based on the file prefix
                    symbol = filename.name.split('_')[0]

                    # load each csv file into the database

                    # create a table in the database if it does not exist
                    table = create_table_if_not_exists(symbol, kline_interval)

                    # load the csv file into temp table
                    if (load_file_to_temp_table(filename)):
                        # insert into the main table
                        query, table_name = qry.load_kline_temp_to_main(symbol, kline_interval)
                        load_into_main_and_truncate_temp(query, table_name)

                # Archive the loaded ingest


def load_into_main_and_truncate_temp(query, table_name):
    try:
        with tscdb.CursorFromConnectionPool() as cursor:
            cursor.execute(query)
            print("Kline data transferred from temp_kline_binance table to {}".format(table_name))
            cursor.execute("TRUNCATE TABLE temp_kline_binance")
            cursor.execute("commit")
    except Exception as e:
        print("Error: {}".format(e))
        return False

    return True


def load_file_to_temp_table(filename):
    try:
        f = open(filename.path, 'r')
        with tscdb.CursorFromConnectionPool() as cursor:
            cursor.copy_from(f, "temp_kline_binance", sep=',')
            print("File {} loaded to temp_kline_binance".format(filename.path))
            cursor.execute('commit')
    except Exception as e:
        print("Error: {}".format(e))
        return False

    return True


def create_table_if_not_exists(symbol, kline_interval):
    """
    Creates a table in the TimescaleDB database if it does not exist.

    :param table_name: The name of the table to create.
    :param column_names: The names of the columns to create in the table.
    :return: None
    """


    #check if table exists
    query, table_name = qry.check_if_table_exists (symbol, kline_interval)
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


#load only specific symbols to the database
def load_csv_symbol_to_db(root_dir, kline_intervel,delimiter=','):
    """
    Loads a CSV file into a TimescaleDB database.
    :param root_dir:
    :param delimiter: The delimiter to use in the CSV file.
    :return: None
    """
    PATH = root_dir
    kline_interval = kline_intervel

    #initialise db connection
    initiate_market_data_conn()

    symbol_list = qry.get_active_symbols()

    #loop through each directory in the main directory
    for sub_dir in os.scandir(PATH):
        if sub_dir.is_dir() and sub_dir.name in symbol_list: #improve the logic
            print("Processing Directory: " + sub_dir.name)
            for filename in os.scandir(PATH + sub_dir.name):
                if filename.is_file():
                    print("Processing: " + filename.path)

                    # identify the symbol based on the file prefix
                    symbol = filename.name.split('_')[0]

                    # load each csv file into the database

                    # create a table in the database if it does not exist
                    table = create_table_if_not_exists(symbol, kline_interval)

                    # load the csv file into temp table
                    if (load_file_to_temp_table(filename)):
                        # insert into the main table
                        query, table_name = qry.load_kline_temp_to_main(symbol, kline_interval)
                        load_into_main_and_truncate_temp(query, table_name)

                # Archive the loaded ingest

#load_csv_to_db(cfg.DATA_ROOT_DIR,Client.KLINE_INTERVAL_1DAY)
#load_csv_symbol_to_db("/media/vamsi/Elements 8TB/crypto/binance_historical_data/1minute/",Client.KLINE_INTERVAL_1MINUTE)
