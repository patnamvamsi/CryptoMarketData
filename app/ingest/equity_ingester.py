"""
Equity Bhavcopy Ingester — thin subclass of BaseIngester.
Delegates fetching/normalization to the existing nse_bhavcopy_ingest module.
"""

from datetime import date
from pathlib import Path

import pandas as pd

from app.ingest.base_ingest import BaseIngester, SHARE_DIR

# Re-use all the heavy logic already written
from app.ingest.nse_bhavcopy_ingest import (
    fetch_bhavcopy_for_date,
    normalize_bhavcopy,
    load_to_timescaledb,
    setup_equity_table,
    EQUITY_TABLE,
)


class EquityIngester(BaseIngester):
    pipeline_name = "equity"
    csv_dir = SHARE_DIR / "bhavcopy_equity"
    parquet_dir = SHARE_DIR / "parquet" / "equity_daily"
    progress_file = SHARE_DIR / "ingest_progress.json"
    table_name = EQUITY_TABLE
    table_columns = [
        'time', 'symbol', 'series', 'open', 'high', 'low', 'close',
        'last', 'prev_close', 'volume', 'turnover', 'trades',
        'deliverable_qty', 'delivery_pct',
    ]

    def csv_filename(self, trade_date: date) -> str:
        return f"bhavcopy_{trade_date:%Y-%m-%d}.csv"

    def parquet_filename(self, trade_date: date) -> str:
        return f"bhavcopy_{trade_date:%Y-%m-%d}.parquet"

    def setup_table(self, conn):
        setup_equity_table(conn)

    def fetch_data(self, trade_date: date):
        return fetch_bhavcopy_for_date(trade_date)

    def normalize_data(self, df: pd.DataFrame, trade_date: date) -> pd.DataFrame:
        return normalize_bhavcopy(df, trade_date)

    def load_to_db(self, df: pd.DataFrame, conn) -> int:
        return load_to_timescaledb(df, conn)
