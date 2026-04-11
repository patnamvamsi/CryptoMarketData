"""
fix_fundamentals.py
-------------------
Reloads both fundamentals parquet files into the DB with correct column mapping.

DB schema (fundamentals):
  as_of_date, symbol, pe_ratio, pb_ratio, div_yield, market_cap,
  revenue, net_income, debt_equity

Parquet: valuation_snapshot.parquet
  columns: symbol, snapshot_date, trailingPE, priceToBook, debtToEquity,
           revenueGrowth, earningsGrowth, trailingEps, bookValue,
           dividendYield, payoutRatio, marketCap, enterpriseValue,
           ebitda, totalRevenue, netIncomeToCommon, totalDebt, totalCash,
           beta, profitMargins, grossMargins, operatingMargins,
           sector, industry, forwardPE, forwardEps, returnOnEquity,
           returnOnAssets, currentRatio, freeCashflow, operatingCashflow

Parquet: earnings_calendar.parquet
  columns: symbol, from_date, to_date, period, financial_year,
           filing_date, consolidated, audited, ind_as, revenue,
           other_income, total_income, finance_costs, profit_before_tax,
           net_profit, share_capital, pat_owners
  -> No direct as_of_date: use filing_date as proxy.
"""

import logging
import os
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("fix_fundamentals")

DB_HOST = os.getenv("TIMESCALE_HOST", "192.168.0.201")
DB_NAME = os.getenv("TIMESCALE_MARKET_DATA_DB", "market_data")
DB_USER = os.getenv("TIMESCALE_USERNAME", "postgres")
DB_PASS = os.getenv("TIMESCALE_PASSWORD", "postgres")
DATA_ROOT = "/media/vboxuser/test/NSE_Data/fundamentals"


def get_conn():
    return psycopg2.connect(
        host=DB_HOST, dbname=DB_NAME, user=DB_USER, password=DB_PASS
    )


def load_valuation(conn):
    path = f"{DATA_ROOT}/valuation_snapshot.parquet"
    log.info(f"Loading {path}")
    df = pd.read_parquet(path)
    log.info(f"  {len(df)} rows, columns: {list(df.columns)}")

    # Map parquet cols → DB cols
    rows = []
    for _, r in df.iterrows():
        rows.append((
            r.get("snapshot_date"),        # as_of_date
            r.get("symbol"),               # symbol
            r.get("trailingPE"),           # pe_ratio
            r.get("priceToBook"),          # pb_ratio
            r.get("dividendYield"),        # div_yield
            r.get("marketCap"),            # market_cap
            r.get("totalRevenue"),         # revenue
            r.get("netIncomeToCommon"),    # net_income
            r.get("debtToEquity"),         # debt_equity
        ))

    cur = conn.cursor()
    # Clear existing valuation rows first
    cur.execute("DELETE FROM fundamentals WHERE true")
    log.info("  Cleared existing fundamentals rows")

    execute_values(
        cur,
        """INSERT INTO fundamentals
           (as_of_date, symbol, pe_ratio, pb_ratio, div_yield,
            market_cap, revenue, net_income, debt_equity)
           VALUES %s
           ON CONFLICT DO NOTHING""",
        rows,
        page_size=1000,
    )
    conn.commit()
    log.info(f"  Inserted {cur.rowcount if cur.rowcount >= 0 else len(rows)} rows (valuation_snapshot)")
    cur.close()


def load_earnings(conn):
    """
    earnings_calendar has no as_of_date — use filing_date.
    The fundamentals table schema only has 9 columns; earnings data
    doesn't fit cleanly. We'll store revenue + net_income keyed by
    filing_date so the data isn't lost. pe_ratio/pb_ratio/div_yield
    will be NULL for these rows.
    """
    path = f"{DATA_ROOT}/earnings_calendar.parquet"
    log.info(f"Loading {path}")
    df = pd.read_parquet(path)
    log.info(f"  {len(df)} rows, columns: {list(df.columns)}")

    rows = []
    for _, r in df.iterrows():
        as_of = r.get("filing_date")
        if pd.isnull(as_of):
            as_of = r.get("to_date")
        if pd.isnull(as_of):
            continue
        rows.append((
            as_of,                   # as_of_date
            r.get("symbol"),         # symbol
            None,                    # pe_ratio
            None,                    # pb_ratio
            None,                    # div_yield
            None,                    # market_cap
            r.get("revenue"),        # revenue
            r.get("net_profit"),     # net_income
            None,                    # debt_equity
        ))

    log.info(f"  {len(rows)} valid rows after filtering nulls")
    cur = conn.cursor()
    execute_values(
        cur,
        """INSERT INTO fundamentals
           (as_of_date, symbol, pe_ratio, pb_ratio, div_yield,
            market_cap, revenue, net_income, debt_equity)
           VALUES %s
           ON CONFLICT DO NOTHING""",
        rows,
        page_size=2000,
    )
    conn.commit()
    log.info(f"  Inserted {len(rows)} rows (earnings_calendar)")
    cur.close()


def main():
    conn = get_conn()
    log.info("Connected to DB")
    load_valuation(conn)
    load_earnings(conn)
    conn.close()
    log.info("Done.")


if __name__ == "__main__":
    main()
