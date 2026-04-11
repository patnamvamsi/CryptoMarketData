"""
NSE F&O Bhavcopy Historical Data Ingestion Pipeline
====================================================
Downloads NSE derivatives (futures + options) bhavcopy for all trading days,
stores as CSV + Parquet files, and loads into TimescaleDB.

Data sources:
  - NSE Archives direct download (2000-2023): consistent CSV format in ZIP
  - nselib fno_bhav_copy (2024+): newer format with more fields

Instrument types:
  - FUTIDX: Index Futures (Nifty, BankNifty, etc.)
  - FUTSTK: Stock Futures
  - OPTIDX: Index Options
  - OPTSTK: Stock Options

Free data, no API key needed.
"""

import os
import sys
import time
import logging
import traceback
import json
import zipfile
import io
from datetime import datetime, date, timedelta
from pathlib import Path
from random import uniform

import numpy as np
import pandas as pd
import requests
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
        logging.FileHandler('logs/nse_fo_bhavcopy_ingest.log')
    ]
)

# ============================================================================
# CONFIG
# ============================================================================
SHARE_DIR = Path("/media/vboxuser/test/NSE_Data")
CSV_DIR = SHARE_DIR / "bhavcopy_fo"
PARQUET_DIR = SHARE_DIR / "parquet" / "fo_daily"

MIN_DELAY = 0.5
MAX_DELAY = 2.0

FO_TABLE = "nse_fo_daily"

# Month abbreviations for NSE URL
MONTH_MAP = {
    1: 'JAN', 2: 'FEB', 3: 'MAR', 4: 'APR', 5: 'MAY', 6: 'JUN',
    7: 'JUL', 8: 'AUG', 9: 'SEP', 10: 'OCT', 11: 'NOV', 12: 'DEC'
}


# ============================================================================
# DATABASE
# ============================================================================
def get_db_connection():
    return psycopg2.connect(
        host=TIMESCALE_HOST, port=TIMESCALE_PORT,
        database=TIMESCALE_MARKET_DATA_DB,
        user=TIMESCALE_USERNAME, password=TIMESCALE_PASSWORD
    )


def setup_fo_table(conn):
    cur = conn.cursor()
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {FO_TABLE} (
            time            DATE NOT NULL,
            instrument      TEXT NOT NULL,
            symbol          TEXT NOT NULL,
            expiry          DATE NOT NULL,
            strike          NUMERIC,
            option_type     TEXT,
            open            NUMERIC,
            high            NUMERIC,
            low             NUMERIC,
            close           NUMERIC,
            settle_price    NUMERIC,
            contracts       NUMERIC,
            value_lakh      NUMERIC,
            open_interest   NUMERIC,
            change_in_oi    NUMERIC,
            underlying_price NUMERIC
        );
    """)

    cur.execute("""
        SELECT COUNT(*) FROM timescaledb_information.hypertables
        WHERE hypertable_name = %s
    """, (FO_TABLE,))
    if cur.fetchone()[0] == 0:
        cur.execute(f"""
            SELECT create_hypertable('{FO_TABLE}', 'time',
                chunk_time_interval => INTERVAL '3 months',
                if_not_exists => TRUE);
        """)
        logger.info(f"Created hypertable {FO_TABLE}")

    cur.execute(f"""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_{FO_TABLE}_pk
            ON {FO_TABLE} (symbol, instrument, expiry, strike, option_type, time DESC);
        CREATE INDEX IF NOT EXISTS idx_{FO_TABLE}_time
            ON {FO_TABLE} (time DESC);
        CREATE INDEX IF NOT EXISTS idx_{FO_TABLE}_symbol
            ON {FO_TABLE} (symbol, time DESC);
        CREATE INDEX IF NOT EXISTS idx_{FO_TABLE}_instrument
            ON {FO_TABLE} (instrument, time DESC);
    """)
    conn.commit()
    cur.close()
    logger.info(f"Table {FO_TABLE} ready")


# ============================================================================
# HTTP SESSION (with NSE cookies)
# ============================================================================
_http_session = None
_session_created = None


def get_http_session() -> requests.Session:
    """Get/refresh HTTP session with NSE cookies."""
    global _http_session, _session_created

    # Refresh session every 5 minutes
    if _http_session is None or (datetime.now() - _session_created).seconds > 300:
        _http_session = requests.Session()
        _http_session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                          '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Referer': 'https://www.nseindia.com/',
        })
        try:
            _http_session.get('https://www.nseindia.com', timeout=15)
        except Exception:
            pass  # Continue anyway, sometimes works without cookies
        _session_created = datetime.now()

    return _http_session


# ============================================================================
# DATA FETCHING
# ============================================================================
def fetch_fo_from_nse_archives(trade_date: date) -> pd.DataFrame:
    """
    Download F&O bhavcopy ZIP from NSE archives (works ~2000-2023).
    URL pattern: nsearchives.nseindia.com/content/historical/DERIVATIVES/{YEAR}/{MON}/fo{DD}{MON}{YEAR}bhav.csv.zip
    """
    day = trade_date.strftime("%d")
    mon = MONTH_MAP[trade_date.month]
    year = trade_date.year

    url = (f"https://nsearchives.nseindia.com/content/historical/DERIVATIVES/"
           f"{year}/{mon}/fo{day}{mon}{year}bhav.csv.zip")

    session = get_http_session()
    try:
        r = session.get(url, timeout=20)
        if r.status_code == 200 and len(r.content) > 500:
            z = zipfile.ZipFile(io.BytesIO(r.content))
            csv_files = [f for f in z.namelist() if f.endswith('.csv')]
            if csv_files:
                df = pd.read_csv(z.open(csv_files[0]))
                if not df.empty:
                    df['_source'] = 'nse_archives'
                    return df
        elif r.status_code == 404:
            return None  # Holiday
    except zipfile.BadZipFile:
        logger.debug(f"Bad zip for {trade_date}")
    except Exception as e:
        logger.debug(f"NSE archives failed for {trade_date}: {e}")

    return None


def fetch_fo_from_nselib(trade_date: date) -> pd.DataFrame:
    """Fetch from nselib (2024+)."""
    from nselib import derivatives
    date_str = trade_date.strftime("%d-%m-%Y")
    df = derivatives.fno_bhav_copy(trade_date=date_str)
    if df is not None and not df.empty:
        df['_source'] = 'nselib'
        return df
    return None


def fetch_fo_bhavcopy_for_date(trade_date: date) -> pd.DataFrame:
    """Try multiple sources in priority order."""
    # nselib for 2024+
    if trade_date.year >= 2024:
        try:
            df = fetch_fo_from_nselib(trade_date)
            if df is not None:
                return df
        except Exception as e:
            logger.debug(f"nselib F&O failed for {trade_date}: {e}")

    # NSE archives for all years (primary for pre-2024)
    try:
        df = fetch_fo_from_nse_archives(trade_date)
        if df is not None:
            return df
    except Exception as e:
        logger.debug(f"NSE archives F&O failed for {trade_date}: {e}")

    # nselib as final fallback for 2024+ if archives failed
    if trade_date.year >= 2024:
        try:
            df = fetch_fo_from_nselib(trade_date)
            if df is not None:
                return df
        except Exception:
            pass

    return None


# ============================================================================
# NORMALIZATION
# ============================================================================
def normalize_fo_bhavcopy(df: pd.DataFrame, trade_date: date) -> pd.DataFrame:
    """Normalize F&O bhavcopy from either source into standard schema."""
    if df is None or df.empty:
        return pd.DataFrame()

    source = df.get('_source', pd.Series(['unknown'])).iloc[0]
    df.columns = [c.strip() for c in df.columns]

    if source == 'nselib':
        # nselib format (2024+)
        col_map = {
            'FinInstrmTp': 'instrument',
            'TckrSymb': 'symbol',
            'XpryDt': 'expiry',
            'StrkPric': 'strike',
            'OptnTp': 'option_type',
            'OpnPric': 'open',
            'HghPric': 'high',
            'LwPric': 'low',
            'ClsPric': 'close',
            'SttlmPric': 'settle_price',
            'TtlTradgVol': 'contracts',
            'TtlTrfVal': 'value_lakh',
            'OpnIntrst': 'open_interest',
            'ChngInOpnIntrst': 'change_in_oi',
            'UndrlygPric': 'underlying_price',
        }
        df = df.rename(columns=col_map)

        # Map instrument types
        inst_map = {'STF': 'FUTSTK', 'IDF': 'FUTIDX', 'STO': 'OPTSTK', 'IDO': 'OPTIDX'}
        if 'instrument' in df.columns:
            df['instrument'] = df['instrument'].map(inst_map).fillna(df['instrument'])

    else:
        # NSE archives format (consistent 2000-2023)
        col_map = {
            'INSTRUMENT': 'instrument',
            'SYMBOL': 'symbol',
            'EXPIRY_DT': 'expiry',
            'STRIKE_PR': 'strike',
            'OPTION_TYP': 'option_type',
            'OPEN': 'open',
            'HIGH': 'high',
            'LOW': 'low',
            'CLOSE': 'close',
            'SETTLE_PR': 'settle_price',
            'CONTRACTS': 'contracts',
            'VAL_INLAKH': 'value_lakh',
            'OPEN_INT': 'open_interest',
            'CHG_IN_OI': 'change_in_oi',
        }
        df = df.rename(columns=col_map)
        df['underlying_price'] = None

    # Clean up
    df['time'] = trade_date
    df['symbol'] = df['symbol'].str.strip()

    # Parse expiry date
    if 'expiry' in df.columns:
        df['expiry'] = pd.to_datetime(df['expiry'], dayfirst=True, infer_datetime_format=True).dt.date

    # Replace XX option type with FUT for futures
    if 'option_type' in df.columns:
        df['option_type'] = df['option_type'].fillna('XX')
        df['option_type'] = df['option_type'].str.strip()

    # Filter to standard instrument types
    valid_instruments = ['FUTIDX', 'FUTSTK', 'OPTIDX', 'OPTSTK']
    if 'instrument' in df.columns:
        df = df[df['instrument'].isin(valid_instruments)].copy()

    out_cols = ['time', 'instrument', 'symbol', 'expiry', 'strike', 'option_type',
                'open', 'high', 'low', 'close', 'settle_price',
                'contracts', 'value_lakh', 'open_interest', 'change_in_oi',
                'underlying_price']

    for col in out_cols:
        if col not in df.columns:
            df[col] = None

    df = df[out_cols].copy()

    numeric_cols = ['strike', 'open', 'high', 'low', 'close', 'settle_price',
                    'contracts', 'value_lakh', 'open_interest', 'change_in_oi',
                    'underlying_price']
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
    df.to_csv(csv_year_dir / f"fo_bhavcopy_{date_str}.csv", index=False)

    pq_year_dir = PARQUET_DIR / f"year={year}"
    pq_year_dir.mkdir(parents=True, exist_ok=True)
    df.to_parquet(pq_year_dir / f"fo_bhavcopy_{date_str}.parquet", index=False, engine='pyarrow')


def load_to_timescaledb(df: pd.DataFrame, conn):
    if df.empty:
        return 0

    cur = conn.cursor()
    cols = ['time', 'instrument', 'symbol', 'expiry', 'strike', 'option_type',
            'open', 'high', 'low', 'close', 'settle_price',
            'contracts', 'value_lakh', 'open_interest', 'change_in_oi',
            'underlying_price']

    values = []
    for row in df[cols].itertuples(index=False, name=None):
        values.append(tuple(None if (isinstance(v, float) and pd.isna(v)) else v for v in row))

    if conn is None:
        return 0

    insert_sql = f"""
        INSERT INTO {FO_TABLE} ({', '.join(cols)})
        VALUES %s
        ON CONFLICT (symbol, instrument, expiry, strike, option_type, time)
        DO UPDATE SET
            open = EXCLUDED.open, high = EXCLUDED.high,
            low = EXCLUDED.low, close = EXCLUDED.close,
            settle_price = EXCLUDED.settle_price,
            contracts = EXCLUDED.contracts, value_lakh = EXCLUDED.value_lakh,
            open_interest = EXCLUDED.open_interest, change_in_oi = EXCLUDED.change_in_oi,
            underlying_price = EXCLUDED.underlying_price
    """
    execute_values(cur, insert_sql, values, page_size=2000)
    conn.commit()
    cur.close()
    return len(values)


# ============================================================================
# PROGRESS
# ============================================================================
PROGRESS_FILE = SHARE_DIR / "fo_ingest_progress.json"

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

    try:
        conn = get_db_connection()
        setup_fo_table(conn)
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

    logger.info(f"Starting F&O ingestion: {start_date} to {end_date} ({len(dates)} business days)")

    success_count = 0
    fail_count = 0
    consecutive_fails = 0
    total_rows = progress.get("total_rows", 0)

    for i, trade_date in enumerate(dates):
        date_str = trade_date.strftime("%Y-%m-%d")

        csv_path = CSV_DIR / str(trade_date.year) / f"fo_bhavcopy_{date_str}.csv"
        if skip_existing and csv_path.exists():
            success_count += 1
            consecutive_fails = 0
            continue

        try:
            raw_df = fetch_fo_bhavcopy_for_date(trade_date)
            if raw_df is None or raw_df.empty:
                logger.debug(f"{date_str}: No F&O data (holiday?)")
                fail_count += 1
                consecutive_fails += 1
                if consecutive_fails > 5:
                    time.sleep(MAX_DELAY * 2)
                else:
                    time.sleep(MIN_DELAY)
                continue

            consecutive_fails = 0
            df = normalize_fo_bhavcopy(raw_df, trade_date)
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

            if success_count % 50 == 0 or i < 20:
                logger.info(
                    f"[{i+1}/{len(dates)}] {date_str}: {rows:,} rows | "
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
            traceback.print_exc()
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
        f"F&O INGESTION COMPLETE\n"
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
    parser = argparse.ArgumentParser(description="NSE F&O Bhavcopy Ingestion")
    parser.add_argument("--start", default="2000-06-01",
                        help="Start date YYYY-MM-DD (F&O started Jun 2000)")
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
