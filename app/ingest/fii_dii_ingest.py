#!/usr/bin/env python3
"""
FII/DII Data Ingestion
=======================
Downloads institutional flow data from NSE:

1. F&O Participant-wise OI  (NSE archive CSV, 2012-present) — daily
2. F&O Participant-wise Vol (NSE archive CSV, 2012-present) — daily
3. Cash Market FII/DII      (NSE fiidiiTradeReact API)      — daily only (no history)
4. Bulk Deals               (NSE archive CSV, historical)   — daily

Data saved as Parquet to /media/vboxuser/test/NSE_Data/fii_dii/

Usage:
    python -m app.ingest.fii_dii_ingest --backfill
    python -m app.ingest.fii_dii_ingest --daily
"""

import argparse
import json
import logging
import signal
import time
from datetime import datetime, date, timedelta
from pathlib import Path
from io import StringIO

import pandas as pd
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ============================================================================
# CONFIG
# ============================================================================
DATA_ROOT = Path("/media/vboxuser/test/NSE_Data/fii_dii")
PROGRESS_FILE = Path("/media/vboxuser/test/NSE_Data/fii_dii_progress.json")

# F&O participant data goes back to early 2012
FAO_START_DATE = date(2012, 1, 2)

NSE_FAO_OI_URL = "https://nsearchives.nseindia.com/content/nsccl/fao_participant_oi_{date}.csv"
NSE_FAO_VOL_URL = "https://nsearchives.nseindia.com/content/nsccl/fao_participant_vol_{date}.csv"
NSE_CASH_FIIDII_URL = "https://www.nseindia.com/api/fiidiiTradeReact"
NSE_BULK_DEALS_URL = "https://nsearchives.nseindia.com/content/equities/bulk_{date}.csv"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122",
    "Accept": "*/*",
    "Referer": "https://www.nseindia.com/",
}
REQUEST_DELAY = 0.5
MAX_RETRIES = 3

# ============================================================================
# SHUTDOWN HANDLING
# ============================================================================
_shutdown = False

def _signal_handler(sig, frame):
    global _shutdown
    logger.info("Shutdown signal received...")
    _shutdown = True

signal.signal(signal.SIGTERM, _signal_handler)
signal.signal(signal.SIGINT, _signal_handler)

# ============================================================================
# SESSION
# ============================================================================
_session = None

def get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update(HEADERS)
    return _session

# ============================================================================
# PROGRESS TRACKING
# ============================================================================
def load_progress() -> dict:
    if PROGRESS_FILE.exists():
        try:
            with open(PROGRESS_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "fao_done_dates": [],
        "bulk_done_dates": [],
        "cash_fiidii_done": False,
        "stats": {"fao_rows": 0, "bulk_rows": 0, "cash_rows": 0, "errors": 0},
    }

def save_progress(prog: dict):
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROGRESS_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(prog, f, indent=2, default=str)
    tmp.replace(PROGRESS_FILE)

# ============================================================================
# TRADING DATES
# ============================================================================
def get_trading_dates(start: date, end: date) -> list:
    """Generate weekdays between start and end."""
    days = []
    current = start
    while current <= end:
        if current.weekday() < 5:  # Mon-Fri
            days.append(current)
        current += timedelta(days=1)
    return days

# ============================================================================
# F&O PARTICIPANT OI + VOL
# ============================================================================
def fetch_fao_participant(dt: date, data_type: str = "oi") -> pd.DataFrame:
    """
    Fetch F&O participant-wise OI or volume for a given date.
    data_type: 'oi' or 'vol'
    Returns DataFrame with columns: date, client_type, + all position columns
    """
    date_str = dt.strftime("%d%m%Y")
    url = NSE_FAO_OI_URL.format(date=date_str) if data_type == "oi" else NSE_FAO_VOL_URL.format(date=date_str)

    sess = get_session()
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            time.sleep(REQUEST_DELAY)
            r = sess.get(url, timeout=15)

            if r.status_code == 404:
                return pd.DataFrame()  # Not a trading day or data not available

            if r.status_code != 200:
                logger.warning(f"HTTP {r.status_code} for {url}")
                time.sleep(attempt * 3)
                continue

            text = r.text.strip()
            if not text or "DOCTYPE" in text:
                return pd.DataFrame()

            # Parse: skip first header line, read CSV from second line
            lines = text.split("\n")
            csv_start = next((i for i, l in enumerate(lines) if l.strip().startswith("Client Type")), 1)
            csv_text = "\n".join(lines[csv_start:])

            df = pd.read_csv(StringIO(csv_text))
            df.columns = [c.strip().lower().replace(" ", "_").replace("/", "_") for c in df.columns]

            # Remove TOTAL row
            df = df[df.iloc[:, 0].str.strip().str.upper() != "TOTAL"]
            df = df[df.iloc[:, 0].notna() & (df.iloc[:, 0] != "")]

            # Rename client_type column
            df = df.rename(columns={df.columns[0]: "client_type"})
            df["client_type"] = df["client_type"].str.strip()
            df["date"] = pd.Timestamp(dt)
            df["data_type"] = data_type

            # Convert numeric columns
            for col in df.columns:
                if col not in ["client_type", "date", "data_type"]:
                    df[col] = pd.to_numeric(df[col].astype(str).str.replace(",", "").str.strip(), errors="coerce")

            return df

        except Exception as e:
            logger.warning(f"Error fetching FAO {data_type} {dt} (attempt {attempt}): {e}")
            if attempt < MAX_RETRIES:
                time.sleep(attempt * 3)

    return pd.DataFrame()


# ============================================================================
# CASH MARKET FII/DII (TODAY ONLY)
# ============================================================================
def fetch_cash_fiidii_today() -> pd.DataFrame:
    """Fetch today's FII/DII cash market data from NSE API."""
    sess = get_session()
    try:
        r = sess.get(NSE_CASH_FIIDII_URL, timeout=15)
        if r.status_code != 200:
            return pd.DataFrame()

        data = r.json()
        if not data:
            return pd.DataFrame()

        df = pd.DataFrame(data)
        df.columns = [c.lower().replace(" ", "_") for c in df.columns]

        # Parse date
        df["date"] = pd.to_datetime(df["date"], format="%d-%b-%Y", errors="coerce")

        # Convert values to float
        for col in ["buyvalue", "sellvalue", "netvalue"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        df = df.rename(columns={
            "category": "client_type",
            "buyvalue": "buy_value_cr",
            "sellvalue": "sell_value_cr",
            "netvalue": "net_value_cr",
        })

        return df

    except Exception as e:
        logger.error(f"Error fetching cash FII/DII: {e}")
        return pd.DataFrame()


# ============================================================================
# BULK DEALS
# ============================================================================
def fetch_bulk_deals(dt: date) -> pd.DataFrame:
    """Fetch bulk deals for a given date."""
    date_str = dt.strftime("%d%m%Y")
    url = NSE_BULK_DEALS_URL.format(date=date_str)

    sess = get_session()
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            time.sleep(REQUEST_DELAY)
            r = sess.get(url, timeout=15)

            if r.status_code == 404:
                return pd.DataFrame()

            if r.status_code != 200:
                time.sleep(attempt * 3)
                continue

            text = r.text.strip()
            if not text or "DOCTYPE" in text or len(text) < 50:
                return pd.DataFrame()

            df = pd.read_csv(StringIO(text))
            df.columns = [c.strip().lower().replace(" ", "_").replace("/", "_") for c in df.columns]
            df["date"] = pd.Timestamp(dt)

            return df

        except Exception as e:
            logger.warning(f"Error fetching bulk deals {dt} (attempt {attempt}): {e}")
            if attempt < MAX_RETRIES:
                time.sleep(attempt * 3)

    return pd.DataFrame()


# ============================================================================
# SAVE TO PARQUET
# ============================================================================
def save_parquet(df: pd.DataFrame, filename: str, dedup_cols: list = None):
    """Append to parquet file, deduplicating on given columns."""
    if df.empty:
        return 0

    out_path = DATA_ROOT / filename
    DATA_ROOT.mkdir(parents=True, exist_ok=True)

    if out_path.exists():
        try:
            existing = pd.read_parquet(out_path)
            combined = pd.concat([existing, df], ignore_index=True)
            if dedup_cols:
                combined = combined.drop_duplicates(subset=dedup_cols, keep="last")
            combined.to_parquet(out_path, index=False)
            return len(df)
        except Exception as e:
            logger.warning(f"Error merging {filename}: {e}, overwriting")

    df.to_parquet(out_path, index=False)
    return len(df)


# ============================================================================
# BACKFILL
# ============================================================================
def run_backfill(start: date = None, end: date = None):
    """Download all historical F&O participant OI/Vol and bulk deals."""
    start = start or FAO_START_DATE
    end = end or date.today()

    prog = load_progress()
    fao_done = set(prog.get("fao_done_dates", []))
    bulk_done = set(prog.get("bulk_done_dates", []))
    stats = prog.get("stats", {"fao_rows": 0, "bulk_rows": 0, "cash_rows": 0, "errors": 0})

    trading_dates = get_trading_dates(start, end)
    fao_remaining = [d for d in trading_dates if d.isoformat() not in fao_done]

    logger.info(f"Starting backfill: {len(fao_remaining)} dates remaining ({start} → {end})")

    batch_fao_oi = []
    batch_fao_vol = []
    batch_bulk = []
    batch_size = 50

    for i, dt in enumerate(fao_remaining):
        if _shutdown:
            break

        # F&O Participant OI
        oi_df = fetch_fao_participant(dt, "oi")
        if not oi_df.empty:
            batch_fao_oi.append(oi_df)
            stats["fao_rows"] += len(oi_df)

        # F&O Participant Vol
        vol_df = fetch_fao_participant(dt, "vol")
        if not vol_df.empty:
            batch_fao_vol.append(vol_df)

        # Bulk Deals
        bulk_df = fetch_bulk_deals(dt)
        if not bulk_df.empty:
            batch_bulk.append(bulk_df)
            stats["bulk_rows"] += len(bulk_df)

        fao_done.add(dt.isoformat())

        if (i + 1) % 10 == 0:
            logger.info(
                f"[{i+1}/{len(fao_remaining)}] {dt}: "
                f"FAO rows={stats['fao_rows']:,} bulk_rows={stats['bulk_rows']:,}"
            )

        # Batch save
        if (i + 1) % batch_size == 0:
            if batch_fao_oi:
                save_parquet(pd.concat(batch_fao_oi, ignore_index=True), "fao_participant_oi.parquet",
                             dedup_cols=["date", "client_type"])
                batch_fao_oi = []
            if batch_fao_vol:
                save_parquet(pd.concat(batch_fao_vol, ignore_index=True), "fao_participant_vol.parquet",
                             dedup_cols=["date", "client_type"])
                batch_fao_vol = []
            if batch_bulk:
                save_parquet(pd.concat(batch_bulk, ignore_index=True), "bulk_deals.parquet",
                             dedup_cols=["date", "symbol", "client_name"])
                batch_bulk = []

            prog["fao_done_dates"] = list(fao_done)
            prog["stats"] = stats
            prog["updated_at"] = datetime.utcnow().isoformat()
            save_progress(prog)

    # Final batch save
    if batch_fao_oi:
        save_parquet(pd.concat(batch_fao_oi, ignore_index=True), "fao_participant_oi.parquet",
                     dedup_cols=["date", "client_type"])
    if batch_fao_vol:
        save_parquet(pd.concat(batch_fao_vol, ignore_index=True), "fao_participant_vol.parquet",
                     dedup_cols=["date", "client_type"])
    if batch_bulk:
        save_parquet(pd.concat(batch_bulk, ignore_index=True), "bulk_deals.parquet",
                     dedup_cols=["date", "symbol", "client_name"])

    # Also capture today's cash FII/DII
    cash_df = fetch_cash_fiidii_today()
    if not cash_df.empty:
        save_parquet(cash_df, "cash_fiidii_daily.parquet", dedup_cols=["date", "client_type"])
        stats["cash_rows"] += len(cash_df)

    prog["fao_done_dates"] = list(fao_done)
    prog["stats"] = stats
    prog["updated_at"] = datetime.utcnow().isoformat()
    save_progress(prog)

    logger.info(f"Backfill complete: {stats}")
    return stats


# ============================================================================
# DAILY
# ============================================================================
def run_daily():
    """Fetch yesterday's (or today's) FII/DII data."""
    today = date.today()
    yesterday = today - timedelta(days=1)
    # Use yesterday if today is weekend
    if today.weekday() >= 5:
        target = today - timedelta(days=today.weekday() - 4)
    else:
        target = yesterday

    logger.info(f"Daily FII/DII update for {target}")

    prog = load_progress()
    stats = prog.get("stats", {"fao_rows": 0, "bulk_rows": 0, "cash_rows": 0, "errors": 0})

    # F&O data
    oi_df = fetch_fao_participant(target, "oi")
    if not oi_df.empty:
        save_parquet(oi_df, "fao_participant_oi.parquet", dedup_cols=["date", "client_type"])
        stats["fao_rows"] += len(oi_df)
        logger.info(f"FAO OI: {len(oi_df)} rows")

    vol_df = fetch_fao_participant(target, "vol")
    if not vol_df.empty:
        save_parquet(vol_df, "fao_participant_vol.parquet", dedup_cols=["date", "client_type"])
        logger.info(f"FAO Vol: {len(vol_df)} rows")

    # Cash FII/DII (today only)
    cash_df = fetch_cash_fiidii_today()
    if not cash_df.empty:
        save_parquet(cash_df, "cash_fiidii_daily.parquet", dedup_cols=["date", "client_type"])
        stats["cash_rows"] += len(cash_df)
        logger.info(f"Cash FII/DII: {len(cash_df)} rows")

    # Bulk deals
    bulk_df = fetch_bulk_deals(target)
    if not bulk_df.empty:
        save_parquet(bulk_df, "bulk_deals.parquet", dedup_cols=["date", "symbol", "client_name"])
        stats["bulk_rows"] += len(bulk_df)
        logger.info(f"Bulk deals: {len(bulk_df)} rows")

    prog["stats"] = stats
    prog["updated_at"] = datetime.utcnow().isoformat()
    save_progress(prog)
    return stats


# ============================================================================
# MAIN
# ============================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FII/DII Data Ingestion")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--backfill", action="store_true")
    group.add_argument("--daily", action="store_true")
    parser.add_argument("--start", type=str, help="Start date YYYY-MM-DD (backfill only)")
    parser.add_argument("--end", type=str, help="End date YYYY-MM-DD (backfill only)")
    args = parser.parse_args()

    if args.backfill:
        start = date.fromisoformat(args.start) if args.start else None
        end = date.fromisoformat(args.end) if args.end else None
        result = run_backfill(start=start, end=end)
        print(f"Done: {result}")
    elif args.daily:
        result = run_daily()
        print(f"Done: {result}")
