#!/usr/bin/env python3
"""
Options Chain Snapshot Ingestion
==================================
Captures full options chain (all strikes, all near expiries) with calculated
Greeks and IV for NIFTY, BANKNIFTY, and top F&O stocks.

Snapshots are taken at market close (15:29 IST Mon-Fri) and optionally
at intraday intervals (10:30, 12:00, 14:00).

Greeks calculated via Black-Scholes-Merton (scipy) since Zerodha doesn't
provide them via API.

Usage:
    python -m app.ingest.options_chain_snapshot --snapshot       # take snapshot now
    python -m app.ingest.options_chain_snapshot --backfill-oi    # backfill OI from bhavcopy
    python -m app.ingest.options_chain_snapshot --test           # test with small universe
"""

import argparse
import json
import logging
import math
import time
import warnings
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from scipy.stats import norm

warnings.filterwarnings("ignore")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ============================================================================
# CONFIG
# ============================================================================
DATA_ROOT = Path("/media/vboxuser/test/NSE_Data/options_snapshots")

# Risk-free rate (RBI repo rate ~6.5%)
RISK_FREE_RATE = 0.065

# Universe: indices + top F&O stocks
INDEX_UNIVERSE = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"]

TOP_FO_STOCKS = [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK",
    "HINDUNILVR", "BAJFINANCE", "SBIN", "BHARTIARTL", "WIPRO",
    "AXISBANK", "KOTAKBANK", "LT", "TATAMOTORS", "MARUTI",
    "SUNPHARMA", "ADANIENT", "ONGC", "POWERGRID", "NTPC",
    "TITAN", "HCLTECH", "ULTRACEMCO", "ASIANPAINT", "INDUSINDBK",
    "BAJAJFINSV", "TATASTEEL", "HINDALCO", "M&M", "NESTLEIND",
]

# How many expiries to capture (nearest N)
MAX_EXPIRIES = 3

# Strike range: ATM ± N strikes
STRIKE_RANGE = 15  # capture ATM ± 15 strikes

# Zerodha API rate limit
QUOTE_BATCH_SIZE = 250  # max instruments per quote call
QUOTE_DELAY = 0.35       # seconds between batches


# ============================================================================
# BLACK-SCHOLES-MERTON GREEKS
# ============================================================================
def bsm_greeks(S: float, K: float, T: float, r: float, sigma: float,
               option_type: str = 'c') -> dict:
    """
    Calculate BSM theoretical price + Greeks.
    Returns dict with: theoretical_price, delta, gamma, theta, vega, rho
    """
    if T <= 1e-6 or sigma <= 1e-6 or S <= 0 or K <= 0:
        return {}
    try:
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)

        nd1 = norm.cdf(d1)
        nd2 = norm.cdf(d2)
        npd1 = norm.pdf(d1)

        if option_type == 'c':
            price = S * nd1 - K * math.exp(-r * T) * nd2
            delta = nd1
            rho_ = K * T * math.exp(-r * T) * nd2 / 100
        else:
            price = K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
            delta = nd1 - 1
            rho_ = -K * T * math.exp(-r * T) * norm.cdf(-d2) / 100

        gamma = npd1 / (S * sigma * math.sqrt(T))
        vega = S * npd1 * math.sqrt(T) / 100       # per 1% σ change
        theta = (-(S * npd1 * sigma) / (2 * math.sqrt(T))) / 365

        return {
            "theoretical_price": round(price, 4),
            "delta": round(delta, 6),
            "gamma": round(gamma, 8),
            "theta": round(theta, 6),
            "vega": round(vega, 6),
            "rho": round(rho_, 6),
        }
    except Exception:
        return {}


def calc_iv(market_price: float, S: float, K: float, T: float, r: float,
            option_type: str = 'c', tol: float = 1e-5, max_iter: int = 200) -> Optional[float]:
    """
    Newton-Raphson IV solver.
    Returns annualised IV as decimal (0.20 = 20%), or None if not solvable.
    """
    if T <= 1e-6 or market_price <= 0:
        return None

    intrinsic = max(0.0, (S - K) if option_type == 'c' else (K - S))
    if market_price < intrinsic * 0.99:
        return None  # price below intrinsic — no real solution

    sigma = 0.30  # initial guess
    for _ in range(max_iter):
        g = bsm_greeks(S, K, T, r, sigma, option_type)
        if not g:
            return None
        v = g["vega"] * 100  # full vega (not per 1%)
        if abs(v) < 1e-10:
            break
        diff = g["theoretical_price"] - market_price
        sigma -= diff / v
        sigma = max(1e-6, min(sigma, 20.0))  # clamp
        if abs(diff) < tol:
            break

    return round(sigma, 6) if 0 < sigma < 10 else None


# ============================================================================
# ZERODHA CLIENT
# ============================================================================
_kite = None
_kite_init_time = 0.0


def get_kite():
    global _kite, _kite_init_time
    now = time.time()
    if _kite and (now - _kite_init_time) < 72000:
        return _kite

    from kiteconnect import KiteConnect
    from app.auth.zerodha_auto_auth import get_access_token

    kite = KiteConnect(api_key="pv8jjbv19goiaj0m")
    token = get_access_token()
    kite.set_access_token(token)

    _kite = kite
    _kite_init_time = now
    logger.info("Zerodha auth OK")
    return kite


# ============================================================================
# BUILD OPTIONS UNIVERSE
# ============================================================================
def build_universe(kite, underlyings: list, max_expiries: int = MAX_EXPIRIES,
                   strike_range: int = STRIKE_RANGE) -> dict:
    """
    Returns {underlying: {'spot': price, 'options': [instruments], 'lot_size': n}}
    """
    logger.info("Loading NFO instrument list...")
    nfo = kite.instruments("NFO")
    nse = kite.instruments("NSE")
    bse_instruments = None

    # Build spot price map
    nse_map = {i["tradingsymbol"]: i for i in nse}
    spot_tokens = {}
    for underlying in underlyings:
        if underlying in nse_map:
            spot_tokens[underlying] = nse_map[underlying]["instrument_token"]
        elif underlying == "SENSEX":
            if bse_instruments is None:
                bse_instruments = kite.instruments("BSE")
            bse_map = {i["tradingsymbol"]: i for i in bse_instruments}
            if "SENSEX" in bse_map:
                spot_tokens["SENSEX"] = bse_map["SENSEX"]["instrument_token"]

    # Fetch spot prices in batch
    if spot_tokens:
        spot_quotes = kite.quote(list(spot_tokens.values()))
        spot_prices = {}
        for underlying, token in spot_tokens.items():
            q = spot_quotes.get(str(token)) or spot_quotes.get(token)
            if q:
                spot_prices[underlying] = q["last_price"]
    else:
        spot_prices = {}

    # For index options use NIFTY 50 token
    index_spot_map = {
        "NIFTY": "NIFTY 50",
        "BANKNIFTY": "NIFTY BANK",
        "FINNIFTY": "NIFTY FIN SERVICE",
        "MIDCPNIFTY": "NIFTY MID SELECT",
        "SENSEX": "SENSEX",
    }

    # Re-fetch index spots by name
    for idx, nse_name in index_spot_map.items():
        if idx in underlyings and idx not in spot_prices:
            match = next((i for i in nse if i["tradingsymbol"] == nse_name), None)
            if match:
                q = kite.quote([match["instrument_token"]])
                if q:
                    spot_prices[idx] = list(q.values())[0]["last_price"]

    universe = {}
    for underlying in underlyings:
        spot = spot_prices.get(underlying)
        if not spot:
            logger.warning(f"No spot price for {underlying}, skipping")
            continue

        # Filter options for this underlying
        opts = [i for i in nfo if i["name"] == underlying
                and i["instrument_type"] in ("CE", "PE")]

        if not opts:
            logger.debug(f"No options found for {underlying}")
            continue

        # Get nearest N expiries
        expiries = sorted(set(i["expiry"] for i in opts))[:max_expiries]

        # Filter by strikes near ATM
        lot_size = opts[0]["lot_size"] if opts else 1

        filtered = []
        for exp in expiries:
            exp_opts = [i for i in opts if i["expiry"] == exp]

            # Determine ATM strike
            strikes = sorted(set(i["strike"] for i in exp_opts))
            if not strikes:
                continue

            # Find tick (smallest strike gap)
            tick = min(strikes[i+1] - strikes[i] for i in range(len(strikes)-1)) if len(strikes) > 1 else 50
            atm = min(strikes, key=lambda x: abs(x - spot))

            # ATM ± strike_range
            target_strikes = {atm + i * tick for i in range(-strike_range, strike_range + 1)}
            selected = [i for i in exp_opts if i["strike"] in target_strikes]
            filtered.extend(selected)

        universe[underlying] = {
            "spot": spot,
            "options": filtered,
            "lot_size": lot_size,
            "expiries": [str(e) for e in expiries],
        }
        logger.info(f"{underlying}: spot={spot}, {len(filtered)} options across {len(expiries)} expiries")

    return universe


# ============================================================================
# FETCH QUOTES IN BATCHES
# ============================================================================
def fetch_quotes_batched(kite, instruments: list) -> dict:
    """Fetch quotes for all instruments in batches of QUOTE_BATCH_SIZE."""
    all_quotes = {}
    tokens = [i["instrument_token"] for i in instruments]

    for i in range(0, len(tokens), QUOTE_BATCH_SIZE):
        batch = tokens[i:i + QUOTE_BATCH_SIZE]
        try:
            quotes = kite.quote(batch)
            all_quotes.update(quotes)
        except Exception as e:
            logger.warning(f"Quote batch error (tokens {i}-{i+len(batch)}): {e}")
        if i + QUOTE_BATCH_SIZE < len(tokens):
            time.sleep(QUOTE_DELAY)

    return all_quotes


# ============================================================================
# TAKE SNAPSHOT
# ============================================================================
def take_snapshot(underlyings: list = None, snapshot_time: str = None) -> pd.DataFrame:
    """
    Take a full options chain snapshot with Greeks.
    Returns DataFrame with all option data.
    """
    kite = get_kite()

    if underlyings is None:
        underlyings = INDEX_UNIVERSE + TOP_FO_STOCKS

    snap_time = snapshot_time or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    snap_date = datetime.now().date()

    logger.info(f"Taking options snapshot at {snap_time} for {len(underlyings)} underlyings")

    universe = build_universe(kite, underlyings)

    if not universe:
        logger.error("Empty universe — no options found")
        return pd.DataFrame()

    # Collect all instruments
    all_instruments = []
    for und, data in universe.items():
        all_instruments.extend(data["options"])

    logger.info(f"Total options to quote: {len(all_instruments)}")

    # Fetch all quotes
    quotes = fetch_quotes_batched(kite, all_instruments)
    logger.info(f"Quotes received: {len(quotes)}")

    # Build rows
    rows = []
    for und, data in universe.items():
        spot = data["spot"]

        for inst in data["options"]:
            token = inst["instrument_token"]
            q = quotes.get(str(token)) or quotes.get(token)
            if not q:
                continue

            ltp = q.get("last_price", 0)
            oi = q.get("oi", 0)
            volume = q.get("volume", 0)
            ohlc = q.get("ohlc", {})
            bid = q.get("depth", {}).get("buy", [{}])[0].get("price", 0)
            ask = q.get("depth", {}).get("sell", [{}])[0].get("price", 0)

            # Days to expiry
            expiry_date = inst["expiry"]
            if hasattr(expiry_date, "date"):
                expiry_date = expiry_date.date()
            dte = max(0, (expiry_date - snap_date).days)
            T = max(dte / 365, 1 / 365)

            # Calculate IV and Greeks
            flag = 'c' if inst["instrument_type"] == "CE" else 'p'
            iv = None
            greeks = {}

            if ltp > 0:
                iv = calc_iv(ltp, spot, inst["strike"], T, RISK_FREE_RATE, flag)
                if iv:
                    greeks = bsm_greeks(spot, inst["strike"], T, RISK_FREE_RATE, iv, flag)

            rows.append({
                "snapshot_time": snap_time,
                "snapshot_date": snap_date,
                "underlying": und,
                "spot_price": spot,
                "symbol": inst["tradingsymbol"],
                "expiry": str(inst["expiry"]),
                "strike": inst["strike"],
                "option_type": inst["instrument_type"],
                "dte": dte,
                "ltp": ltp,
                "open": ohlc.get("open", 0),
                "high": ohlc.get("high", 0),
                "low": ohlc.get("low", 0),
                "close": ohlc.get("close", 0),
                "volume": volume,
                "oi": oi,
                "oi_day_high": q.get("oi_day_high", 0),
                "oi_day_low": q.get("oi_day_low", 0),
                "bid": bid,
                "ask": ask,
                "iv": iv,
                "delta": greeks.get("delta"),
                "gamma": greeks.get("gamma"),
                "theta": greeks.get("theta"),
                "vega": greeks.get("vega"),
                "rho": greeks.get("rho"),
                "lot_size": inst["lot_size"],
                "instrument_token": token,
            })

    df = pd.DataFrame(rows)
    logger.info(f"Snapshot complete: {len(df)} rows, {df['iv'].notna().sum()} with valid IV")
    return df


# ============================================================================
# SAVE SNAPSHOT
# ============================================================================
def save_snapshot(df: pd.DataFrame, label: str = "close"):
    """Save snapshot to partitioned Parquet."""
    if df.empty:
        logger.warning("Empty snapshot — nothing to save")
        return

    snap_date = df["snapshot_date"].iloc[0]
    if hasattr(snap_date, "date"):
        snap_date = snap_date.date()

    out_dir = DATA_ROOT / str(snap_date.year) / f"{snap_date.month:02d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{snap_date}_{label}.parquet"

    df.to_parquet(out_path, index=False)
    logger.info(f"Saved {len(df)} rows → {out_path}")
    return out_path


# ============================================================================
# MAIN
# ============================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Options Chain Snapshot")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--snapshot", action="store_true", help="Take snapshot now")
    group.add_argument("--test", action="store_true", help="Test with NIFTY + BANKNIFTY only")
    parser.add_argument("--label", default="close", help="Snapshot label (close/intraday/etc)")
    args = parser.parse_args()

    if args.test:
        df = take_snapshot(underlyings=["NIFTY", "BANKNIFTY"])
        if not df.empty:
            print(f"\nSnapshot: {len(df)} rows")
            print(df[["underlying", "expiry", "strike", "option_type", "ltp", "oi", "iv", "delta", "theta"]].head(20).to_string())
            save_snapshot(df, label="test")

    elif args.snapshot:
        df = take_snapshot()
        if not df.empty:
            save_snapshot(df, label=args.label)
            print(f"Snapshot saved: {len(df)} rows")
