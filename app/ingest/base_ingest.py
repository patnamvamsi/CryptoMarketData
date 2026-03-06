"""
Base Ingester Class
===================
Extracts the shared boilerplate from equity/FO/index ingestion scripts:
  - Progress tracking (JSON file)
  - File saving (CSV + Parquet)
  - Optional DB loading (graceful degradation when offline)
  - Graceful shutdown via threading.Event
  - Retry logic with exponential backoff on consecutive failures
  - Business-day iteration with skip_existing support

Subclasses implement:
  - pipeline_name          (str)
  - csv_dir / parquet_dir  (Path)
  - csv_filename(date)     (str)
  - parquet_filename(date) (str)
  - progress_file          (Path)
  - table_name             (str)
  - table_columns          (list[str])
  - setup_table(conn)
  - fetch_data(date) -> DataFrame | None
  - normalize_data(df, date) -> DataFrame
  - load_to_db(df, conn) -> int
"""

import json
import logging
import os
import time
import threading
from abc import ABC, abstractmethod
from datetime import date, datetime, timedelta
from pathlib import Path
from random import uniform

import pandas as pd

try:
    import psycopg2
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '..', '.env'))

logger = logging.getLogger(__name__)

SHARE_DIR = Path("/media/vboxuser/test/NSE_Data")

TIMESCALE_HOST = os.getenv('TIMESCALE_HOST', 'localhost')
TIMESCALE_PORT = os.getenv('TIMESCALE_PORT', '5432')
TIMESCALE_USERNAME = os.getenv('TIMESCALE_USERNAME', 'postgres')
TIMESCALE_PASSWORD = os.getenv('TIMESCALE_PASSWORD', '')
TIMESCALE_MARKET_DATA_DB = os.getenv('TIMESCALE_MARKET_DATA_DB', 'market_data_dev1')

# ---------------------------------------------------------------------------
# Global registry – keeps track of running ingesters so the API / CLI can
# query status or request a stop.
# ---------------------------------------------------------------------------
_running_ingesters: dict[str, "BaseIngester"] = {}
_registry_lock = threading.Lock()


def get_running_ingesters() -> dict[str, "BaseIngester"]:
    with _registry_lock:
        return dict(_running_ingesters)


class BaseIngester(ABC):
    """Abstract base for all NSE ingestion pipelines."""

    # -- subclass must set these -------------------------------------------
    pipeline_name: str = "base"
    csv_dir: Path = SHARE_DIR
    parquet_dir: Path = SHARE_DIR
    progress_file: Path = SHARE_DIR / "progress.json"
    table_name: str = ""
    table_columns: list[str] = []

    min_delay: float = 0.5
    max_delay: float = 2.0

    def __init__(self):
        self._stop_event = threading.Event()
        self._status: dict = {
            "state": "idle",          # idle | running | stopping | finished | error
            "started_at": None,
            "current_date": None,
            "processed": 0,
            "skipped": 0,
            "failed": 0,
            "total_dates": 0,
            "error": None,
        }
        self._status_lock = threading.Lock()

    # -- abstract interface ------------------------------------------------

    @abstractmethod
    def csv_filename(self, trade_date: date) -> str: ...

    @abstractmethod
    def parquet_filename(self, trade_date: date) -> str: ...

    @abstractmethod
    def setup_table(self, conn) -> None: ...

    @abstractmethod
    def fetch_data(self, trade_date: date) -> pd.DataFrame | None: ...

    @abstractmethod
    def normalize_data(self, df: pd.DataFrame, trade_date: date) -> pd.DataFrame: ...

    @abstractmethod
    def load_to_db(self, df: pd.DataFrame, conn) -> int: ...

    # -- progress ----------------------------------------------------------

    def load_progress(self) -> dict:
        if self.progress_file.exists():
            with open(self.progress_file) as f:
                return json.load(f)
        return {"last_completed_date": None, "total_days": 0, "total_rows": 0, "failed_dates": []}

    def save_progress(self, progress: dict):
        self.progress_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.progress_file, 'w') as f:
            json.dump(progress, f, indent=2, default=str)

    # -- status ------------------------------------------------------------

    def get_status(self) -> dict:
        with self._status_lock:
            return dict(self._status)

    def _set_status(self, **kwargs):
        with self._status_lock:
            self._status.update(kwargs)

    # -- stop --------------------------------------------------------------

    def request_stop(self):
        """Signal the ingester to stop gracefully after the current date."""
        self._stop_event.set()
        self._set_status(state="stopping")

    @property
    def should_stop(self) -> bool:
        return self._stop_event.is_set()

    # -- DB ----------------------------------------------------------------

    def get_db_connection(self):
        if not HAS_PSYCOPG2:
            return None
        return psycopg2.connect(
            host=TIMESCALE_HOST, port=TIMESCALE_PORT,
            database=TIMESCALE_MARKET_DATA_DB,
            user=TIMESCALE_USERNAME, password=TIMESCALE_PASSWORD,
        )

    def _try_db_connect(self):
        try:
            conn = self.get_db_connection()
            self.setup_table(conn)
            return conn
        except Exception as e:
            logger.warning(f"[{self.pipeline_name}] DB unavailable ({e}), file-only mode")
            return None

    # -- file save ---------------------------------------------------------

    def save_to_files(self, df: pd.DataFrame, trade_date: date):
        if df.empty:
            return
        year = trade_date.year

        csv_year_dir = self.csv_dir / str(year)
        csv_year_dir.mkdir(parents=True, exist_ok=True)
        df.to_csv(csv_year_dir / self.csv_filename(trade_date), index=False)

        pq_year_dir = self.parquet_dir / f"year={year}"
        pq_year_dir.mkdir(parents=True, exist_ok=True)
        df.to_parquet(pq_year_dir / self.parquet_filename(trade_date), index=False, engine='pyarrow')

    # -- main loop ---------------------------------------------------------

    def ingest(self, start_date: date, end_date: date | None = None,
               skip_existing: bool = True) -> dict:
        """
        Run the ingestion loop.  Returns summary dict.
        Safe to call from any thread; registers itself so the API can track it.
        """
        if end_date is None:
            end_date = date.today() - timedelta(days=1)

        os.makedirs("logs", exist_ok=True)
        self._stop_event.clear()

        # Register
        with _registry_lock:
            _running_ingesters[self.pipeline_name] = self

        conn = self._try_db_connect()

        progress = self.load_progress()
        last_done = progress.get("last_completed_date")
        if last_done and skip_existing:
            resume_from = datetime.strptime(last_done, "%Y-%m-%d").date() + timedelta(days=1)
            if resume_from > start_date:
                logger.info(f"[{self.pipeline_name}] Resuming from {resume_from}")
                start_date = resume_from

        bdays = pd.bdate_range(start=start_date, end=end_date, freq='B')
        dates = [d.date() for d in bdays]

        self._set_status(
            state="running", started_at=datetime.now().isoformat(),
            current_date=None, processed=0, skipped=0, failed=0,
            total_dates=len(dates), error=None,
        )

        logger.info(f"[{self.pipeline_name}] Ingesting {start_date} → {end_date} ({len(dates)} bdays)")

        success = 0
        fail = 0
        consecutive_fails = 0
        total_rows = progress.get("total_rows", 0)

        try:
            for i, trade_date in enumerate(dates):
                if self.should_stop:
                    logger.info(f"[{self.pipeline_name}] Stop requested, saving progress")
                    break

                date_str = trade_date.strftime("%Y-%m-%d")
                self._set_status(current_date=date_str)

                # skip existing
                csv_path = self.csv_dir / str(trade_date.year) / self.csv_filename(trade_date)
                if skip_existing and csv_path.exists():
                    success += 1
                    consecutive_fails = 0
                    self._set_status(skipped=self._status["skipped"] + 1)
                    continue

                try:
                    raw_df = self.fetch_data(trade_date)
                    if raw_df is None or raw_df.empty:
                        fail += 1
                        consecutive_fails += 1
                        delay = self.max_delay * 2 if consecutive_fails > 5 else self.min_delay
                        time.sleep(delay)
                        self._set_status(failed=fail)
                        continue

                    consecutive_fails = 0
                    df = self.normalize_data(raw_df, trade_date)
                    if df.empty:
                        fail += 1
                        time.sleep(self.min_delay)
                        self._set_status(failed=fail)
                        continue

                    self.save_to_files(df, trade_date)

                    rows = len(df)
                    if conn is not None:
                        try:
                            rows = self.load_to_db(df, conn)
                            total_rows += rows
                        except Exception as db_err:
                            logger.warning(f"[{self.pipeline_name}] DB write failed ({db_err}), file-only")
                            try: conn.rollback()
                            except: pass
                            try: conn.close()
                            except: pass
                            conn = None

                    success += 1
                    progress["last_completed_date"] = date_str
                    progress["total_days"] = progress.get("total_days", 0) + 1
                    progress["total_rows"] = total_rows

                    if success % 20 == 0:
                        self.save_progress(progress)

                    if success % 50 == 0 or i < 10:
                        logger.info(
                            f"[{self.pipeline_name}] [{i+1}/{len(dates)}] {date_str}: "
                            f"{rows} rows | OK:{success} Fail:{fail}"
                        )

                    self._set_status(processed=success)
                    time.sleep(uniform(self.min_delay, self.max_delay))

                except Exception as e:
                    logger.error(f"[{self.pipeline_name}] {date_str}: {e}")
                    fail += 1
                    self._set_status(failed=fail)
                    if conn:
                        try: conn.close()
                        except: pass
                    conn = self._try_db_connect()
                    time.sleep(self.max_delay * 3)

        finally:
            self.save_progress(progress)
            if conn:
                try: conn.close()
                except: pass
            state = "finished" if not self.should_stop else "stopped"
            self._set_status(state=state)
            with _registry_lock:
                _running_ingesters.pop(self.pipeline_name, None)

        summary = {"success": success, "failed": fail, "total_rows": total_rows}
        logger.info(f"[{self.pipeline_name}] Done: {summary}")
        return summary
