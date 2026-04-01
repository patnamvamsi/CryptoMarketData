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
from typing import Optional, Union

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
    },

    # NSE equity daily (matches actual parquet + DB columns)
    "equity_daily": {
        "table": "nse_equity_daily",
        "parquet_glob": "parquet/equity_daily/year=*/*.parquet",
        "columns": [
            "time",
            "symbol",
            "series",
            "open",
            "high",
            "low",
            "close",
            "last",
            "prev_close",
            "volume",
            "turnover",
            "trades",
            "deliverable_qty",
            "delivery_pct",
        ],
        "date_cols": ["time"],
        "conflict_cols": ["time", "symbol"],
    },

    # NSE index daily
    "index_daily": {
        "table": "nse_index_daily",
        "parquet_glob": "parquet/index_daily/year=*/*.parquet",
        "columns": [
            "time",
            "index_name",
            "open",
            "high",
            "low",
            "close",
            "points_change",
            "pct_change",
            "volume",
            "turnover_cr",
            "pe",
            "pb",
            "div_yield",
        ],
        "date_cols": ["time"],
        "conflict_cols": ["time", "index_name"],
    },

    # Corporate events — dividends & splits share the same target table.
    # Both parquet files have [ex_date, amount/ratio, symbol, event_type].
    # We normalise to the target schema: ex_date, symbol, event_type, amount.
    "corporate_events": {
        "table": "corporate_events",
        "static": True,  # single flat file, no date in filename
        # glob picks up both dividends.parquet and splits.parquet
        "parquet_glob": "corporate_events/*.parquet",
        "columns": [
            "ex_date",
            "symbol",
            "event_type",
            "amount",
        ],
        "date_cols": ["ex_date"],
        "conflict_cols": ["ex_date", "symbol", "event_type"],
        # special: rename 'ratio' → 'amount' for splits (handled in load_parquet override)
        "_rename": {"ratio": "amount"},
    },

    # FII/DII participation data
    # FII/DII cash daily — columns: client_type, date, buy_value_cr, sell_value_cr, net_value_cr
    # NOTE: fao_participant_oi.parquet and fao_participant_vol.parquet have different schemas
    # and are not loaded here (future: separate tables for OI/vol data)
    "fii_dii": {
        "table": "fii_dii_fo",
        "static": True,
        "parquet_glob": "fii_dii/cash_fiidii_daily.parquet",
        "columns": [
            "date",          # → time
            "client_type",   # → category
            "buy_value_cr",  # → buy_value
            "sell_value_cr", # → sell_value
            "net_value_cr",  # → net_value
        ],
        "date_cols": ["date"],
        "conflict_cols": ["time", "category"],
        "_rename": {
            "date": "time",
            "client_type": "category",
            "buy_value_cr": "buy_value",
            "sell_value_cr": "sell_value",
            "net_value_cr": "net_value",
        },
    },

    # Global signals
    # Source: date, signal, ticker, open, high, low, close, volume
    # Target: time, signal_name, value, source
    # We map: date→time, signal→signal_name, close→value, ticker→source
    "global_signals": {
        "table": "global_signals",
        "static": True,
        "parquet_glob": "global_signals/global_signals.parquet",
        "columns": [
            "date",   # → time
            "signal", # → signal_name
            "close",  # → value
            "ticker", # → source
        ],
        "date_cols": ["date"],
        "conflict_cols": ["time", "signal_name"],
        "_rename": {
            "date": "time",
            "signal": "signal_name",
            "close": "value",
            "ticker": "source",
        },
    },

    # Fundamentals
    # Source has many yfinance columns; map to the fundamentals schema
    "fundamentals": {
        "table": "fundamentals",
        "static": True,
        # Only valuation_snapshot has the right columns; earnings_calendar has different schema
        "parquet_glob": "fundamentals/valuation_snapshot.parquet",
        "columns": [
            "snapshot_date",   # → as_of_date
            "symbol",
            "trailingPE",      # → pe_ratio
            "priceToBook",     # → pb_ratio
            "dividendYield",   # → div_yield
            "marketCap",       # → market_cap
            "totalRevenue",    # → revenue
            "netIncomeToCommon",# → net_income
            "debtToEquity",    # → debt_equity
        ],
        "date_cols": ["snapshot_date"],
        "conflict_cols": ["as_of_date", "symbol"],
        "_rename": {
            "snapshot_date": "as_of_date",
            "trailingPE": "pe_ratio",
            "priceToBook": "pb_ratio",
            "dividendYield": "div_yield",
            "marketCap": "market_cap",
            "totalRevenue": "revenue",
            "netIncomeToCommon": "net_income",
            "debtToEquity": "debt_equity",
        },
    },
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


def get_max_time(conn, table: str, time_col: str = "time") -> Optional[date]:
    with conn.cursor() as cur:
        cur.execute(f"SELECT MAX({time_col}) FROM {table}")
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
    """Return sorted list of (file_date, path) for all matching parquet files.
    For files with no date in the name (e.g. dividends.parquet), use date.min
    so they are always considered for loading."""
    files = []
    for p in data_root.glob(glob_pattern):
        d = extract_date_from_filename(p)
        if d is None:
            d = date.min  # static files — always include, sort first
        files.append((d, p))
    files.sort(key=lambda x: x[0])
    return files


def load_parquet(
    path: Path,
    columns: list[str],
    date_cols: list[str],
    rename_map: Optional[dict] = None,
) -> pd.DataFrame:
    df = pd.read_parquet(path)

    # Apply column renames first so subsequent lookups use source names
    if rename_map:
        df = df.rename(columns=rename_map)

    # After rename, columns list may use the *new* names already — rebuild
    # the effective column list: prefer new names if rename occurred
    effective_columns = []
    for col in columns:
        new_col = rename_map.get(col, col) if rename_map else col
        effective_columns.append(new_col)

    # Ensure all expected columns exist (after rename)
    for col in effective_columns:
        if col not in df.columns:
            raise ValueError(f"Missing column '{col}' in {path}")

    df = df[effective_columns].copy()

    # Parse date columns (use renamed name)
    for col in date_cols:
        renamed_col = rename_map.get(col, col) if rename_map else col
        if renamed_col in df.columns:
            df[renamed_col] = pd.to_datetime(df[renamed_col], errors="coerce").dt.date

    # Determine the primary time column (first date_col after rename)
    if date_cols:
        primary_time_col = rename_map.get(date_cols[0], date_cols[0]) if rename_map else date_cols[0]
        if primary_time_col in df.columns:
            before = len(df)
            df = df.dropna(subset=[primary_time_col])
            dropped = before - len(df)
            if dropped:
                log.warning(f"  Dropped {dropped} rows with null '{primary_time_col}'")

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
    rename_map: Optional[dict] = cfg.get("_rename")

    # Determine the effective name of the primary time column (after any rename)
    primary_date_col_src = date_cols[0] if date_cols else "time"
    primary_time_col = rename_map.get(primary_date_col_src, primary_date_col_src) if rename_map else primary_date_col_src

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
        max_time = get_max_time(conn, table, time_col=primary_time_col)
        conn.close()

    log.info(f"DB MAX({primary_time_col}) = {max_time}")

    # Discover parquet files
    all_files = discover_files(data_root, cfg["parquet_glob"])
    log.info(f"Found {len(all_files)} total parquet files")

    # Filter: only files with date > max_time and not already processed
    # Static files (date.min) always bypass the max_time filter — they must be re-evaluated
    is_static = cfg.get("static", False)
    if max_time and not is_static:
        pending = [(d, p) for d, p in all_files if d > max_time and str(p) not in processed_files]
    else:
        pending = [(d, p) for d, p in all_files if str(p) not in processed_files]

    log.info(f"Pending files to ingest: {len(pending)}")
    if not pending:
        log.info("Nothing to do — DB is up to date.")
        return

    total_inserted = table_progress.get("total_inserted", 0)
    total_skipped = table_progress.get("total_skipped", 0)

    # Build INSERT SQL — use renamed column names for the DB INSERT
    effective_columns = [rename_map.get(c, c) if rename_map else c for c in columns]
    col_list = ", ".join(effective_columns)
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
            df = load_parquet(fpath, columns, date_cols, rename_map=rename_map)
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
    all_choices = list(TABLE_CONFIG.keys()) + ["all"]
    p = argparse.ArgumentParser(description="Resumable NSE parquet → TimescaleDB gap-fill")
    p.add_argument(
        "--table", required=True, choices=all_choices,
        help=f"Table key to sync, or 'all' to run every table. Choices: {all_choices}",
    )
    p.add_argument("--data-root", default=os.environ.get("NSE_DATA_ROOT", "/media/vboxuser/test/NSE_Data"))
    p.add_argument("--db-host", default=os.environ.get("DB_HOST", "192.168.0.201"))
    p.add_argument("--db-port", type=int, default=int(os.environ.get("DB_PORT", "5432")))
    p.add_argument("--db-name", default=os.environ.get("DB_NAME", "market_data"))
    p.add_argument("--db-user", default=os.environ.get("DB_USER", "postgres"))
    p.add_argument("--db-pass", default=os.environ.get("DB_PASS", "postgres"))
    p.add_argument("--dry-run", action="store_true", help="Scan files without writing to DB")
    p.add_argument("--batch-size", type=int, default=10000, help="Rows per execute_values page")
    return p.parse_args()


def main():
    args = parse_args()
    progress_path = Path(args.data_root) / "db_sync_progress.json"

    if args.table == "all":
        tables_to_run = list(TABLE_CONFIG.keys())
        log.info("Running all tables in sequence: %s", tables_to_run)
        for table_key in tables_to_run:
            try:
                ingest_table(args, TABLE_CONFIG[table_key], progress_path)
            except SystemExit:
                # ingest_table calls sys.exit(1) on per-file failure
                log.error("Table %s failed — continuing with next", table_key)
    else:
        cfg = TABLE_CONFIG[args.table]
        ingest_table(args, cfg, progress_path)


if __name__ == "__main__":
    main()
