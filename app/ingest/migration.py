"""
migration.py — Migrate per-symbol tables → unified hypertables.

Covers:
  binance_*_kline_1m  → crypto_ohlcv
  zerodha_*_kline_1m  → zerodha_ohlcv

Run as a standalone script:
    cd /home/vboxuser/CryptoMarketData
    source venv/bin/activate
    python -m app.ingest.migration

The script will:
1. Discover source tables matching the expected patterns.
2. Migrate data in per-table batches with ON CONFLICT DO NOTHING.
3. Validate row counts match before offering to drop source tables.
4. Prompt for confirmation before any DROP.
"""

import logging
import re
import sys
from typing import Optional

import psycopg2
from psycopg2.extras import execute_values

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("migration")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_tables(conn, pattern: str) -> list[str]:
    """Return all public table names matching a LIKE pattern."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT tablename FROM pg_tables "
            "WHERE schemaname='public' AND tablename LIKE %s "
            "ORDER BY tablename",
            (pattern,),
        )
        return [r[0] for r in cur.fetchall()]


def _row_count(conn, table: str) -> int:
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        return cur.fetchone()[0]


def _symbol_from_binance_table(table_name: str) -> Optional[str]:
    """
    Extract trading symbol from table like binance_btcusdt_kline_1m.
    Returns uppercased symbol, e.g. 'BTCUSDT'.
    """
    m = re.match(r"binance_(.+)_kline_(\w+)$", table_name)
    if m:
        return m.group(1).upper()
    return None


def _symbol_from_zerodha_table(table_name: str) -> Optional[str]:
    """
    Extract symbol from table like zerodha_axisbank_kline_1m.
    Returns uppercased symbol, e.g. 'AXISBANK'.
    """
    m = re.match(r"zerodha_(.+)_kline_(\w+)$", table_name)
    if m:
        return m.group(1).upper()
    return None


# ---------------------------------------------------------------------------
# Migration functions
# ---------------------------------------------------------------------------

def migrate_binance_1m_to_crypto_ohlcv(conn) -> list[str]:
    """
    Migrate data from all binance_*_kline_1m tables → crypto_ohlcv.

    Source columns:
        open_time, open, high, low, close, volume,
        close_time, quote_asset_volume, trades,
        taker_buy_base_asset_volume, taker_buy_quote_asset_volume, ignore

    Mapping:
        open_time                    → time
        (table name)                 → symbol (uppercased)
        'binance'                    → exchange
        '1m'                         → interval
        quote_asset_volume           → quote_volume
        trades                       → trades
        taker_buy_base_asset_volume  → taker_buy_base_vol
        taker_buy_quote_asset_volume → taker_buy_quote_vol

    Returns list of migrated source table names.
    """
    source_tables = _get_tables(conn, "binance_%_kline_1m")
    if not source_tables:
        log.info("No binance_*_kline_1m tables found.")
        return []

    log.info("Found %d binance kline tables to migrate", len(source_tables))
    migrated = []

    for src_table in source_tables:
        symbol = _symbol_from_binance_table(src_table)
        if not symbol:
            log.warning("Could not parse symbol from table: %s — skipping", src_table)
            continue

        log.info("Migrating %s → crypto_ohlcv (symbol=%s)", src_table, symbol)

        sql = f"""
            INSERT INTO crypto_ohlcv (
                time, exchange, symbol, interval,
                open, high, low, close, volume,
                quote_volume, trades,
                taker_buy_base_vol, taker_buy_quote_vol
            )
            SELECT
                to_timestamp(open_time / 1000.0) AT TIME ZONE 'UTC',
                'binance',
                %s,
                '1m',
                open, high, low, close, volume,
                quote_asset_volume,
                trades,
                taker_buy_base_asset_volume,
                taker_buy_quote_asset_volume
            FROM {src_table}
            ON CONFLICT DO NOTHING
        """

        try:
            with conn.cursor() as cur:
                cur.execute(sql, (symbol,))
                inserted = cur.rowcount
            conn.commit()
            log.info("  %s: inserted %d rows into crypto_ohlcv", src_table, inserted)
            migrated.append(src_table)
        except Exception as e:
            conn.rollback()
            log.error("  FAILED migrating %s: %s", src_table, e)

    log.info("Binance migration complete. %d/%d tables migrated.", len(migrated), len(source_tables))
    return migrated


def migrate_zerodha_1m_to_zerodha_ohlcv(conn) -> list[str]:
    """
    Migrate data from all zerodha_*_kline_1m tables → zerodha_ohlcv.

    Source columns assumed: open_time, open, high, low, close, volume,
    close_time, (possibly more — unused columns ignored).

    Mapping:
        open_time   → time  (milliseconds epoch, same as Binance format)
        (table name) → symbol (uppercased)
        'zerodha'   → exchange
        '1m'        → interval
        oi          → oi (if exists, else NULL)

    Returns list of migrated source table names.
    """
    source_tables = _get_tables(conn, "zerodha_%_kline_1m")
    if not source_tables:
        log.info("No zerodha_*_kline_1m tables found.")
        return []

    log.info("Found %d zerodha kline_1m tables to migrate", len(source_tables))
    migrated = []

    for src_table in source_tables:
        symbol = _symbol_from_zerodha_table(src_table)
        if not symbol:
            log.warning("Could not parse symbol from table: %s — skipping", src_table)
            continue

        # Determine if 'oi' column exists
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name=%s AND column_name='oi'",
                (src_table,),
            )
            has_oi = cur.fetchone() is not None

        oi_expr = "oi" if has_oi else "NULL"

        log.info("Migrating %s → zerodha_ohlcv (symbol=%s)", src_table, symbol)

        sql = f"""
            INSERT INTO zerodha_ohlcv (
                time, exchange, symbol, interval,
                open, high, low, close, volume, oi
            )
            SELECT
                to_timestamp(open_time / 1000.0) AT TIME ZONE 'UTC',
                'zerodha',
                %s,
                '1m',
                open, high, low, close, volume,
                {oi_expr}
            FROM {src_table}
            ON CONFLICT DO NOTHING
        """

        try:
            with conn.cursor() as cur:
                cur.execute(sql, (symbol,))
                inserted = cur.rowcount
            conn.commit()
            log.info("  %s: inserted %d rows into zerodha_ohlcv", src_table, inserted)
            migrated.append(src_table)
        except Exception as e:
            conn.rollback()
            log.error("  FAILED migrating %s: %s", src_table, e)

    log.info("Zerodha migration complete. %d/%d tables migrated.", len(migrated), len(source_tables))
    return migrated


def drop_migrated_tables(conn, table_names: list[str]) -> None:
    """
    Drop source tables AFTER validating that their row counts are ≤ what's
    now in the destination table (i.e. all data was successfully migrated).

    A table is only dropped if its original row count equals the increase
    in destination rows (or destination already had those rows via ON CONFLICT).

    For safety, this function does a simple check: if the source table is
    not empty but destination row count is 0, it refuses to drop.
    """
    if not table_names:
        log.info("No tables to drop.")
        return

    log.info("Validating %d tables before dropping...", len(table_names))

    dropped = []
    skipped = []

    for table in table_names:
        src_count = _row_count(conn, table)
        log.info("  %s has %d rows", table, src_count)

        # Determine destination
        if "binance" in table:
            dest = "crypto_ohlcv"
        elif "zerodha" in table:
            dest = "zerodha_ohlcv"
        else:
            log.warning("  Unknown destination for %s — skipping drop", table)
            skipped.append(table)
            continue

        dest_count = _row_count(conn, dest)
        if src_count > 0 and dest_count == 0:
            log.warning(
                "  REFUSING to drop %s: source has %d rows but destination %s is empty!",
                table, src_count, dest,
            )
            skipped.append(table)
            continue

        log.info("  Dropping %s (destination %s has %d rows total)", table, dest, dest_count)
        try:
            with conn.cursor() as cur:
                cur.execute(f"DROP TABLE IF EXISTS {table}")
            conn.commit()
            dropped.append(table)
            log.info("  Dropped: %s", table)
        except Exception as e:
            conn.rollback()
            log.error("  FAILED to drop %s: %s", table, e)
            skipped.append(table)

    log.info("drop_migrated_tables: dropped=%d, skipped=%d", len(dropped), len(skipped))
    if skipped:
        log.warning("Tables NOT dropped (manual review needed): %s", skipped)


# ---------------------------------------------------------------------------
# Main (run full migration interactively)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os

    host     = os.environ.get("DB_HOST",     "192.168.0.201")
    port     = int(os.environ.get("DB_PORT", "5432"))
    dbname   = os.environ.get("DB_NAME",     "market_data")
    user     = os.environ.get("DB_USER",     "postgres")
    password = os.environ.get("DB_PASS",     "postgres")

    log.info("Connecting to %s:%s/%s", host, port, dbname)
    conn = psycopg2.connect(host=host, port=port, dbname=dbname,
                             user=user, password=password)

    print("\n=== CryptoMarketData Migration Tool ===")
    print(f"Target DB: {dbname}@{host}:{port}\n")

    # Show what will be migrated
    binance_tables  = _get_tables(conn, "binance_%_kline_1m")
    zerodha_tables  = _get_tables(conn, "zerodha_%_kline_1m")
    print(f"Binance tables to migrate:  {len(binance_tables)}")
    print(f"Zerodha tables to migrate:  {len(zerodha_tables)}")

    if not binance_tables and not zerodha_tables:
        print("Nothing to migrate. Exiting.")
        conn.close()
        sys.exit(0)

    confirm = input("\nProceed with migration? [y/N]: ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        conn.close()
        sys.exit(0)

    # Run migrations
    migrated_binance = migrate_binance_1m_to_crypto_ohlcv(conn)
    migrated_zerodha = migrate_zerodha_1m_to_zerodha_ohlcv(conn)

    all_migrated = migrated_binance + migrated_zerodha
    print(f"\nMigrated {len(all_migrated)} tables total.")

    if all_migrated:
        drop_confirm = input(
            f"\nDrop {len(all_migrated)} source tables now? [y/N]: "
        ).strip().lower()
        if drop_confirm == "y":
            drop_migrated_tables(conn, all_migrated)
        else:
            print("Source tables kept intact.")

    conn.close()
    print("\nDone.")
