"""
gdelt_loader.py — Bulk loader for GDELT sentiment data into the social_media DB.

GDELT data lives in a separate DB from market data:
  Host:   192.168.0.201:5432
  DB:     social_media
  Table:  gdelt_sentiment

File layout:
  /media/vboxuser/test/NSE_Data/gdelt_sentiment/{YYYY}/{MM}/{DD}.parquet

Parquet columns:
  datetime, date, source, url, tone, positive_score, negative_score,
  polarity, activity_ref_density, self_ref_density, word_count,
  themes, locations, persons, organizations, gkg_record_id

Usage:
    python -m app.ingest.gdelt_loader
    python -m app.ingest.gdelt_loader --dry-run
    python -m app.ingest.gdelt_loader --since 2026-01-01
"""

import argparse
import json
import logging
import os
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("gdelt_loader")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
GDELT_DATA_ROOT = Path("/media/vboxuser/test/NSE_Data/gdelt_sentiment")
PROGRESS_FILE   = GDELT_DATA_ROOT / "gdelt_load_progress.json"

DB_CONFIG = {
    "host":     os.environ.get("SOCIAL_DB_HOST", "192.168.0.201"),
    "port":     int(os.environ.get("SOCIAL_DB_PORT", "5432")),
    "dbname":   "social_media",
    "user":     os.environ.get("SOCIAL_DB_USER", "postgres"),
    "password": os.environ.get("SOCIAL_DB_PASS", "postgres"),
}

BATCH_SIZE = 50_000

INSERT_SQL = """
    INSERT INTO gdelt_sentiment (
        time, date, source, url, tone,
        positive_score, negative_score, polarity,
        activity_ref_density, self_ref_density, word_count,
        themes, locations, persons, organizations, gkg_record_id
    )
    SELECT * FROM (VALUES %s) AS v(
        time, date, source, url, tone,
        positive_score, negative_score, polarity,
        activity_ref_density, self_ref_density, word_count,
        themes, locations, persons, organizations, gkg_record_id
    )
    WHERE NOT EXISTS (
        SELECT 1 FROM gdelt_sentiment g
        WHERE g.gkg_record_id = v.gkg_record_id
    )
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_conn():
    return psycopg2.connect(**DB_CONFIG)


def discover_files(since: Optional[date] = None) -> list[tuple[date, Path]]:
    """Walk YYYY/MM/DD.parquet structure, return sorted (file_date, path) pairs."""
    files = []
    for year_dir in sorted(GDELT_DATA_ROOT.glob("[0-9][0-9][0-9][0-9]")):
        for month_dir in sorted(year_dir.glob("[0-9][0-9]")):
            for pfile in sorted(month_dir.glob("*.parquet")):
                try:
                    # filename is the day: DD.parquet
                    day = int(pfile.stem)
                    month = int(month_dir.name)
                    year = int(year_dir.name)
                    file_date = date(year, month, day)
                    if since and file_date < since:
                        continue
                    files.append((file_date, pfile))
                except (ValueError, TypeError):
                    log.warning(f"Skipping unrecognised file: {pfile}")
    return files


def load_progress() -> set:
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE) as f:
            return set(json.load(f).get("processed", []))
    return set()


def save_progress(processed: set) -> None:
    tmp = PROGRESS_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump({"processed": sorted(processed), "updated": datetime.utcnow().isoformat()}, f, indent=2)
    tmp.replace(PROGRESS_FILE)


def df_to_tuples(df: pd.DataFrame) -> list[tuple]:
    """Convert DataFrame rows to tuples, coercing NaN/NaT → None."""
    rows = []
    for row in df.itertuples(index=False, name=None):
        rows.append(tuple(
            None if (v is pd.NaT or (isinstance(v, float) and pd.isna(v))) else v
            for v in row
        ))
    return rows


def load_parquet(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)

    # Rename datetime → time for DB
    if "datetime" in df.columns:
        df = df.rename(columns={"datetime": "time"})

    # Ensure correct column order matching INSERT_SQL
    cols = [
        "time", "date", "source", "url", "tone",
        "positive_score", "negative_score", "polarity",
        "activity_ref_density", "self_ref_density", "word_count",
        "themes", "locations", "persons", "organizations", "gkg_record_id",
    ]
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"Missing columns in {path}: {missing}")

    df = df[cols].copy()

    # Convert time column
    df["time"] = pd.to_datetime(df["time"], errors="coerce", utc=True)

    # Convert list columns to PostgreSQL array format
    for arr_col in ["themes", "locations", "persons", "organizations"]:
        df[arr_col] = df[arr_col].apply(
            lambda x: list(x) if isinstance(x, (list, tuple)) else
                      (x.tolist() if hasattr(x, "tolist") else [])
        )

    return df.dropna(subset=["time"])


# ---------------------------------------------------------------------------
# Core loader
# ---------------------------------------------------------------------------

def get_max_time(conn) -> Optional[datetime]:
    with conn.cursor() as cur:
        cur.execute("SELECT MAX(time) FROM gdelt_sentiment")
        row = cur.fetchone()
        return row[0] if row and row[0] else None


def ingest_file(conn, path: Path, dry_run: bool = False) -> int:
    df = load_parquet(path)
    if df.empty:
        return 0

    if dry_run:
        log.info(f"  [DRY RUN] Would insert {len(df):,} rows")
        return len(df)

    rows = df_to_tuples(df)
    total_inserted = 0

    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i:i + BATCH_SIZE]
        with conn.cursor() as cur:
            execute_values(cur, INSERT_SQL, batch, page_size=BATCH_SIZE)
            inserted = max(cur.rowcount, 0)
        conn.commit()
        total_inserted += inserted

    return total_inserted


def run_load(since: Optional[date] = None, dry_run: bool = False) -> None:
    log.info("=== GDELT Sentiment Loader → social_media DB ===")
    log.info(f"Data root: {GDELT_DATA_ROOT}")

    files = discover_files(since=since)
    log.info(f"Found {len(files)} parquet files")

    processed = load_progress()
    pending = [(d, p) for d, p in files if str(p) not in processed]
    log.info(f"Pending (not yet loaded): {len(pending)}")

    if not pending:
        log.info("Nothing to do — all files already loaded.")
        return

    conn = get_conn()
    max_time = get_max_time(conn)
    log.info(f"Current MAX(time) in gdelt_sentiment: {max_time}")

    total_inserted = 0
    total_dupes = 0

    for i, (file_date, fpath) in enumerate(pending, 1):
        log.info(f"[{i}/{len(pending)}] {fpath} (date={file_date})")
        try:
            rows_before = total_inserted
            inserted = ingest_file(conn, fpath, dry_run=dry_run)
            dupes = 0  # ON CONFLICT DO NOTHING handles this
            total_inserted += inserted
            log.info(f"  → inserted {inserted:,} rows (running total: {total_inserted:,})")

            if not dry_run:
                processed.add(str(fpath))
                save_progress(processed)

        except Exception as e:
            log.error(f"  FAILED on {fpath}: {e}")
            log.error("  Skipping file and continuing...")
            try:
                conn.rollback()
            except Exception:
                pass
            continue

    conn.close()
    log.info(f"\n=== Done ===")
    log.info(f"Total inserted: {total_inserted:,}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(description="Load GDELT sentiment parquet files → social_media.gdelt_sentiment")
    p.add_argument("--since",    type=date.fromisoformat, default=None,
                   help="Only load files from this date onward (YYYY-MM-DD)")
    p.add_argument("--dry-run",  action="store_true", help="Scan files without writing to DB")
    p.add_argument("--data-root", default=str(GDELT_DATA_ROOT),
                   help=f"Override GDELT data root (default: {GDELT_DATA_ROOT})")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.data_root != str(GDELT_DATA_ROOT):
        GDELT_DATA_ROOT = Path(args.data_root)
    run_load(since=args.since, dry_run=args.dry_run)
