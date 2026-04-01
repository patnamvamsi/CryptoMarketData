"""
schema.py — Hypertable DDL definitions for the CryptoMarketData project.

Covers all unified OHLCV / event tables (new) and compression policies for
existing NSE tables.

Usage:
    from app.db.schema import apply_schema, apply_compression
    conn = psycopg2.connect(...)
    apply_schema(conn)
    apply_compression(conn)
"""

import logging
from typing import Optional

import psycopg2

log = logging.getLogger("schema")

# ---------------------------------------------------------------------------
# CREATE TABLE statements  (new tables only)
# ---------------------------------------------------------------------------
CREATE_TABLE_STATEMENTS: dict[str, str] = {
    "crypto_ohlcv": """
        CREATE TABLE IF NOT EXISTS crypto_ohlcv (
            time               TIMESTAMPTZ  NOT NULL,
            exchange           TEXT         NOT NULL,
            symbol             TEXT         NOT NULL,
            interval           TEXT         NOT NULL,
            open               NUMERIC,
            high               NUMERIC,
            low                NUMERIC,
            close              NUMERIC,
            volume             NUMERIC,
            quote_volume       NUMERIC,
            trades             INTEGER,
            taker_buy_base_vol NUMERIC,
            taker_buy_quote_vol NUMERIC
        )
    """,

    "zerodha_ohlcv": """
        CREATE TABLE IF NOT EXISTS zerodha_ohlcv (
            time       TIMESTAMPTZ  NOT NULL,
            exchange   TEXT         NOT NULL,
            symbol     TEXT         NOT NULL,
            interval   TEXT         NOT NULL,
            open       NUMERIC,
            high       NUMERIC,
            low        NUMERIC,
            close      NUMERIC,
            volume     NUMERIC,
            oi         NUMERIC
        )
    """,

    "corporate_events": """
        CREATE TABLE IF NOT EXISTS corporate_events (
            ex_date            DATE    NOT NULL,
            symbol             TEXT    NOT NULL,
            event_type         TEXT    NOT NULL,
            amount             NUMERIC,
            record_date        DATE,
            announcement_date  DATE
        )
    """,

    "fii_dii_fo": """
        CREATE TABLE IF NOT EXISTS fii_dii_fo (
            time        DATE    NOT NULL,
            category    TEXT    NOT NULL,
            buy_value   NUMERIC,
            sell_value  NUMERIC,
            net_value   NUMERIC
        )
    """,

    "global_signals": """
        CREATE TABLE IF NOT EXISTS global_signals (
            time         DATE    NOT NULL,
            signal_name  TEXT    NOT NULL,
            value        NUMERIC,
            source       TEXT
        )
    """,

    "fundamentals": """
        CREATE TABLE IF NOT EXISTS fundamentals (
            as_of_date   DATE    NOT NULL,
            symbol       TEXT    NOT NULL,
            pe_ratio     NUMERIC,
            pb_ratio     NUMERIC,
            div_yield    NUMERIC,
            market_cap   NUMERIC,
            revenue      NUMERIC,
            net_income   NUMERIC,
            debt_equity  NUMERIC
        )
    """,

    "fundamentals_quarterly": """
        CREATE TABLE IF NOT EXISTS fundamentals_quarterly (
            symbol          TEXT        NOT NULL,
            period_end      DATE        NOT NULL,
            statement       TEXT        NOT NULL,
            metric          TEXT        NOT NULL,
            value           NUMERIC
        )
    """,

    "fundamentals_info": """
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
        )
    """,

    # gdelt_sentiment lives in the 'social_media' DB — see app/ingest/gdelt_loader.py

    "options_iv": """
        CREATE TABLE IF NOT EXISTS options_iv (
            time          TIMESTAMPTZ  NOT NULL,
            symbol        TEXT         NOT NULL,
            expiry        DATE,
            strike        NUMERIC,
            option_type   TEXT,
            underlying    TEXT,
            spot_price    NUMERIC,
            settle_price  NUMERIC,
            dte           INTEGER,
            iv            NUMERIC,
            delta         NUMERIC,
            gamma         NUMERIC,
            theta         NUMERIC,
            vega          NUMERIC,
            rho           NUMERIC,
            div_yield     NUMERIC,
            div_yield_src TEXT,
            open_interest NUMERIC,
            contracts     NUMERIC
        )
    """,
}

# ---------------------------------------------------------------------------
# HYPERTABLE statements
# ---------------------------------------------------------------------------
# create_hypertable(table, time_col, chunk_time_interval, if_not_exists=>TRUE)
HYPERTABLE_STATEMENTS: dict[str, str] = {
    "crypto_ohlcv": """
        SELECT create_hypertable(
            'crypto_ohlcv', 'time',
            chunk_time_interval => INTERVAL '1 week',
            if_not_exists       => TRUE
        )
    """,
    "zerodha_ohlcv": """
        SELECT create_hypertable(
            'zerodha_ohlcv', 'time',
            chunk_time_interval => INTERVAL '1 week',
            if_not_exists       => TRUE
        )
    """,
    "corporate_events": """
        SELECT create_hypertable(
            'corporate_events', 'ex_date',
            chunk_time_interval => INTERVAL '1 year',
            if_not_exists       => TRUE
        )
    """,
    "fii_dii_fo": """
        SELECT create_hypertable(
            'fii_dii_fo', 'time',
            chunk_time_interval => INTERVAL '1 month',
            if_not_exists       => TRUE
        )
    """,
    "global_signals": """
        SELECT create_hypertable(
            'global_signals', 'time',
            chunk_time_interval => INTERVAL '1 month',
            if_not_exists       => TRUE
        )
    """,
    "fundamentals": """
        SELECT create_hypertable(
            'fundamentals', 'as_of_date',
            chunk_time_interval => INTERVAL '1 year',
            if_not_exists       => TRUE
        )
    """,
    "fundamentals_quarterly": """
        SELECT create_hypertable(
            'fundamentals_quarterly', 'period_end',
            chunk_time_interval => INTERVAL '1 year',
            if_not_exists       => TRUE
        )
    """,
    "fundamentals_info": """
        SELECT create_hypertable(
            'fundamentals_info', 'snapshot_date',
            chunk_time_interval => INTERVAL '1 year',
            if_not_exists       => TRUE
        )
    """,
    # gdelt_sentiment → social_media DB (see app/ingest/gdelt_loader.py)
    "options_iv": """
        SELECT create_hypertable(
            'options_iv', 'time',
            chunk_time_interval => INTERVAL '1 month',
            if_not_exists       => TRUE
        )
    """,
}

# ---------------------------------------------------------------------------
# INDEX statements
# ---------------------------------------------------------------------------
INDEX_STATEMENTS: dict[str, list[str]] = {
    "crypto_ohlcv": [
        "CREATE INDEX IF NOT EXISTS idx_crypto_ohlcv_symbol_time ON crypto_ohlcv (symbol, time DESC)",
        "CREATE INDEX IF NOT EXISTS idx_crypto_ohlcv_exchange_symbol ON crypto_ohlcv (exchange, symbol, time DESC)",
    ],
    "zerodha_ohlcv": [
        "CREATE INDEX IF NOT EXISTS idx_zerodha_ohlcv_symbol_time ON zerodha_ohlcv (symbol, time DESC)",
    ],
    "corporate_events": [
        "CREATE INDEX IF NOT EXISTS idx_corp_events_symbol ON corporate_events (symbol, ex_date DESC)",
    ],
    "fii_dii_fo": [
        "CREATE INDEX IF NOT EXISTS idx_fii_dii_fo_category ON fii_dii_fo (category, time DESC)",
    ],
    "global_signals": [
        "CREATE INDEX IF NOT EXISTS idx_global_signals_name ON global_signals (signal_name, time DESC)",
    ],
    "fundamentals": [
        "CREATE INDEX IF NOT EXISTS idx_fundamentals_symbol ON fundamentals (symbol, as_of_date DESC)",
    ],
    "fundamentals_quarterly": [
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_fq_symbol_period_metric ON fundamentals_quarterly (symbol, period_end, statement, metric)",
        "CREATE INDEX IF NOT EXISTS idx_fq_symbol ON fundamentals_quarterly (symbol, period_end DESC)",
    ],
    "fundamentals_info": [
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_fi_symbol_date ON fundamentals_info (symbol, snapshot_date)",
        "CREATE INDEX IF NOT EXISTS idx_fi_symbol ON fundamentals_info (symbol, snapshot_date DESC)",
    ],
    # gdelt_sentiment → social_media DB
    "options_iv": [
        "CREATE INDEX IF NOT EXISTS idx_options_iv_symbol_time ON options_iv (symbol, time DESC)",
        "CREATE INDEX IF NOT EXISTS idx_options_iv_expiry ON options_iv (symbol, expiry, time DESC)",
    ],
    # Existing tables — add secondary indexes
    "nse_equity_daily": [
        "CREATE INDEX IF NOT EXISTS idx_nse_equity_daily_symbol ON nse_equity_daily (symbol, time DESC)",
    ],
    "nse_fo_daily": [
        "CREATE INDEX IF NOT EXISTS idx_nse_fo_daily_symbol ON nse_fo_daily (symbol, time DESC)",
    ],
    "nse_index_daily": [
        "CREATE INDEX IF NOT EXISTS idx_nse_index_daily_name ON nse_index_daily (index_name, time DESC)",
    ],
}

# ---------------------------------------------------------------------------
# COMPRESSION statements
# ---------------------------------------------------------------------------
# Each entry is a list of SQL statements to run in order:
#   1. ALTER TABLE ... SET (timescaledb.compress, ...)
#   2. SELECT add_compression_policy(...)
COMPRESSION_STATEMENTS: dict[str, list[str]] = {
    # ---- new tables ----
    "crypto_ohlcv": [
        """
        ALTER TABLE crypto_ohlcv SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'symbol',
            timescaledb.compress_orderby   = 'time DESC'
        )
        """,
        "SELECT add_compression_policy('crypto_ohlcv', INTERVAL '7 days', if_not_exists => TRUE)",
    ],
    "zerodha_ohlcv": [
        """
        ALTER TABLE zerodha_ohlcv SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'symbol',
            timescaledb.compress_orderby   = 'time DESC'
        )
        """,
        "SELECT add_compression_policy('zerodha_ohlcv', INTERVAL '7 days', if_not_exists => TRUE)",
    ],
    "corporate_events": [
        """
        ALTER TABLE corporate_events SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'symbol',
            timescaledb.compress_orderby   = 'ex_date DESC'
        )
        """,
        "SELECT add_compression_policy('corporate_events', INTERVAL '3 months', if_not_exists => TRUE)",
    ],
    "fii_dii_fo": [
        """
        ALTER TABLE fii_dii_fo SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'category',
            timescaledb.compress_orderby   = 'time DESC'
        )
        """,
        "SELECT add_compression_policy('fii_dii_fo', INTERVAL '3 months', if_not_exists => TRUE)",
    ],
    "global_signals": [
        """
        ALTER TABLE global_signals SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'signal_name',
            timescaledb.compress_orderby   = 'time DESC'
        )
        """,
        "SELECT add_compression_policy('global_signals', INTERVAL '3 months', if_not_exists => TRUE)",
    ],
    "fundamentals": [
        """
        ALTER TABLE fundamentals SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'symbol',
            timescaledb.compress_orderby   = 'as_of_date DESC'
        )
        """,
        "SELECT add_compression_policy('fundamentals', INTERVAL '6 months', if_not_exists => TRUE)",
    ],
    "fundamentals_quarterly": [
        """
        ALTER TABLE fundamentals_quarterly SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'symbol',
            timescaledb.compress_orderby   = 'period_end DESC'
        )
        """,
        "SELECT add_compression_policy('fundamentals_quarterly', INTERVAL '1 year', if_not_exists => TRUE)",
    ],
    "fundamentals_info": [
        """
        ALTER TABLE fundamentals_info SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'symbol',
            timescaledb.compress_orderby   = 'snapshot_date DESC'
        )
        """,
        "SELECT add_compression_policy('fundamentals_info', INTERVAL '6 months', if_not_exists => TRUE)",
    ],
    # gdelt_sentiment → social_media DB
    "options_iv": [
        """
        ALTER TABLE options_iv SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'symbol',
            timescaledb.compress_orderby   = 'time DESC'
        )
        """,
        "SELECT add_compression_policy('options_iv', INTERVAL '1 month', if_not_exists => TRUE)",
    ],
    # ---- existing tables ----
    # nse_equity_daily: segmentby='symbol'
    "nse_equity_daily": [
        """
        ALTER TABLE nse_equity_daily SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'symbol',
            timescaledb.compress_orderby   = 'time DESC'
        )
        """,
        "SELECT add_compression_policy('nse_equity_daily', INTERVAL '3 months', if_not_exists => TRUE)",
    ],
    # nse_fo_daily: segmentby='symbol'
    "nse_fo_daily": [
        """
        ALTER TABLE nse_fo_daily SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'symbol',
            timescaledb.compress_orderby   = 'time DESC'
        )
        """,
        "SELECT add_compression_policy('nse_fo_daily', INTERVAL '3 months', if_not_exists => TRUE)",
    ],
    # nse_index_daily: no 'symbol' col — use index_name
    "nse_index_daily": [
        """
        ALTER TABLE nse_index_daily SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'index_name',
            timescaledb.compress_orderby   = 'time DESC'
        )
        """,
        "SELECT add_compression_policy('nse_index_daily', INTERVAL '3 months', if_not_exists => TRUE)",
    ],
}

# ---------------------------------------------------------------------------
# Apply helpers
# ---------------------------------------------------------------------------

def _exec(cur, sql: str, label: str) -> None:
    """Execute a single SQL statement, logging clearly on failure."""
    sql = sql.strip()
    try:
        cur.execute(sql)
        log.debug("OK: %s", label)
    except psycopg2.errors.DuplicateTable:
        log.debug("Already exists (DuplicateTable): %s", label)
    except psycopg2.errors.DuplicateObject:
        log.debug("Already exists (DuplicateObject): %s", label)
    except Exception as e:
        # For hypertable/compression errors that include "already" we treat as idempotent
        msg = str(e).lower()
        if "already" in msg or "duplicate" in msg:
            log.debug("Already done: %s — %s", label, e)
        else:
            log.warning("FAILED %s: %s", label, e)
            raise


def apply_schema(conn, tables: Optional[list] = None) -> None:
    """
    Apply CREATE TABLE, hypertable, and index DDL for the given tables.
    If tables=None, apply everything in CREATE_TABLE_STATEMENTS.

    Idempotent — uses IF NOT EXISTS everywhere; catches duplicate errors.
    """
    if tables is None:
        tables = list(CREATE_TABLE_STATEMENTS.keys())

    with conn.cursor() as cur:
        for table in tables:
            # 1. Create table
            if table in CREATE_TABLE_STATEMENTS:
                log.info("Creating table: %s", table)
                _exec(cur, CREATE_TABLE_STATEMENTS[table], f"CREATE TABLE {table}")
                conn.commit()

            # 2. Hypertable
            if table in HYPERTABLE_STATEMENTS:
                log.info("Creating hypertable: %s", table)
                try:
                    _exec(cur, HYPERTABLE_STATEMENTS[table], f"create_hypertable {table}")
                    conn.commit()
                except Exception:
                    conn.rollback()

            # 3. Indexes
            for idx_sql in INDEX_STATEMENTS.get(table, []):
                try:
                    _exec(cur, idx_sql, f"INDEX on {table}")
                    conn.commit()
                except Exception:
                    conn.rollback()

    log.info("apply_schema complete for: %s", tables)


def apply_compression(conn, tables: Optional[list] = None) -> None:
    """
    Apply compression settings and policies for the given tables.
    If tables=None, apply all entries in COMPRESSION_STATEMENTS.

    Idempotent — catches already-configured errors gracefully.
    """
    if tables is None:
        tables = list(COMPRESSION_STATEMENTS.keys())

    with conn.cursor() as cur:
        for table in tables:
            stmts = COMPRESSION_STATEMENTS.get(table, [])
            for stmt in stmts:
                try:
                    _exec(cur, stmt, f"COMPRESSION on {table}")
                    conn.commit()
                except Exception:
                    conn.rollback()

    log.info("apply_compression complete for: %s", tables)


# ---------------------------------------------------------------------------
# CLI convenience
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse, os
    import psycopg2

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="Apply TimescaleDB schema")
    parser.add_argument("--host", default=os.environ.get("DB_HOST", "192.168.0.201"))
    parser.add_argument("--port", type=int, default=5432)
    parser.add_argument("--dbname", default=os.environ.get("DB_NAME", "market_data"))
    parser.add_argument("--user", default="postgres")
    parser.add_argument("--password", default=os.environ.get("DB_PASS", "postgres"))
    parser.add_argument("--tables", nargs="*", help="Tables to process (default: all)")
    parser.add_argument("--compression-only", action="store_true")
    args = parser.parse_args()

    conn = psycopg2.connect(host=args.host, port=args.port,
                             dbname=args.dbname, user=args.user, password=args.password)
    try:
        if not args.compression_only:
            apply_schema(conn, args.tables)
        apply_compression(conn, args.tables)
    finally:
        conn.close()
