from app.config import config as cfg
import pandas as pd
from binance.client import Client
from app.db.timescaledb import crud as q
from app.cache.decorators import cache_result
from app.config import config


@cache_result(
    key_pattern="cryptomarket:exchange_info:binance",
    ttl=3600,  # 1 hour
    enabled_check=lambda: config.ENABLE_REDIS_CACHE
)
def refresh_binance_symbols(session):

    client = Client(cfg.API_KEY, cfg.API_SECRET)
    symbol_list = client.get_exchange_info()
    df = pd.json_normalize(symbol_list['symbols'])
    df['priority'] = 9999
    df['active'] = False
    df['version'] = 1
    df['last_updated'] = pd.to_datetime('now')
    df.set_index('symbol', inplace=True)
    df.columns = df.columns.str.lower()
    q.update_binance_symbols(df, session)


def set_symbol_priority(symbol, priority, session, activate=True):
    q.update_symbol_config(symbol, priority, activate, session)
    return("Success")





