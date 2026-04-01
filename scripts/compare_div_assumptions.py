#!/usr/bin/env python3
"""
Compare IV calculations under different dividend yield assumptions.
Shows impact of dividend assumption on IV and Greeks.
"""

import math
import psycopg2
import pandas as pd
from scipy.stats import norm

DB_CONFIG = dict(host="192.168.0.201", port=5432, dbname="market_data",
                 user="postgres", password="postgres")
RISK_FREE = 0.065

def fallback_iv(flag, S, K, t, r, q, price, tol=1e-6, max_iter=200):
    def bsm(iv):
        if iv <= 0: return 0
        d1 = (math.log(S/K) + (r - q + 0.5*iv**2)*t) / (iv*math.sqrt(t))
        d2 = d1 - iv*math.sqrt(t)
        if flag == 'c':
            return S*math.exp(-q*t)*norm.cdf(d1) - K*math.exp(-r*t)*norm.cdf(d2)
        else:
            return K*math.exp(-r*t)*norm.cdf(-d2) - S*math.exp(-q*t)*norm.cdf(-d1)
    lo, hi = 0.001, 20.0
    for _ in range(max_iter):
        mid = (lo+hi)/2
        diff = bsm(mid) - price
        if abs(diff) < tol: return mid
        if diff > 0: hi = mid
        else: lo = mid
    return None

def bsm_greeks(flag, S, K, t, r, q, iv):
    d1 = (math.log(S/K) + (r - q + 0.5*iv**2)*t) / (iv*math.sqrt(t))
    d2 = d1 - iv*math.sqrt(t)
    sign = 1 if flag == 'c' else -1
    delta = sign * math.exp(-q*t) * norm.cdf(sign*d1)
    gamma = math.exp(-q*t) * norm.pdf(d1) / (S*iv*math.sqrt(t))
    theta = (-S*math.exp(-q*t)*norm.pdf(d1)*iv/(2*math.sqrt(t))
             - sign*r*K*math.exp(-r*t)*norm.cdf(sign*d2)
             + sign*q*S*math.exp(-q*t)*norm.cdf(sign*d1)) / 365.0
    vega  = S*math.exp(-q*t)*norm.pdf(d1)*math.sqrt(t)/100.0
    return delta, gamma, theta, vega

conn = psycopg2.connect(**DB_CONFIG)

# Get NIFTY ATM options for 2026-03-19
df = pd.read_sql("""
    SELECT time::date as date, symbol, expiry, strike, option_type,
           spot_price as spot, settle_price as price, dte
    FROM options_iv
    WHERE symbol = 'NIFTY'
      AND time::date = '2026-03-19'
      AND expiry = '2026-03-24'
      AND settle_price > 0
      AND spot_price > 0
    ORDER BY strike
""", conn)

if df.empty:
    print("No data yet — options_iv table still loading. Run after backfill completes.")
    exit()

# Dividend scenarios to compare
scenarios = {
    "q=0.0%   (zero dividend)":     0.000,
    "q=1.0%   (static NIFTY est)":  0.010,
    "q=1.5%   (static pre-2012)":   0.015,
    "q=2.0%   (higher estimate)":   0.020,
    "q=actual (from nse_index_daily)": None,  # will use actual
}

# Get actual div yield for NIFTY on 2026-03-19
actual_dy = pd.read_sql("""
    SELECT div_yield FROM nse_index_daily
    WHERE index_name = 'Nifty 50' AND time::date = '2026-03-19'
""", conn)
actual = float(actual_dy["div_yield"].iloc[0]) if not actual_dy.empty else 0.015
scenarios["q=actual (from nse_index_daily)"] = actual

print(f"\n{'='*80}")
print(f"NIFTY IV Comparison — Different Dividend Assumptions")
print(f"Date: 2026-03-19 | Expiry: 2026-03-24 | Spot: {df['spot'].iloc[0]:.2f}")
print(f"Actual div yield from DB: {actual*100:.3f}%")
print(f"{'='*80}\n")

# ATM strikes only (within 2% of spot)
spot = df['spot'].iloc[0]
atm = df[(df['strike'] >= spot*0.98) & (df['strike'] <= spot*1.02)].copy()

print(f"{'Strike':>8} {'Type':>4} {'Price':>8}", end="")
for sc in scenarios:
    label = sc.split("(")[0].strip()
    print(f" {label:>12}", end="")
print()
print("-"*100)

for _, row in atm.iterrows():
    flag = 'c' if row['option_type'] == 'CE' else 'p'
    t = row['dte'] / 365.0
    S = float(row['spot'])
    K = float(row['strike'])
    price = float(row['price'])

    print(f"{K:>8.0f} {row['option_type']:>4} {price:>8.2f}", end="")
    for sc_name, q in scenarios.items():
        try:
            iv = fallback_iv(flag, S, K, t, RISK_FREE, q, price)
            if iv and 0.001 < iv < 20:
                print(f" {iv*100:>11.2f}%", end="")
            else:
                print(f" {'N/A':>12}", end="")
        except:
            print(f" {'ERR':>12}", end="")
    print()

# Summary: impact of dividend assumption on ATM IV
print(f"\n{'='*80}")
print("SUMMARY: Impact of dividend assumption on ATM CE IV")
print(f"{'Scenario':<40} {'ATM IV':>10} {'vs zero-div':>12} {'Delta':>8}")
print("-"*80)

atm_ce = atm[atm['option_type']=='CE']
if not atm_ce.empty:
    r = atm_ce.iloc[len(atm_ce)//2]  # middle ATM strike
    flag, t = 'c', r['dte']/365.0
    S, K, price = float(r['spot']), float(r['strike']), float(r['price'])

    iv_zero = fallback_iv(flag, S, K, t, RISK_FREE, 0.0, price)
    for sc_name, q in scenarios.items():
        iv = fallback_iv(flag, S, K, t, RISK_FREE, q, price)
        if iv:
            delta, _, _, _ = bsm_greeks(flag, S, K, t, RISK_FREE, q, iv)
            diff = (iv - iv_zero)*100 if iv_zero else 0
            print(f"  {sc_name:<38} {iv*100:>9.2f}% {diff:>+11.2f}% {delta:>8.4f}")

conn.close()
