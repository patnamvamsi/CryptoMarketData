#!/usr/bin/env python3
"""
NSE Corporate Events Ingestion
================================
Downloads corporate actions (dividends, bonus, splits, results, board meetings)
from NSE India public API. Saves as Parquet files partitioned by year/month.

NSE requires session cookies — always fetch the homepage first, then reuse cookies.

Usage:
    python -m app.ingest.nse_corporate_events --backfill
    python -m app.ingest.nse_corporate_events --daily
    python -m app.ingest.nse_corporate_events --backfill --start 2015-01-01
"""

import json
import logging
import sys
import time
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)

# ============================================================================
# CONFIG
# ============================================================================
DATA_ROOT = Path("/media/vboxuser/test/NSE_Data/corporate_events")
PROGRESS_FILE = Path("/media/vboxuser/test/NSE_Data/corporate_events_progress.json")

BACKFILL_START = date(2010, 1, 1)

NSE_HOME_URL = "https://www.nseindia.com/"
NSE_CORP_ACTIONS_URL = (
    "https://www.nseindia.com/api/corporates-corporateActions"
    "?index=equities&from_date={from_date}&to_date={to_date}"
)

# NSE requires browser-like headers with session cookies
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.nseindia.com/",
    "Connection": "keep-alive",
}

REQUEST_DELAY = 1.5   # seconds between API calls
MAX_RETRIES = 3


# ============================================================================
# SESSION MANAGEMENT
# ============================================================================
_session: Optional[requests.Session] = None
_session_born: float = 0.0
SESSION_TTL = 600  # refresh cookies every 10 minutes


def get_nse_session() -> requests.Session:
    """Return a requests Session with valid NSE cookies. Refreshes as needed."""
    global _session, _session_born

    now = time.time()
    if _session is None or (now - _session_born) > SESSION_TTL:
        sess = requests.Session()
        sess.headers.update(HEADERS)

        logger.debug("Fetching NSE homepage to obtain session cookies...")
        try:
            resp = sess.get(NSE_HOME_URL, timeout=15)
            resp.raise_for_status()
            logger.debug(f"NSE homepage OK ({len(resp.content)} bytes), cookies: {list(sess.cookies.keys())}")
        except Exception as e:
            logger.warning(f"NSE homepage fetch failed: {e}. Proceeding anyway.")

        time.sleep(1.0)
        _session = sess
        _session_born = now

    return _session


# ============================================================================
# PROGRESS TRACKING
# ============================================================================
def load_progress() -> dict:
    """Load completed-months set from progress file."""
    if PROGRESS_FILE.exists():
        try:
            with open(PROGRESS_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "started_at": datetime.now().isoformat(),
        "completed_months": [],   # list of "YYYY-MM" strings
        "stats": {"months_fetched": 0, "total_rows": 0, "errors": 0},
    }


def save_progress(prog: dict):
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    prog["updated_at"] = datetime.now().isoformat()
    with open(PROGRESS_FILE, "w") as f:
        json.dump(prog, f, indent=2, default=str)


def is_month_done(prog: dict, year: int, month: int) -> bool:
    return f"{year}-{month:02d}" in prog.get("completed_months", [])


def mark_month_done(prog: dict, year: int, month: int, row_count: int):
    key = f"{year}-{month:02d}"
    if key not in prog["completed_months"]:
        prog["completed_months"].append(key)
    prog["stats"]["months_fetched"] = prog["stats"].get("months_fetched", 0) + 1
    prog["stats"]["total_rows"] = prog["stats"].get("total_rows", 0) + row_count


# ============================================================================
# DATA FETCHING
# ============================================================================
def fetch_corporate_actions(from_dt: date, to_dt: date) -> Optional[list]:
    """
    Fetch corporate actions for [from_dt, to_dt] from NSE API.
    Returns a list of dicts, or None on unrecoverable failure.
    """
    from_str = from_dt.strftime("%d-%m-%Y")
    to_str = to_dt.strftime("%d-%m-%Y")
    url = NSE_CORP_ACTIONS_URL.format(from_date=from_str, to_date=to_str)

    sess = get_nse_session()

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            time.sleep(REQUEST_DELAY)
            resp = sess.get(url, timeout=20)

            if resp.status_code == 429:
                wait = attempt * 10
                logger.warning(f"Rate limited (429). Waiting {wait}s...")
                time.sleep(wait)
                continue

            if resp.status_code == 401 or resp.status_code == 403:
                logger.warning(f"Cookie expired ({resp.status_code}). Refreshing session...")
                global _session, _session_born
                _session = None
                _session_born = 0.0
                sess = get_nse_session()
                continue

            resp.raise_for_status()

            data = resp.json()

            # NSE may return a list directly or a dict with a key
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                # Try common keys
                for key in ("data", "results", "corporateActions", "CA"):
                    if key in data and isinstance(data[key], list):
                        return data[key]
                # Fallback — return as single-element list if non-empty
                if data:
                    return [data]
                return []

            return []

        except requests.exceptions.Timeout:
            logger.warning(f"Timeout on attempt {attempt}/{MAX_RETRIES} for {from_str}→{to_str}")
            if attempt == MAX_RETRIES:
                return None
            time.sleep(attempt * 5)

        except requests.exceptions.ConnectionError as e:
            logger.warning(f"Connection error (attempt {attempt}): {e}")
            if attempt == MAX_RETRIES:
                return None
            time.sleep(attempt * 10)

        except Exception as e:
            logger.error(f"Unexpected error fetching {from_str}→{to_str}: {e}")
            if attempt == MAX_RETRIES:
                return None
            time.sleep(attempt * 3)

    return None


def normalize_records(records: list, year: int, month: int) -> pd.DataFrame:
    """Normalize raw NSE JSON records into a clean DataFrame."""
    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)

    # Strip whitespace from column names
    df.columns = [c.strip() for c in df.columns]

    # Lowercase column map for flexible renaming
    col_lower = {c.lower().replace(" ", "_").replace("-", "_"): c for c in df.columns}

    rename_map = {}
    field_aliases = {
        "symbol":           ["symbol", "scrip_symbol", "scripsymbol", "smbl"],
        "company_name":     ["company_name", "companyname", "company", "corp"],
        "series":           ["series", "srs"],
        "ex_date":          ["ex_date", "exdate", "ex_dt", "exdt"],
        "purpose":          ["purpose", "action", "corporate_action", "subject"],
        "record_date":      ["record_date", "recdate", "rec_date"],
        "bc_start_date":    ["bc_start_date", "bcstartdate", "book_closure_start"],
        "bc_end_date":      ["bc_end_date", "bcenddate", "book_closure_end"],
        "nd_start_date":    ["nd_start_date", "ndstartdate"],
        "nd_end_date":      ["nd_end_date", "ndenddate"],
        "actual_ex_date":   ["actual_ex_date", "actualexdate"],
    }

    for standard, aliases in field_aliases.items():
        for alias in aliases:
            if alias in col_lower and col_lower[alias] not in rename_map:
                rename_map[col_lower[alias]] = standard
                break

    df = df.rename(columns=rename_map)

    # Ensure required columns exist
    for col in ["symbol", "company_name", "ex_date", "purpose"]:
        if col not in df.columns:
            df[col] = None

    # Add partition metadata
    df["_year"] = year
    df["_month"] = month

    # Parse ex_date to a proper date
    if "ex_date" in df.columns:
        df["ex_date"] = pd.to_datetime(df["ex_date"], errors="coerce", dayfirst=True)

    # Drop fully-empty rows
    df = df.dropna(how="all")

    return df


# ============================================================================
# STORAGE
# ============================================================================
def save_month(df: pd.DataFrame, year: int, month: int):
    """Save a month's corporate actions to Parquet."""
    if df.empty:
        return

    out_dir = DATA_ROOT / str(year)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{month:02d}.parquet"

    # Upsert: merge with existing if file already exists
    if out_path.exists():
        try:
            existing = pd.read_parquet(out_path)
            df = pd.concat([existing, df], ignore_index=True).drop_duplicates()
        except Exception as e:
            logger.warning(f"Could not read existing parquet {out_path}: {e}. Overwriting.")

    df.to_parquet(out_path, index=False, engine="pyarrow")
    logger.info(f"Saved {len(df)} rows → {out_path}")


# ============================================================================
# INGESTION RUNNERS
# ============================================================================
def ingest_month(year: int, month: int, prog: dict) -> int:
    """
    Fetch and save one calendar month of corporate actions.
    Returns number of rows saved (0 on skip/error).
    """
    if is_month_done(prog, year, month):
        logger.debug(f"Skipping {year}-{month:02d} (already done)")
        return 0

    from_dt = date(year, month, 1)
    # Last day of month
    if month == 12:
        to_dt = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        to_dt = date(year, month + 1, 1) - timedelta(days=1)

    # Don't fetch future months
    today = date.today()
    if from_dt > today:
        return 0
    to_dt = min(to_dt, today)

    logger.info(f"Fetching corporate actions {from_dt} → {to_dt}")
    records = fetch_corporate_actions(from_dt, to_dt)

    if records is None:
        logger.error(f"Failed to fetch {year}-{month:02d} after {MAX_RETRIES} retries")
        prog["stats"]["errors"] = prog["stats"].get("errors", 0) + 1
        return 0

    df = normalize_records(records, year, month)
    save_month(df, year, month)
    row_count = len(df)
    mark_month_done(prog, year, month, row_count)
    return row_count


def run_backfill(start: Optional[date] = None):
    """
    Backfill from BACKFILL_START (or given start) to today, one month at a time.
    Resume-safe: skips already-completed months.
    """
    prog = load_progress()
    start_dt = start or BACKFILL_START
    today = date.today()

    current = date(start_dt.year, start_dt.month, 1)
    end = date(today.year, today.month, 1)

    total_months = 0
    total_rows = 0

    while current <= end:
        rows = ingest_month(current.year, current.month, prog)
        total_rows += rows
        total_months += 1
        save_progress(prog)

        # Advance one month
        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)

    logger.info(
        f"Backfill complete: {total_months} months processed, "
        f"{total_rows} total rows, errors: {prog['stats'].get('errors', 0)}"
    )
    save_progress(prog)
    return {"months": total_months, "rows": total_rows}


def run_daily():
    """Fetch yesterday + today corporate actions (for daily scheduler job)."""
    prog = load_progress()
    today = date.today()

    # Fetch current month (and previous if near start of month)
    months_to_fetch = [(today.year, today.month)]
    if today.day <= 3:
        # Near start of month, also refresh previous month
        prev = today.replace(day=1) - timedelta(days=1)
        months_to_fetch.insert(0, (prev.year, prev.month))
        # Force re-fetch by removing from completed list
        for y, m in months_to_fetch:
            key = f"{y}-{m:02d}"
            if key in prog.get("completed_months", []):
                prog["completed_months"].remove(key)

    total_rows = 0
    for year, month in months_to_fetch:
        # Force re-fetch current month (may have new data today)
        key = f"{year}-{month:02d}"
        if key in prog.get("completed_months", []):
            prog["completed_months"].remove(key)

        rows = ingest_month(year, month, prog)
        total_rows += rows

    save_progress(prog)
    logger.info(f"Daily run complete: {total_rows} rows fetched")
    return {"rows": total_rows}


# ============================================================================
# MAIN
# ============================================================================
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="NSE Corporate Events Ingestion")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--backfill", action="store_true", help="Historical backfill from 2010-01-01")
    mode.add_argument("--daily", action="store_true", help="Fetch today's data (daily job)")
    parser.add_argument("--start", default=None, help="Override backfill start date YYYY-MM-DD")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(
                Path("/media/vboxuser/test/NSE_Data") / "corporate_events_ingest.log"
            ),
        ],
    )

    if args.backfill:
        start_date = (
            datetime.strptime(args.start, "%Y-%m-%d").date() if args.start else None
        )
        result = run_backfill(start=start_date)
        print(f"Done: {result}")
    elif args.daily:
        result = run_daily()
        print(f"Done: {result}")
