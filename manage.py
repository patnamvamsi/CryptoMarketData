#!/usr/bin/env python3
"""
manage.py — CLI for CryptoMarketData NSE ingestion pipelines.

Usage:
    python manage.py ingest equity --start 2000-01-01 --end 2026-03-05
    python manage.py ingest fo --start 2000-06-01
    python manage.py ingest index --start 2012-05-01
    python manage.py ingest all
    python manage.py ingest status
    python manage.py ingest corporate-events [--start YYYY-MM-DD]
    python manage.py ingest gdelt [--start YYYY-MM-DD]
    python manage.py ingest zerodha-ohlcv [--data-root /path] [--dry-run]
    python manage.py ingest crypto [--data-root /path] [--dry-run]
    python manage.py ingest options-iv [--start YYYY-MM-DD] [--end YYYY-MM-DD] [--resume]
    python manage.py ingest yfinance-db [--data-root /path] [--dry-run]
    python manage.py db load-files --source equity
    python manage.py db load-files --source all --format parquet
    python manage.py db schema [--tables TABLE ...]
    python manage.py status
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
    # zerodha-ohlcv: bulk-load all zerodha_intraday parquet → DB          #
    # ------------------------------------------------------------------ #
    if args.pipeline == "zerodha-ohlcv":
        from app.ingest.zerodha_ohlcv_loader import main as zerodha_main
        import sys as _sys
        argv_orig = _sys.argv
        _sys.argv = ["zerodha_ohlcv_loader"]
        if getattr(args, "data_root", None):
            _sys.argv += ["--data-root", args.data_root]
        if getattr(args, "dry_run", False):
            _sys.argv += ["--dry-run"]
        zerodha_main()
        _sys.argv = argv_orig
        return

    # ------------------------------------------------------------------ #
    # crypto: bulk-load Binance 1m CSV → DB                               #
    # ------------------------------------------------------------------ #
    if args.pipeline == "crypto":
        from app.ingest.crypto_csv_loader import main as crypto_main
        import sys as _sys
        argv_orig = _sys.argv
        _sys.argv = ["crypto_csv_loader"]
        if getattr(args, "data_root", None):
            _sys.argv += ["--data-root", args.data_root]
        if getattr(args, "dry_run", False):
            _sys.argv += ["--dry-run"]
        crypto_main()
        _sys.argv = argv_orig
        return

    # ------------------------------------------------------------------ #
    # options-iv: compute IV + Greeks from nse_fo_daily → options_iv      #
    # ------------------------------------------------------------------ #
    if args.pipeline == "options-iv":
        from app.ingest.options_iv_backfill import main as iv_main
        import sys as _sys
        argv_orig = _sys.argv
        _sys.argv = ["options_iv_backfill"]
        if getattr(args, "start", None):
            _sys.argv += ["--start", args.start]
        if getattr(args, "end", None):
            _sys.argv += ["--end", args.end]
        if getattr(args, "resume", False):
            _sys.argv += ["--resume"]
        if getattr(args, "dry_run", False):
            _sys.argv += ["--dry-run"]
        iv_main()
        _sys.argv = argv_orig
        return

    # ------------------------------------------------------------------ #
    # yfinance-db: load yfinance JSONs → fundamentals_quarterly + info    #
    # ------------------------------------------------------------------ #
    if args.pipeline == "yfinance-db":
        from app.ingest.yfinance_db_loader import main as yf_main
        import sys as _sys
        argv_orig = _sys.argv
        _sys.argv = ["yfinance_db_loader"]
        if getattr(args, "data_root", None):
            _sys.argv += ["--data-root", args.data_root]
        if getattr(args, "dry_run", False):
            _sys.argv += ["--dry-run"]
        yf_main()
        _sys.argv = argv_orig
        return

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
    elif args.db_command == "schema":
        import psycopg2
        from app.db.schema import apply_schema, apply_compression
        conn = psycopg2.connect(
            host="192.168.0.201", port=5432, dbname="market_data",
            user="postgres", password="postgres",
        )
        tables = getattr(args, "tables", None) or None
        print(f"▶ Applying schema for: {tables or 'all'}")
        apply_schema(conn, tables)
        apply_compression(conn, tables)
        conn.close()
        print("✓ Schema applied")
    else:
        print(f"Unknown db command: {args.db_command}")


def cmd_status(args):
    """Quick status: active processes, DB row counts, progress files."""
    import subprocess, os
    from pathlib import Path

    print("=== Active ingest processes ===")
    result = subprocess.run(
        ["pgrep", "-fa", "python"],
        capture_output=True, text=True,
    )
    for line in result.stdout.strip().splitlines():
        if any(k in line for k in ["ingest", "loader", "backfill", "chain"]):
            print(f"  {line}")

    print("\n=== Progress files ===")
    progress_files = [
        "/media/vboxuser/test/NSE_Data/zerodha_ohlcv_loader_progress.json",
        "/media/vboxuser/test/NSE_Data/crypto_csv_loader_progress.json",
        "/media/vboxuser/test/NSE_Data/options_iv_progress.json",
        "/media/vboxuser/test/NSE_Data/yfinance_db_loader_progress.json",
        "/media/vboxuser/test/NSE_Data/full_db_sync_state.txt",
    ]
    for pf in progress_files:
        p = Path(pf)
        if not p.exists():
            print(f"  {p.name}: not started")
            continue
        if pf.endswith(".json"):
            with open(pf) as f:
                data = json.load(f)
            if "processed_files" in data:
                print(f"  {p.name}: {len(data['processed_files']):,} files done, {data.get('total_inserted',0):,} rows")
            elif "done" in data:
                stats = data.get("stats", data.get("quarterly_rows", ""))
                print(f"  {p.name}: {len(data['done']):,} done | {stats}")
            elif "done_dates" in data:
                print(f"  {p.name}: {len(data['done_dates']):,} dates done, {data.get('total_rows',0):,} rows")
        else:
            lines = p.read_text().strip().splitlines()
            print(f"  {p.name}: {len(lines)} steps done")

    print("\n=== Recent log tails ===")
    logs = [
        "/tmp/zerodha_resume.log",
        "/tmp/chain_zerodha.log",
        "/tmp/chain_crypto.log",
    ]
    for lf in logs:
        if os.path.exists(lf):
            lines = open(lf).readlines()
            last = lines[-1].strip() if lines else "(empty)"
            print(f"  {os.path.basename(lf)}: {last}")


def main():
    parser = argparse.ArgumentParser(
        prog="manage.py",
        description="CryptoMarketData management CLI",
    )
    sub = parser.add_subparsers(dest="command")

    # -- ingest -------------------------------------------------------------
    ingest_p = sub.add_parser("ingest", help="Run NSE/crypto data ingestion pipelines")
    ingest_p.add_argument(
        "pipeline",
        choices=[
            "equity", "fo", "index", "all", "status",
            "corporate-events", "gdelt",
            "zerodha-ohlcv", "crypto",
            "options-iv", "yfinance-db",
        ],
        help=(
            "Pipeline to run: equity|fo|index|all|status|"
            "corporate-events|gdelt|zerodha-ohlcv|crypto|options-iv|yfinance-db"
        ),
    )
    ingest_p.add_argument("--start",     default=None,  help="Start date YYYY-MM-DD")
    ingest_p.add_argument("--end",       default=None,  help="End date YYYY-MM-DD (default: today)")
    ingest_p.add_argument("--no-skip",   action="store_true", help="Re-download existing files")
    ingest_p.add_argument("--resume",    action="store_true", help="Skip already-processed dates/files")
    ingest_p.add_argument("--dry-run",   action="store_true", help="Parse & validate without writing to DB")
    ingest_p.add_argument("--data-root", default=None,  help="Override data root directory")
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

    schema_p = db_sub.add_parser("schema", help="Apply schema DDL and compression policies")
    schema_p.add_argument(
        "--tables", nargs="*",
        help="Specific tables to apply (default: all)",
    )
    db_p.set_defaults(func=cmd_db)

    # -- status -------------------------------------------------------------
    status_p = sub.add_parser("status", help="Show active ingest processes and progress")
    status_p.set_defaults(func=cmd_status)

    # -- parse & dispatch ---------------------------------------------------
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
