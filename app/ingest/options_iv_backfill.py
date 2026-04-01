#!/usr/bin/env python3
"""
options_iv_backfill.py
----------------------
Computes IV and Greeks for all historical NSE options from nse_fo_daily
and writes results to the options_iv hypertable.

Sources:
  - nse_fo_daily     → settle_price, underlying_price, strike, expiry, option_type, OI
  - nse_index_daily  → div_yield for index underlyings (exact daily, 2012+)
  - corporate_events → trailing dividend yield for stock options
  - static fallback  → pre-2012 index div yields (NIFTY ~1.5%, BANKNIFTY ~0.8%)

Greeks lib: py_vollib (Black-Scholes-Merton)

Usage:
    python3 -m app.ingest.options_iv_backfill [--start 2001-01-01] [--end 2026-03-31]
    python3 -m app.ingest.options_iv_backfill --resume          # skip already-done dates
    python3 -m app.ingest.options_iv_backfill --workers 4       # parallel date chunks
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import signal
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

# py_vollib
try:
    from py_vollib.black_scholes_merton.implied_volatility import implied_volatility as bsm_iv
    from py_vollib.black_scholes_merton import black_scholes_merton as bsm_price
    from py_vollib.black_scholes_merton.greeks.analytical import (
        delta, gamma, theta, vega, rho
    )
    VOLLIB_OK = True
except ImportError:
    VOLLIB_OK = False
    # Fallback: pure-python BSM (no py_vollib)
    from scipy.stats import norm

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("options_iv_backfill")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DB_HOST     = "192.168.0.201"
DB_PORT     = 5432
DB_NAME     = "market_data"
DB_USER     = "postgres"
DB_PASS     = "postgres"

PROGRESS_FILE = Path("/media/vboxuser/test/NSE_Data/options_iv_progress.json")
PARQUET_DIR   = Path("/media/vboxuser/test/NSE_Data/options_snapshots/backfill")
RISK_FREE     = 0.065          # RBI repo rate ~6.5%
MIN_DTE       = 0              # include expiry day
MAX_DTE       = 365            # ignore ultra-long-dated
BATCH_SIZE    = 50_000

# Static pre-2012 index div yields
STATIC_DIV_YIELD = {
    "NIFTY":     0.015,
    "BANKNIFTY": 0.008,
    "NIFTYIT":   0.010,
    "FINNIFTY":  0.008,
}

_shutdown = False
def _sig(sig, frame):
    global _shutdown
    log.info("Shutdown signal — finishing current batch...")
    _shutdown = True
signal.signal(signal.SIGTERM, _sig)
signal.signal(signal.SIGINT,  _sig)


# ---------------------------------------------------------------------------
# Progress
# ---------------------------------------------------------------------------
def load_progress() -> dict:
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE) as f:
            return json.load(f)
    return {"done_dates": [], "total_rows": 0}

def save_progress(p: dict):
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROGRESS_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(p, f, indent=2, default=str)
    tmp.replace(PROGRESS_FILE)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
def get_conn():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASS, connect_timeout=15,
    )

INSERT_SQL = """
    INSERT INTO options_iv
        (time, symbol, expiry, strike, option_type, underlying, spot_price,
         settle_price, dte, iv, delta, gamma, theta, vega, rho,
         div_yield, div_yield_src, open_interest, contracts)
    VALUES %s
    ON CONFLICT DO NOTHING
"""


# ---------------------------------------------------------------------------
# Dividend yield helpers
# ---------------------------------------------------------------------------
INDEX_NAME_MAP = {
    # nse_index_daily name (uppercased, spaces removed) → FO symbol
    "NIFTY50":          "NIFTY",
    "NIFTYBANK":        "BANKNIFTY",
    "NIFTYMIDCAP150":   "MIDCPNIFTY",
    "NIFTYMIDCAP50":    "MIDCPNIFTY",
    "NIFTYFINSERVICE":  "FINNIFTY",
    "NIFTYFINSRV25":    "FINNIFTY",
    "NIFTYNEXT50":      "NIFTYNXT50",
}

def build_index_div_yields(conn) -> pd.DataFrame:
    """Load daily div_yield from nse_index_daily for 2012+."""
    try:
        df = pd.read_sql(
            "SELECT time::date AS date, index_name, div_yield FROM nse_index_daily "
            "WHERE div_yield IS NOT NULL ORDER BY time",
            conn,
        )
        df["index_name"] = df["index_name"].str.upper().str.replace(" ", "").str.replace("-", "")
        # Map index names to FO symbols
        df["index_name"] = df["index_name"].apply(lambda x: INDEX_NAME_MAP.get(x, x))
        # div_yield is stored as percentage (e.g. 1.36 = 1.36%), convert to decimal
        df["div_yield"] = df["div_yield"] / 100.0
        return df
    except Exception as e:
        log.warning(f"Could not load index div yields: {e}")
        return pd.DataFrame()


def build_stock_trailing_div(conn) -> dict:
    """
    For stock options: compute trailing 12M dividend yield per symbol.
    Returns {symbol: {date: yield}} dict.
    """
    try:
        events = pd.read_sql(
            "SELECT ex_date, symbol, amount FROM corporate_events "
            "WHERE event_type ILIKE '%dividend%' AND amount > 0",
            conn,
        )
        prices = pd.read_sql(
            "SELECT time::date AS date, symbol, close FROM nse_equity_daily "
            "WHERE close > 0 ORDER BY time",
            conn,
        )
    except Exception as e:
        log.warning(f"Could not load dividend data: {e}")
        return {}

    if events.empty or prices.empty:
        return {}

    events["ex_date"] = pd.to_datetime(events["ex_date"]).dt.date
    prices["date"] = pd.to_datetime(prices["date"]).dt.date

    # Per symbol: sum dividends in trailing 12M, divide by close
    result = {}
    for symbol, grp in events.groupby("symbol"):
        sym_prices = prices[prices["symbol"] == symbol].set_index("date")["close"]
        sym_result = {}
        for _, row in grp.iterrows():
            ex = row["ex_date"]
            # Trailing 12M dividends
            try:
                t12_start = date(ex.year - 1, ex.month, ex.day)
            except ValueError:
                # Feb 29 in a leap year → use Feb 28
                t12_start = date(ex.year - 1, ex.month, 28)
            t12_div = grp[
                (grp["ex_date"] > t12_start) & (grp["ex_date"] <= ex)
            ]["amount"].sum()
            # Close price on or before ex_date
            candidates = sym_prices[sym_prices.index <= ex]
            if candidates.empty or t12_div == 0:
                continue
            close = float(candidates.iloc[-1])
            if close > 0:
                sym_result[ex] = t12_div / close
        if sym_result:
            result[symbol] = sym_result
    return result


# ---------------------------------------------------------------------------
# IV + Greeks
# ---------------------------------------------------------------------------
def _safe_iv(flag: str, S: float, K: float, t: float, r: float,
             q: float, price: float) -> float | None:
    """Compute IV; return None on failure."""
    if price <= 0 or t <= 0 or S <= 0 or K <= 0:
        return None
    try:
        if VOLLIB_OK:
            iv = bsm_iv(price, S, K, t, r, q, flag)  # correct order: price,S,K,t,r,q,flag
        else:
            iv = _fallback_iv(flag, S, K, t, r, q, price)
        if math.isfinite(iv) and 0.001 < iv < 30.0:
            return round(iv, 6)
    except Exception:
        pass
    return None


def _bsm_greeks_scipy(flag: str, S: float, K: float, t: float, r: float,
                      q: float, iv: float) -> dict:
    """BSM Greeks via scipy — pure python fallback."""
    try:
        d1 = (math.log(S / K) + (r - q + 0.5 * iv**2) * t) / (iv * math.sqrt(t))
        d2 = d1 - iv * math.sqrt(t)
        sign = 1 if flag == "c" else -1
        nd1  = norm.cdf(sign * d1)
        nd2  = norm.cdf(sign * d2)
        npd1 = norm.pdf(d1)
        delta_v = sign * math.exp(-q * t) * nd1
        gamma_v = math.exp(-q * t) * npd1 / (S * iv * math.sqrt(t))
        theta_v = (
            -S * math.exp(-q * t) * npd1 * iv / (2 * math.sqrt(t))
            - sign * r * K * math.exp(-r * t) * nd2
            + sign * q * S * math.exp(-q * t) * nd1
        ) / 365.0
        vega_v  = S * math.exp(-q * t) * npd1 * math.sqrt(t) / 100.0
        rho_v   = sign * K * t * math.exp(-r * t) * nd2 / 100.0
        return {
            "delta": round(delta_v, 6),
            "gamma": round(gamma_v, 8),
            "theta": round(theta_v, 6),
            "vega":  round(vega_v, 6),
            "rho":   round(rho_v, 6),
        }
    except Exception:
        return {}


def _safe_greeks(flag: str, S: float, K: float, t: float, r: float,
                 q: float, iv: float) -> dict:
    """Compute BSM Greeks; use py_vollib if available, scipy fallback otherwise."""
    if not iv or t <= 0:
        return {}
    try:
        if VOLLIB_OK:
            return {
                "delta": round(delta(flag, S, K, t, r, iv, q), 6),
                "gamma": round(gamma(flag, S, K, t, r, iv, q), 8),
                "theta": round(theta(flag, S, K, t, r, iv, q), 6),
                "vega":  round(vega(flag, S, K, t, r, iv, q), 6),
                "rho":   round(rho(flag, S, K, t, r, iv, q), 6),
            }
        else:
            return _bsm_greeks_scipy(flag, S, K, t, r, q, iv)
    except Exception:
        return {}


def _fallback_iv(flag, S, K, t, r, q, price, tol=1e-6, max_iter=200):
    """Bisection IV solver (fallback when py_vollib unavailable)."""
    def bsm(iv):
        d1 = (math.log(S / K) + (r - q + 0.5 * iv**2) * t) / (iv * math.sqrt(t))
        d2 = d1 - iv * math.sqrt(t)
        if flag == "c":
            return (S * math.exp(-q*t) * norm.cdf(d1)
                    - K * math.exp(-r*t) * norm.cdf(d2))
        else:
            return (K * math.exp(-r*t) * norm.cdf(-d2)
                    - S * math.exp(-q*t) * norm.cdf(-d1))
    lo, hi = 0.001, 20.0
    for _ in range(max_iter):
        mid = (lo + hi) / 2
        diff = bsm(mid) - price
        if abs(diff) < tol:
            return mid
        if diff > 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


# ---------------------------------------------------------------------------
# Process one trade date
# ---------------------------------------------------------------------------
def process_date(
    trade_date: date,
    fo_df: pd.DataFrame,
    index_dy_df: pd.DataFrame,
    stock_dy: dict,
) -> list[tuple]:
    """
    Given a slice of nse_fo_daily for trade_date, compute IV+Greeks.
    Returns list of row tuples ready for INSERT.
    """
    # Filter options only
    opts = fo_df[fo_df["option_type"].isin(["CE", "PE"])].copy()
    opts = opts[opts["settle_price"] > 0]
    opts = opts[opts["underlying_price"] > 0]
    opts = opts[opts["strike"] > 0]

    # DTE
    opts["dte"] = (pd.to_datetime(opts["expiry"]).dt.date.apply(
        lambda e: (e - trade_date).days
    ))
    opts = opts[(opts["dte"] >= MIN_DTE) & (opts["dte"] <= MAX_DTE)]

    if opts.empty:
        return []

    # Build index div-yield lookup for today
    if not index_dy_df.empty:
        today_idx = index_dy_df[index_dy_df["date"] == trade_date].set_index("index_name")["div_yield"]
    else:
        today_idx = pd.Series(dtype=float)

    rows = []
    for _, r in opts.iterrows():
        symbol   = str(r["symbol"])
        expiry   = r["expiry"]
        strike   = float(r["strike"])
        opt_type = str(r["option_type"])
        S        = float(r["underlying_price"])
        price    = float(r["settle_price"])
        dte      = int(r["dte"])
        oi       = r.get("open_interest")
        contracts = r.get("contracts")

        t = dte / 365.0
        flag = "c" if opt_type == "CE" else "p"

        # Determine div yield
        underlying = str(r.get("instrument", symbol)).split("-")[0].strip()
        dy_src = "static"
        dy = 0.0

        sym_upper = symbol.upper()
        idx_key = sym_upper.replace(" ", "")
        if idx_key in today_idx.index:
            val = today_idx[idx_key]
            dy = float(val.iloc[0] if hasattr(val, 'iloc') else val)
            dy_src = "nse_index_daily"
        elif idx_key in STATIC_DIV_YIELD:
            dy = STATIC_DIV_YIELD[idx_key]
            dy_src = "static_pre2012"
        elif sym_upper in stock_dy:
            # Find nearest date ≤ trade_date
            sym_dates = sorted(d for d in stock_dy[sym_upper] if d <= trade_date)
            if sym_dates:
                dy = stock_dy[sym_upper][sym_dates[-1]]
                dy_src = "corporate_events"

        iv = _safe_iv(flag, S, strike, t, RISK_FREE, dy, price)
        greeks = _safe_greeks(flag, S, strike, t, RISK_FREE, dy, iv) if iv else {}

        rows.append((
            pd.Timestamp(trade_date, tz="UTC"),  # time
            symbol,
            expiry,
            strike,
            opt_type,
            underlying,
            S,
            price,
            dte,
            iv,
            greeks.get("delta"),
            greeks.get("gamma"),
            greeks.get("theta"),
            greeks.get("vega"),
            greeks.get("rho"),
            dy,
            dy_src,
            float(oi)  if oi is not None else None,
            float(contracts) if contracts is not None else None,
        ))
    return rows


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Options IV + Greeks backfill")
    parser.add_argument("--start",   default="2001-01-01", help="Start date YYYY-MM-DD")
    parser.add_argument("--end",     default=str(date.today()), help="End date YYYY-MM-DD")
    parser.add_argument("--resume",  action="store_true", help="Skip already-processed dates")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    end   = date.fromisoformat(args.end)

    progress = load_progress()
    done_dates = set(progress.get("done_dates", []))
    total_rows = progress.get("total_rows", 0)

    log.info("Connecting to DB...")
    conn = get_conn()

    log.info("Loading auxiliary data (index div yields, stock dividends)...")
    index_dy_df = build_index_div_yields(conn)
    stock_dy    = build_stock_trailing_div(conn)
    log.info(f"  Index div yields: {len(index_dy_df)} rows | Stock div symbols: {len(stock_dy)}")

    log.info("Fetching distinct trade dates from nse_fo_daily...")
    cur = conn.cursor()
    cur.execute(
        """
        SELECT DISTINCT time::date AS trade_date
        FROM nse_fo_daily
        WHERE option_type IN ('CE', 'PE')
          AND time::date >= %s AND time::date <= %s
        ORDER BY trade_date
        """,
        (start, end),
    )
    all_dates = [row[0] for row in cur.fetchall()]
    cur.close()
    log.info(f"  Found {len(all_dates)} trade dates")

    if args.resume:
        all_dates = [d for d in all_dates if str(d) not in done_dates]
    log.info(f"  Processing {len(all_dates)} trade dates")

    PARQUET_DIR.mkdir(parents=True, exist_ok=True)
    write_conn = None if args.dry_run else get_conn()

    for i, trade_date in enumerate(all_dates):
        if _shutdown:
            break

        day_str = str(trade_date)

        # Fetch only this date's rows — no bulk load into RAM
        day_fo = pd.read_sql(
            """
            SELECT time::date AS trade_date, instrument, symbol, expiry,
                   strike, option_type, settle_price, underlying_price,
                   open_interest, contracts
            FROM nse_fo_daily
            WHERE option_type IN ('CE', 'PE')
              AND time::date = %s
            ORDER BY time
            """,
            conn,
            params=(trade_date,),
        )
        rows = process_date(trade_date, day_fo, index_dy_df, stock_dy)

        if rows:
            if not args.dry_run:
                cur = write_conn.cursor()
                for batch_start in range(0, len(rows), BATCH_SIZE):
                    batch = rows[batch_start:batch_start + BATCH_SIZE]
                    execute_values(cur, INSERT_SQL, batch)
                write_conn.commit()
                cur.close()
            total_rows += len(rows)

        done_dates.add(day_str)

        if (i + 1) % 100 == 0 or _shutdown:
            log.info(f"[{i+1}/{len(all_dates)}] {day_str}: {len(rows)} rows | total: {total_rows:,}")
            save_progress({"done_dates": list(done_dates), "total_rows": total_rows})

    save_progress({"done_dates": list(done_dates), "total_rows": total_rows})
    if write_conn:
        write_conn.close()
    conn.close()
    log.info(f"Done. Total rows inserted: {total_rows:,}")


if __name__ == "__main__":
    main()
