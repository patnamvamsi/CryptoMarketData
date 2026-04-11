"""
zerodha_ohlcv_loader.py
-----------------------
Bulk-loads all zerodha_intraday parquet files into the zerodha_ohlcv hypertable.

Folder structure:
  {DATA_ROOT}/zerodha_intraday/{EXCHANGE}/{asset_type}/{INTERVAL}/{SYMBOL}/{YYYY-MM}.parquet

Parquet columns: date, open, high, low, close, volume
Target table:    zerodha_ohlcv (time, exchange, symbol, interval, open, high, low, close, volume, oi)

Usage:
    python3 -m app.ingest.zerodha_ohlcv_loader [--data-root /path] [--db-host ...] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("zerodha_ohlcv_loader")

DATA_ROOT_DEFAULT = "/media/vboxuser/test/NSE_Data"
PROGRESS_FILE = "zerodha_ohlcv_loader_progress.json"

BATCH_SIZE = 50_000


def get_connection(host, port, dbname, user, password):
    return psycopg2.connect(
        host=host, port=port, dbname=dbname, user=user, password=password
    )


def load_progress(path: Path) -> dict:
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


def save_progress(path: Path, data: dict):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def discover_files(data_root: Path) -> list[dict]:
    """
    Walk zerodha_intraday and return list of dicts with:
      path, exchange, interval, symbol
    """
    results = []
    base = data_root / "zerodha_intraday"
    for pq in sorted(base.rglob("*.parquet")):
        rel = pq.relative_to(base)
        parts = rel.parts
        # Expected: exchange / asset_type / interval / symbol / YYYY-MM.parquet
        if len(parts) < 5:
            continue
        exchange = parts[0]
        interval = parts[2]
        symbol = parts[3]
        results.append({
            "path": pq,
            "exchange": exchange,
            "interval": interval,
            "symbol": symbol,
        })
    return results


def load_file(info: dict) -> Optional[pd.DataFrame]:
    df = pd.read_parquet(info["path"])
    if df.empty:
        return None

    # Rename date → time
    if "date" in df.columns:
        df = df.rename(columns={"date": "time"})
    elif "time" not in df.columns:
        log.warning(f"  No time/date column in {info['path'].name}, skipping")
        return None

    df["time"] = pd.to_datetime(df["time"], utc=True, errors="coerce")
    df = df.dropna(subset=["time"])

    df["exchange"] = info["exchange"]
    df["symbol"] = info["symbol"]
    df["interval"] = info["interval"]

    # Ensure oi column exists
    if "oi" not in df.columns:
        df["oi"] = None

    cols = ["time", "exchange", "symbol", "interval", "open", "high", "low", "close", "volume", "oi"]
    df = df[[c for c in cols if c in df.columns]]
    return df


INSERT_SQL = """
    INSERT INTO zerodha_ohlcv (time, exchange, symbol, interval, open, high, low, close, volume, oi)
    VALUES %s
    ON CONFLICT DO NOTHING
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", default=DATA_ROOT_DEFAULT)
    parser.add_argument("--db-host", default="192.168.0.201")
    parser.add_argument("--db-port", type=int, default=5432)
    parser.add_argument("--db-name", default="market_data")
    parser.add_argument("--db-user", default="postgres")
    parser.add_argument("--db-pass", default="postgres")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    data_root = Path(args.data_root)
    progress_path = data_root / PROGRESS_FILE
    progress = load_progress(progress_path)
    processed: set = set(progress.get("processed_files", []))
    total_inserted = progress.get("total_inserted", 0)

    log.info("Discovering zerodha_intraday parquet files...")
    all_files = discover_files(data_root)
    pending = [f for f in all_files if str(f["path"]) not in processed]
    log.info(f"Total files: {len(all_files)} | Pending: {len(pending)} | Already done: {len(processed)}")

    if not pending:
        log.info("Nothing to do.")
        return

    conn = None if args.dry_run else get_connection(
        args.db_host, args.db_port, args.db_name, args.db_user, args.db_pass
    )

    for i, info in enumerate(pending, 1):
        fpath = info["path"]
        log.info(f"[{i}/{len(pending)}] {fpath.relative_to(data_root)} ({info['exchange']} {info['symbol']} {info['interval']})")

        if args.dry_run:
            log.info("  [DRY RUN]")
            processed.add(str(fpath))
            continue

        try:
            df = load_file(info)
            if df is None or df.empty:
                log.warning("  Empty after load, skipping")
                processed.add(str(fpath))
                continue

            rows = [tuple(r) for r in df.itertuples(index=False, name=None)]
            inserted = 0
            cur = conn.cursor()
            for start in range(0, len(rows), BATCH_SIZE):
                batch = rows[start:start + BATCH_SIZE]
                execute_values(cur, INSERT_SQL, batch)
                inserted += cur.rowcount if cur.rowcount > 0 else len(batch)
            conn.commit()
            cur.close()

            total_inserted += inserted
            log.info(f"  Inserted {inserted:,} rows | running total: {total_inserted:,}")
            processed.add(str(fpath))

            # Save progress every 100 files
            if i % 100 == 0:
                save_progress(progress_path, {
                    "processed_files": list(processed),
                    "total_inserted": total_inserted,
                })

        except Exception as e:
            log.error(f"  FAILED: {e}")
            conn.rollback()
            save_progress(progress_path, {
                "processed_files": list(processed),
                "total_inserted": total_inserted,
            })
            log.error("  Progress saved. Re-run to resume.")
            sys.exit(1)

    save_progress(progress_path, {
        "processed_files": list(processed),
        "total_inserted": total_inserted,
    })

    if conn:
        conn.close()

    log.info(f"\n=== Done === Total inserted: {total_inserted:,}")


if __name__ == "__main__":
    main()
