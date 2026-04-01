"""
db_writer.py — Unified DataWriter for all ingesters.

Tries to write records to the database first.  On failure, writes to a
Parquet fallback file so no data is lost.  Pending fallback files can be
flushed back into the DB later (e.g. on service restart).

Usage:
    from app.db.db_writer import DataWriter, WriteResult
    from pathlib import Path

    writer = DataWriter(
        conn_factory = lambda: psycopg2.connect(...),
        fallback_dir = Path("/data/fallback"),
        table        = "crypto_ohlcv",
    )

    result = writer.write_batch(records)
    flushed = writer.flush_fallback()
"""

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

log = logging.getLogger("db_writer")

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class WriteResult:
    rows_attempted: int
    rows_inserted: int           # best-effort count from execute_values
    fallback_used: bool
    fallback_path: Optional[Path] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# DataWriter
# ---------------------------------------------------------------------------

class DataWriter:
    """
    Thread-safe writer that:
    1. Attempts a batch DB insert via execute_values / ON CONFLICT DO NOTHING.
    2. On any DB failure, writes to a Parquet fallback file.
    3. flush_fallback() reloads pending Parquet files into the DB.
    """

    def __init__(
        self,
        conn_factory: Callable,
        fallback_dir: Path,
        table: str,
    ) -> None:
        self.conn_factory = conn_factory
        self.fallback_dir = Path(fallback_dir)
        self.table = table
        self._lock = threading.Lock()

        # Ensure fallback dirs exist
        self._pending_dir.mkdir(parents=True, exist_ok=True)
        self._archive_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def _pending_dir(self) -> Path:
        return self.fallback_dir / self.table / "pending"

    @property
    def _archive_dir(self) -> Path:
        return self.fallback_dir / self.table / "archive"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def write_batch(self, records: list[dict]) -> WriteResult:
        """
        Try DB write first.  On failure, write to Parquet fallback.

        Returns a WriteResult describing what happened.
        """
        if not records:
            return WriteResult(rows_attempted=0, rows_inserted=0, fallback_used=False)

        try:
            inserted = self._write_to_db(records)
            return WriteResult(
                rows_attempted=len(records),
                rows_inserted=inserted,
                fallback_used=False,
            )
        except Exception as db_err:
            log.warning(
                "DB write failed for table=%s (%s rows): %s — activating fallback",
                self.table, len(records), db_err,
            )
            try:
                fpath = self._write_fallback(records)
                return WriteResult(
                    rows_attempted=len(records),
                    rows_inserted=0,
                    fallback_used=True,
                    fallback_path=fpath,
                    error=str(db_err),
                )
            except Exception as fb_err:
                log.error("Fallback write ALSO failed for table=%s: %s", self.table, fb_err)
                return WriteResult(
                    rows_attempted=len(records),
                    rows_inserted=0,
                    fallback_used=True,
                    error=f"DB: {db_err} | Fallback: {fb_err}",
                )

    def flush_fallback(self) -> int:
        """
        Load all pending fallback Parquet files for this table into the DB.
        Archives each file after successful load.

        Returns total rows loaded.
        """
        pending_files = sorted(self._pending_dir.glob("*.parquet"))
        if not pending_files:
            log.debug("No pending fallback files for table=%s", self.table)
            return 0

        log.info(
            "flush_fallback: found %d pending file(s) for table=%s",
            len(pending_files), self.table,
        )
        total_loaded = 0

        for fpath in pending_files:
            try:
                df = pd.read_parquet(fpath)
                records = df.to_dict(orient="records")
                if not records:
                    log.warning("Empty fallback file, archiving: %s", fpath.name)
                    self._archive_file(fpath)
                    continue

                # Load in batches of 50k
                batch_size = 50_000
                file_loaded = 0
                for start in range(0, len(records), batch_size):
                    batch = records[start : start + batch_size]
                    inserted = self._write_to_db(batch)
                    file_loaded += inserted

                total_loaded += file_loaded
                log.info(
                    "Recovered %d rows from fallback file %s → table=%s",
                    file_loaded, fpath.name, self.table,
                )
                self._archive_file(fpath)

            except Exception as e:
                log.error("Failed to flush fallback file %s: %s — skipping", fpath.name, e)

        return total_loaded

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _write_to_db(self, records: list[dict]) -> int:
        """
        Insert records via execute_values with ON CONFLICT DO NOTHING.
        Returns best-effort inserted row count.
        """
        if not records:
            return 0

        columns = list(records[0].keys())
        col_list = ", ".join(columns)
        insert_sql = f"""
            INSERT INTO {self.table} ({col_list})
            VALUES %s
            ON CONFLICT DO NOTHING
        """

        rows = [
            tuple(
                None
                if (v is pd.NaT or (isinstance(v, float) and pd.isna(v)))
                else v
                for v in rec.values()
            )
            for rec in records
        ]

        conn = self.conn_factory()
        try:
            with conn.cursor() as cur:
                execute_values(cur, insert_sql, rows, page_size=10_000)
                inserted = cur.rowcount if cur.rowcount >= 0 else len(rows)
            conn.commit()
            return inserted
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _write_fallback(self, records: list[dict]) -> Path:
        """
        Write records to a timestamped Parquet file in the pending directory.
        Thread-safe.
        """
        ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        fpath = self._pending_dir / f"{ts}.parquet"

        with self._lock:
            df = pd.DataFrame(records)
            df.to_parquet(fpath, index=False)

        log.info(
            "Fallback: wrote %d rows to %s", len(records), fpath
        )
        return fpath

    def _archive_file(self, fpath: Path) -> None:
        """Move a processed pending file to the archive directory."""
        dest = self._archive_dir / fpath.name
        fpath.rename(dest)
        log.debug("Archived fallback file: %s → %s", fpath.name, self._archive_dir)
