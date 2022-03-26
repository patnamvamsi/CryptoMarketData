from config import config as cfg
import pandas as pd
from binance.client import Client
from db.timescaledb import queries as q




def refresh_binance_symbols():

    client = Client(cfg.API_KEY, cfg.API_SECRET)
    symbol_list = client.get_exchange_info()
    df = pd.json_normalize(symbol_list['symbols'])
    df['priority'] = 9999
    df['active'] = False
    df['version'] = 1
    df['last_updated'] = pd.to_datetime('now')
    df.set_index('symbol', inplace=True)
    df.columns = df.columns.str.lower()
    q.update_binance_symbols(df)



#refresh_binance_symbols()

#q.update_symbol_config('BTCAUD',1,True)

