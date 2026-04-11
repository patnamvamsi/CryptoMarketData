#!/usr/bin/env python3
"""
NSE Corporate Events Ingestion (via yfinance)
==============================================
Downloads corporate actions (dividends, stock splits) for all NSE equities
using Yahoo Finance. Saves as Parquet files partitioned by event type.

Usage:
    python -m app.ingest.nse_corporate_events --backfill
    python -m app.ingest.nse_corporate_events --daily
    python -m app.ingest.nse_corporate_events --symbol RELIANCE
"""

import argparse
import json
import logging
import signal
import sys
import time
from datetime import datetime, date
from pathlib import Path

import pandas as pd
import yfinance as yf

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ============================================================================
# CONFIG
# ============================================================================
DATA_ROOT = Path("/media/vboxuser/test/NSE_Data/corporate_events")
PROGRESS_FILE = Path("/media/vboxuser/test/NSE_Data/corporate_events_progress.json")
REQUEST_DELAY = 0.3   # seconds between yfinance calls
MAX_RETRIES = 3

# ============================================================================
# SHUTDOWN HANDLING
# ============================================================================
_shutdown = False

def _signal_handler(sig, frame):
    global _shutdown
    logger.info("Shutdown signal received, finishing current symbol...")
    _shutdown = True

if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)


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
    return {"done": [], "stats": {"total_symbols": 0, "total_rows": 0, "errors": 0}}


def save_progress(prog: dict):
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROGRESS_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(prog, f, indent=2, default=str)
    tmp.replace(PROGRESS_FILE)


# ============================================================================
# NSE SYMBOL LIST
# ============================================================================
def get_nse_symbols() -> list:
    """Get full NSE equity symbol list via nselib."""
    try:
        from nselib import capital_market
        df = capital_market.equity_list()
        symbols = df["SYMBOL"].str.strip().tolist()
        logger.info(f"Loaded {len(symbols)} NSE symbols from nselib")
        return symbols
    except Exception as e:
        logger.error(f"Failed to load NSE symbol list: {e}")
        return []


# ============================================================================
# FETCH CORPORATE ACTIONS
# ============================================================================
def fetch_symbol_actions(symbol: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Fetch dividends and splits for a symbol via yfinance.
    Returns (dividends_df, splits_df).
    """
    yf_symbol = f"{symbol}.NS"

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            tk = yf.Ticker(yf_symbol)
            actions = tk.actions  # DataFrame with Dividends + Stock Splits columns

            if actions is None or actions.empty:
                return pd.DataFrame(), pd.DataFrame()

            # Dividends
            divs = actions[actions["Dividends"] > 0][["Dividends"]].copy()
            divs = divs.reset_index()
            divs.columns = ["ex_date", "amount"]
            divs["symbol"] = symbol
            divs["event_type"] = "dividend"
            divs["ex_date"] = pd.to_datetime(divs["ex_date"]).dt.tz_localize(None)

            # Splits
            splits = actions[actions["Stock Splits"] > 0][["Stock Splits"]].copy()
            splits = splits.reset_index()
            splits.columns = ["ex_date", "ratio"]
            splits["symbol"] = symbol
            splits["event_type"] = "split"
            splits["ex_date"] = pd.to_datetime(splits["ex_date"]).dt.tz_localize(None)

            return divs, splits

        except Exception as e:
            if attempt < MAX_RETRIES:
                time.sleep(attempt * 2)
            else:
                logger.warning(f"Failed to fetch {symbol} after {MAX_RETRIES} attempts: {e}")
                return pd.DataFrame(), pd.DataFrame()

    return pd.DataFrame(), pd.DataFrame()


# ============================================================================
# SAVE TO PARQUET
# ============================================================================
def save_parquet(df: pd.DataFrame, event_type: str):
    """Append rows to the parquet file for the given event type."""
    if df.empty:
        return 0

    out_path = DATA_ROOT / f"{event_type}.parquet"
    DATA_ROOT.mkdir(parents=True, exist_ok=True)

    if out_path.exists():
        existing = pd.read_parquet(out_path)
        combined = pd.concat([existing, df], ignore_index=True)
        combined = combined.drop_duplicates(subset=["symbol", "ex_date"])
        combined.to_parquet(out_path, index=False)
        return len(df)
    else:
        df.to_parquet(out_path, index=False)
        return len(df)


# ============================================================================
# BACKFILL
# ============================================================================
def run_backfill(symbol_filter: str = None):
    """Download corporate actions for all NSE equities."""
    symbols = get_nse_symbols()
    if not symbols:
        logger.error("No symbols loaded. Aborting.")
        return

    if symbol_filter:
        symbols = [s for s in symbols if s.upper() == symbol_filter.upper()]
        logger.info(f"Filtered to symbol: {symbol_filter}")

    prog = load_progress()
    done_set = set(prog.get("done", []))
    stats = prog.get("stats", {"total_symbols": 0, "total_rows": 0, "errors": 0})

    remaining = [s for s in symbols if s not in done_set]
    total = len(symbols)
    logger.info(f"Starting backfill: {len(remaining)} remaining of {total} symbols")

    all_divs = []
    all_splits = []
    batch_size = 100  # Save to parquet every N symbols

    for i, symbol in enumerate(remaining):
        if _shutdown:
            logger.info("Shutdown requested, saving progress...")
            break

        divs, splits = fetch_symbol_actions(symbol)

        rows = len(divs) + len(splits)
        stats["total_rows"] += rows
        stats["total_symbols"] += 1

        if not divs.empty:
            all_divs.append(divs)
        if not splits.empty:
            all_splits.append(splits)

        done_set.add(symbol)

        if (i + 1) % 10 == 0:
            logger.info(
                f"[{i+1}/{len(remaining)}] {symbol}: "
                f"{len(divs)} divs, {len(splits)} splits | "
                f"Total rows: {stats['total_rows']:,}"
            )

        # Batch save every N symbols
        if (i + 1) % batch_size == 0:
            if all_divs:
                save_parquet(pd.concat(all_divs, ignore_index=True), "dividends")
                all_divs = []
            if all_splits:
                save_parquet(pd.concat(all_splits, ignore_index=True), "splits")
                all_splits = []

            prog["done"] = list(done_set)
            prog["stats"] = stats
            prog["updated_at"] = datetime.utcnow().isoformat()
            save_progress(prog)

        time.sleep(REQUEST_DELAY)

    # Final save
    if all_divs:
        save_parquet(pd.concat(all_divs, ignore_index=True), "dividends")
    if all_splits:
        save_parquet(pd.concat(all_splits, ignore_index=True), "splits")

    prog["done"] = list(done_set)
    prog["stats"] = stats
    prog["updated_at"] = datetime.utcnow().isoformat()
    save_progress(prog)

    logger.info(f"Backfill complete: {stats}")
    return stats


# ============================================================================
# DAILY UPDATE
# ============================================================================
def run_daily():
    """Refresh corporate actions for all symbols (picks up new events)."""
    logger.info("Running daily corporate events refresh...")
    # For daily mode, just re-run backfill — yfinance always returns latest data
    # and save_parquet deduplicates, so it's safe to re-run
    return run_backfill()


# ============================================================================
# MAIN
# ============================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NSE Corporate Events Ingestion")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--backfill", action="store_true", help="Download all historical corporate actions")
    group.add_argument("--daily", action="store_true", help="Daily refresh mode")
    group.add_argument("--symbol", type=str, help="Fetch single symbol only")
    args = parser.parse_args()

    if args.backfill:
        result = run_backfill()
        print(f"Done: {result}")
    elif args.daily:
        result = run_daily()
        print(f"Done: {result}")
    elif args.symbol:
        result = run_backfill(symbol_filter=args.symbol)
        print(f"Done: {result}")
