#!/usr/bin/env python3
"""
Zerodha Intraday Data Ingestion
================================
Downloads historical candle data from Zerodha Kite Connect API.
Supports all exchanges, intervals, and instrument types.
Saves to Parquet files, optionally loads to TimescaleDB.

Usage:
    python -m app.ingest.zerodha_intraday_ingest --phase 1
    python -m app.ingest.zerodha_intraday_ingest --phase 2
    python -m app.ingest.zerodha_intraday_ingest --phase all
"""

import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import List, Dict, Optional, Tuple

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '..', '.env'))

logger = logging.getLogger(__name__)

# ============================================================================
# CONFIG
# ============================================================================
DATA_ROOT = Path("/media/vboxuser/test/NSE_Data/zerodha_intraday")
PROGRESS_DIR = Path("/media/vboxuser/test/NSE_Data")

# Rate limit: 3 req/sec for historical candles
RATE_LIMIT_DELAY = 0.35  # seconds between requests (slightly conservative)

# Max date range per request by interval
INTERVAL_MAX_DAYS = {
    'minute': 60,
    '3minute': 100,
    '5minute': 100,
    '10minute': 100,
    '15minute': 200,
    '30minute': 200,
    '60minute': 400,
    'day': 2000,
}

# How far back to try (approximate start dates)
INTERVAL_START_YEAR = {
    'minute': 2015,
    '3minute': 2015,
    '5minute': 2015,
    '10minute': 2015,
    '15minute': 2015,
    '30minute': 2015,
    '60minute': 2015,
    'day': 2010,
}

# Intervals to fetch per phase
PHASE_INTERVALS = {
    1: ['minute', '5minute', '15minute', '60minute', 'day'],
    2: ['minute', '5minute', '15minute', '60minute', 'day'],
    3: ['day', 'minute'],  # daily first (quick), then 1-min
    4: ['day', 'minute'],
    5: ['day', 'minute'],
    6: ['minute', '5minute', '15minute', '60minute', 'day'],
    7: ['minute', 'day'],
}

# Graceful shutdown
_shutdown = False

def _signal_handler(sig, frame):
    global _shutdown
    logger.info("Shutdown signal received, finishing current symbol...")
    _shutdown = True

if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)


# ============================================================================
# KITE CLIENT
# ============================================================================
_kite = None
_kite_init_time = None

def get_kite():
    """Get or create authenticated KiteConnect client. Re-auths if token is >20 hours old."""
    global _kite, _kite_init_time
    
    now = time.time()
    if _kite and _kite_init_time and (now - _kite_init_time) < 72000:  # 20 hours
        return _kite
    
    from kiteconnect import KiteConnect
    api_key = os.getenv('ZERODHA_API_KEY')
    
    # Try auto-auth first
    try:
        from app.auth.zerodha_auto_auth import get_access_token
        access_token = get_access_token()
        logger.info("Auto-auth successful")
    except Exception as e:
        logger.warning(f"Auto-auth failed ({e}), trying saved token...")
        token_file = os.path.join(os.path.dirname(__file__), '..', 'zerodha_access_token.txt')
        if os.path.exists(token_file):
            access_token = open(token_file).read().strip()
        else:
            raise RuntimeError("No Zerodha access token available")
    
    _kite = KiteConnect(api_key=api_key)
    _kite.set_access_token(access_token)
    _kite_init_time = now
    
    # Save token for other processes
    token_file = os.path.join(os.path.dirname(__file__), '..', 'zerodha_access_token.txt')
    with open(token_file, 'w') as f:
        f.write(access_token)
    
    return _kite


# ============================================================================
# PROGRESS TRACKING
# ============================================================================
def progress_file(phase: int) -> Path:
    return PROGRESS_DIR / f"zerodha_progress_phase{phase}.json"

def load_progress(phase: int) -> dict:
    pf = progress_file(phase)
    if pf.exists():
        with open(pf) as f:
            return json.load(f)
    return {
        "phase": phase,
        "started_at": datetime.now().isoformat(),
        "symbols": {},  # symbol -> {interval -> last_completed_date}
        "stats": {"total_candles": 0, "total_api_calls": 0, "errors": 0},
    }

def save_progress(phase: int, prog: dict):
    prog["updated_at"] = datetime.now().isoformat()
    pf = progress_file(phase)
    with open(pf, 'w') as f:
        json.dump(prog, f, indent=2, default=str)


# ============================================================================
# DATA FETCHING
# ============================================================================
def fetch_historical(
    instrument_token: int,
    from_date: datetime,
    to_date: datetime,
    interval: str,
    continuous: bool = False,
    oi: bool = False,
) -> List[dict]:
    """Fetch historical candles with rate limiting and retry."""
    kite = get_kite()
    
    for attempt in range(3):
        try:
            time.sleep(RATE_LIMIT_DELAY)
            data = kite.historical_data(
                instrument_token, from_date, to_date, interval,
                continuous=continuous, oi=oi,
            )
            return data
        except Exception as e:
            err_str = str(e)
            if '429' in err_str or 'Too many' in err_str.lower():
                wait = (attempt + 1) * 5
                logger.warning(f"Rate limited, waiting {wait}s...")
                time.sleep(wait)
            elif '403' in err_str or 'TokenException' in err_str or 'access_token' in err_str.lower() or 'api_key' in err_str.lower() or 'nsufficient permission' in err_str:
                logger.warning(f"Token expired/invalid, re-authenticating... ({err_str[:80]})")
                global _kite, _kite_init_time
                _kite = None
                _kite_init_time = None
                kite = get_kite()
            elif 'NetworkException' in err_str or 'ConnectionError' in err_str:
                wait = (attempt + 1) * 10
                logger.warning(f"Network error, waiting {wait}s: {e}")
                time.sleep(wait)
            else:
                if attempt == 2:
                    raise
                time.sleep(2)
    return []


def fetch_symbol_interval(
    instrument_token: int,
    symbol: str,
    interval: str,
    exchange: str,
    subdir: str,
    continuous: bool = False,
    oi: bool = False,
    prog: dict = None,
    phase: int = 1,
) -> Tuple[int, int]:
    """
    Fetch all available data for a symbol+interval, saving to parquet.
    Returns (candles_fetched, api_calls_made).
    """
    global _shutdown
    
    # Check progress
    sym_key = f"{exchange}:{symbol}"
    sym_prog = prog.get("symbols", {}).get(sym_key, {})
    last_done = sym_prog.get(interval)
    
    max_days = INTERVAL_MAX_DAYS[interval]
    start_year = INTERVAL_START_YEAR[interval]
    start_date = datetime(start_year, 1, 1)
    end_date = datetime.now()
    
    if last_done:
        # Resume from where we left off
        last_dt = datetime.fromisoformat(last_done)
        if last_dt.date() >= (end_date - timedelta(days=1)).date():
            return 0, 0  # Already complete
        start_date = last_dt + timedelta(days=1)
    
    # Create output directory
    out_dir = DATA_ROOT / exchange / subdir / interval / symbol
    out_dir.mkdir(parents=True, exist_ok=True)
    
    total_candles = 0
    total_calls = 0
    current = start_date
    all_data = []
    current_month = None
    empty_chunks = 0
    
    while current < end_date:
        if _shutdown:
            break
        
        chunk_end = min(current + timedelta(days=max_days), end_date)
        
        try:
            data = fetch_historical(
                instrument_token, current, chunk_end, interval,
                continuous=continuous, oi=oi,
            )
            total_calls += 1
            
            if data:
                empty_chunks = 0
                for candle in data:
                    candle['date'] = candle['date'].replace(tzinfo=None) if candle['date'].tzinfo else candle['date']
                all_data.extend(data)
                total_candles += len(data)
                
                # Write parquet monthly for intraday, yearly for daily
                if interval == 'day':
                    write_period = 'year'
                else:
                    write_period = 'month'
                
                # Check if we crossed a period boundary
                last_candle_date = data[-1]['date']
                if write_period == 'month':
                    new_month = (last_candle_date.year, last_candle_date.month)
                else:
                    new_month = last_candle_date.year
                
                if current_month is not None and new_month != current_month and all_data:
                    _flush_to_parquet(all_data, out_dir, current_month, write_period)
                    all_data = []
                current_month = new_month
            else:
                empty_chunks += 1
                # Skip ahead faster if no data for extended periods
                if empty_chunks >= 3 and interval in ('minute', '3minute', '5minute'):
                    # Jump ahead 6 months
                    current = chunk_end + timedelta(days=120)
                    empty_chunks = 0
                    continue
            
        except Exception as e:
            logger.error(f"Error fetching {symbol} {interval} {current.date()}-{chunk_end.date()}: {e}")
            prog["stats"]["errors"] = prog["stats"].get("errors", 0) + 1
        
        current = chunk_end + timedelta(days=1)
    
    # Flush remaining data
    if all_data:
        _flush_to_parquet(all_data, out_dir, current_month, 'month' if interval != 'day' else 'year')
    
    # Update progress
    if not _shutdown and total_calls > 0:
        if sym_key not in prog["symbols"]:
            prog["symbols"][sym_key] = {}
        prog["symbols"][sym_key][interval] = end_date.isoformat()
    
    prog["stats"]["total_candles"] = prog["stats"].get("total_candles", 0) + total_candles
    prog["stats"]["total_api_calls"] = prog["stats"].get("total_api_calls", 0) + total_calls
    
    return total_candles, total_calls


def _flush_to_parquet(data: list, out_dir: Path, period_key, period_type: str):
    """Write candle data to a parquet file."""
    if not data:
        return
    
    df = pd.DataFrame(data)
    
    if period_type == 'month':
        year, month = period_key
        filename = f"{year}-{month:02d}.parquet"
    else:
        filename = f"{period_key}.parquet"
    
    filepath = out_dir / filename
    
    # If file exists, merge (upsert by date)
    if filepath.exists():
        try:
            existing = pd.read_parquet(filepath)
            df = pd.concat([existing, df]).drop_duplicates(subset=['date']).sort_values('date')
        except Exception:
            pass
    
    df.to_parquet(filepath, index=False, engine='pyarrow')


# ============================================================================
# PHASE DEFINITIONS
# ============================================================================
def get_phase1_instruments() -> List[dict]:
    """NSE Indices with 1-min data."""
    kite = get_kite()
    instruments = kite.instruments('NSE')
    indices = [i for i in instruments if i['segment'] == 'INDICES']
    
    # Test which ones have 1-min data (check recent date)
    working = []
    test_date = datetime.now() - timedelta(days=7)
    # Find a weekday
    while test_date.weekday() >= 5:
        test_date -= timedelta(days=1)
    test_end = test_date + timedelta(days=1)
    
    for idx in indices:
        try:
            data = fetch_historical(idx['instrument_token'], test_date, test_end, 'minute')
            if data:
                working.append(idx)
        except:
            pass
    
    logger.info(f"Phase 1: Found {len(working)} indices with 1-min data out of {len(indices)}")
    return working


def get_phase2_instruments() -> List[dict]:
    """NSE FnO stocks (211 underlyings)."""
    kite = get_kite()
    nfo = kite.instruments('NFO')
    nse = kite.instruments('NSE')
    
    fno_names = set(i['name'] for i in nfo)
    fno_stocks = [i for i in nse if i['segment'] == 'NSE' and i['tradingsymbol'] in fno_names]
    
    # Some names don't match tradingsymbol exactly, also try name field
    nse_by_name = {i['name']: i for i in nse if i['segment'] == 'NSE'}
    for name in fno_names:
        if name in nse_by_name and nse_by_name[name] not in fno_stocks:
            fno_stocks.append(nse_by_name[name])
    
    # Deduplicate
    seen = set()
    unique = []
    for s in fno_stocks:
        if s['instrument_token'] not in seen:
            seen.add(s['instrument_token'])
            unique.append(s)
    
    logger.info(f"Phase 2: Found {len(unique)} FnO stocks")
    return unique


def get_phase3_instruments() -> List[dict]:
    """Remaining NSE equities (not in Phase 2)."""
    kite = get_kite()
    nse = kite.instruments('NSE')
    nfo = kite.instruments('NFO')
    
    fno_names = set(i['name'] for i in nfo)
    equities = [i for i in nse if i['segment'] == 'NSE' and i['tradingsymbol'] not in fno_names and i['name'] not in fno_names]
    
    logger.info(f"Phase 3: Found {len(equities)} remaining NSE equities")
    return equities


def get_phase4_instruments() -> List[dict]:
    """NFO futures — continuous daily + live contract intraday."""
    kite = get_kite()
    nfo = kite.instruments('NFO')
    futures = [i for i in nfo if i['instrument_type'] == 'FUT']
    logger.info(f"Phase 4: Found {len(futures)} live NFO futures")
    return futures


def get_phase5_instruments() -> Tuple[List[dict], List[dict]]:
    """BSE equities + indices."""
    kite = get_kite()
    bse = kite.instruments('BSE')
    equities = [i for i in bse if i['segment'] == 'BSE']
    indices = [i for i in bse if i['segment'] == 'INDICES']
    logger.info(f"Phase 5: Found {len(equities)} BSE equities + {len(indices)} indices")
    return equities, indices


def get_phase6_instruments() -> Tuple[List[dict], List[dict]]:
    """MCX commodities + indices."""
    kite = get_kite()
    mcx = kite.instruments('MCX')
    futures = [i for i in mcx if i['instrument_type'] == 'FUT']
    indices = [i for i in mcx if i['segment'] == 'INDICES']
    logger.info(f"Phase 6: Found {len(futures)} MCX futures + {len(indices)} indices")
    return futures, indices


def get_phase7_instruments() -> Tuple[List[dict], List[dict]]:
    """CDS + BFO instruments."""
    kite = get_kite()
    cds_futs = [i for i in kite.instruments('CDS') if i['instrument_type'] == 'FUT']
    bfo_futs = [i for i in kite.instruments('BFO') if i['instrument_type'] == 'FUT']
    logger.info(f"Phase 7: Found {len(cds_futs)} CDS futures + {len(bfo_futs)} BFO futures")
    return cds_futs, bfo_futs


# ============================================================================
# PHASE RUNNERS
# ============================================================================
def run_phase1():
    """Fetch all NSE indices, all intervals."""
    global _shutdown
    phase = 1
    prog = load_progress(phase)
    instruments = get_phase1_instruments()
    
    total_symbols = len(instruments)
    intervals = PHASE_INTERVALS[phase]
    
    for i, inst in enumerate(instruments):
        if _shutdown:
            break
        
        symbol = inst['tradingsymbol']
        token = inst['instrument_token']
        
        for interval in intervals:
            if _shutdown:
                break
            
            candles, calls = fetch_symbol_interval(
                token, symbol, interval, 'NSE', 'indices',
                prog=prog, phase=phase,
            )
            
            if candles > 0:
                logger.info(
                    f"[Phase 1] {i+1}/{total_symbols} {symbol} {interval}: "
                    f"{candles:,} candles, {calls} calls"
                )
        
        # Save progress after each symbol
        save_progress(phase, prog)
    
    logger.info(f"[Phase 1] DONE: {prog['stats']}")
    save_progress(phase, prog)
    return prog


def run_phase2():
    """Fetch all FnO stocks, all intervals."""
    global _shutdown
    phase = 2
    prog = load_progress(phase)
    instruments = get_phase2_instruments()
    
    total_symbols = len(instruments)
    intervals = PHASE_INTERVALS[phase]
    
    for i, inst in enumerate(instruments):
        if _shutdown:
            break
        
        symbol = inst['tradingsymbol']
        token = inst['instrument_token']
        
        for interval in intervals:
            if _shutdown:
                break
            
            candles, calls = fetch_symbol_interval(
                token, symbol, interval, 'NSE', 'equities',
                prog=prog, phase=phase,
            )
            
            if candles > 0:
                logger.info(
                    f"[Phase 2] {i+1}/{total_symbols} {symbol} {interval}: "
                    f"{candles:,} candles, {calls} calls"
                )
        
        save_progress(phase, prog)
    
    logger.info(f"[Phase 2] DONE: {prog['stats']}")
    save_progress(phase, prog)
    return prog


def run_phase3():
    """Fetch remaining NSE equities."""
    global _shutdown
    phase = 3
    prog = load_progress(phase)
    instruments = get_phase3_instruments()
    
    total_symbols = len(instruments)
    intervals = PHASE_INTERVALS[phase]
    
    for i, inst in enumerate(instruments):
        if _shutdown:
            break
        
        symbol = inst['tradingsymbol']
        token = inst['instrument_token']
        
        for interval in intervals:
            if _shutdown:
                break
            
            candles, calls = fetch_symbol_interval(
                token, symbol, interval, 'NSE', 'equities',
                prog=prog, phase=phase,
            )
            
            if candles > 0:
                logger.info(
                    f"[Phase 3] {i+1}/{total_symbols} {symbol} {interval}: "
                    f"{candles:,} candles, {calls} calls"
                )
        
        save_progress(phase, prog)
        
        # Log every 100 symbols
        if (i + 1) % 100 == 0:
            logger.info(f"[Phase 3] Progress: {i+1}/{total_symbols}, stats: {prog['stats']}")
    
    logger.info(f"[Phase 3] DONE: {prog['stats']}")
    save_progress(phase, prog)
    return prog


def run_phase4():
    """NFO futures — continuous daily + live contract intraday."""
    global _shutdown
    phase = 4
    prog = load_progress(phase)
    futures = get_phase4_instruments()
    
    # Group by underlying for continuous daily
    underlyings = {}
    for f in futures:
        name = f['name']
        if name not in underlyings:
            underlyings[name] = f  # Use first (nearest expiry) for continuous
    
    # Step 1: Continuous daily for each underlying
    logger.info(f"[Phase 4] Fetching continuous daily for {len(underlyings)} underlyings")
    for i, (name, inst) in enumerate(underlyings.items()):
        if _shutdown:
            break
        
        candles, calls = fetch_symbol_interval(
            inst['instrument_token'], name, 'day', 'NFO', 'continuous_daily',
            continuous=True, oi=True, prog=prog, phase=phase,
        )
        if candles > 0:
            logger.info(f"[Phase 4] Continuous {name} day: {candles:,} candles")
        save_progress(phase, prog)
    
    # Step 2: Live contract intraday
    logger.info(f"[Phase 4] Fetching intraday for {len(futures)} live futures")
    for i, inst in enumerate(futures):
        if _shutdown:
            break
        
        symbol = inst['tradingsymbol']
        token = inst['instrument_token']
        
        for interval in ['minute', 'day']:
            if _shutdown:
                break
            candles, calls = fetch_symbol_interval(
                token, symbol, interval, 'NFO', 'futures',
                oi=True, prog=prog, phase=phase,
            )
            if candles > 0:
                logger.info(f"[Phase 4] {symbol} {interval}: {candles:,} candles")
        
        save_progress(phase, prog)
    
    logger.info(f"[Phase 4] DONE: {prog['stats']}")
    save_progress(phase, prog)
    return prog


def run_phase5():
    """BSE equities + indices."""
    global _shutdown
    phase = 5
    prog = load_progress(phase)
    equities, indices = get_phase5_instruments()
    
    # Indices first
    logger.info(f"[Phase 5] Fetching {len(indices)} BSE indices")
    for i, inst in enumerate(indices):
        if _shutdown:
            break
        symbol = inst['tradingsymbol']
        for interval in ['minute', 'day']:
            if _shutdown:
                break
            candles, calls = fetch_symbol_interval(
                inst['instrument_token'], symbol, interval, 'BSE', 'indices',
                prog=prog, phase=phase,
            )
            if candles > 0:
                logger.info(f"[Phase 5] BSE Index {symbol} {interval}: {candles:,} candles")
        save_progress(phase, prog)
    
    # Equities
    total = len(equities)
    logger.info(f"[Phase 5] Fetching {total} BSE equities")
    for i, inst in enumerate(equities):
        if _shutdown:
            break
        symbol = inst['tradingsymbol']
        for interval in ['day', 'minute']:
            if _shutdown:
                break
            candles, calls = fetch_symbol_interval(
                inst['instrument_token'], symbol, interval, 'BSE', 'equities',
                prog=prog, phase=phase,
            )
            if candles > 0:
                logger.info(f"[Phase 5] {i+1}/{total} {symbol} {interval}: {candles:,} candles")
        save_progress(phase, prog)
        if (i + 1) % 100 == 0:
            logger.info(f"[Phase 5] Progress: {i+1}/{total}, stats: {prog['stats']}")
    
    logger.info(f"[Phase 5] DONE: {prog['stats']}")
    save_progress(phase, prog)
    return prog


def run_phase6():
    """MCX commodities + indices."""
    global _shutdown
    phase = 6
    prog = load_progress(phase)
    futures, indices = get_phase6_instruments()
    
    # Indices
    for inst in indices:
        if _shutdown:
            break
        symbol = inst['tradingsymbol']
        for interval in PHASE_INTERVALS[phase]:
            if _shutdown:
                break
            candles, calls = fetch_symbol_interval(
                inst['instrument_token'], symbol, interval, 'MCX', 'indices',
                prog=prog, phase=phase,
            )
            if candles > 0:
                logger.info(f"[Phase 6] MCX Index {symbol} {interval}: {candles:,} candles")
        save_progress(phase, prog)
    
    # Continuous daily for unique commodities
    underlyings = {}
    for f in futures:
        if f['name'] not in underlyings:
            underlyings[f['name']] = f
    
    for name, inst in underlyings.items():
        if _shutdown:
            break
        candles, calls = fetch_symbol_interval(
            inst['instrument_token'], name, 'day', 'MCX', 'continuous_daily',
            continuous=True, oi=True, prog=prog, phase=phase,
        )
        if candles > 0:
            logger.info(f"[Phase 6] MCX Continuous {name} day: {candles:,} candles")
        save_progress(phase, prog)
    
    # Live futures intraday
    for inst in futures:
        if _shutdown:
            break
        symbol = inst['tradingsymbol']
        for interval in ['minute', 'day']:
            if _shutdown:
                break
            candles, calls = fetch_symbol_interval(
                inst['instrument_token'], symbol, interval, 'MCX', 'futures',
                oi=True, prog=prog, phase=phase,
            )
            if candles > 0:
                logger.info(f"[Phase 6] {symbol} {interval}: {candles:,} candles")
        save_progress(phase, prog)
    
    logger.info(f"[Phase 6] DONE: {prog['stats']}")
    save_progress(phase, prog)
    return prog


def run_phase7():
    """CDS + BFO."""
    global _shutdown
    phase = 7
    prog = load_progress(phase)
    cds_futs, bfo_futs = get_phase7_instruments()
    
    for label, futs, exchange in [('CDS', cds_futs, 'CDS'), ('BFO', bfo_futs, 'BFO')]:
        # Continuous daily
        underlyings = {}
        for f in futs:
            if f['name'] not in underlyings:
                underlyings[f['name']] = f
        
        for name, inst in underlyings.items():
            if _shutdown:
                break
            candles, calls = fetch_symbol_interval(
                inst['instrument_token'], name, 'day', exchange, 'continuous_daily',
                continuous=True, oi=True, prog=prog, phase=phase,
            )
            if candles > 0:
                logger.info(f"[Phase 7] {exchange} Continuous {name} day: {candles:,} candles")
            save_progress(phase, prog)
        
        # Live contract intraday
        for inst in futs:
            if _shutdown:
                break
            symbol = inst['tradingsymbol']
            for interval in ['minute', 'day']:
                if _shutdown:
                    break
                candles, calls = fetch_symbol_interval(
                    inst['instrument_token'], symbol, interval, exchange, 'futures',
                    oi=True, prog=prog, phase=phase,
                )
                if candles > 0:
                    logger.info(f"[Phase 7] {symbol} {interval}: {candles:,} candles")
            save_progress(phase, prog)
    
    logger.info(f"[Phase 7] DONE: {prog['stats']}")
    save_progress(phase, prog)
    return prog


PHASE_RUNNERS = {
    1: run_phase1,
    2: run_phase2,
    3: run_phase3,
    4: run_phase4,
    5: run_phase5,
    6: run_phase6,
    7: run_phase7,
}


def run_all_phases():
    """Run all phases sequentially."""
    for phase_num in sorted(PHASE_RUNNERS.keys()):
        if _shutdown:
            break
        logger.info(f"{'='*60}")
        logger.info(f"Starting Phase {phase_num}")
        logger.info(f"{'='*60}")
        PHASE_RUNNERS[phase_num]()


# ============================================================================
# MAIN
# ============================================================================
if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Zerodha Intraday Data Ingestion')
    parser.add_argument('--phase', type=str, default='all', help='Phase number (1-7) or "all"')
    args = parser.parse_args()
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(PROGRESS_DIR / 'zerodha_ingest.log'),
        ]
    )
    
    if args.phase == 'all':
        run_all_phases()
    else:
        phase = int(args.phase)
        if phase in PHASE_RUNNERS:
            PHASE_RUNNERS[phase]()
        else:
            print(f"Unknown phase: {phase}. Valid: 1-7 or 'all'")
            sys.exit(1)
