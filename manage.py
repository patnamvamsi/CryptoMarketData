#!/usr/bin/env python3
"""
manage.py — CLI for CryptoMarketData NSE ingestion pipelines.

Usage:
    python manage.py ingest equity --start 2000-01-01 --end 2026-03-05
    python manage.py ingest fo --start 2000-06-01
    python manage.py ingest index --start 2012-05-01
    python manage.py ingest all
    python manage.py ingest status
    python manage.py db load-files --source equity
    python manage.py db load-files --source all --format parquet
"""

import argparse
import json
import logging
import sys
import os
from datetime import date, datetime, timedelta

# Ensure project root is on sys.path so `app.*` imports work
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

# Prevent app/__init__.py from importing heavy DB/kafka deps when running CLI.
# We pre-populate the app package as a namespace so sub-imports work cleanly.
import types
if 'app' not in sys.modules:
    app_mod = types.ModuleType('app')
    app_mod.__path__ = [os.path.join(PROJECT_ROOT, 'app')]
    sys.modules['app'] = app_mod
if 'app.ingest' not in sys.modules:
    ingest_mod = types.ModuleType('app.ingest')
    ingest_mod.__path__ = [os.path.join(PROJECT_ROOT, 'app', 'ingest')]
    sys.modules['app.ingest'] = ingest_mod

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
)


def cmd_ingest(args):
    # ------------------------------------------------------------------ #
    # New standalone ingesters: corporate-events, gdelt                   #
    # ------------------------------------------------------------------ #
    if args.pipeline == "corporate-events":
        from app.ingest.nse_corporate_events import run_backfill, run_daily
        start = (datetime.strptime(args.start, "%Y-%m-%d").date()
                 if args.start else None)
        if start:
            print(f"▶ NSE corporate events backfill from {start}...")
            result = run_backfill(start=start)
        else:
            print("▶ NSE corporate events daily run...")
            result = run_daily()
        print(f"✓ corporate-events: {json.dumps(result, default=str)}")
        return

    if args.pipeline == "gdelt":
        from app.ingest.gdelt_ingest import run_backfill as gdelt_backfill, run_latest
        start = (datetime.strptime(args.start, "%Y-%m-%d").date()
                 if args.start else None)
        if start:
            print(f"▶ GDELT backfill from {start}...")
            result = gdelt_backfill(start=start)
        else:
            print("▶ GDELT latest run...")
            result = run_latest()
        print(f"✓ gdelt: {json.dumps(result, default=str)}")
        return

    # ------------------------------------------------------------------ #
    # Existing pipeline runner                                            #
    # ------------------------------------------------------------------ #
    from app.ingest.pipeline_runner import (
        start_ingest, get_all_status, PIPELINE_DEFAULTS, PIPELINE_CLASSES,
    )

    if args.pipeline == "status":
        status = get_all_status()
        print(json.dumps(status, indent=2, default=str))
        return

    pipelines = list(PIPELINE_CLASSES) if args.pipeline == "all" else [args.pipeline]

    for name in pipelines:
        start = (datetime.strptime(args.start, "%Y-%m-%d").date()
                 if args.start else None)
        end = (datetime.strptime(args.end, "%Y-%m-%d").date()
               if args.end else None)

        print(f"▶ Starting {name} ingestion...")
        result = start_ingest(
            name, start_date=start, end_date=end,
            skip_existing=not args.no_skip, background=False,
        )
        print(f"✓ {name}: {json.dumps(result, default=str)}")


def cmd_db(args):
    if args.db_command == "load-files":
        from app.ingest.bulk_db_loader import load_files_to_db
        print(f"▶ Bulk-loading {args.source} ({args.format}) files to TimescaleDB...")
        result = load_files_to_db(args.source, file_format=args.format)
        if result:
            print(f"✓ {json.dumps(result, default=str)}")
    else:
        print(f"Unknown db command: {args.db_command}")


def main():
    parser = argparse.ArgumentParser(
        prog="manage.py",
        description="CryptoMarketData management CLI",
    )
    sub = parser.add_subparsers(dest="command")

    # -- ingest -------------------------------------------------------------
    ingest_p = sub.add_parser("ingest", help="Run NSE data ingestion pipelines")
    ingest_p.add_argument(
        "pipeline",
        choices=["equity", "fo", "index", "all", "status", "corporate-events", "gdelt"],
        help=(
            "Pipeline to run: equity|fo|index|all|status|"
            "corporate-events|gdelt"
        ),
    )
    ingest_p.add_argument("--start", default=None, help="Start date YYYY-MM-DD")
    ingest_p.add_argument("--end", default=None, help="End date YYYY-MM-DD (default: yesterday)")
    ingest_p.add_argument("--no-skip", action="store_true", help="Re-download existing files")
    ingest_p.set_defaults(func=cmd_ingest)

    # -- db -----------------------------------------------------------------
    db_p = sub.add_parser("db", help="Database operations")
    db_sub = db_p.add_subparsers(dest="db_command")

    load_p = db_sub.add_parser("load-files", help="Bulk load CSV/Parquet files to TimescaleDB")
    load_p.add_argument(
        "--source",
        choices=["equity", "fo", "index", "all"],
        required=True,
        help="Which dataset to load",
    )
    load_p.add_argument(
        "--format",
        choices=["csv", "parquet"],
        default="csv",
        help="File format to read (default: csv)",
    )
    db_p.set_defaults(func=cmd_db)

    # -- parse & dispatch ---------------------------------------------------
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
