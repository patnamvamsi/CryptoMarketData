#!/usr/bin/env python3
"""
GDELT 2.0 GKG News Sentiment Ingestion
========================================
Downloads GDELT Global Knowledge Graph (GKG) 15-minute files and filters
for India-relevant news. Extracts tone/sentiment scores and saves as Parquet.

No API key required — GDELT is completely free and open.

Master file list: http://data.gdeltproject.org/gdeltv2/masterfilelist.txt
Each entry: <size> <hash> <url>  (URL ends with .gkg.csv.zip)

GKG columns (27 tab-separated fields):
  GKGRECORDID, DATE, SourceCollectionIdentifier, SourceCommonName,
  DocumentIdentifier, Counts, V2Counts, Themes, V2Themes, Locations,
  V2Locations, Persons, V2Persons, Organizations, V2Organizations,
  V2Tone, Dates, GCAM, SharingImage, RelatedImages, SocialImageEmbeds,
  SocialVideoEmbeds, Quotations, AllNames, Amounts, TranslationInfo, Extras

V2Tone: Tone,PositiveScore,NegativeScore,Polarity,ActivityReferenceDensity,
        SelfReferenceDensity,WordCount

Usage:
    python -m app.ingest.gdelt_ingest --backfill
    python -m app.ingest.gdelt_ingest --latest
    python -m app.ingest.gdelt_ingest --backfill --start 2024-01-01
"""

import io
import logging
import sys
import time
import zipfile
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)

# ============================================================================
# CONFIG
# ============================================================================
DATA_ROOT = Path("/media/vboxuser/test/NSE_Data/gdelt_sentiment")
PROGRESS_DIR = Path("/media/vboxuser/test/NSE_Data")

MASTER_FILE_URL = "http://data.gdeltproject.org/gdeltv2/masterfilelist.txt"
BACKFILL_YEARS = 2   # fetch last N years

REQUEST_DELAY = 0.5   # seconds between file downloads
MAX_RETRIES = 3

# India-relevance filters
INDIA_LOCATION_TOKENS = ["India", "INDIA", "IN#"]
INDIA_SOURCE_KEYWORDS = [
    "times of india", "hindustan times", "ndtv", "the hindu",
    "economic times", "business standard", "livemint", "mint",
    "moneycontrol", "zee news", "india today", "firstpost",
    "theprint", "scroll", "wire", "deccan", "tribune",
]

# GKG column names (tab-separated, 27 fields)
GKG_COLUMNS = [
    "GKGRECORDID", "DATE", "SourceCollectionIdentifier", "SourceCommonName",
    "DocumentIdentifier", "Counts", "V2Counts", "Themes", "V2Themes",
    "Locations", "V2Locations", "Persons", "V2Persons", "Organizations",
    "V2Organizations", "V2Tone", "Dates", "GCAM", "SharingImage",
    "RelatedImages", "SocialImageEmbeds", "SocialVideoEmbeds", "Quotations",
    "AllNames", "Amounts", "TranslationInfo", "Extras",
]


# ============================================================================
# PROGRESS TRACKING
# ============================================================================
def _progress_path() -> Path:
    return PROGRESS_DIR / "gdelt_progress.json"


def load_progress() -> dict:
    import json
    p = _progress_path()
    if p.exists():
        try:
            with open(p) as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "started_at": datetime.now().isoformat(),
        "completed_files": [],   # list of filename stems already processed
        "stats": {"files_processed": 0, "rows_saved": 0, "errors": 0},
    }


def save_progress(prog: dict):
    import json
    p = _progress_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    prog["updated_at"] = datetime.now().isoformat()
    with open(p, "w") as f:
        json.dump(prog, f, indent=2, default=str)


def is_file_done(prog: dict, filename: str) -> bool:
    return filename in prog.get("completed_files", [])


def mark_file_done(prog: dict, filename: str, rows: int):
    if filename not in prog["completed_files"]:
        prog["completed_files"].append(filename)
    prog["stats"]["files_processed"] = prog["stats"].get("files_processed", 0) + 1
    prog["stats"]["rows_saved"] = prog["stats"].get("rows_saved", 0) + rows


# ============================================================================
# MASTER FILE LIST
# ============================================================================
def fetch_master_file_list(cutoff_date: date) -> list[tuple[str, str]]:
    """
    Download the GDELT master file list and return GKG entries newer than cutoff_date.
    Returns list of (filename_stem, url) tuples, sorted oldest-first.
    """
    logger.info(f"Downloading GDELT master file list (cutoff: {cutoff_date})...")

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(MASTER_FILE_URL, timeout=60)
            resp.raise_for_status()
            break
        except Exception as e:
            logger.warning(f"Master list fetch attempt {attempt}: {e}")
            if attempt == MAX_RETRIES:
                raise
            time.sleep(attempt * 5)

    lines = resp.text.strip().splitlines()
    logger.info(f"Master list has {len(lines):,} total entries")

    gkg_entries = []
    cutoff_str = cutoff_date.strftime("%Y%m%d")

    for line in lines:
        parts = line.strip().split()
        if len(parts) < 3:
            continue
        url = parts[-1]
        if ".gkg.csv.zip" not in url:
            continue

        # Extract datetime from filename: 20230101000000.gkg.csv.zip
        fname = url.split("/")[-1]
        date_part = fname.split(".")[0]  # "20230101000000"
        if len(date_part) < 8:
            continue

        if date_part[:8] < cutoff_str:
            continue

        gkg_entries.append((fname, url))

    # Sort oldest-first
    gkg_entries.sort(key=lambda x: x[0])
    logger.info(f"Found {len(gkg_entries):,} GKG files since {cutoff_date}")
    return gkg_entries


# ============================================================================
# INDIA RELEVANCE FILTER
# ============================================================================
def is_india_relevant(row: pd.Series) -> bool:
    """Return True if the GKG row is relevant to India."""
    # Check V2Locations for India
    v2loc = str(row.get("V2Locations", "") or "")
    if any(token in v2loc for token in INDIA_LOCATION_TOKENS):
        return True

    # Check Locations column (V1)
    loc = str(row.get("Locations", "") or "")
    if "India" in loc:
        return True

    # Check SourceCommonName
    source = str(row.get("SourceCommonName", "") or "").lower()
    if any(kw in source for kw in INDIA_SOURCE_KEYWORDS):
        return True

    # Check Themes for India-tagged economic content
    themes = str(row.get("Themes", "") or "")
    v2themes = str(row.get("V2Themes", "") or "")
    all_themes = themes + ";" + v2themes
    if "INDIA" in all_themes.upper() or ("ECON" in all_themes and "India" in v2loc):
        return True

    return False


def filter_india_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Filter DataFrame to India-relevant rows."""
    if df.empty:
        return df
    mask = df.apply(is_india_relevant, axis=1)
    return df[mask].copy()


# ============================================================================
# TONE PARSING
# ============================================================================
def parse_v2tone(tone_str: str) -> dict:
    """
    Parse V2Tone field: Tone,PositiveScore,NegativeScore,Polarity,
    ActivityReferenceDensity,SelfReferenceDensity,WordCount
    """
    result = {
        "tone": None, "positive_score": None, "negative_score": None,
        "polarity": None, "activity_ref_density": None,
        "self_ref_density": None, "word_count": None,
    }
    if not tone_str or pd.isna(tone_str):
        return result

    parts = str(tone_str).split(",")
    keys = [
        "tone", "positive_score", "negative_score", "polarity",
        "activity_ref_density", "self_ref_density", "word_count",
    ]
    for i, key in enumerate(keys):
        if i < len(parts):
            try:
                result[key] = float(parts[i])
            except (ValueError, TypeError):
                pass
    return result


def _safe_split_list(value: str, delimiter: str = ";") -> list:
    """Split a delimited string into a cleaned list, skipping empty parts."""
    if not value or pd.isna(value):
        return []
    return [p.strip() for p in str(value).split(delimiter) if p.strip()]


# ============================================================================
# FILE PROCESSING
# ============================================================================
def process_gkg_file(url: str) -> Optional[pd.DataFrame]:
    """
    Download and process one GDELT GKG zip file.
    Returns a filtered, normalized DataFrame or None on failure.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            time.sleep(REQUEST_DELAY)
            resp = requests.get(url, timeout=60)
            if resp.status_code == 404:
                logger.warning(f"404 for {url} — skipping")
                return pd.DataFrame()
            resp.raise_for_status()

            # Unzip in memory
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                csv_files = [n for n in zf.namelist() if n.endswith(".csv")]
                if not csv_files:
                    return pd.DataFrame()
                with zf.open(csv_files[0]) as f:
                    df = pd.read_csv(
                        f,
                        sep="\t",
                        header=None,
                        names=GKG_COLUMNS,
                        dtype=str,
                        on_bad_lines="skip",
                        encoding="utf-8",
                        low_memory=False,
                    )

            # Filter to India-relevant rows immediately (saves memory)
            df = filter_india_rows(df)

            if df.empty:
                return df

            return df

        except zipfile.BadZipFile:
            logger.warning(f"Bad zip file: {url} (attempt {attempt})")
            if attempt == MAX_RETRIES:
                return None
            time.sleep(attempt * 3)

        except requests.exceptions.Timeout:
            logger.warning(f"Timeout on {url} (attempt {attempt})")
            if attempt == MAX_RETRIES:
                return None
            time.sleep(attempt * 5)

        except Exception as e:
            logger.error(f"Error processing {url} (attempt {attempt}): {e}")
            if attempt == MAX_RETRIES:
                return None
            time.sleep(attempt * 3)

    return None


def normalize_gkg(df: pd.DataFrame) -> pd.DataFrame:
    """Extract clean columns from the raw GKG DataFrame."""
    if df.empty:
        return pd.DataFrame()

    records = []
    for _, row in df.iterrows():
        # Parse datetime from DATE field: YYYYMMDDHHMMSS
        date_str = str(row.get("DATE", "") or "")
        try:
            dt = datetime.strptime(date_str[:14], "%Y%m%d%H%M%S")
        except Exception:
            dt = None

        # Parse tone
        tone_data = parse_v2tone(row.get("V2Tone", ""))

        # Parse list fields
        themes = _safe_split_list(row.get("V2Themes", "") or row.get("Themes", ""))
        locations = _safe_split_list(row.get("V2Locations", "") or row.get("Locations", ""))
        persons = _safe_split_list(row.get("V2Persons", "") or row.get("Persons", ""))
        orgs = _safe_split_list(row.get("V2Organizations", "") or row.get("Organizations", ""))

        records.append({
            "datetime": dt,
            "date": dt.date() if dt else None,
            "source": str(row.get("SourceCommonName", "") or ""),
            "url": str(row.get("DocumentIdentifier", "") or ""),
            "tone": tone_data["tone"],
            "positive_score": tone_data["positive_score"],
            "negative_score": tone_data["negative_score"],
            "polarity": tone_data["polarity"],
            "activity_ref_density": tone_data["activity_ref_density"],
            "self_ref_density": tone_data["self_ref_density"],
            "word_count": tone_data["word_count"],
            "themes": themes,
            "locations": locations,
            "persons": persons,
            "organizations": orgs,
            "gkg_record_id": str(row.get("GKGRECORDID", "") or ""),
        })

    out = pd.DataFrame(records)
    return out


# ============================================================================
# STORAGE
# ============================================================================
def save_day_parquet(df: pd.DataFrame, day: date):
    """Append/merge rows for a given day into YYYY/MM/DD.parquet."""
    if df.empty:
        return 0

    out_dir = DATA_ROOT / str(day.year) / f"{day.month:02d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{day.day:02d}.parquet"

    # Merge with existing
    if out_path.exists():
        try:
            existing = pd.read_parquet(out_path)
            df = pd.concat([existing, df], ignore_index=True)
            # Dedup on record ID (fast) or url+datetime
            if "gkg_record_id" in df.columns and df["gkg_record_id"].notna().any():
                df = df.drop_duplicates(subset=["gkg_record_id"])
            else:
                df = df.drop_duplicates(subset=["url", "datetime"], keep="last")
        except Exception as e:
            logger.warning(f"Could not merge with existing {out_path}: {e}")

    df.to_parquet(out_path, index=False, engine="pyarrow")
    return len(df)


# ============================================================================
# INGESTION RUNNERS
# ============================================================================
def run_backfill(start: Optional[date] = None):
    """
    Download all GDELT GKG files for the last 2 years (or since `start`).
    Resume-safe: skips already-processed files.
    """
    prog = load_progress()

    cutoff = start or (date.today() - timedelta(days=BACKFILL_YEARS * 365))
    entries = fetch_master_file_list(cutoff)

    total_files = len(entries)
    processed = 0
    skipped = 0
    errors = 0

    for i, (fname, url) in enumerate(entries):
        if is_file_done(prog, fname):
            skipped += 1
            continue

        logger.info(f"[{i+1}/{total_files}] Processing {fname}")
        raw_df = process_gkg_file(url)

        if raw_df is None:
            logger.error(f"Failed to process {fname}")
            prog["stats"]["errors"] = prog["stats"].get("errors", 0) + 1
            errors += 1
            save_progress(prog)
            continue

        if raw_df.empty:
            mark_file_done(prog, fname, 0)
            processed += 1
            if processed % 50 == 0:
                save_progress(prog)
            continue

        clean_df = normalize_gkg(raw_df)
        rows_saved = 0

        if not clean_df.empty and "date" in clean_df.columns:
            for day, group in clean_df.groupby("date"):
                if day is not None:
                    rows_saved += save_day_parquet(group, day)

        mark_file_done(prog, fname, rows_saved)
        processed += 1

        if processed % 20 == 0:
            save_progress(prog)
            logger.info(
                f"Progress: {processed}/{total_files - skipped} processed "
                f"({skipped} skipped, {errors} errors)"
            )

    save_progress(prog)
    logger.info(
        f"Backfill complete: {processed} files processed, "
        f"{skipped} skipped, {errors} errors"
    )
    return {"processed": processed, "skipped": skipped, "errors": errors}


def run_latest():
    """
    Fetch only the most recent GKG files (last 24 hours).
    For scheduled runs every 6 hours.
    """
    prog = load_progress()

    cutoff = date.today() - timedelta(days=1)
    entries = fetch_master_file_list(cutoff)

    rows_total = 0
    for fname, url in entries:
        if is_file_done(prog, fname):
            continue

        logger.info(f"Latest run: processing {fname}")
        raw_df = process_gkg_file(url)
        if raw_df is None:
            prog["stats"]["errors"] = prog["stats"].get("errors", 0) + 1
            continue

        if raw_df.empty:
            mark_file_done(prog, fname, 0)
            continue

        clean_df = normalize_gkg(raw_df)
        if not clean_df.empty and "date" in clean_df.columns:
            for day, group in clean_df.groupby("date"):
                if day is not None:
                    rows_total += save_day_parquet(group, day)

        mark_file_done(prog, fname, rows_total)

    save_progress(prog)
    logger.info(f"Latest run complete: {rows_total} rows saved")
    return {"rows": rows_total}


# ============================================================================
# MAIN
# ============================================================================
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="GDELT GKG News Sentiment Ingestion")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--backfill", action="store_true", help="Download last 2 years of GKG data")
    mode.add_argument("--latest", action="store_true", help="Fetch latest 15-min files (last 24h)")
    parser.add_argument(
        "--start", default=None,
        help="Override backfill start date YYYY-MM-DD (e.g. 2024-01-01)"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(
                Path("/media/vboxuser/test/NSE_Data") / "gdelt_ingest.log"
            ),
        ],
    )

    if args.backfill:
        start_date = (
            datetime.strptime(args.start, "%Y-%m-%d").date() if args.start else None
        )
        result = run_backfill(start=start_date)
        print(f"Done: {result}")
    elif args.latest:
        result = run_latest()
        print(f"Done: {result}")
