"""
Bulk DB Loader
==============
Reads existing CSV/Parquet files from disk and bulk-loads them into TimescaleDB.
For when the DB comes back online after being down during file-only ingestion.

Usage:
    python manage.py db load-files --source equity
    python manage.py db load-files --source fo
    python manage.py db load-files --source index
    python manage.py db load-files --source all
"""

import logging
import os
from datetime import date
from pathlib import Path

import pandas as pd

try:
    import psycopg2
    from psycopg2.extras import execute_values
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '..', '.env'))

logger = logging.getLogger(__name__)

SHARE_DIR = Path("/media/vboxuser/test/NSE_Data")

TIMESCALE_HOST = os.getenv('TIMESCALE_HOST', 'localhost')
TIMESCALE_PORT = os.getenv('TIMESCALE_PORT', '5432')
TIMESCALE_USERNAME = os.getenv('TIMESCALE_USERNAME', 'postgres')
TIMESCALE_PASSWORD = os.getenv('TIMESCALE_PASSWORD', '')
TIMESCALE_MARKET_DATA_DB = os.getenv('TIMESCALE_MARKET_DATA_DB', 'market_data_dev1')


def get_db_connection():
    return psycopg2.connect(
        host=TIMESCALE_HOST, port=TIMESCALE_PORT,
        database=TIMESCALE_MARKET_DATA_DB,
        user=TIMESCALE_USERNAME, password=TIMESCALE_PASSWORD,
    )


# ---------------------------------------------------------------------------
# Source configs
# ---------------------------------------------------------------------------
SOURCES = {
    "equity": {
        "csv_dir": SHARE_DIR / "bhavcopy_equity",
        "parquet_dir": SHARE_DIR / "parquet" / "equity_daily",
        "file_glob": "bhavcopy_*.csv",
        "setup_fn": "app.ingest.nse_bhavcopy_ingest.setup_equity_table",
        "load_fn": "app.ingest.nse_bhavcopy_ingest.load_to_timescaledb",
    },
    "fo": {
        "csv_dir": SHARE_DIR / "bhavcopy_fo",
        "parquet_dir": SHARE_DIR / "parquet" / "fo_daily",
        "file_glob": "fo_bhavcopy_*.csv",
        "setup_fn": "app.ingest.nse_fo_bhavcopy_ingest.setup_fo_table",
        "load_fn": "app.ingest.nse_fo_bhavcopy_ingest.load_to_timescaledb",
    },
    "index": {
        "csv_dir": SHARE_DIR / "index_daily",
        "parquet_dir": SHARE_DIR / "parquet" / "index_daily",
        "file_glob": "index_daily_*.csv",
        "setup_fn": "app.ingest.nse_index_daily_ingest.setup_index_table",
        "load_fn": "app.ingest.nse_index_daily_ingest.load_to_timescaledb",
    },
}


def _import(dotted: str):
    """Import a function from a dotted path like 'pkg.mod.func'."""
    mod_path, func_name = dotted.rsplit('.', 1)
    import importlib
    mod = importlib.import_module(mod_path)
    return getattr(mod, func_name)


def load_files_to_db(source: str, file_format: str = "csv", batch_size: int = 50):
    """
    Bulk-load files for a given source into TimescaleDB.

    Args:
        source: "equity", "fo", "index", or "all"
        file_format: "csv" or "parquet"
        batch_size: commit every N files
    """
    if source == "all":
        for s in SOURCES:
            load_files_to_db(s, file_format, batch_size)
        return

    if source not in SOURCES:
        raise ValueError(f"Unknown source: {source}. Choose from {list(SOURCES)} or 'all'")

    cfg = SOURCES[source]
    setup_fn = _import(cfg["setup_fn"])
    load_fn = _import(cfg["load_fn"])

    conn = get_db_connection()
    setup_fn(conn)

    if file_format == "parquet":
        base_dir = cfg["parquet_dir"]
        files = sorted(base_dir.rglob("*.parquet"))
    else:
        base_dir = cfg["csv_dir"]
        files = sorted(base_dir.rglob(cfg["file_glob"]))

    logger.info(f"[bulk-load] {source}: found {len(files)} {file_format} files in {base_dir}")

    loaded = 0
    total_rows = 0
    errors = 0

    for i, fpath in enumerate(files):
        try:
            if file_format == "parquet":
                df = pd.read_parquet(fpath)
            else:
                df = pd.read_csv(fpath)

            if df.empty:
                continue

            # Ensure 'time' column is proper date type for DB
            if 'time' in df.columns:
                df['time'] = pd.to_datetime(df['time']).dt.date

            rows = load_fn(df, conn)
            total_rows += rows
            loaded += 1

            if loaded % batch_size == 0:
                logger.info(f"[bulk-load] {source}: {loaded}/{len(files)} files, {total_rows:,} rows")

        except Exception as e:
            logger.error(f"[bulk-load] {source}: error loading {fpath.name}: {e}")
            errors += 1
            try:
                conn.rollback()
            except:
                pass
            # Reconnect
            try:
                conn.close()
            except:
                pass
            conn = get_db_connection()

    conn.close()
    logger.info(
        f"[bulk-load] {source} DONE: {loaded} files, {total_rows:,} rows loaded, {errors} errors"
    )
    return {"loaded_files": loaded, "total_rows": total_rows, "errors": errors}
