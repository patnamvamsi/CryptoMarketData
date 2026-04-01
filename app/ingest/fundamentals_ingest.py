#!/usr/bin/env python3
"""
Fundamentals Data Ingestion
=============================
Collects quarterly financial data for all NSE equities from two sources:

1. NSE API + XBRL filings  → Quarterly P&L (Revenue, EBITDA, PAT, EPS)
                              Filing dates (earnings calendar) back to 2005
2. yfinance                → Balance sheet, valuation ratios, shareholding %

Data saved to /media/vboxuser/test/NSE_Data/fundamentals/

Usage:
    python -m app.ingest.fundamentals_ingest --backfill
    python -m app.ingest.fundamentals_ingest --daily
    python -m app.ingest.fundamentals_ingest --symbol RELIANCE
"""

import argparse
import json
import logging
import signal
import time
import warnings
import xml.etree.ElementTree as ET
from datetime import datetime, date
from io import StringIO
from pathlib import Path
from typing import Optional

import pandas as pd
import requests
import yfinance as yf

warnings.filterwarnings("ignore")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ============================================================================
# CONFIG
# ============================================================================
DATA_ROOT = Path("/media/vboxuser/test/NSE_Data/fundamentals")
PROGRESS_FILE = Path("/media/vboxuser/test/NSE_Data/fundamentals_progress.json")
REQUEST_DELAY = 0.5
MAX_RETRIES = 3

NSE_RESULTS_URL = "https://www.nseindia.com/api/corporates-financial-results?symbol={symbol}&index=equities&period=Quarterly"
NSE_ANNUAL_URL  = "https://www.nseindia.com/api/corporates-financial-results?symbol={symbol}&index=equities&period=Annual"

# Key XBRL financial tags to extract
XBRL_TAGS = {
    "RevenueFromOperations":                    "revenue",
    "OtherIncome":                              "other_income",
    "Income":                                   "total_income",
    "ProfitBeforeTax":                          "profit_before_tax",
    "ProfitLossForPeriod":                      "net_profit",
    "ProfitOrLossAttributableToOwnersOfParent": "pat_owners",
    "PaidUpValueOfEquityShareCapital":          "share_capital",
    "BasicEarningsLossPerShare":                "eps_basic",
    "DilutedEarningsLossPerShare":              "eps_diluted",
    "Depreciation":                             "depreciation",
    "FinanceCosts":                             "finance_costs",
    "TotalEquity":                              "total_equity",
    "TotalAssets":                              "total_assets",
    "TotalDebt":                                "total_debt",
}

# ============================================================================
# SHUTDOWN
# ============================================================================
_shutdown = False

def _signal_handler(sig, frame):
    global _shutdown
    logger.info("Shutdown signal received...")
    _shutdown = True

if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

# ============================================================================
# PROGRESS
# ============================================================================
def load_progress() -> dict:
    if PROGRESS_FILE.exists():
        try:
            with open(PROGRESS_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"done": [], "stats": {"symbols": 0, "rows": 0, "errors": 0}}

def save_progress(prog: dict):
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROGRESS_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(prog, f, indent=2, default=str)
    tmp.replace(PROGRESS_FILE)

# ============================================================================
# NSE SESSION
# ============================================================================
_nse_session = None

def get_nse_session() -> requests.Session:
    global _nse_session
    if _nse_session is None:
        _nse_session = requests.Session()
        _nse_session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122",
            "Accept": "application/json",
            "Referer": "https://www.nseindia.com/",
        })
    return _nse_session

# ============================================================================
# NSE SYMBOL LIST
# ============================================================================
def get_nse_symbols() -> list:
    try:
        from nselib import capital_market
        df = capital_market.equity_list()
        return df["SYMBOL"].str.strip().tolist()
    except Exception as e:
        logger.error(f"Failed to load symbols: {e}")
        return []

# ============================================================================
# FETCH NSE EARNINGS CALENDAR (filing metadata)
# ============================================================================
def fetch_nse_earnings_calendar(symbol: str) -> list:
    """
    Fetch list of quarterly earnings filings for a symbol from NSE.
    Returns list of filing metadata dicts (no actual P&L numbers).
    Goes back to ~2005 for most companies.
    """
    sess = get_nse_session()
    url = NSE_RESULTS_URL.format(symbol=symbol)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            time.sleep(REQUEST_DELAY)
            r = sess.get(url, timeout=20)
            if r.status_code == 200:
                data = r.json()
                return data if isinstance(data, list) else []
            elif r.status_code == 404:
                return []
        except Exception as e:
            if attempt < MAX_RETRIES:
                time.sleep(attempt * 3)

    return []

# ============================================================================
# FETCH XBRL FINANCIALS
# ============================================================================
def fetch_xbrl_financials(xbrl_url: str) -> dict:
    """
    Fetch and parse an XBRL filing to extract key financial figures.
    Returns dict of {field_name: value_in_rupees}
    """
    if not xbrl_url or xbrl_url.endswith('-'):
        return {}

    sess = get_nse_session()
    try:
        time.sleep(0.3)
        r = sess.get(xbrl_url, timeout=20)
        if r.status_code != 200 or not r.text.strip().startswith('<?xml'):
            return {}

        root = ET.fromstring(r.text)
        result = {}

        for elem in root.iter():
            tag = elem.tag.split('}')[-1] if '}' in elem.tag else elem.tag
            if tag in XBRL_TAGS and elem.text:
                try:
                    result[XBRL_TAGS[tag]] = float(elem.text.strip().replace(',', ''))
                except ValueError:
                    pass

        return result

    except Exception as e:
        logger.debug(f"XBRL parse error {xbrl_url}: {e}")
        return {}

# ============================================================================
# FETCH YFINANCE FUNDAMENTALS
# ============================================================================
def fetch_yfinance_fundamentals(symbol: str) -> dict:
    """
    Fetch balance sheet, valuation, shareholding from yfinance.
    Returns dict with quarterly_income, quarterly_balance, major_holders, info.
    """
    yf_symbol = f"{symbol}.NS"
    try:
        tk = yf.Ticker(yf_symbol)

        result = {}

        # Quarterly income statement
        qi = tk.quarterly_income_stmt
        if qi is not None and not qi.empty:
            qi_rows = {}
            for col in qi.columns:
                col_data = {}
                for idx in qi.index:
                    val = qi.loc[idx, col]
                    if pd.notna(val):
                        col_data[str(idx)] = float(val)
                if col_data:
                    qi_rows[str(col.date() if hasattr(col, 'date') else col)] = col_data
            result["quarterly_income"] = qi_rows

        # Quarterly balance sheet
        qb = tk.quarterly_balance_sheet
        if qb is not None and not qb.empty:
            qb_rows = {}
            for col in qb.columns:
                col_data = {}
                for idx in qb.index:
                    val = qb.loc[idx, col]
                    if pd.notna(val):
                        col_data[str(idx)] = float(val)
                if col_data:
                    qb_rows[str(col.date() if hasattr(col, 'date') else col)] = col_data
            result["quarterly_balance"] = qb_rows

        # Major holders
        mh = tk.major_holders
        if mh is not None and not mh.empty:
            result["major_holders"] = mh.to_dict()

        # Key info metrics
        info = tk.info or {}
        info_keys = [
            'trailingPE', 'forwardPE', 'priceToBook', 'returnOnEquity',
            'returnOnAssets', 'debtToEquity', 'currentRatio', 'revenueGrowth',
            'earningsGrowth', 'trailingEps', 'forwardEps', 'bookValue',
            'dividendYield', 'payoutRatio', 'marketCap', 'enterpriseValue',
            'ebitda', 'totalRevenue', 'netIncomeToCommon', 'totalDebt',
            'totalCash', 'freeCashflow', 'operatingCashflow', 'beta',
            'profitMargins', 'grossMargins', 'operatingMargins', 'sector', 'industry',
        ]
        result["info"] = {k: info.get(k) for k in info_keys if info.get(k) is not None}

        return result

    except Exception as e:
        logger.warning(f"yfinance error for {symbol}: {e}")
        return {}

# ============================================================================
# BUILD EARNINGS CALENDAR DATAFRAME
# ============================================================================
def build_earnings_calendar(symbol: str, filings: list) -> pd.DataFrame:
    """
    Convert NSE filing metadata list to a clean DataFrame.
    """
    rows = []
    for f in filings:
        xbrl_data = {}

        # Fetch actual numbers from XBRL if available
        xbrl_url = f.get("xbrl", "")
        if xbrl_url and not xbrl_url.endswith('-'):
            xbrl_data = fetch_xbrl_financials(xbrl_url)

        row = {
            "symbol": symbol,
            "from_date": f.get("fromDate"),
            "to_date": f.get("toDate"),
            "period": f.get("relatingTo"),
            "financial_year": f.get("financialYear"),
            "filing_date": f.get("filingDate"),
            "consolidated": f.get("consolidated"),
            "audited": f.get("audited"),
            "ind_as": f.get("indAs"),
            **xbrl_data,  # Revenue, PAT, EPS etc from XBRL
        }
        rows.append(row)

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["from_date"] = pd.to_datetime(df["from_date"], format="%d-%b-%Y", errors="coerce")
    df["to_date"] = pd.to_datetime(df["to_date"], format="%d-%b-%Y", errors="coerce")
    df["filing_date"] = pd.to_datetime(df["filing_date"], format="%d-%b-%Y %H:%M", errors="coerce")
    return df

# ============================================================================
# SAVE
# ============================================================================
def save_parquet(df: pd.DataFrame, filename: str, dedup_cols: list):
    if df.empty:
        return 0
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    out_path = DATA_ROOT / filename
    if out_path.exists():
        try:
            existing = pd.read_parquet(out_path)
            combined = pd.concat([existing, df], ignore_index=True)
            combined = combined.drop_duplicates(subset=dedup_cols, keep="last")
            combined.to_parquet(out_path, index=False)
        except Exception:
            df.to_parquet(out_path, index=False)
    else:
        df.to_parquet(out_path, index=False)
    return len(df)

def save_json(data: dict, symbol: str):
    """Save yfinance rich data as JSON per symbol."""
    out_dir = DATA_ROOT / "yfinance"
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / f"{symbol}.json", "w") as f:
        json.dump(data, f, indent=2, default=str)

# ============================================================================
# PROCESS ONE SYMBOL
# ============================================================================
def process_symbol(symbol: str) -> dict:
    """Fetch and save all fundamental data for one symbol."""
    result = {"symbol": symbol, "nse_rows": 0, "yf_ok": False, "errors": 0}

    # 1. NSE Earnings Calendar + XBRL financials
    filings = fetch_nse_earnings_calendar(symbol)
    if filings:
        df = build_earnings_calendar(symbol, filings)
        if not df.empty:
            n = save_parquet(df, "earnings_calendar.parquet",
                             dedup_cols=["symbol", "from_date", "consolidated"])
            result["nse_rows"] = n

    # 2. yfinance balance sheet + info + shareholding
    yf_data = fetch_yfinance_fundamentals(symbol)
    if yf_data:
        save_json(yf_data, symbol)
        result["yf_ok"] = True

        # Also flatten info into a parquet row
        info = yf_data.get("info", {})
        if info:
            info_row = {"symbol": symbol, "snapshot_date": date.today(), **info}
            info_df = pd.DataFrame([info_row])
            save_parquet(info_df, "valuation_snapshot.parquet",
                         dedup_cols=["symbol", "snapshot_date"])

    return result

# ============================================================================
# BACKFILL
# ============================================================================
def run_backfill(symbol_filter: str = None):
    symbols = get_nse_symbols()
    if not symbols:
        logger.error("No symbols")
        return

    if symbol_filter:
        symbols = [s for s in symbols if s.upper() == symbol_filter.upper()]

    prog = load_progress()
    done_set = set(prog.get("done", []))
    stats = prog.get("stats", {"symbols": 0, "rows": 0, "errors": 0})

    remaining = [s for s in symbols if s not in done_set]
    logger.info(f"Fundamentals backfill: {len(remaining)} remaining of {len(symbols)}")

    for i, symbol in enumerate(remaining):
        if _shutdown:
            break

        try:
            result = process_symbol(symbol)
            stats["symbols"] += 1
            stats["rows"] += result["nse_rows"]
            done_set.add(symbol)

            if (i + 1) % 20 == 0:
                logger.info(
                    f"[{i+1}/{len(remaining)}] {symbol}: "
                    f"nse_rows={result['nse_rows']} yf={result['yf_ok']} | "
                    f"total_rows={stats['rows']:,}"
                )
                prog["done"] = list(done_set)
                prog["stats"] = stats
                prog["updated_at"] = datetime.utcnow().isoformat()
                save_progress(prog)

        except Exception as e:
            logger.error(f"Error processing {symbol}: {e}")
            stats["errors"] += 1

        time.sleep(REQUEST_DELAY)

    prog["done"] = list(done_set)
    prog["stats"] = stats
    prog["updated_at"] = datetime.utcnow().isoformat()
    save_progress(prog)

    logger.info(f"Backfill complete: {stats}")
    return stats

# ============================================================================
# DAILY
# ============================================================================
def run_daily():
    """Refresh latest quarter's results for all symbols (runs after market close)."""
    logger.info("Running daily fundamentals refresh...")
    symbols = get_nse_symbols()
    total = 0
    for sym in symbols:
        if _shutdown:
            break
        filings = fetch_nse_earnings_calendar(sym)
        if filings:
            # Only process the most recent filing
            latest = sorted(filings, key=lambda x: x.get("filingDate", ""), reverse=True)[:2]
            df = build_earnings_calendar(sym, latest)
            if not df.empty:
                n = save_parquet(df, "earnings_calendar.parquet",
                                 dedup_cols=["symbol", "from_date", "consolidated"])
                total += n
        time.sleep(REQUEST_DELAY)
    logger.info(f"Daily fundamentals update: {total} rows")
    return {"rows": total}

# ============================================================================
# MAIN
# ============================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fundamentals Ingestion")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--backfill", action="store_true")
    group.add_argument("--daily", action="store_true")
    group.add_argument("--symbol", type=str)
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
