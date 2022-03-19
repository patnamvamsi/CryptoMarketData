import sqlalchemy
from sqlalchemy import create_engine
from config import config as cfg
import pandas as pd
from binance.client import Client

ts_engine = create_engine('postgresql://'+ cfg.TIMESCALE_USERNAME+':'+
                       cfg.TIMESCALE_PASSWORD + '@' +
                       cfg.TIMESCALE_HOST + ':' +
                       cfg.TIMESCALE_PORT + '/' +
                       cfg.TIMESCALE_MARKET_DATA_DB)



def update_binance_symbols():
    client = Client(cfg.API_KEY, cfg.API_SECRET)

    symbol_list = client.get_exchange_info()
    df = pd.json_normalize(symbol_list['symbols'])
    df['priority'] = 9999
    df['active'] = False
    df['version'] = 1
    df.to_sql('binance_symbols', ts_engine, if_exists='append',dtype =
                {'filters':sqlalchemy.types.JSON})

    print(symbol_list)


update_binance_symbols()