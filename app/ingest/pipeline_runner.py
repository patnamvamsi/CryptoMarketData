"""
Pipeline Runner
===============
Central module for creating, launching, and querying NSE ingestion pipelines.
Used by both manage.py (CLI) and app/main.py (FastAPI + APScheduler).
"""

import logging
import os
import threading
from datetime import date, datetime, timedelta

from app.ingest.base_ingest import get_running_ingesters, SHARE_DIR
from app.ingest.equity_ingester import EquityIngester
from app.ingest.fo_ingester import FOIngester
from app.ingest.index_ingester import IndexIngester

logger = logging.getLogger(__name__)

# Pipeline defaults (start dates)
PIPELINE_DEFAULTS = {
    "equity": {"start": "2000-01-01"},
    "fo":     {"start": "2000-06-01"},
    "index":  {"start": "2012-05-01"},
}

PIPELINE_CLASSES = {
    "equity": EquityIngester,
    "fo":     FOIngester,
    "index":  IndexIngester,
}


def create_ingester(name: str):
    """Instantiate an ingester by pipeline name."""
    cls = PIPELINE_CLASSES.get(name)
    if cls is None:
        raise ValueError(f"Unknown pipeline: {name}. Choose from {list(PIPELINE_CLASSES)}")
    return cls()


def start_ingest(name: str, start_date: date | None = None,
                 end_date: date | None = None, skip_existing: bool = True,
                 background: bool = False) -> dict:
    """
    Start an ingestion pipeline.

    If background=True, runs in a daemon thread and returns immediately.
    Otherwise blocks until completion.
    """
    running = get_running_ingesters()
    if name in running:
        return {"error": f"Pipeline '{name}' is already running"}

    if start_date is None:
        start_date = datetime.strptime(PIPELINE_DEFAULTS[name]["start"], "%Y-%m-%d").date()
    if end_date is None:
        end_date = date.today() - timedelta(days=1)

    ingester = create_ingester(name)

    if background:
        t = threading.Thread(
            target=ingester.ingest,
            args=(start_date, end_date, skip_existing),
            name=f"ingest-{name}",
            daemon=True,
        )
        t.start()
        return {"status": "started", "pipeline": name,
                "start_date": str(start_date), "end_date": str(end_date)}
    else:
        return ingester.ingest(start_date, end_date, skip_existing)


def stop_ingest(name: str) -> dict:
    """Request a running pipeline to stop gracefully."""
    running = get_running_ingesters()
    ingester = running.get(name)
    if ingester is None:
        return {"error": f"Pipeline '{name}' is not running"}
    ingester.request_stop()
    return {"status": "stop_requested", "pipeline": name}


def get_all_status() -> dict:
    """Return status for all pipelines (running + last progress from files)."""
    result = {}
    running = get_running_ingesters()

    for name, cls in PIPELINE_CLASSES.items():
        info = {}
        # Live status if running
        if name in running:
            info["live"] = running[name].get_status()

        # Persisted progress from file
        ingester = cls()
        progress = ingester.load_progress()
        info["progress"] = progress
        result[name] = info

    return result


def daily_catchup(name: str):
    """
    Lightweight catch-up: fetch only missing days from last progress to yesterday.
    Designed to be called from APScheduler.
    """
    logger.info(f"[scheduler] Daily catch-up for {name}")
    ingester = create_ingester(name)
    progress = ingester.load_progress()
    last_done = progress.get("last_completed_date")

    if last_done:
        start = datetime.strptime(last_done, "%Y-%m-%d").date() + timedelta(days=1)
    else:
        start = datetime.strptime(PIPELINE_DEFAULTS[name]["start"], "%Y-%m-%d").date()

    end = date.today() - timedelta(days=1)

    if start > end:
        logger.info(f"[scheduler] {name}: already up to date (last: {last_done})")
        return

    ingester.ingest(start, end, skip_existing=True)


def daily_catchup_all():
    """Run daily catch-up for all three pipelines sequentially."""
    for name in PIPELINE_CLASSES:
        try:
            daily_catchup(name)
        except Exception as e:
            logger.error(f"[scheduler] {name} catch-up failed: {e}")
