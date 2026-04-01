#!/usr/bin/env python3
"""
yfinance_db_loader.py
---------------------
Loads yfinance per-symbol JSON files from fundamentals/yfinance/ into DB tables.

Creates two tables:
  fundamentals_quarterly  — quarterly income + balance sheet rows per symbol/date
  fundamentals_info       — latest snapshot of key valuation metrics (upserts)

Source:
  /media/vboxuser/test/NSE_Data/fundamentals/yfinance/{SYMBOL}.json
  Keys: quarterly_income, quarterly_balance, major_holders, info

Usage:
    python3 -m app.ingest.yfinance_db_loader [--data-root /path] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from datetime import date

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("yfinance_db_loader")

DATA_ROOT_DEFAULT = "/media/vboxuser/test/NSE_Data/fundamentals/yfinance"
PROGRESS_FILE     = Path("/media/vboxuser/test/NSE_Data/yfinance_db_loader_progress.json")

DB_HOST = "192.168.0.201"
DB_PORT = 5432
DB_NAME = "market_data"
DB_USER = "postgres"
DB_PASS = "postgres"

BATCH_SIZE = 10_000

# ---------------------------------------------------------------------------
# Schema DDL
# ---------------------------------------------------------------------------
DDL = """
CREATE TABLE IF NOT EXISTS fundamentals_quarterly (
    symbol          TEXT        NOT NULL,
    period_end      DATE        NOT NULL,
    statement       TEXT        NOT NULL,   -- 'income' or 'balance'
    metric          TEXT        NOT NULL,
    value           NUMERIC
);

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM timescaledb_information.hypertables
        WHERE hypertable_name = 'fundamentals_quarterly'
    ) THEN
        PERFORM create_hypertable(
            'fundamentals_quarterly', 'period_end',
            chunk_time_interval => INTERVAL '1 year',
            if_not_exists       => TRUE
        );
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS idx_fq_symbol_period_metric
    ON fundamentals_quarterly (symbol, period_end, statement, metric);

CREATE INDEX IF NOT EXISTS idx_fq_symbol ON fundamentals_quarterly (symbol, period_end DESC);

CREATE TABLE IF NOT EXISTS fundamentals_info (
    symbol          TEXT        NOT NULL,
    snapshot_date   DATE        NOT NULL,
    pe_trailing     NUMERIC,
    pe_forward      NUMERIC,
    pb_ratio        NUMERIC,
    roe             NUMERIC,
    roa             NUMERIC,
    debt_equity     NUMERIC,
    current_ratio   NUMERIC,
    rev_growth      NUMERIC,
    earn_growth     NUMERIC,
    eps_trailing    NUMERIC,
    eps_forward     NUMERIC,
    book_value      NUMERIC,
    div_yield       NUMERIC,
    payout_ratio    NUMERIC,
    market_cap      NUMERIC,
    enterprise_val  NUMERIC,
    ebitda          NUMERIC,
    total_revenue   NUMERIC,
    net_income      NUMERIC,
    total_debt      NUMERIC,
    total_cash      NUMERIC,
    free_cashflow   NUMERIC,
    op_cashflow     NUMERIC,
    beta            NUMERIC,
    profit_margin   NUMERIC,
    gross_margin    NUMERIC,
    op_margin       NUMERIC,
    sector          TEXT,
    industry        TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_fi_symbol_date
    ON fundamentals_info (symbol, snapshot_date);
CREATE INDEX IF NOT EXISTS idx_fi_symbol ON fundamentals_info (symbol, snapshot_date DESC);
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def get_conn():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASS, connect_timeout=15,
    )


def apply_ddl(conn):
    with conn.cursor() as cur:
        for stmt in DDL.split(";"):
            stmt = stmt.strip()
            if stmt and not stmt.startswith("--"):
                try:
                    cur.execute(stmt)
                    conn.commit()
                except Exception as e:
                    conn.rollback()
                    if "already" not in str(e).lower():
                        log.warning(f"DDL warning: {e}")


def load_progress() -> dict:
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE) as f:
            return json.load(f)
    return {"done": [], "quarterly_rows": 0, "info_rows": 0}


def save_progress(p: dict):
    tmp = PROGRESS_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(p, f, indent=2)
    tmp.replace(PROGRESS_FILE)


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------
INFO_MAP = {
    "trailingPE":        "pe_trailing",
    "forwardPE":         "pe_forward",
    "priceToBook":       "pb_ratio",
    "returnOnEquity":    "roe",
    "returnOnAssets":    "roa",
    "debtToEquity":      "debt_equity",
    "currentRatio":      "current_ratio",
    "revenueGrowth":     "rev_growth",
    "earningsGrowth":    "earn_growth",
    "trailingEps":       "eps_trailing",
    "forwardEps":        "eps_forward",
    "bookValue":         "book_value",
    "dividendYield":     "div_yield",
    "payoutRatio":       "payout_ratio",
    "marketCap":         "market_cap",
    "enterpriseValue":   "enterprise_val",
    "ebitda":            "ebitda",
    "totalRevenue":      "total_revenue",
    "netIncomeToCommon": "net_income",
    "totalDebt":         "total_debt",
    "totalCash":         "total_cash",
    "freeCashflow":      "free_cashflow",
    "operatingCashflow": "op_cashflow",
    "beta":              "beta",
    "profitMargins":     "profit_margin",
    "grossMargins":      "gross_margin",
    "operatingMargins":  "op_margin",
    "sector":            "sector",
    "industry":          "industry",
}


def parse_quarterly(symbol: str, data: dict) -> list[tuple]:
    """Extract (symbol, period_end, statement, metric, value) tuples."""
    rows = []
    for stmt_key, stmt_name in [("quarterly_income", "income"), ("quarterly_balance", "balance")]:
        stmt_data = data.get(stmt_key, {})
        for period_str, metrics in stmt_data.items():
            try:
                period_end = pd.to_datetime(period_str).date()
            except Exception:
                continue
            for metric, value in metrics.items():
                if value is not None:
                    try:
                        rows.append((symbol, period_end, stmt_name, str(metric), float(value)))
                    except (TypeError, ValueError):
                        pass
    return rows


def parse_info(symbol: str, data: dict) -> tuple | None:
    """Extract fundamentals_info row."""
    info = data.get("info", {})
    if not info:
        return None
    snap_date = date.today()
    row = {"symbol": symbol, "snapshot_date": snap_date}
    for src_key, dst_col in INFO_MAP.items():
        val = info.get(src_key)
        if val is not None:
            row[dst_col] = val
    return row


QUARTERLY_INSERT = """
    INSERT INTO fundamentals_quarterly (symbol, period_end, statement, metric, value)
    VALUES %s
    ON CONFLICT (symbol, period_end, statement, metric)
    DO UPDATE SET value = EXCLUDED.value
"""

INFO_COLS = [
    "symbol", "snapshot_date", "pe_trailing", "pe_forward", "pb_ratio",
    "roe", "roa", "debt_equity", "current_ratio", "rev_growth", "earn_growth",
    "eps_trailing", "eps_forward", "book_value", "div_yield", "payout_ratio",
    "market_cap", "enterprise_val", "ebitda", "total_revenue", "net_income",
    "total_debt", "total_cash", "free_cashflow", "op_cashflow", "beta",
    "profit_margin", "gross_margin", "op_margin", "sector", "industry",
]

INFO_INSERT = f"""
    INSERT INTO fundamentals_info ({', '.join(INFO_COLS)})
    VALUES %s
    ON CONFLICT (symbol, snapshot_date)
    DO UPDATE SET {', '.join(f'{c} = EXCLUDED.{c}' for c in INFO_COLS if c not in ('symbol', 'snapshot_date'))}
"""


def main():
    parser = argparse.ArgumentParser(description="Load yfinance JSON → DB")
    parser.add_argument("--data-root", default=DATA_ROOT_DEFAULT)
    parser.add_argument("--dry-run",   action="store_true")
    args = parser.parse_args()

    data_root = Path(args.data_root)
    if not data_root.exists():
        log.error(f"Data root not found: {data_root}")
        sys.exit(1)

    json_files = sorted(data_root.glob("*.json"))
    log.info(f"Found {len(json_files)} yfinance JSON files in {data_root}")

    progress = load_progress()
    done_set = set(progress.get("done", []))
    q_rows_total = progress.get("quarterly_rows", 0)
    i_rows_total = progress.get("info_rows", 0)

    pending = [f for f in json_files if f.stem not in done_set]
    log.info(f"Pending: {len(pending)} | Already done: {len(done_set)}")

    if not pending:
        log.info("Nothing to do.")
        return

    conn = None if args.dry_run else get_conn()

    if not args.dry_run:
        log.info("Applying DDL...")
        apply_ddl(conn)

    quarterly_batch = []
    info_batch      = []

    def flush_quarterly(cur):
        nonlocal q_rows_total
        if quarterly_batch:
            execute_values(cur, QUARTERLY_INSERT, quarterly_batch)
            q_rows_total += len(quarterly_batch)
            quarterly_batch.clear()

    def flush_info(cur):
        nonlocal i_rows_total
        if info_batch:
            rows = [tuple(row.get(c) for c in INFO_COLS) for row in info_batch]
            execute_values(cur, INFO_INSERT, rows)
            i_rows_total += len(info_batch)
            info_batch.clear()

    for i, jf in enumerate(pending, 1):
        symbol = jf.stem
        try:
            with open(jf) as f:
                data = json.load(f)
        except Exception as e:
            log.warning(f"[{i}/{len(pending)}] {symbol}: JSON read error: {e}")
            done_set.add(symbol)
            continue

        q_rows = parse_quarterly(symbol, data)
        quarterly_batch.extend(q_rows)

        info_row = parse_info(symbol, data)
        if info_row:
            info_batch.append(info_row)

        done_set.add(symbol)

        if len(quarterly_batch) >= BATCH_SIZE or (i % 100 == 0):
            if not args.dry_run:
                cur = conn.cursor()
                flush_quarterly(cur)
                flush_info(cur)
                conn.commit()
                cur.close()
            log.info(f"[{i}/{len(pending)}] {symbol} | quarterly_rows: {q_rows_total:,} | info_rows: {i_rows_total:,}")
            save_progress({"done": list(done_set), "quarterly_rows": q_rows_total, "info_rows": i_rows_total})

    # Final flush
    if not args.dry_run and (quarterly_batch or info_batch):
        cur = conn.cursor()
        flush_quarterly(cur)
        flush_info(cur)
        conn.commit()
        cur.close()

    save_progress({"done": list(done_set), "quarterly_rows": q_rows_total, "info_rows": i_rows_total})
    if conn:
        conn.close()

    log.info(f"Done. quarterly_rows={q_rows_total:,} | info_rows={i_rows_total:,}")


if __name__ == "__main__":
    main()
