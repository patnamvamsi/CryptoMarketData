#!/usr/bin/env python3
"""
Global Market Signals Ingestion
==================================
Downloads historical and daily data for global macro indicators:

- SGX Nifty (overnight India signal) — ^NSEI proxy via S&P futures
- S&P 500 (^GSPC)
- Dow Jones (^DJI)
- NASDAQ (^IXIC)
- VIX (^VIX) — US volatility
- USD/INR (USDINR=X)
- EUR/INR (EURINR=X)
- Crude Oil WTI (CL=F)
- Brent Crude (BZ=F)
- Gold (GC=F)
- Silver (SI=F)
- US 10Y Treasury Yield (^TNX)
- Nikkei 225 (^N225)
- Hang Seng (^HSI)
- FTSE 100 (^FTSE)
- DAX (^GDAXI)

Usage:
    python -m app.ingest.global_signals_ingest --backfill
    python -m app.ingest.global_signals_ingest --daily
"""

import argparse
import logging
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
DATA_ROOT = Path("/media/vboxuser/test/NSE_Data/global_signals")
BACKFILL_START = "2000-01-01"
REQUEST_DELAY = 0.5

SIGNALS = {
    # US Equities
    "sp500":        {"ticker": "^GSPC",   "name": "S&P 500"},
    "dow":          {"ticker": "^DJI",    "name": "Dow Jones"},
    "nasdaq":       {"ticker": "^IXIC",   "name": "NASDAQ"},
    # Volatility
    "vix":          {"ticker": "^VIX",    "name": "CBOE VIX"},
    # Currencies
    "usdinr":       {"ticker": "USDINR=X","name": "USD/INR"},
    "eurinr":       {"ticker": "EURINR=X","name": "EUR/INR"},
    "gbpinr":       {"ticker": "GBPINR=X","name": "GBP/INR"},
    # Commodities
    "crude_wti":    {"ticker": "CL=F",    "name": "Crude Oil WTI"},
    "crude_brent":  {"ticker": "BZ=F",    "name": "Brent Crude"},
    "gold":         {"ticker": "GC=F",    "name": "Gold"},
    "silver":       {"ticker": "SI=F",    "name": "Silver"},
    "natural_gas":  {"ticker": "NG=F",    "name": "Natural Gas"},
    # Bonds
    "us_10y":       {"ticker": "^TNX",    "name": "US 10Y Treasury Yield"},
    "us_2y":        {"ticker": "^IRX",    "name": "US 13W T-Bill"},
    # Asian Markets
    "nikkei":       {"ticker": "^N225",   "name": "Nikkei 225"},
    "hang_seng":    {"ticker": "^HSI",    "name": "Hang Seng"},
    "shanghai":     {"ticker": "000001.SS","name": "Shanghai Composite"},
    # European Markets
    "ftse":         {"ticker": "^FTSE",   "name": "FTSE 100"},
    "dax":          {"ticker": "^GDAXI",  "name": "DAX"},
    # India VIX (already in OHLCV but handy here too)
    "india_vix":    {"ticker": "^INDIAVIX","name": "India VIX"},
}


# ============================================================================
# FETCH
# ============================================================================
def fetch_signal(key: str, ticker: str, start: str = BACKFILL_START,
                 end: str = None) -> pd.DataFrame:
    """Fetch OHLCV data for a single signal."""
    end = end or date.today().isoformat()
    try:
        df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
        if df.empty:
            logger.warning(f"{key} ({ticker}): no data")
            return pd.DataFrame()

        df = df.reset_index()
        df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in df.columns]
        df = df.rename(columns={"date": "date", "adj close": "close"})

        # Standardise columns
        df["signal"] = key
        df["ticker"] = ticker
        df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None).dt.date

        # Keep relevant columns
        keep = ["date", "signal", "ticker", "open", "high", "low", "close", "volume"]
        df = df[[c for c in keep if c in df.columns]]

        logger.info(f"{key} ({ticker}): {len(df)} rows ({df['date'].min()} → {df['date'].max()})")
        return df

    except Exception as e:
        logger.error(f"Error fetching {key} ({ticker}): {e}")
        return pd.DataFrame()


# ============================================================================
# SAVE
# ============================================================================
def save_signals(df: pd.DataFrame):
    """Save/merge all signals into a single Parquet file."""
    if df.empty:
        return

    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    out_path = DATA_ROOT / "global_signals.parquet"

    if out_path.exists():
        existing = pd.read_parquet(out_path)
        combined = pd.concat([existing, df], ignore_index=True)
        combined = combined.drop_duplicates(subset=["date", "signal"], keep="last")
        combined = combined.sort_values(["signal", "date"])
        combined.to_parquet(out_path, index=False)
    else:
        df = df.sort_values(["signal", "date"])
        df.to_parquet(out_path, index=False)

    logger.info(f"Saved global signals → {out_path} ({len(df)} new rows)")


# ============================================================================
# BACKFILL
# ============================================================================
def run_backfill(start: str = BACKFILL_START):
    """Download full history for all signals."""
    logger.info(f"Starting global signals backfill from {start}")
    all_dfs = []

    for i, (key, meta) in enumerate(SIGNALS.items()):
        df = fetch_signal(key, meta["ticker"], start=start)
        if not df.empty:
            all_dfs.append(df)
        time.sleep(REQUEST_DELAY)

    if all_dfs:
        combined = pd.concat(all_dfs, ignore_index=True)
        save_signals(combined)
        logger.info(f"Backfill complete: {len(combined):,} total rows across {len(all_dfs)} signals")
        return {"signals": len(all_dfs), "rows": len(combined)}
    return {"signals": 0, "rows": 0}


# ============================================================================
# DAILY
# ============================================================================
def run_daily():
    """Fetch yesterday's data for all signals."""
    yesterday = (date.today()).isoformat()  # yfinance end is exclusive so use today
    start = (date.today().replace(day=1)).isoformat()  # last month for safety

    logger.info("Running daily global signals update...")
    all_dfs = []

    for key, meta in SIGNALS.items():
        df = fetch_signal(key, meta["ticker"], start=start, end=yesterday)
        if not df.empty:
            all_dfs.append(df)
        time.sleep(REQUEST_DELAY)

    if all_dfs:
        combined = pd.concat(all_dfs, ignore_index=True)
        save_signals(combined)
        logger.info(f"Daily update complete: {len(combined)} rows")
        return {"signals": len(all_dfs), "rows": len(combined)}
    return {"signals": 0, "rows": 0}


# ============================================================================
# MAIN
# ============================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Global Market Signals")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--backfill", action="store_true")
    group.add_argument("--daily", action="store_true")
    parser.add_argument("--start", default=BACKFILL_START)
    args = parser.parse_args()

    if args.backfill:
        result = run_backfill(start=args.start)
        print(f"Done: {result}")
    elif args.daily:
        result = run_daily()
        print(f"Done: {result}")
