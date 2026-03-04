"""
NSE Bhavcopy Historical Data Ingestion Pipeline
================================================
Downloads NSE equity bhavcopy for all trading days from 2000 onwards,
stores as CSV + Parquet files, and loads into TimescaleDB.

Data sources (priority order):
  1. nselib: bhav_copy_with_delivery (2020+, includes delivery data)
  2. jugaad_data: bhavcopy_save (2000+, basic OHLCV)

Free data, no API key needed.
"""

import os
import sys
import time
import logging
import traceback
import json
from datetime import datetime, date, timedelta
from pathlib import Path
from random import uniform
from io import StringIO

import numpy as np
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '..', '.env'))

TIMESCALE_HOST = os.getenv('TIMESCALE_HOST', 'localhost')
TIMESCALE_PORT = os.getenv('TIMESCALE_PORT', '5432')
TIMESCALE_USERNAME = os.getenv('TIMESCALE_USERNAME', 'postgres')
TIMESCALE_PASSWORD = os.getenv('TIMESCALE_PASSWORD', '')
TIMESCALE_MARKET_DATA_DB = os.getenv('TIMESCALE_MARKET_DATA_DB', 'market_data_dev1')

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('logs/nse_bhavcopy_ingest.log')
    ]
)

# ============================================================================
# CONFIG
# ============================================================================
SHARE_DIR = Path("/media/vboxuser/test/NSE_Data")
CSV_DIR = SHARE_DIR / "bhavcopy_equity"
PARQUET_DIR = SHARE_DIR / "parquet" / "equity_daily"

MIN_DELAY = 0.5
MAX_DELAY = 2.0

EQUITY_TABLE = "nse_equity_daily"


# ============================================================================
# DATABASE
# ============================================================================
def get_db_connection():
    return psycopg2.connect(
        host=TIMESCALE_HOST, port=TIMESCALE_PORT,
        database=TIMESCALE_MARKET_DATA_DB,
        user=TIMESCALE_USERNAME, password=TIMESCALE_PASSWORD
    )


def setup_equity_table(conn):
    cur = conn.cursor()
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {EQUITY_TABLE} (
            time            DATE NOT NULL,
            symbol          TEXT NOT NULL,
            series          TEXT,
            open            NUMERIC,
            high            NUMERIC,
            low             NUMERIC,
            close           NUMERIC,
            last            NUMERIC,
            prev_close      NUMERIC,
            volume          NUMERIC,
            turnover        NUMERIC,
            trades          NUMERIC,
            deliverable_qty NUMERIC,
            delivery_pct    NUMERIC
        );
    """)
    cur.execute("""
        SELECT COUNT(*) FROM timescaledb_information.hypertables
        WHERE hypertable_name = %s
    """, (EQUITY_TABLE,))
    if cur.fetchone()[0] == 0:
        cur.execute(f"""
            SELECT create_hypertable('{EQUITY_TABLE}', 'time',
                chunk_time_interval => INTERVAL '1 year',
                if_not_exists => TRUE);
        """)
        logger.info(f"Created hypertable {EQUITY_TABLE}")

    cur.execute(f"""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_{EQUITY_TABLE}_symbol_series_time
            ON {EQUITY_TABLE} (symbol, series, time DESC);
        CREATE INDEX IF NOT EXISTS idx_{EQUITY_TABLE}_time
            ON {EQUITY_TABLE} (time DESC);
        CREATE INDEX IF NOT EXISTS idx_{EQUITY_TABLE}_symbol_time
            ON {EQUITY_TABLE} (symbol, time DESC);
    """)
    conn.commit()
    cur.close()
    logger.info(f"Table {EQUITY_TABLE} ready")


# ============================================================================
# DATA FETCHING - Multi-source
# ============================================================================
def fetch_via_nselib(trade_date: date) -> pd.DataFrame:
    """Fetch from nselib (2020+, includes delivery data)."""
    from nselib import capital_market
    date_str = trade_date.strftime("%d-%m-%Y")
    df = capital_market.bhav_copy_with_delivery(trade_date=date_str)
    if df is not None and not df.empty:
        return df
    return None


def fetch_via_jugaad(trade_date: date) -> pd.DataFrame:
    """Fetch from jugaad_data (2000+, basic OHLCV)."""
    from jugaad_data.nse import bhavcopy_save, bhavcopy_raw
    import tempfile

    # Try bhavcopy_raw first (returns CSV string)
    try:
        raw = bhavcopy_raw(trade_date)
        if raw and isinstance(raw, str) and len(raw) > 100:
            df = pd.read_csv(StringIO(raw))
            if not df.empty:
                df['_source'] = 'jugaad_raw'
                return df
    except Exception:
        pass

    # Fallback to bhavcopy_save
    tmpdir = tempfile.mkdtemp()
    try:
        bhavcopy_save(trade_date, tmpdir)
        files = [f for f in os.listdir(tmpdir) if f.endswith('.csv')]
        if files:
            df = pd.read_csv(os.path.join(tmpdir, files[0]))
            if not df.empty:
                df['_source'] = 'jugaad_save'
                return df
    except Exception:
        pass
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

    return None


def fetch_bhavcopy_for_date(trade_date: date) -> pd.DataFrame:
    """Try multiple sources in priority order."""
    # nselib for 2020+ (richer data)
    if trade_date.year >= 2020:
        try:
            df = fetch_via_nselib(trade_date)
            if df is not None:
                df['_source'] = 'nselib'
                return df
        except Exception as e:
            logger.debug(f"nselib failed for {trade_date}: {e}")

    # jugaad_data for all years
    try:
        df = fetch_via_jugaad(trade_date)
        if df is not None:
            return df
    except Exception as e:
        logger.debug(f"jugaad failed for {trade_date}: {e}")

    return None


# ============================================================================
# NORMALIZATION
# ============================================================================
def normalize_bhavcopy(df: pd.DataFrame, trade_date: date) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    # Strip whitespace from column names
    df.columns = [c.strip() for c in df.columns]

    # Build column mapping
    cols_lower = {c.lower().replace(' ', '_').replace('.', ''): c for c in df.columns}

    mappings = {
        'symbol': ['symbol', 'tckrsymb', 'ticker'],
        'series': ['series', 'srs', 'sctysrs'],
        'open': ['open', 'open_price', 'openpric'],
        'high': ['high', 'high_price', 'hghpric'],
        'low': ['low', 'low_price', 'lwpric'],
        'close': ['close', 'close_price', 'clspric'],
        'last': ['last', 'last_price', 'lstpric'],
        'prev_close': ['prev_close', 'prevclose', 'prevclsprc', 'prcvscls'],
        'volume': ['volume', 'tottrdqty', 'ttl_trd_qnty', 'total_traded_quantity'],
        'turnover': ['turnover', 'tottrdval', 'turnover_lacs', 'total_turnover'],
        'trades': ['trades', 'no_of_trades', 'no_of_trades', 'totaltrades', 'ttl_no_mkt_trans'],
        'deliverable_qty': ['deliverable_qty', 'delvry_qty', 'deliv_qty', 'dlvbl_qty'],
        'delivery_pct': ['delivery_pct', 'delvry_pct', 'deliv_per', '%_dly_qt_to_traded_qty',
                         'pctdlytottrdqty'],
    }

    col_map = {}
    for standard_name, variations in mappings.items():
        for var in variations:
            if var in cols_lower:
                col_map[cols_lower[var]] = standard_name
                break

    if 'symbol' not in col_map.values():
        logger.warning(f"No symbol column in: {list(df.columns)}")
        return pd.DataFrame()

    df = df.rename(columns=col_map)

    # Filter to equity series
    if 'series' in df.columns:
        df = df[df['series'].isin(['EQ', 'BE', 'BZ', 'SM', 'ST', 'E1'])].copy()
    
    # Remove rows with empty symbols
    df = df[df['symbol'].notna() & (df['symbol'].str.strip() != '')].copy()
    df['symbol'] = df['symbol'].str.strip()

    df['time'] = trade_date

    out_cols = ['time', 'symbol', 'series', 'open', 'high', 'low', 'close',
                'last', 'prev_close', 'volume', 'turnover', 'trades',
                'deliverable_qty', 'delivery_pct']

    for col in out_cols:
        if col not in df.columns:
            df[col] = None

    df = df[out_cols].copy()

    numeric_cols = ['open', 'high', 'low', 'close', 'last', 'prev_close',
                    'volume', 'turnover', 'trades', 'deliverable_qty', 'delivery_pct']
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors='coerce')

    return df


# ============================================================================
# STORAGE
# ============================================================================
def save_to_files(df: pd.DataFrame, trade_date: date):
    if df.empty:
        return
    year = trade_date.year
    date_str = trade_date.strftime("%Y-%m-%d")

    csv_year_dir = CSV_DIR / str(year)
    csv_year_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(csv_year_dir / f"bhavcopy_{date_str}.csv", index=False)

    pq_year_dir = PARQUET_DIR / f"year={year}"
    pq_year_dir.mkdir(parents=True, exist_ok=True)
    df.to_parquet(pq_year_dir / f"bhavcopy_{date_str}.parquet", index=False, engine='pyarrow')


def load_to_timescaledb(df: pd.DataFrame, conn):
    if df.empty:
        return 0

    cur = conn.cursor()
    cols = ['time', 'symbol', 'series', 'open', 'high', 'low', 'close',
            'last', 'prev_close', 'volume', 'turnover', 'trades',
            'deliverable_qty', 'delivery_pct']

    clean_df = df[cols].copy()
    values = []
    for row in clean_df.itertuples(index=False, name=None):
        values.append(tuple(None if (isinstance(v, float) and pd.isna(v)) else v for v in row))

    insert_sql = f"""
        INSERT INTO {EQUITY_TABLE} ({', '.join(cols)})
        VALUES %s
        ON CONFLICT (symbol, series, time) DO UPDATE SET
            open = EXCLUDED.open, high = EXCLUDED.high,
            low = EXCLUDED.low, close = EXCLUDED.close,
            last = EXCLUDED.last, prev_close = EXCLUDED.prev_close,
            volume = EXCLUDED.volume, turnover = EXCLUDED.turnover,
            trades = EXCLUDED.trades,
            deliverable_qty = EXCLUDED.deliverable_qty,
            delivery_pct = EXCLUDED.delivery_pct
    """
    execute_values(cur, insert_sql, values, page_size=1000)
    conn.commit()
    cur.close()
    return len(values)


# ============================================================================
# PROGRESS
# ============================================================================
PROGRESS_FILE = SHARE_DIR / "ingest_progress.json"

def load_progress() -> dict:
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE) as f:
            return json.load(f)
    return {"last_completed_date": None, "total_days": 0, "total_rows": 0, "failed_dates": []}

def save_progress(progress: dict):
    with open(PROGRESS_FILE, 'w') as f:
        json.dump(progress, f, indent=2, default=str)


# ============================================================================
# MAIN
# ============================================================================
def ingest_date_range(start_date: date, end_date: date, skip_existing: bool = True):
    os.makedirs("logs", exist_ok=True)

    conn = get_db_connection()
    setup_equity_table(conn)

    progress = load_progress()
    last_done = progress.get("last_completed_date")
    if last_done and skip_existing:
        resume_from = datetime.strptime(last_done, "%Y-%m-%d").date() + timedelta(days=1)
        if resume_from > start_date:
            logger.info(f"Resuming from {resume_from} (last completed: {last_done})")
            start_date = resume_from

    bdays = pd.bdate_range(start=start_date, end=end_date, freq='B')
    dates = [d.date() for d in bdays]

    logger.info(f"Starting ingestion: {start_date} to {end_date} ({len(dates)} business days)")

    success_count = 0
    fail_count = 0
    consecutive_fails = 0
    total_rows = progress.get("total_rows", 0)

    for i, trade_date in enumerate(dates):
        date_str = trade_date.strftime("%Y-%m-%d")

        # Skip if CSV already exists
        csv_path = CSV_DIR / str(trade_date.year) / f"bhavcopy_{date_str}.csv"
        if skip_existing and csv_path.exists():
            success_count += 1
            consecutive_fails = 0
            continue

        try:
            raw_df = fetch_bhavcopy_for_date(trade_date)
            if raw_df is None or raw_df.empty:
                logger.debug(f"{date_str}: No data (holiday?)")
                fail_count += 1
                consecutive_fails += 1
                # If too many consecutive fails, slow down
                if consecutive_fails > 5:
                    time.sleep(MAX_DELAY * 2)
                else:
                    time.sleep(MIN_DELAY)
                continue

            consecutive_fails = 0
            df = normalize_bhavcopy(raw_df, trade_date)
            if df.empty:
                logger.warning(f"{date_str}: Empty after normalization")
                fail_count += 1
                time.sleep(MIN_DELAY)
                continue

            save_to_files(df, trade_date)
            rows = load_to_timescaledb(df, conn)
            total_rows += rows
            success_count += 1

            progress["last_completed_date"] = date_str
            progress["total_days"] = progress.get("total_days", 0) + 1
            progress["total_rows"] = total_rows

            if success_count % 20 == 0:
                save_progress(progress)

            if success_count % 50 == 0 or i < 20:
                logger.info(
                    f"[{i+1}/{len(dates)}] {date_str}: {rows} rows | "
                    f"Total: {total_rows:,} | OK: {success_count} Fail: {fail_count}"
                )

            time.sleep(uniform(MIN_DELAY, MAX_DELAY))

        except KeyboardInterrupt:
            logger.info("Interrupted! Saving progress...")
            save_progress(progress)
            conn.close()
            return
        except Exception as e:
            logger.error(f"{date_str}: Error - {e}")
            fail_count += 1
            try:
                conn.close()
            except:
                pass
            conn = get_db_connection()
            time.sleep(MAX_DELAY * 3)

    save_progress(progress)
    conn.close()

    logger.info(
        f"\n{'='*60}\n"
        f"INGESTION COMPLETE\n"
        f"{'='*60}\n"
        f"Range: {start_date} to {end_date}\n"
        f"Success: {success_count} | Failed/Holidays: {fail_count}\n"
        f"Total rows: {total_rows:,}\n"
        f"CSV: {CSV_DIR}\n"
        f"Parquet: {PARQUET_DIR}\n"
        f"{'='*60}"
    )
    return {"success": success_count, "failed": fail_count, "total_rows": total_rows}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="NSE Bhavcopy Ingestion")
    parser.add_argument("--start", default="2000-01-01", help="Start date YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="End date YYYY-MM-DD (default: yesterday)")
    parser.add_argument("--no-skip", action="store_true", help="Re-download existing files")
    parser.add_argument("--reset", action="store_true", help="Reset progress")
    args = parser.parse_args()

    end = date.today() - timedelta(days=1) if args.end is None else datetime.strptime(args.end, "%Y-%m-%d").date()
    start = datetime.strptime(args.start, "%Y-%m-%d").date()

    if args.reset:
        if PROGRESS_FILE.exists():
            PROGRESS_FILE.unlink()
            logger.info("Progress reset")

    ingest_date_range(start, end, skip_existing=not args.no_skip)
