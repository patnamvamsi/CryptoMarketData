#!/usr/bin/env python3
"""
Load Zerodha intraday parquet files into TimescaleDB.

Structure: zerodha_intraday/{exchange}/{type}/{interval}/{symbol}/YYYY-MM.parquet
Table name: zerodha_{exchange}_{sanitized_symbol}_{interval}

All tables have columns: time, open, high, low, close, volume
"""

import os
import re
import sys
import logging
import json
from pathlib import Path
from datetime import datetime

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

# ── Config ──────────────────────────────────────────────────────────────────
DATA_ROOT = Path("/media/vboxuser/test/NSE_Data/zerodha_intraday")
STATE_FILE = Path("/media/vboxuser/test/NSE_Data/zerodha_parquet_db_state.json")
LOG_FILE = "/media/vboxuser/test/NSE_Data/zerodha_parquet_db.log"
DB_CONFIG = dict(host="192.168.0.201", port=5432, dbname="market_data",
                 user="postgres", password="postgres")
BATCH_SIZE = 50000

# Interval name mapping (parquet dir → DB suffix)
INTERVAL_MAP = {
    "minute": "1m", "1minute": "1m",
    "3minute": "3m", "5minute": "5m",
    "10minute": "10m", "15minute": "15m",
    "30minute": "30m", "60minute": "1h",
    "day": "1d",
}

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()]
)
log = logging.getLogger(__name__)

# ── State ────────────────────────────────────────────────────────────────────
def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"done_tables": [], "errors": {}}

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))

# ── DB helpers ───────────────────────────────────────────────────────────────
def get_conn():
    return psycopg2.connect(**DB_CONFIG)

def sanitize(name):
    return re.sub(r'[^a-z0-9]', '_', name.lower()).strip('_')

def get_table_name(exchange, symbol, interval_dir):
    interval = INTERVAL_MAP.get(interval_dir, interval_dir)
    sym = sanitize(symbol)
    exch = sanitize(exchange)
    return f"zerodha_{exch}_{sym}_kline_{interval}"

def ensure_table(conn, table):
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT EXISTS(
                SELECT 1 FROM pg_tables
                WHERE schemaname='public' AND tablename=%s
            )""", (table,))
        if cur.fetchone()[0]:
            return

        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS public."{table}" (
                time TIMESTAMPTZ NOT NULL,
                open  DOUBLE PRECISION,
                high  DOUBLE PRECISION,
                low   DOUBLE PRECISION,
                close DOUBLE PRECISION,
                volume BIGINT
            )
        """)
        try:
            cur.execute(f"SELECT create_hypertable('{table}', 'time', if_not_exists => TRUE)")
        except Exception:
            pass
        cur.execute(f"""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_{table}_time
            ON public."{table}" (time)
        """)
    conn.commit()

def load_table(conn, table, df):
    """COPY df into table, upsert on time conflict."""
    if df.empty:
        return 0
    df = df.rename(columns={"date": "time"})
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df[["time","open","high","low","close","volume"]].drop_duplicates("time")
    rows = [tuple(r) for r in df.itertuples(index=False)]
    with conn.cursor() as cur:
        execute_values(cur, f"""
            INSERT INTO public."{table}" (time, open, high, low, close, volume)
            VALUES %s
            ON CONFLICT (time) DO NOTHING
        """, rows, page_size=BATCH_SIZE)
    conn.commit()
    return len(rows)

# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    state = load_state()
    done = set(state["done_tables"])

    # Discover all symbol dirs
    symbol_dirs = []
    for exchange_dir in sorted(DATA_ROOT.iterdir()):
        if not exchange_dir.is_dir(): continue
        exchange = exchange_dir.name
        for type_dir in sorted(exchange_dir.iterdir()):
            if not type_dir.is_dir(): continue
            for interval_dir in sorted(type_dir.iterdir()):
                if not interval_dir.is_dir(): continue
                interval = interval_dir.name
                for symbol_dir in sorted(interval_dir.iterdir()):
                    if not symbol_dir.is_dir(): continue
                    symbol_dirs.append((exchange, interval, symbol_dir.name, symbol_dir))

    total = len(symbol_dirs)
    log.info(f"Found {total} symbol/interval combinations to load")

    conn = get_conn()
    loaded = 0
    errors = 0

    for i, (exchange, interval, symbol, sym_dir) in enumerate(symbol_dirs):
        table = get_table_name(exchange, symbol, interval)

        if table in done:
            continue

        parquet_files = sorted(sym_dir.glob("*.parquet"))
        if not parquet_files:
            continue

        try:
            dfs = []
            for f in parquet_files:
                try:
                    dfs.append(pd.read_parquet(f))
                except Exception as e:
                    log.warning(f"  Skip bad file {f.name}: {e}")
            if not dfs:
                done.add(table)
                continue

            df = pd.concat(dfs, ignore_index=True)
            ensure_table(conn, table)
            rows = load_table(conn, table, df)
            done.add(table)
            loaded += 1

            if loaded % 500 == 0:
                state["done_tables"] = list(done)
                save_state(state)
                log.info(f"[{i+1}/{total}] {loaded} tables loaded, {errors} errors")
            elif loaded % 100 == 0:
                log.info(f"[{i+1}/{total}] ✓ {table}: {rows} rows")

        except Exception as e:
            errors += 1
            state["errors"][table] = str(e)
            log.error(f"  ERR {table}: {e}")
            try: conn.rollback()
            except: pass
            try: conn.close()
            except: pass
            conn = get_conn()

    state["done_tables"] = list(done)
    save_state(state)
    conn.close()
    log.info(f"=== DONE: {loaded} tables loaded, {errors} errors ===")

if __name__ == "__main__":
    main()
