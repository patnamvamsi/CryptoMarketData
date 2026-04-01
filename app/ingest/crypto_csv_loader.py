"""
crypto_csv_loader.py
--------------------
Bulk-loads Binance 1m OHLCV CSV files into the crypto_ohlcv hypertable.

Folder structure:
  /media/vboxuser/test/1minute/{SYMBOL}/{SYMBOL}_{DD-Mon-YYYY}_{DD-Mon-YYYY}

CSV columns (no header, Binance kline format):
  open_time(unix ms), open, high, low, close, volume,
  close_time, quote_volume, trades,
  taker_buy_base_vol, taker_buy_quote_vol, ignore

Target table: crypto_ohlcv (time, symbol, open, high, low, close, volume,
                              quote_volume, trades, taker_buy_base_vol, taker_buy_quote_vol)

Usage:
    python3 -m app.ingest.crypto_csv_loader [--data-root /path] [--db-host ...] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("crypto_csv_loader")

DATA_ROOT_DEFAULT = "/media/vboxuser/test/1minute"
PROGRESS_FILE = "/media/vboxuser/test/NSE_Data/crypto_csv_loader_progress.json"
BATCH_SIZE = 100_000

CSV_COLS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "trades",
    "taker_buy_base_vol", "taker_buy_quote_vol", "ignore",
]

INSERT_SQL = """
    INSERT INTO crypto_ohlcv
        (time, exchange, symbol, interval, open, high, low, close, volume,
         quote_volume, trades, taker_buy_base_vol, taker_buy_quote_vol)
    VALUES %s
    ON CONFLICT DO NOTHING
"""


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
    """Walk 1minute/ and return all CSV files with their symbol."""
    results = []
    for symbol_dir in sorted(data_root.iterdir()):
        if not symbol_dir.is_dir():
            continue
        symbol = symbol_dir.name
        for csv_file in sorted(symbol_dir.iterdir()):
            if csv_file.is_file():
                results.append({"path": csv_file, "symbol": symbol})
    return results


def load_file(info: dict) -> pd.DataFrame:
    df = pd.read_csv(
        info["path"],
        header=None,
        names=CSV_COLS,
        dtype={
            "open_time": "float64",
            "open": "float64", "high": "float64",
            "low": "float64", "close": "float64",
            "volume": "float64", "quote_volume": "float64",
            "trades": "float64",
            "taker_buy_base_vol": "float64",
            "taker_buy_quote_vol": "float64",
        },
        on_bad_lines="skip",
    )
    # open_time is unix ms → UTC timestamp
    df["time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True, errors="coerce")
    df = df.dropna(subset=["time"])
    df["symbol"] = info["symbol"]
    df["exchange"] = "BINANCE"
    df["interval"] = "1m"

    keep = ["time", "exchange", "symbol", "interval", "open", "high", "low", "close", "volume",
            "quote_volume", "trades", "taker_buy_base_vol", "taker_buy_quote_vol"]
    df = df[keep]
    return df


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
    progress_path = Path(PROGRESS_FILE)
    progress = load_progress(progress_path)
    processed: set = set(progress.get("processed_files", []))
    total_inserted = progress.get("total_inserted", 0)

    log.info("Discovering crypto 1m CSV files...")
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
        symbol = info["symbol"]
        log.info(f"[{i}/{len(pending)}] {symbol}/{fpath.name}")

        if args.dry_run:
            processed.add(str(fpath))
            continue

        try:
            df = load_file(info)
            if df.empty:
                log.warning("  Empty, skipping")
                processed.add(str(fpath))
                continue

            rows = [tuple(r) for r in df.itertuples(index=False, name=None)]
            inserted = 0
            cur = conn.cursor()
            for start in range(0, len(rows), BATCH_SIZE):
                batch = rows[start:start + BATCH_SIZE]
                execute_values(cur, INSERT_SQL, batch)
                inserted += len(batch)
            conn.commit()
            cur.close()

            total_inserted += inserted
            log.info(f"  Inserted {inserted:,} rows | running total: {total_inserted:,}")
            processed.add(str(fpath))

            if i % 50 == 0:
                save_progress(progress_path, {
                    "processed_files": list(processed),
                    "total_inserted": total_inserted,
                })

        except Exception as e:
            log.error(f"  FAILED: {e}")
            if conn:
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
