"""
F&O Bhavcopy Ingester — thin subclass of BaseIngester.
"""

from datetime import date
from pathlib import Path

import pandas as pd

from app.ingest.base_ingest import BaseIngester, SHARE_DIR
from app.ingest.nse_fo_bhavcopy_ingest import (
    fetch_fo_bhavcopy_for_date,
    normalize_fo_bhavcopy,
    load_to_timescaledb,
    setup_fo_table,
    FO_TABLE,
)


class FOIngester(BaseIngester):
    pipeline_name = "fo"
    csv_dir = SHARE_DIR / "bhavcopy_fo"
    parquet_dir = SHARE_DIR / "parquet" / "fo_daily"
    progress_file = SHARE_DIR / "fo_ingest_progress.json"
    table_name = FO_TABLE
    table_columns = [
        'time', 'instrument', 'symbol', 'expiry', 'strike', 'option_type',
        'open', 'high', 'low', 'close', 'settle_price',
        'contracts', 'value_lakh', 'open_interest', 'change_in_oi',
        'underlying_price',
    ]

    def csv_filename(self, trade_date: date) -> str:
        return f"fo_bhavcopy_{trade_date:%Y-%m-%d}.csv"

    def parquet_filename(self, trade_date: date) -> str:
        return f"fo_bhavcopy_{trade_date:%Y-%m-%d}.parquet"

    def setup_table(self, conn):
        setup_fo_table(conn)

    def fetch_data(self, trade_date: date):
        return fetch_fo_bhavcopy_for_date(trade_date)

    def normalize_data(self, df: pd.DataFrame, trade_date: date) -> pd.DataFrame:
        return normalize_fo_bhavcopy(df, trade_date)

    def load_to_db(self, df: pd.DataFrame, conn) -> int:
        return load_to_timescaledb(df, conn)
