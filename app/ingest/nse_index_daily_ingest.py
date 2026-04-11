"""
NSE Index Daily Data Ingestion Pipeline
========================================
Downloads daily closing values for ALL NSE indices from NSE archives.
Stores as CSV + Parquet files, optionally loads into TimescaleDB.

Data source: nsearchives.nseindia.com/content/indices/ind_close_all_{DDMMYYYY}.csv
Available from ~mid-2012 to present.
Contains: all indices in one file per day (~135 indices).

Free data, no API key needed.
"""

import os
import sys
import time
import logging
import json
from datetime import datetime, date, timedelta
from pathlib import Path
from random import uniform

import pandas as pd
import requests

try:
    import psycopg2
    from psycopg2.extras import execute_values
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False

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
        logging.FileHandler('logs/nse_index_ingest.log')
    ]
)

# ============================================================================
# CONFIG
# ============================================================================
SHARE_DIR = Path("/media/vboxuser/test/NSE_Data")
CSV_DIR = SHARE_DIR / "index_daily"
PARQUET_DIR = SHARE_DIR / "parquet" / "index_daily"

MIN_DELAY = 0.3
MAX_DELAY = 1.5

INDEX_TABLE = "nse_index_daily"


# ============================================================================
# DATABASE (optional)
# ============================================================================
def get_db_connection():
    if not HAS_PSYCOPG2:
        return None
    return psycopg2.connect(
        host=TIMESCALE_HOST, port=TIMESCALE_PORT,
        database=TIMESCALE_MARKET_DATA_DB,
        user=TIMESCALE_USERNAME, password=TIMESCALE_PASSWORD
    )


def setup_index_table(conn):
    if conn is None:
        return
    cur = conn.cursor()
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {INDEX_TABLE} (
            time            DATE NOT NULL,
            index_name      TEXT NOT NULL,
            open            NUMERIC,
            high            NUMERIC,
            low             NUMERIC,
            close           NUMERIC,
            points_change   NUMERIC,
            pct_change      NUMERIC,
            volume          NUMERIC,
            turnover_cr     NUMERIC,
            pe              NUMERIC,
            pb              NUMERIC,
            div_yield       NUMERIC
        );
    """)
    cur.execute("""
        SELECT COUNT(*) FROM timescaledb_information.hypertables
        WHERE hypertable_name = %s
    """, (INDEX_TABLE,))
    if cur.fetchone()[0] == 0:
        cur.execute(f"""
            SELECT create_hypertable('{INDEX_TABLE}', 'time',
                chunk_time_interval => INTERVAL '1 year',
                if_not_exists => TRUE);
        """)
        logger.info(f"Created hypertable {INDEX_TABLE}")

    cur.execute(f"""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_{INDEX_TABLE}_name_time
            ON {INDEX_TABLE} (index_name, time DESC);
        CREATE INDEX IF NOT EXISTS idx_{INDEX_TABLE}_time
            ON {INDEX_TABLE} (time DESC);
    """)
    conn.commit()
    cur.close()
    logger.info(f"Table {INDEX_TABLE} ready")


# ============================================================================
# HTTP SESSION
# ============================================================================
_http_session = None
_session_created = None


def get_http_session() -> requests.Session:
    global _http_session, _session_created
    if _http_session is None or (datetime.now() - _session_created).seconds > 300:
        _http_session = requests.Session()
        _http_session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                          '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/csv,text/plain,*/*',
            'Accept-Language': 'en-US,en;q=0.5',
        })
        _session_created = datetime.now()
    return _http_session


# ============================================================================
# DATA FETCHING
# ============================================================================
def fetch_index_data_for_date(trade_date: date) -> pd.DataFrame:
    """Download all-index closing values CSV for a given date."""
    date_str = trade_date.strftime("%d%m%Y")
    url = f"https://nsearchives.nseindia.com/content/indices/ind_close_all_{date_str}.csv"

    session = get_http_session()
    try:
        r = session.get(url, timeout=20)
        if r.status_code == 200 and len(r.content) > 200:
            from io import StringIO
            df = pd.read_csv(StringIO(r.text))
            if not df.empty:
                return df
        elif r.status_code == 404:
            return None  # Holiday or no data
    except Exception as e:
        logger.debug(f"Failed to fetch index data for {trade_date}: {e}")

    return None


# ============================================================================
# NORMALIZATION
# ============================================================================
def normalize_index_data(df: pd.DataFrame, trade_date: date) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    df.columns = [c.strip() for c in df.columns]

    col_map = {
        'Index Name': 'index_name',
        'Index Date': '_date',
        'Open Index Value': 'open',
        'High Index Value': 'high',
        'Low Index Value': 'low',
        'Closing Index Value': 'close',
        'Points Change': 'points_change',
        'Change(%)': 'pct_change',
        'Volume': 'volume',
        'Turnover (Rs. Cr.)': 'turnover_cr',
        'P/E': 'pe',
        'P/B': 'pb',
        'Div Yield': 'div_yield',
    }

    df = df.rename(columns=col_map)
    df['time'] = trade_date

    # Clean index_name
    if 'index_name' in df.columns:
        df['index_name'] = df['index_name'].str.strip()
        df = df[df['index_name'].notna() & (df['index_name'] != '')].copy()

    out_cols = ['time', 'index_name', 'open', 'high', 'low', 'close',
                'points_change', 'pct_change', 'volume', 'turnover_cr',
                'pe', 'pb', 'div_yield']

    for col in out_cols:
        if col not in df.columns:
            df[col] = None

    df = df[out_cols].copy()

    numeric_cols = ['open', 'high', 'low', 'close', 'points_change', 'pct_change',
                    'volume', 'turnover_cr', 'pe', 'pb', 'div_yield']
    for col in numeric_cols:
        df[col] = df[col].replace('-', None)
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
    df.to_csv(csv_year_dir / f"index_daily_{date_str}.csv", index=False)

    pq_year_dir = PARQUET_DIR / f"year={year}"
    pq_year_dir.mkdir(parents=True, exist_ok=True)
    df.to_parquet(pq_year_dir / f"index_daily_{date_str}.parquet", index=False, engine='pyarrow')


def load_to_timescaledb(df: pd.DataFrame, conn):
    if df.empty or conn is None:
        return 0

    cur = conn.cursor()
    cols = ['time', 'index_name', 'open', 'high', 'low', 'close',
            'points_change', 'pct_change', 'volume', 'turnover_cr',
            'pe', 'pb', 'div_yield']

    values = []
    for row in df[cols].itertuples(index=False, name=None):
        values.append(tuple(None if (isinstance(v, float) and pd.isna(v)) else v for v in row))

    insert_sql = f"""
        INSERT INTO {INDEX_TABLE} ({', '.join(cols)})
        VALUES %s
        ON CONFLICT (index_name, time) DO UPDATE SET
            open = EXCLUDED.open, high = EXCLUDED.high,
            low = EXCLUDED.low, close = EXCLUDED.close,
            points_change = EXCLUDED.points_change,
            pct_change = EXCLUDED.pct_change,
            volume = EXCLUDED.volume, turnover_cr = EXCLUDED.turnover_cr,
            pe = EXCLUDED.pe, pb = EXCLUDED.pb, div_yield = EXCLUDED.div_yield
    """
    execute_values(cur, insert_sql, values, page_size=1000)
    conn.commit()
    cur.close()
    return len(values)


# ============================================================================
# PROGRESS
# ============================================================================
PROGRESS_FILE = SHARE_DIR / "index_ingest_progress.json"


def load_progress() -> dict:
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE) as f:
            return json.load(f)
    return {"last_completed_date": None, "total_days": 0, "total_rows": 0}


def save_progress(progress: dict):
    with open(PROGRESS_FILE, 'w') as f:
        json.dump(progress, f, indent=2, default=str)


# ============================================================================
# MAIN
# ============================================================================
def ingest_date_range(start_date: date, end_date: date, skip_existing: bool = True):
    os.makedirs("logs", exist_ok=True)

    try:
        conn = get_db_connection()
        setup_index_table(conn)
    except Exception as e:
        logger.warning(f"DB unavailable ({e}), running in file-only mode")
        conn = None

    progress = load_progress()
    last_done = progress.get("last_completed_date")
    if last_done and skip_existing:
        resume_from = datetime.strptime(last_done, "%Y-%m-%d").date() + timedelta(days=1)
        if resume_from > start_date:
            logger.info(f"Resuming from {resume_from} (last completed: {last_done})")
            start_date = resume_from

    bdays = pd.bdate_range(start=start_date, end=end_date, freq='B')
    dates = [d.date() for d in bdays]

    logger.info(f"Starting index ingestion: {start_date} to {end_date} ({len(dates)} business days)")

    success_count = 0
    fail_count = 0
    consecutive_fails = 0
    total_rows = progress.get("total_rows", 0)

    for i, trade_date in enumerate(dates):
        date_str = trade_date.strftime("%Y-%m-%d")

        csv_path = CSV_DIR / str(trade_date.year) / f"index_daily_{date_str}.csv"
        if skip_existing and csv_path.exists():
            success_count += 1
            consecutive_fails = 0
            continue

        try:
            raw_df = fetch_index_data_for_date(trade_date)
            if raw_df is None or raw_df.empty:
                logger.debug(f"{date_str}: No index data (holiday?)")
                fail_count += 1
                consecutive_fails += 1
                if consecutive_fails > 5:
                    time.sleep(MAX_DELAY * 2)
                else:
                    time.sleep(MIN_DELAY)
                continue

            consecutive_fails = 0
            df = normalize_index_data(raw_df, trade_date)
            if df.empty:
                logger.warning(f"{date_str}: Empty after normalization")
                fail_count += 1
                time.sleep(MIN_DELAY)
                continue

            save_to_files(df, trade_date)

            if conn is not None:
                try:
                    rows = load_to_timescaledb(df, conn)
                    total_rows += rows
                except Exception as db_err:
                    logger.warning(f"{date_str}: DB write failed ({db_err}), switching to file-only mode")
                    rows = len(df)
                    try:
                        conn.rollback()
                    except:
                        pass
                    try:
                        conn.close()
                    except:
                        pass
                    conn = None
            else:
                rows = len(df)

            success_count += 1

            progress["last_completed_date"] = date_str
            progress["total_days"] = progress.get("total_days", 0) + 1
            progress["total_rows"] = total_rows

            if success_count % 20 == 0:
                save_progress(progress)

            if success_count % 100 == 0 or i < 10:
                logger.info(
                    f"[{i+1}/{len(dates)}] {date_str}: {rows} indices | "
                    f"Total: {total_rows:,} | OK: {success_count} Fail: {fail_count}"
                )

            time.sleep(uniform(MIN_DELAY, MAX_DELAY))

        except KeyboardInterrupt:
            logger.info("Interrupted! Saving progress...")
            save_progress(progress)
            if conn:
                conn.close()
            return
        except Exception as e:
            logger.error(f"{date_str}: Error - {e}")
            fail_count += 1
            try:
                if conn:
                    conn.close()
            except:
                pass
            try:
                conn = get_db_connection()
            except:
                conn = None
            time.sleep(MAX_DELAY * 3)

    save_progress(progress)
    if conn:
        conn.close()

    logger.info(
        f"\n{'='*60}\n"
        f"INDEX INGESTION COMPLETE\n"
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
    parser = argparse.ArgumentParser(description="NSE Index Daily Ingestion")
    parser.add_argument("--start", default="2012-05-01",
                        help="Start date YYYY-MM-DD (data available from ~mid-2012)")
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
