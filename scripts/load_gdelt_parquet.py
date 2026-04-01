#!/usr/bin/env python3
"""
Load GDELT sentiment parquet files into TimescaleDB gdelt_sentiment hypertable.
Structure: gdelt_sentiment/YYYY/MM/DD.parquet
"""

import json
import logging
import sys
from pathlib import Path

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

DATA_ROOT = Path("/media/vboxuser/test/NSE_Data/gdelt_sentiment")
STATE_FILE = Path("/media/vboxuser/test/NSE_Data/gdelt_parquet_db_state.json")
LOG_FILE = "/media/vboxuser/test/NSE_Data/gdelt_parquet_db.log"
DB_CONFIG = dict(host="192.168.0.201", port=5432, dbname="market_data",
                 user="postgres", password="postgres")
BATCH_SIZE = 50000

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [INFO] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()]
)
log = logging.getLogger(__name__)

def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"done_files": [], "total_rows": 0}

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))

def ensure_table(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS gdelt_sentiment (
                datetime    TIMESTAMPTZ NOT NULL,
                date        DATE,
                source      TEXT,
                url         TEXT,
                tone        DOUBLE PRECISION,
                positive_score DOUBLE PRECISION,
                negative_score DOUBLE PRECISION,
                polarity    DOUBLE PRECISION,
                activity_ref_density DOUBLE PRECISION,
                self_ref_density DOUBLE PRECISION,
                word_count  DOUBLE PRECISION,
                themes      TEXT,
                locations   TEXT,
                persons     TEXT,
                organizations TEXT,
                gkg_record_id TEXT
            );
        """)
        try:
            cur.execute("SELECT create_hypertable('gdelt_sentiment', 'datetime', if_not_exists => TRUE)")
        except Exception:
            pass
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_gdelt_sentiment_datetime
            ON gdelt_sentiment (datetime DESC)
        """)
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_gdelt_record_id
            ON gdelt_sentiment (datetime, gkg_record_id)
        """)
    conn.commit()
    log.info("Table gdelt_sentiment ready")

def load_file(conn, fpath, state):
    key = str(fpath.relative_to(DATA_ROOT))
    if key in state["done_files"]:
        return 0

    try:
        df = pd.read_parquet(fpath)
        if df.empty:
            state["done_files"].append(key)
            return 0

        df["datetime"] = pd.to_datetime(df["datetime"], utc=True, errors="coerce")
        df = df.dropna(subset=["datetime", "gkg_record_id"])

        cols = ["datetime","date","source","url","tone","positive_score","negative_score",
                "polarity","activity_ref_density","self_ref_density","word_count",
                "themes","locations","persons","organizations","gkg_record_id"]
        for c in cols:
            if c not in df.columns:
                df[c] = None
        # Serialize array/list columns to JSON strings
        import numpy as np
        for c in ["themes", "locations", "persons", "organizations"]:
            if c in df.columns:
                df[c] = df[c].apply(lambda x: 
                    ",".join(x) if isinstance(x, (list, np.ndarray)) else str(x) if x is not None else None)

        df = df[cols]

        rows = [tuple(r) for r in df.itertuples(index=False)]
        with conn.cursor() as cur:
            execute_values(cur, """
                INSERT INTO gdelt_sentiment
                    (datetime,date,source,url,tone,positive_score,negative_score,
                     polarity,activity_ref_density,self_ref_density,word_count,
                     themes,locations,persons,organizations,gkg_record_id)
                VALUES %s
                ON CONFLICT (datetime, gkg_record_id) DO NOTHING
            """, rows, page_size=BATCH_SIZE)
        conn.commit()
        state["done_files"].append(key)
        return len(rows)

    except Exception as e:
        log.error(f"ERR {key}: {e}")
        try: conn.rollback()
        except: pass
        return 0

def main():
    state = load_state()
    done_set = set(state["done_files"])

    files = sorted(DATA_ROOT.rglob("*.parquet"))
    total = len(files)
    log.info(f"Found {total} GDELT parquet files")

    conn = psycopg2.connect(**DB_CONFIG)
    ensure_table(conn)

    loaded = 0
    total_rows = state.get("total_rows", 0)

    for i, fpath in enumerate(files):
        key = str(fpath.relative_to(DATA_ROOT))
        if key in done_set:
            continue

        rows = load_file(conn, fpath, state)
        total_rows += rows
        loaded += 1

        if loaded % 50 == 0:
            state["total_rows"] = total_rows
            save_state(state)
            log.info(f"[{i+1}/{total}] {loaded} files loaded, {total_rows:,} rows total")

    state["total_rows"] = total_rows
    save_state(state)
    conn.close()
    log.info(f"=== DONE: {loaded} files, {total_rows:,} rows ===")

if __name__ == "__main__":
    main()
