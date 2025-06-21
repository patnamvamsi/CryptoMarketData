#Only Need to run this for the first time to intialise key tables in the database

import os, sys
sys.path.insert(1, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from ingest import manage_binance_symbols as sym
from db.timescaledb import timescaledb_connect  as c
session_pool = c.get_session_pool()
session = session_pool()

sym.refresh_binance_symbols(session)
sym.set_symbol_priority('BTCUSDT', 1, session)  #change the symbol and priority as needed
session.commit()