"""
Index Daily Ingester — thin subclass of BaseIngester.
"""

from datetime import date
from pathlib import Path

import pandas as pd

from app.ingest.base_ingest import BaseIngester, SHARE_DIR
from app.ingest.nse_index_daily_ingest import (
    fetch_index_data_for_date,
    normalize_index_data,
    load_to_timescaledb,
    setup_index_table,
    INDEX_TABLE,
)


class IndexIngester(BaseIngester):
    pipeline_name = "index"
    csv_dir = SHARE_DIR / "index_daily"
    parquet_dir = SHARE_DIR / "parquet" / "index_daily"
    progress_file = SHARE_DIR / "index_ingest_progress.json"
    table_name = INDEX_TABLE
    table_columns = [
        'time', 'index_name', 'open', 'high', 'low', 'close',
        'points_change', 'pct_change', 'volume', 'turnover_cr',
        'pe', 'pb', 'div_yield',
    ]

    def csv_filename(self, trade_date: date) -> str:
        return f"index_daily_{trade_date:%Y-%m-%d}.csv"

    def parquet_filename(self, trade_date: date) -> str:
        return f"index_daily_{trade_date:%Y-%m-%d}.parquet"

    def setup_table(self, conn):
        setup_index_table(conn)

    def fetch_data(self, trade_date: date):
        return fetch_index_data_for_date(trade_date)

    def normalize_data(self, df: pd.DataFrame, trade_date: date) -> pd.DataFrame:
        return normalize_index_data(df, trade_date)

    def load_to_db(self, df: pd.DataFrame, conn) -> int:
        return load_to_timescaledb(df, conn)
