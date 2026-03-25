"""
db_sync.py — Resumable gap-fill ingest for NSE hypertables.

Usage:
    python app/ingest/db_sync.py --table fo_daily

Flags:
    --table       Target table suffix: fo_daily (more to follow)
    --data-root   Override data root (default: /media/vboxuser/test/NSE_Data)
    --db-host     DB host (default: from env DB_HOST or 192.168.0.189)
    --db-port     DB port (default: 5432)
    --db-name     DB name (default: market_data_dev1)
    --db-user     DB user (default: postgres)
    --db-pass     DB password (default: password)
    --dry-run     Scan files and report what would be inserted, no DB writes
    --batch-size  Rows per execute_values call (default: 10000)

Progress is tracked in <data-root>/db_sync_progress.json — safe to resume after
any interruption.
"""

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("db_sync")


# ---------------------------------------------------------------------------
# Table configuration
# ---------------------------------------------------------------------------
TABLE_CONFIG = {
    "fo_daily": {
        "table": "nse_fo_daily",
        "parquet_glob": "parquet/fo_daily/year=*/fo_bhavcopy_*.parquet",
        # Column order must match the INSERT statement exactly
        "columns": [
            "time",
            "instrument",
            "symbol",
            "expiry",
            "strike",
            "option_type",
            "open",
            "high",
            "low",
            "close",
            "settle_price",
            "contracts",
            "value_lakh",
            "open_interest",
            "change_in_oi",
            "underlying_price",
        ],
        # Columns that are dates (need cast from string)
        "date_cols": ["time", "expiry"],
        # Conflict key — used for ON CONFLICT DO NOTHING
        # nse_fo_daily unique key: (time, instrument, symbol, expiry, strike, option_type)
        "conflict_cols": ["time", "instrument", "symbol", "expiry", "strike", "option_type"],
    }
}


# ---------------------------------------------------------------------------
# Progress tracking
# ---------------------------------------------------------------------------
def load_progress(path: Path) -> dict:
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


def save_progress(path: Path, state: dict) -> None:
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2, default=str)
    tmp.replace(path)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
def get_connection(args) -> psycopg2.extensions.connection:
    return psycopg2.connect(
        host=args.db_host,
        port=args.db_port,
        dbname=args.db_name,
        user=args.db_user,
        password=args.db_pass,
    )


def get_max_time(conn, table: str) -> Optional[date]:
    with conn.cursor() as cur:
        cur.execute(f"SELECT MAX(time) FROM {table}")
        row = cur.fetchone()
        return row[0] if row and row[0] else None


# ---------------------------------------------------------------------------
# Parquet helpers
# ---------------------------------------------------------------------------
def extract_date_from_filename(path: Path) -> Optional[date]:
    """Extract date from filenames like fo_bhavcopy_2024-01-05.parquet"""
    stem = path.stem  # e.g. fo_bhavcopy_2024-01-05
    parts = stem.rsplit("_", 1)
    if len(parts) == 2:
        try:
            return date.fromisoformat(parts[1])
        except ValueError:
            pass
    return None


def discover_files(data_root: Path, glob_pattern: str) -> list[tuple[date, Path]]:
    """Return sorted list of (file_date, path) for all matching parquet files."""
    files = []
    for p in data_root.glob(glob_pattern):
        d = extract_date_from_filename(p)
        if d is not None:
            files.append((d, p))
    files.sort(key=lambda x: x[0])
    return files


def load_parquet(path: Path, columns: list[str], date_cols: list[str]) -> pd.DataFrame:
    df = pd.read_parquet(path)

    # Ensure all expected columns exist
    for col in columns:
        if col not in df.columns:
            raise ValueError(f"Missing column '{col}' in {path}")

    df = df[columns].copy()

    # Parse date columns
    for col in date_cols:
        df[col] = pd.to_datetime(df[col], errors="coerce").dt.date

    # Drop rows where time is null (can't insert without a time)
    before = len(df)
    df = df.dropna(subset=["time"])
    dropped = before - len(df)
    if dropped:
        log.warning(f"  Dropped {dropped} rows with null 'time'")

    return df


def df_to_tuples(df: pd.DataFrame) -> list[tuple]:
    """Convert DataFrame to list of tuples, replacing pd.NA/NaT/NaN with None."""
    records = []
    for row in df.itertuples(index=False, name=None):
        records.append(
            tuple(None if (v is pd.NaT or (isinstance(v, float) and pd.isna(v))) else v for v in row)
        )
    return records


# ---------------------------------------------------------------------------
# Core ingest
# ---------------------------------------------------------------------------
def ingest_table(args, cfg: dict, progress_path: Path) -> None:
    data_root = Path(args.data_root)
    table = cfg["table"]
    columns = cfg["columns"]
    date_cols = cfg["date_cols"]

    log.info(f"=== db_sync: table={table} ===")

    # Load progress state
    progress = load_progress(progress_path)
    table_progress = progress.get(table, {})
    processed_files: set = set(table_progress.get("processed_files", []))

    # Connect and find max time
    if args.dry_run:
        log.info("[DRY RUN] Skipping DB connection for max-time query")
        max_time = None
    else:
        conn = get_connection(args)
        max_time = get_max_time(conn, table)
        conn.close()

    log.info(f"DB MAX(time) = {max_time}")

    # Discover parquet files
    all_files = discover_files(data_root, cfg["parquet_glob"])
    log.info(f"Found {len(all_files)} total parquet files")

    # Filter: only files with date > max_time and not already processed
    if max_time:
        pending = [(d, p) for d, p in all_files if d > max_time and str(p) not in processed_files]
    else:
        pending = [(d, p) for d, p in all_files if str(p) not in processed_files]

    log.info(f"Pending files to ingest: {len(pending)}")
    if not pending:
        log.info("Nothing to do — DB is up to date.")
        return

    total_inserted = table_progress.get("total_inserted", 0)
    total_skipped = table_progress.get("total_skipped", 0)

    # Build INSERT SQL
    col_list = ", ".join(columns)
    # Use ON CONFLICT DO NOTHING for idempotency
    insert_sql = f"""
        INSERT INTO {table} ({col_list})
        VALUES %s
        ON CONFLICT DO NOTHING
    """

    for i, (file_date, fpath) in enumerate(pending, 1):
        log.info(f"[{i}/{len(pending)}] {fpath.name} (date={file_date})")

        if args.dry_run:
            log.info("  [DRY RUN] Would load and insert")
            continue

        try:
            df = load_parquet(fpath, columns, date_cols)
            rows = df_to_tuples(df)

            if not rows:
                log.warning("  No rows after cleaning, skipping")
                processed_files.add(str(fpath))
                _update_and_save_progress(progress, table, processed_files, total_inserted, total_skipped, progress_path)
                continue

            # Batch insert
            conn = get_connection(args)
            try:
                with conn.cursor() as cur:
                    execute_values(cur, insert_sql, rows, page_size=args.batch_size)
                    inserted = cur.rowcount if cur.rowcount >= 0 else len(rows)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

            # Rough dupe count
            dupes = len(rows) - max(0, inserted)
            total_inserted += inserted
            total_skipped += dupes
            log.info(f"  Inserted {inserted:,} rows | dupes skipped: {dupes:,} | running total: {total_inserted:,}")

            processed_files.add(str(fpath))
            _update_and_save_progress(progress, table, processed_files, total_inserted, total_skipped, progress_path)

        except Exception as e:
            log.error(f"  FAILED on {fpath.name}: {e}")
            log.error("  Saving progress and aborting — re-run to resume from this file.")
            save_progress(progress_path, progress)
            sys.exit(1)

    log.info(f"\n=== Done ===")
    log.info(f"Total inserted: {total_inserted:,}")
    log.info(f"Total dupes skipped: {total_skipped:,}")
    save_progress(progress_path, progress)


def _update_and_save_progress(progress, table, processed_files, inserted, skipped, path):
    progress[table] = {
        "processed_files": sorted(processed_files),
        "total_inserted": inserted,
        "total_skipped": skipped,
        "last_updated": datetime.utcnow().isoformat(),
    }
    save_progress(path, progress)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args():
    p = argparse.ArgumentParser(description="Resumable NSE parquet → TimescaleDB gap-fill")
    p.add_argument("--table", required=True, choices=list(TABLE_CONFIG.keys()), help="Table to sync")
    p.add_argument("--data-root", default=os.environ.get("NSE_DATA_ROOT", "/media/vboxuser/test/NSE_Data"))
    p.add_argument("--db-host", default=os.environ.get("DB_HOST", "192.168.0.189"))
    p.add_argument("--db-port", type=int, default=int(os.environ.get("DB_PORT", "5432")))
    p.add_argument("--db-name", default=os.environ.get("DB_NAME", "market_data_dev1"))
    p.add_argument("--db-user", default=os.environ.get("DB_USER", "postgres"))
    p.add_argument("--db-pass", default=os.environ.get("DB_PASS", "password"))
    p.add_argument("--dry-run", action="store_true", help="Scan files without writing to DB")
    p.add_argument("--batch-size", type=int, default=10000, help="Rows per execute_values page")
    return p.parse_args()


def main():
    args = parse_args()
    cfg = TABLE_CONFIG[args.table]
    progress_path = Path(args.data_root) / "db_sync_progress.json"
    ingest_table(args, cfg, progress_path)


if __name__ == "__main__":
    main()
