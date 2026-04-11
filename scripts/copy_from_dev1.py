#!/usr/bin/env python3
"""
Stream tables from market_data_dev1 → market_data on 192.168.0.201
Uses psycopg2 COPY for fast bulk transfer.
"""
import psycopg2
import sys
import os
import time
from datetime import datetime

DB_HOST = "192.168.0.201"
DB_PORT = 5432
DB_USER = "postgres"
DB_PASS = "postgres"
SRC_DB = "market_data_dev1"
TGT_DB = "market_data"

STATE_FILE = "/media/vboxuser/test/NSE_Data/copy_dev1_state.txt"
LOG_FILE = "/media/vboxuser/test/NSE_Data/copy_dev1.log"

def log(msg):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")

def is_done(key):
    try:
        return f"DONE:{key}\n" in open(STATE_FILE).read()
    except: return False

def mark_done(key):
    with open(STATE_FILE, "a") as f:
        f.write(f"DONE:{key}\n")

def get_columns(conn, table):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema='public' AND table_name=%s
            ORDER BY ordinal_position
        """, (table,))
        return [r[0] for r in cur.fetchall()]

def copy_table(src_conn, tgt_conn, table, is_hypertable=False):
    if is_done(table):
        log(f"  SKIP: {table}")
        return True

    try:
        # Get row count
        with src_conn.cursor() as cur:
            cur.execute(f'SELECT count(*) FROM "{table}"')
            src_count = cur.fetchone()[0]

        # Check if table exists on target
        with tgt_conn.cursor() as cur:
            cur.execute("SELECT EXISTS(SELECT 1 FROM pg_tables WHERE schemaname='public' AND tablename=%s)", (table,))
            tgt_exists = cur.fetchone()[0]

        if not tgt_exists:
            # Get DDL from source and create on target
            cols = get_columns(src_conn, table)
            with src_conn.cursor() as cur:
                cur.execute(f"""
                    SELECT 'CREATE TABLE IF NOT EXISTS public."{table}" (' ||
                    string_agg(column_name || ' ' || data_type ||
                        CASE WHEN character_maximum_length IS NOT NULL
                             THEN '(' || character_maximum_length || ')'
                             ELSE '' END,
                        ', ' ORDER BY ordinal_position) || ')'
                    FROM information_schema.columns
                    WHERE table_schema='public' AND table_name=%s
                """, (table,))
                create_ddl = cur.fetchone()[0]
            with tgt_conn.cursor() as cur:
                cur.execute(create_ddl)
            tgt_conn.commit()

        # Truncate if partial
        with tgt_conn.cursor() as cur:
            cur.execute(f'SELECT count(*) FROM "{table}"')
            tgt_count = cur.fetchone()[0]
        if 0 < tgt_count < src_count:
            log(f"  Truncating partial data ({tgt_count} rows)...")
            with tgt_conn.cursor() as cur:
                cur.execute(f'TRUNCATE "{table}"')
            tgt_conn.commit()
            tgt_count = 0

        if tgt_count == src_count and src_count > 0:
            log(f"  ✓ {table}: already {src_count} rows")
            mark_done(table)
            return True

        log(f"  Copying {table}: {src_count} rows...")
        start = time.time()

        import io
        buf = io.BytesIO()
        with src_conn.cursor() as cur:
            cur.copy_expert(f'COPY (SELECT * FROM "{table}") TO STDOUT', buf)
        buf.seek(0)

        with tgt_conn.cursor() as cur:
            cur.copy_expert(f'COPY "{table}" FROM STDIN', buf)
        tgt_conn.commit()

        elapsed = int(time.time() - start)
        with tgt_conn.cursor() as cur:
            cur.execute(f'SELECT count(*) FROM "{table}"')
            final = cur.fetchone()[0]

        if final == src_count:
            mark_done(table)
            log(f"  ✓ {table}: {final} rows in {elapsed}s")
            return True
        else:
            log(f"  ERR {table}: got {final}/{src_count}")
            return False

    except Exception as e:
        log(f"  ERR {table}: {e}")
        try: src_conn.rollback()
        except: pass
        try: tgt_conn.rollback()
        except: pass
        return False

def main():
    log("=== copy_from_dev1.py starting ===")

    src = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=SRC_DB, user=DB_USER, password=DB_PASS)
    tgt = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=TGT_DB, user=DB_USER, password=DB_PASS)

    # NSE critical tables
    for tbl in ["nse_fo_daily", "nse_index_daily", "symbols"]:
        copy_table(src, tgt, tbl)

    # Zerodha klines
    zerodha_tables = [
        "zerodha_hdfcbank_kline_1m", "zerodha_itc_kline_1m", "zerodha_infy_kline_1m",
        "zerodha_sbin_kline_1m", "zerodha_reliance_kline_1m", "zerodha_axisbank_kline_1m",
        "zerodha_lt_kline_1m", "zerodha_icicibank_kline_1m", "zerodha_kotakbank_kline_1m",
        "zerodha_bhartiartl_kline_1m", "zerodha_maruti_kline_1m", "zerodha_hindunilvr_kline_1m",
        "zerodha_asianpaint_kline_1m", "zerodha_wipro_kline_1m", "zerodha_ntpc_kline_1m",
        "zerodha_bajaj_auto_kline_1m", "binance_dogeusdt_kline_1m"
    ]
    for tbl in zerodha_tables:
        copy_table(src, tgt, tbl)

    src.close()
    tgt.close()
    log("=== Phase 1 complete ===")

if __name__ == "__main__":
    main()
