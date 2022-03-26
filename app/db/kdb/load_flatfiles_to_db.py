import pandas as pd
from app.db.kdb import connect_kdb as kdb


def load_kline_csv_to_db(dir, table_name):

    # Connect to the database
    db_conn = kdb.connect_kdb()

    df = pd.read_csv('db/LSKUSDT_01-Jan-2010_20-Aug-2021.csv')

    '''create header for table'''

    db_conn.create_new_table('LSKUSDT', "KLINE")
    db_conn.create_table_from_dataframe("LSKUSDT" ,df)

load_kline_csv_to_db('','')