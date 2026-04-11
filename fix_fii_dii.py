"""
fix_fii_dii.py
--------------
Reloads all 3 FII/DII parquet files into fii_dii_fo with correct column mapping.

DB schema (fii_dii_fo):
  time DATE, category TEXT, buy_value NUMERIC, sell_value NUMERIC, net_value NUMERIC

Parquet files:

1. cash_fiidii_daily.parquet
   columns: client_type, date, buy_value_cr, sell_value_cr, net_value_cr
   → time=date, category=client_type, buy_value=buy_value_cr,
     sell_value=sell_value_cr, net_value=net_value_cr

2. fao_participant_oi.parquet
   columns: client_type, future_index_long, future_index_short, ...
            total_long_contracts, total_short_contracts, date, data_type, ...
   → time=date, category=client_type + '_oi',
     buy_value=total_long_contracts, sell_value=total_short_contracts,
     net_value=total_long_contracts-total_short_contracts

3. fao_participant_vol.parquet
   → same mapping but data_type='vol'
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
log = logging.getLogger("fix_fii_dii")

DB_HOST = os.getenv("TIMESCALE_HOST", "192.168.0.201")
DB_NAME = os.getenv("TIMESCALE_MARKET_DATA_DB", "market_data")
DB_USER = os.getenv("TIMESCALE_USERNAME", "postgres")
DB_PASS = os.getenv("TIMESCALE_PASSWORD", "postgres")
DATA_ROOT = "/media/vboxuser/test/NSE_Data/fii_dii"


def get_conn():
    return psycopg2.connect(
        host=DB_HOST, dbname=DB_NAME, user=DB_USER, password=DB_PASS
    )


def load_cash(conn):
    path = f"{DATA_ROOT}/cash_fiidii_daily.parquet"
    log.info(f"Loading {path}")
    df = pd.read_parquet(path)
    log.info(f"  {len(df)} rows")

    rows = [
        (r["date"], r["client_type"], r["buy_value_cr"], r["sell_value_cr"], r["net_value_cr"])
        for _, r in df.iterrows()
        if not pd.isnull(r.get("date"))
    ]

    cur = conn.cursor()
    execute_values(
        cur,
        """INSERT INTO fii_dii_fo (time, category, buy_value, sell_value, net_value)
           VALUES %s ON CONFLICT DO NOTHING""",
        rows, page_size=500,
    )
    conn.commit()
    log.info(f"  Inserted {len(rows)} cash rows")
    cur.close()


def load_fo(conn, filename, data_type_label):
    path = f"{DATA_ROOT}/{filename}"
    log.info(f"Loading {path}")
    df = pd.read_parquet(path)
    # Keep only core columns, drop the unnamed garbage columns
    keep = ["client_type", "date", "data_type", "total_long_contracts", "total_short_contracts"]
    df = df[[c for c in keep if c in df.columns]].copy()
    log.info(f"  {len(df)} rows, data_types: {df['data_type'].unique() if 'data_type' in df.columns else 'N/A'}")

    rows = []
    for _, r in df.iterrows():
        if pd.isnull(r.get("date")):
            continue
        long_c = r.get("total_long_contracts", 0) or 0
        short_c = r.get("total_short_contracts", 0) or 0
        category = f"{r['client_type']}_{data_type_label}"
        rows.append((
            r["date"],
            category,
            float(long_c),
            float(short_c),
            float(long_c) - float(short_c),
        ))

    cur = conn.cursor()
    execute_values(
        cur,
        """INSERT INTO fii_dii_fo (time, category, buy_value, sell_value, net_value)
           VALUES %s ON CONFLICT DO NOTHING""",
        rows, page_size=2000,
    )
    conn.commit()
    log.info(f"  Inserted {len(rows)} rows ({data_type_label})")
    cur.close()


def main():
    conn = get_conn()
    log.info("Connected to DB")

    # Clear existing data first to avoid partial/duplicate state
    cur = conn.cursor()
    cur.execute("DELETE FROM fii_dii_fo WHERE true")
    conn.commit()
    log.info("Cleared existing fii_dii_fo rows")
    cur.close()

    load_cash(conn)
    load_fo(conn, "fao_participant_oi.parquet", "oi")
    load_fo(conn, "fao_participant_vol.parquet", "vol")

    # Summary
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*), MIN(time), MAX(time) FROM fii_dii_fo")
    count, min_t, max_t = cur.fetchone()
    log.info(f"Final: {count:,} rows, date range {min_t} → {max_t}")
    cur.close()
    conn.close()
    log.info("Done.")


if __name__ == "__main__":
    main()
