"""
startup_loader.py — On-startup fallback recovery.

Scans pending Parquet fallback files for each table and loads them into
the DB in chronological order before the service begins normal operation.

Usage (from app/main.py startup):
    from app.ingest.startup_loader import run_startup_sync
    from pathlib import Path

    run_startup_sync(get_conn_factory(), Path(config.DATA_ROOT_DIR))
"""

import logging
from pathlib import Path
from typing import Callable, Optional

import pandas as pd
from psycopg2.extras import execute_values

log = logging.getLogger("startup_loader")

# Default tables to scan (mirrors schema.py + existing NSE tables)
DEFAULT_TABLES = [
    "crypto_ohlcv",
    "zerodha_ohlcv",
    "corporate_events",
    "fii_dii_fo",
    "global_signals",
    "fundamentals",
    "fundamentals_quarterly",
    "fundamentals_info",
    "gdelt_sentiment",
    "options_iv",
    "nse_equity_daily",
    "nse_fo_daily",
    "nse_index_daily",
]

BATCH_SIZE = 50_000


def run_startup_sync(
    conn_factory: Callable,
    fallback_dir: Path,
    tables: Optional[list] = None,
) -> None:
    """
    On service start: for each table, find pending fallback Parquet files,
    load them into the DB in batches of BATCH_SIZE rows, then archive.

    Parameters
    ----------
    conn_factory : callable
        Returns a new psycopg2 connection each time it is called.
    fallback_dir : Path
        Root directory for fallback files.  Structure expected:
            {fallback_dir}/{table}/pending/*.parquet
            {fallback_dir}/{table}/archive/
    tables : list[str] or None
        Tables to scan.  Defaults to DEFAULT_TABLES.
    """
    fallback_dir = Path(fallback_dir)
    if tables is None:
        tables = DEFAULT_TABLES

    log.info("=== startup_loader: scanning fallback dirs under %s ===", fallback_dir)
    grand_total = 0

    for table in tables:
        pending_dir = fallback_dir / table / "pending"
        archive_dir = fallback_dir / table / "archive"

        if not pending_dir.exists():
            log.debug("No pending dir for table=%s, skipping", table)
            continue

        # Sort files chronologically by name (timestamps in filenames)
        files = sorted(pending_dir.glob("*.parquet"))
        if not files:
            log.debug("No pending files for table=%s", table)
            continue

        log.info("table=%s — found %d pending file(s)", table, len(files))
        archive_dir.mkdir(parents=True, exist_ok=True)
        table_total = 0

        for fpath in files:
            try:
                rows_loaded = _load_file(conn_factory, fpath, table)
                table_total += rows_loaded
                grand_total += rows_loaded

                # Archive
                dest = archive_dir / fpath.name
                fpath.rename(dest)
                log.info(
                    "table=%s — loaded %d rows from %s → archived",
                    table, rows_loaded, fpath.name,
                )
            except Exception as e:
                log.error(
                    "table=%s — ERROR loading %s: %s — skipping file",
                    table, fpath.name, e,
                )
                # Continue processing remaining files

        log.info("table=%s — total rows loaded this startup: %d", table, table_total)

    log.info("=== startup_loader complete — grand total rows loaded: %d ===", grand_total)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_file(conn_factory: Callable, fpath: Path, table: str) -> int:
    """
    Load a single Parquet file into the given table.
    Uses batches of BATCH_SIZE rows.  Returns total inserted row count.
    """
    df = pd.read_parquet(fpath)
    if df.empty:
        log.warning("Empty file: %s — nothing to load", fpath.name)
        return 0

    records = df.to_dict(orient="records")
    total_inserted = 0

    columns = list(records[0].keys())
    col_list = ", ".join(columns)
    insert_sql = f"""
        INSERT INTO {table} ({col_list})
        VALUES %s
        ON CONFLICT DO NOTHING
    """

    conn = conn_factory()
    try:
        for batch_start in range(0, len(records), BATCH_SIZE):
            batch = records[batch_start : batch_start + BATCH_SIZE]
            rows = _to_tuples(batch)

            with conn.cursor() as cur:
                execute_values(cur, insert_sql, rows, page_size=10_000)
                inserted = cur.rowcount if cur.rowcount >= 0 else len(rows)
            conn.commit()
            total_inserted += inserted

            log.debug(
                "table=%s — batch %d-%d: inserted %d rows",
                table, batch_start, batch_start + len(batch), inserted,
            )
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return total_inserted


def _to_tuples(records: list[dict]) -> list[tuple]:
    """Convert list of dicts to list of tuples, replacing NaN/NaT with None."""
    result = []
    for rec in records:
        row = tuple(
            None if (v is pd.NaT or (isinstance(v, float) and pd.isna(v))) else v
            for v in rec.values()
        )
        result.append(row)
    return result
